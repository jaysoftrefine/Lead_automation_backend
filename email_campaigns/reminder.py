"""
Reminder — sends a heads-up email to the admin before a sequence step fires.
"""

import logging
from datetime import datetime
from typing import Dict, Any

from email_campaigns.smtp_sender import send_email
from email_campaigns.db import get_smtp_config

logger = logging.getLogger(__name__)


def send_reminder_email(
    to_email: str,
    step: Dict[str, Any],
    campaign_name: str,
    recipient_count: int,
    smtp_account_id: str | None = None,
) -> bool:
    """
    Send a reminder email to the admin about an upcoming sequence step.
    Returns True if sent successfully.
    """
    try:
        smtp_cfg = get_smtp_config(smtp_account_id)

        step_number    = step.get("step_number", "?")
        template_name  = step.get("template_name") or "Unknown Template"
        subject_text   = step.get("subject") or "(no subject)"
        scheduled_at   = step.get("scheduled_at", "")
        hours_before   = step.get("reminder_hours_before", 24)

        try:
            sched_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00")).replace(tzinfo=None)
            sched_display = sched_dt.strftime("%A, %B %d %Y at %H:%M UTC")
        except Exception:
            sched_display = scheduled_at or "Scheduled time unknown"

        reminder_subject = (
            f"[HirePilot] Tomorrow's Email: Step {step_number} of \"{campaign_name}\" sends to {recipient_count} people"
        )

        reminder_body = f"""Hi,

This is an automated reminder from HirePilot.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📧 TOMORROW'S EMAIL SEQUENCE STEP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Campaign    : {campaign_name}
Step        : #{step_number} — "{template_name}"
Subject     : {subject_text}
Recipients  : {recipient_count} people
Scheduled   : {sched_display}

⚠️  This email will be sent automatically tomorrow.

If you want to review the recipients or cancel this step, please log into
your HirePilot dashboard before the scheduled time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This reminder was sent to {to_email} at 4:00 PM (UTC) the day before sending,
because you configured it as the notification address for this sequence campaign.

— HirePilot AI
"""

        ok, err = send_email(
            to_email=to_email,
            subject=reminder_subject,
            body=reminder_body,
            smtp_cfg=smtp_cfg,
        )

        if ok:
            logger.info(
                f"✅ Reminder sent to {to_email} for step {step_number} of '{campaign_name}'"
            )
        else:
            logger.warning(
                f"⚠️ Reminder failed for step {step_number} of '{campaign_name}': {err}"
            )
        return ok

    except Exception as e:
        logger.error(f"❌ Exception sending reminder: {e}", exc_info=True)
        return False
