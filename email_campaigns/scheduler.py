"""
Sequence Scheduler — background thread that fires due drip campaign steps
and sends reminder emails. Runs every 5 minutes inside the same uvicorn process.
No Redis or Celery required.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How often the scheduler wakes up (seconds)
SCHEDULER_INTERVAL = 300  # 5 minutes

_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


# ─────────────────────────────────────────────────────────────
# Core logic
# ─────────────────────────────────────────────────────────────

def _fire_sequence_step(step: Dict[str, Any]) -> None:
    """
    Execute a single due sequence step:
      1. Load or collect recipients (from stored list or campaign audience for step 1)
      2. Spawn run_campaign_in_background for this step
      3. Mark step as running
    """
    from email_campaigns.db import (
        get_connection,
        update_sequence_step,
    )
    from email_campaigns.campaign_runner import (
        collect_recipients,
        run_campaign_in_background,
    )
    from email_campaigns.smtp_sender import send_email, test_smtp_connection
    from email_campaigns.db import get_smtp_config

    step_id     = step["id"]
    campaign_id = step["campaign_id"]
    step_number = step.get("step_number", 1)
    template_id = step["template_id"]
    smtp_account_id = step.get("smtp_account_id")

    logger.info(f"🚀 Firing sequence step #{step_number} (id={step_id}) for campaign {campaign_id}")

    try:
        # Get template body
        conn = get_connection()
        tpl  = conn.execute("SELECT * FROM email_templates WHERE id = ?", (template_id,)).fetchone()
        conn.close()
        if not tpl:
            logger.error(f"Template {template_id} not found for step {step_id}. Skipping.")
            update_sequence_step(step_id, status="failed")
            return

        # ── Always collect from the campaign's full audience config ──────────
        # Every step sends to all recipients defined in the campaign audience.
        # (The user can deselect individuals in selected_recipients before launch.)
        try:
            config = json.loads(step.get("audience_filter") or "{}")
        except Exception:
            config = {}

        audience_sources    = config.get("audience_sources", ["sqlite"])
        audience_filters    = config.get("audience_filters", {})
        manual_emails       = config.get("manual_emails", [])
        selected_recipients = config.get("selected_recipients", [])

        recipients = collect_recipients(
            audience_sources=audience_sources,
            audience_filters=audience_filters,
            manual_emails=manual_emails,
            selected_recipients=selected_recipients,
        )

        if not recipients:
            logger.warning(f"Empty recipient list for step #{step_number} (id={step_id}). Skipping.")
            update_sequence_step(step_id, status="skipped")
            return

        # Mark step as running before spawning
        update_sequence_step(step_id, status="running", fired_at=datetime.utcnow().isoformat())

        # Build a pseudo campaign_id for logs using step_id so logs are isolated per step
        # We reuse the campaign logs table with the step_id as pseudo-campaign
        pseudo_id = f"{campaign_id}__step{step_number}"

        # Ensure pseudo campaign row exists in email_campaigns for logging
        conn = get_connection()
        existing = conn.execute("SELECT id FROM email_campaigns WHERE id = ?", (pseudo_id,)).fetchone()
        if not existing:
            parent = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if parent:
                p = dict(parent)
                conn.execute("""
                    INSERT OR IGNORE INTO email_campaigns
                        (id, name, template_id, template_name, subject, status, audience_filter,
                         smtp_account_id, campaign_type)
                    VALUES (?, ?, ?, ?, ?, 'running', ?, ?, 'sequence_step')
                """, (
                    pseudo_id,
                    f"{p['name']} – Step {step_number}",
                    template_id,
                    step.get("template_name") or "",
                    step.get("subject") or "",
                    p.get("audience_filter") or "{}",
                    smtp_account_id or p.get("smtp_account_id"),
                ))
                conn.commit()
        conn.close()

        # Launch email sending in a background thread
        step_cc = (tpl.get("cc") if "cc" in tpl.keys() else "") or ""
        t = threading.Thread(
            target=_send_step_and_update,
            args=(
                step_id, pseudo_id,
                step.get("subject") or tpl["subject"],
                tpl["body"],
                recipients,
                smtp_account_id,
                tpl.get("attachment_path"),
                tpl.get("attachment_name"),
                step_cc,
            ),
            daemon=True,
        )
        t.start()

    except Exception as e:
        logger.error(f"❌ Error firing step {step_id}: {e}", exc_info=True)
        update_sequence_step(step_id, status="failed")


def _send_step_and_update(
    step_id: str,
    pseudo_campaign_id: str,
    subject_template: str,
    body_template: str,
    recipients: List[Dict[str, Any]],
    smtp_account_id: Optional[str],
    attachment_path: Optional[str],
    attachment_name: Optional[str],
    cc: Optional[str] = None,
) -> None:
    """Thread worker: sends emails for a step and updates step status when done."""
    from email_campaigns.db import update_sequence_step
    from email_campaigns.campaign_runner import run_campaign_in_background

    try:
        run_campaign_in_background(
            campaign_id=pseudo_campaign_id,
            subject_template=subject_template,
            body_template=body_template,
            recipients=recipients,
            delay_seconds=0.8,
            attachment_path=attachment_path,
            attachment_name=attachment_name,
            smtp_account_id=smtp_account_id,
            cc=cc,
        )
        update_sequence_step(
            step_id,
            status="completed",
            sent_count=len(recipients),
        )
        logger.info(f"✅ Sequence step {step_id} completed — {len(recipients)} sent.")
    except Exception as e:
        update_sequence_step(step_id, status="failed")
        logger.error(f"❌ Step {step_id} send failed: {e}", exc_info=True)


def _check_and_send_reminders() -> None:
    """Send reminder emails for steps whose reminder window has arrived."""
    from email_campaigns.db import (
        get_upcoming_steps_needing_reminder,
        update_sequence_step,
        load_sequence_recipients,
        get_connection,
    )
    from email_campaigns.reminder import send_reminder_email

    steps = get_upcoming_steps_needing_reminder()
    for step in steps:
        try:
            reminder_email = step.get("reminder_email") or ""
            if not reminder_email:
                continue

            step_id     = step["id"]
            step_number = step.get("step_number", 1)
            campaign_name = step.get("campaign_name") or "Campaign"
            smtp_account_id = step.get("smtp_account_id")

            # Count recipients: for step 1, query estimate; for later steps, count stored
            if step_number == 1:
                from email_campaigns.campaign_runner import count_recipients
                import json as _json
                try:
                    config = _json.loads(step.get("audience_filter") or "{}")
                except Exception:
                    config = {}
                count = count_recipients(
                    audience_sources=config.get("audience_sources", ["sqlite"]),
                    audience_filters=config.get("audience_filters", {}),
                    manual_emails=config.get("manual_emails", []),
                    selected_recipients=config.get("selected_recipients", []),
                )
            else:
                # Get step 1 ID for recipient count
                conn = get_connection()
                step1 = conn.execute(
                    "SELECT id FROM campaign_sequences WHERE campaign_id = ? AND step_number = 1",
                    (step["campaign_id"],)
                ).fetchone()
                conn.close()
                if step1:
                    count = len(load_sequence_recipients(step1["id"]))
                else:
                    count = 0

            ok = send_reminder_email(
                to_email=reminder_email,
                step=step,
                campaign_name=campaign_name,
                recipient_count=count,
                smtp_account_id=smtp_account_id,
            )

            if ok:
                update_sequence_step(step_id, reminder_sent=1)

        except Exception as e:
            logger.error(f"Error sending reminder for step {step.get('id')}: {e}", exc_info=True)


def _scheduler_loop() -> None:
    """Main scheduler loop — wakes every SCHEDULER_INTERVAL seconds."""
    logger.info(f"📅 Sequence scheduler started (interval={SCHEDULER_INTERVAL}s)")
    while not _stop_event.is_set():
        try:
            _check_and_send_reminders()
        except Exception as e:
            logger.error(f"Scheduler reminder check error: {e}", exc_info=True)

        try:
            from email_campaigns.db import get_due_sequence_steps
            due_steps = get_due_sequence_steps()
            if due_steps:
                logger.info(f"📬 Scheduler found {len(due_steps)} due sequence step(s).")
            for step in due_steps:
                _fire_sequence_step(step)
        except Exception as e:
            logger.error(f"Scheduler step-fire error: {e}", exc_info=True)

        # Wait for next cycle or until stop is requested
        _stop_event.wait(timeout=SCHEDULER_INTERVAL)

    logger.info("📅 Sequence scheduler stopped.")


# ─────────────────────────────────────────────────────────────
# Public lifecycle functions (called from app.py)
# ─────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Start the background scheduler thread. Safe to call multiple times."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.info("Scheduler already running.")
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="SequenceScheduler")
    _scheduler_thread.start()


def stop_scheduler() -> None:
    """Signal the scheduler to stop gracefully."""
    _stop_event.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=10)
    logger.info("Sequence scheduler stopped.")
