"""Per-lead outreach drip: auto + open + due date → send template → advance stage.

Reuses existing Company/Freelancer templates + SMTP. Not campaign_sequences.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.logging import logger
from config.settings import settings
from db.sqlite import sqlite_manager
from email_campaigns.db import get_connection, get_smtp_config
from email_campaigns.smtp_sender import send_email
from email_campaigns.template_engine import build_context, resolve_variables

TEMPLATE_BY_TYPE_STAGE = {
    ("company", 1): "Company - Initial Outreach",
    ("company", 2): "Company - Follow-up",
    ("company", 3): "Company - Final Follow-up",
    ("personal", 1): "Freelancer - Initial Outreach",
    ("personal", 2): "Freelancer - Follow-up",
    ("personal", 3): "Freelancer - Final Follow-up",
}

STAGE_LABELS = {
    1: "Initial Outreach",
    2: "Follow-up",
    3: "Final Follow-up",
}

_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_last_daily_run_date: Optional[date] = None


def template_name_for(lead_type: str, stage: int) -> Optional[str]:
    lt = (lead_type or "").strip().lower()
    if lt not in ("company", "personal"):
        return None
    return TEMPLATE_BY_TYPE_STAGE.get((lt, int(stage or 1)))


def advance_after_send(
    stage: int,
    sent_on: date,
    gap_days: Optional[int] = None,
) -> Tuple[int, Optional[str], str]:
    """Return (next_stage, next_send_at YYYY-MM-DD|None, outreach_state)."""
    stage = int(stage or 1)
    if stage >= 3:
        return 3, None, "closed"
    next_stage = stage + 1
    gap = gap_days if gap_days is not None else int(settings.outreach_stage_gap_days)
    next_date = (sent_on + timedelta(days=gap)).isoformat()
    return next_stage, next_date, "open"


def _contacts_list(contacts: Any) -> List[Any]:
    if isinstance(contacts, str):
        try:
            contacts = json.loads(contacts)
        except Exception:
            contacts = []
    return contacts or []


def has_verified_email(contacts: Any) -> bool:
    """True if any contact has an address that passed SMTP verification."""
    for c in _contacts_list(contacts):
        if hasattr(c, "email"):
            email, verified = (getattr(c, "email", None) or "").strip(), bool(getattr(c, "is_verified", False))
        else:
            email, verified = (c.get("email") or "").strip(), bool(c.get("is_verified"))
        if email and "@" in email and verified:
            return True
    return False


def _first_contact_email(lead: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """First verified contact email only — never send to unverified / missing addresses."""
    for c in _contacts_list(lead.get("contacts")):
        email = (c.get("email") or "").strip()
        if email and "@" in email and c.get("is_verified"):
            return email, c.get("name"), c.get("role")
    return None, None, None


def _load_template_by_name(name: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM email_templates WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def send_one_outreach_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Send current-stage mail for one lead if eligible. Updates stage/date on success."""
    job_url = lead.get("job_url")
    lead_type = (lead.get("lead_type") or "others").lower()
    stage = int(lead.get("outreach_stage") or 1)
    mode = (lead.get("outreach_mode") or "manual").lower()
    state = (lead.get("outreach_state") or "open").lower()

    if mode != "auto":
        return {"ok": False, "skipped": "not_auto", "job_url": job_url}
    if state != "open":
        return {"ok": False, "skipped": "closed", "job_url": job_url}
    if lead_type not in ("company", "personal"):
        return {"ok": False, "skipped": "lead_type", "job_url": job_url}

    tpl_name = template_name_for(lead_type, stage)
    if not tpl_name:
        return {"ok": False, "error": "no_template", "job_url": job_url}
    tpl = _load_template_by_name(tpl_name)
    if not tpl:
        return {"ok": False, "error": f"template_missing:{tpl_name}", "job_url": job_url}

    to_email, person_name, role = _first_contact_email(lead)
    if not to_email:
        # Drop from auto queue until a verified address exists
        if job_url:
            sqlite_manager.update_lead_outreach(job_url, clear_next_send_at=True)
        return {"ok": False, "skipped": "no_verified_email", "job_url": job_url}

    smtp_cfg = get_smtp_config()
    sender_name = smtp_cfg.get("from_name", "HirePilot AI")
    ctx = build_context(
        person_name=person_name,
        role=role,
        job_requirement=lead.get("title"),
        company_name=lead.get("company"),
        website=lead.get("company_domain"),
        sender_name=sender_name,
        email=to_email,
        company_description=lead.get("company_summary") or lead.get("lead_summary"),
    )
    subject, body = resolve_variables(tpl.get("subject") or "", tpl.get("body") or "", ctx)
    ok, err = send_email(
        to_email,
        subject,
        body,
        smtp_cfg,
        attachment_path=tpl.get("attachment_path") or None,
        attachment_name=tpl.get("attachment_name") or None,
        cc=tpl.get("cc") or "",
    )
    if not ok:
        logger.error(f"Outreach send failed for {job_url}: {err}")
        return {"ok": False, "error": err, "job_url": job_url, "to": to_email}

    today = date.today()
    next_stage, next_send, new_state = advance_after_send(stage, today)
    sqlite_manager.update_lead_outreach(
        job_url,
        outreach_stage=next_stage,
        outreach_state=new_state,
        next_send_at=next_send,
        last_sent_at=datetime.utcnow().isoformat(),
        clear_next_send_at=(next_send is None),
    )
    return {
        "ok": True,
        "job_url": job_url,
        "to": to_email,
        "template": tpl_name,
        "sent_stage": stage,
        "next_stage": next_stage,
        "next_send_at": next_send,
        "outreach_state": new_state,
    }


def process_due_outreach(today: Optional[date] = None) -> Dict[str, Any]:
    """Send all due auto+open leads. Returns summary counts."""
    today = today or date.today()
    sqlite_manager.connect()
    due = sqlite_manager.get_due_outreach_leads(today.isoformat())
    results = []
    for lead in due:
        try:
            results.append(send_one_outreach_lead(lead))
        except Exception as e:
            logger.error(f"Outreach error for {lead.get('job_url')}: {e}")
            results.append({"ok": False, "error": str(e), "job_url": lead.get("job_url")})
        time.sleep(0.6)
    sent = sum(1 for r in results if r.get("ok"))
    failed = sum(1 for r in results if not r.get("ok") and r.get("error"))
    skipped = sum(1 for r in results if r.get("skipped"))
    logger.info(f"Outreach daily run: due={len(due)} sent={sent} failed={failed} skipped={skipped}")
    return {"due": len(due), "sent": sent, "failed": failed, "skipped": skipped, "results": results}


def _parse_schedule_time(time_str: str) -> Tuple[int, int]:
    cleaned = (time_str or "09:00").strip().upper()
    try:
        if "PM" in cleaned or "AM" in cleaned:
            dt = datetime.strptime(cleaned, "%I:%M %p")
            return dt.hour, dt.minute
        parts = cleaned.split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 9, 0


def _scheduler_loop() -> None:
    global _last_daily_run_date
    from config.settings import settings

    target = getattr(settings, "outreach_schedule_daily_time", "09:00")
    hour, minute = _parse_schedule_time(target)
    logger.info(f"📬 Outreach scheduler started. Daily fire at {hour:02d}:{minute:02d}")

    while not _stop_event.is_set():
        now = datetime.now()
        if (
            now.hour == hour
            and now.minute == minute
            and _last_daily_run_date != now.date()
        ):
            _last_daily_run_date = now.date()
            try:
                process_due_outreach(now.date())
            except Exception as e:
                logger.error(f"Outreach daily run failed: {e}", exc_info=True)
        _stop_event.wait(30)


def start_outreach_scheduler() -> None:
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="outreach-scheduler")
    _scheduler_thread.start()


def stop_outreach_scheduler() -> None:
    _stop_event.set()


# ponytail: one runnable check for drip + verified-email gate (fails if logic breaks)
if __name__ == "__main__":
    assert template_name_for("company", 1) == "Company - Initial Outreach"
    assert template_name_for("personal", 3) == "Freelancer - Final Follow-up"
    assert template_name_for("others", 1) is None
    s, d, st = advance_after_send(1, date(2026, 9, 10), gap_days=6)
    assert (s, d, st) == (2, "2026-09-16", "open")
    s, d, st = advance_after_send(3, date(2026, 9, 10), gap_days=6)
    assert (s, d, st) == (3, None, "closed")
    assert has_verified_email([{"email": "a@b.com", "is_verified": True}]) is True
    assert has_verified_email([{"email": "a@b.com", "is_verified": False}]) is False
    assert has_verified_email([{"email": "", "is_verified": True}]) is False
    assert _first_contact_email({"contacts": [{"email": "x@y.com", "is_verified": False, "name": "X"}]})[0] is None
    assert _first_contact_email({"contacts": [{"email": "x@y.com", "is_verified": True, "name": "X"}]}) == (
        "x@y.com",
        "X",
        None,
    )
    print("outreach_automation self-check OK")
