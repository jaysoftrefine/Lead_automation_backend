"""Per-lead outreach drip: auto + open + due date -> send template -> advance stage.

Supports:
- Timezone-aware scheduling based on candidate/company location
- Configurable sender routing (both, company only, freelancer only)
- Multiple outreach stages with local business hour dispatch
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.logging import logger
from config.settings import settings
from db.sqlite import sqlite_manager
from email_campaigns.db import (
    get_connection,
    get_smtp_config,
    get_outreach_smtp_routing,
    get_outreach_smtp_config,
)
from email_campaigns.smtp_sender import send_email
from email_campaigns.template_engine import build_context, resolve_variables
from email_campaigns.timezone_helper import (
    resolve_timezone,
    calculate_stage_send_datetime,
)

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

DEFAULT_SEND_HOUR = 9
_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_last_daily_run_date: Optional[date] = None


def normalize_next_send_at(value: Optional[str], default_hour: int = DEFAULT_SEND_HOUR) -> Optional[str]:
    """Normalize to YYYY-MM-DDTHH:00:00 (hourly precision)."""
    raw = (value or "").strip()
    if not raw:
        return None
    raw = raw.replace(" ", "T")
    try:
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            date_part = raw[:10]
            hour = default_hour
            if "T" in raw:
                time_part = raw.split("T", 1)[1]
                hour = int(time_part.split(":")[0])
            if hour < 0 or hour > 23:
                hour = default_hour
            return f"{date_part}T{hour:02d}:00:00"
    except Exception:
        return None
    return None


def parse_send_hour(value: Optional[str], default_hour: int = DEFAULT_SEND_HOUR) -> int:
    norm = normalize_next_send_at(value, default_hour=default_hour)
    if not norm:
        return default_hour
    try:
        return int(norm[11:13])
    except Exception:
        return default_hour


def template_name_for(lead_type: str, stage: int) -> Optional[str]:
    lt = (lead_type or "").strip().lower()
    if lt in ("personal", "freelancer"):
        lt = "personal"
    if lt not in ("company", "personal"):
        return None
    return TEMPLATE_BY_TYPE_STAGE.get((lt, int(stage or 1)))


def advance_after_send(
    stage: int,
    sent_on: date,
    gap_days: Optional[int] = None,
    location: Optional[str] = None,
    send_hour: Optional[int] = None,
    resolved_tz: Optional[str] = None,
) -> Tuple[int, Optional[str], str]:
    """Return (next_stage, next_send_at ISO|None, outreach_state).
    Uses resolved_tz (cached IANA name) or falls back to geocoding location.
    """
    stage = int(stage or 1)
    if stage >= 3:
        return 3, None, "closed"
    next_stage = stage + 1
    gap = gap_days if gap_days is not None else int(settings.outreach_stage_gap_days)

    if send_hour is not None and 0 <= int(send_hour) <= 23:
        target_day = sent_on + timedelta(days=gap)
        next_send_iso = f"{target_day.isoformat()}T{int(send_hour):02d}:00:00"
    else:
        next_send_iso = calculate_stage_send_datetime(
            stage=next_stage,
            location=location,
            from_date=sent_on,
            delay_days=gap,
            resolved_tz=resolved_tz,
        )

    return next_stage, next_send_iso, "open"


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
        cur = conn.cursor()
        cur.execute("SELECT * FROM email_templates WHERE name = ? LIMIT 1", (name,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _send_single_template(
    tpl_name: str,
    lead: Dict[str, Any],
    to_email: str,
    person_name: Optional[str],
    role: Optional[str],
    smtp_cfg: Dict[str, Any],
    fallback_sender: str = "HirePilot AI",
) -> Tuple[bool, Optional[str]]:
    """Helper to render template variables and deliver email via specified SMTP config."""
    tpl = _load_template_by_name(tpl_name)
    if not tpl:
        return False, f"template_missing:{tpl_name}"

    sender_name = smtp_cfg.get("from_name") or fallback_sender
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
    return ok, err


def send_one_outreach_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Send current-stage mail for one lead if eligible.
    Supports firing Company, Freelancer, or Both templates, with sender routing and timezone scheduling.
    """
    job_url = lead.get("job_url")
    stage = int(lead.get("outreach_stage") or 1)
    mode = (lead.get("outreach_mode") or "manual").lower()
    state = (lead.get("outreach_state") or "open").lower()

    if mode != "auto":
        return {"ok": False, "skipped": "not_auto", "job_url": job_url}
    if state != "open":
        return {"ok": False, "skipped": "closed", "job_url": job_url}

    to_email, person_name, role = _first_contact_email(lead)
    if not to_email:
        if job_url:
            sqlite_manager.update_lead_outreach(job_url, clear_next_send_at=True)
        return {"ok": False, "skipped": "no_verified_email", "job_url": job_url}

    routing = get_outreach_smtp_routing()
    strategy = routing.get("sending_mode") or getattr(settings, "outreach_sending_mode", "both")

    sent_templates = []
    errors = []

    if strategy == "both":
        co_tpl = template_name_for("company", stage)
        co_smtp = get_outreach_smtp_config(lead_type="company")
        ok1, err1 = _send_single_template(co_tpl, lead, to_email, person_name, role, co_smtp, "Stephan Arnas")
        if ok1:
            sent_templates.append(co_tpl)
        else:
            errors.append(f"Company send failed: {err1}")

        time.sleep(1.5)

        exclude_id = co_smtp.get("account_id") or co_smtp.get("id")
        free_tpl = template_name_for("personal", stage)
        free_smtp = get_outreach_smtp_config(lead_type="personal", exclude_account_id=exclude_id)
        ok2, err2 = _send_single_template(free_tpl, lead, to_email, person_name, role, free_smtp, "Shani")
        if ok2:
            sent_templates.append(free_tpl)
        else:
            errors.append(f"Freelancer send failed: {err2}")

        if not ok1 and not ok2:
            logger.error(f"Both outreach sends failed for {job_url}: {errors}")
            return {"ok": False, "error": "; ".join(errors), "job_url": job_url, "to": to_email}

    elif strategy == "company":
        tpl_name = template_name_for("company", stage)
        smtp_cfg = get_outreach_smtp_config(lead_type="company")
        ok, err = _send_single_template(tpl_name, lead, to_email, person_name, role, smtp_cfg, "Stephan Arnas")
        if not ok:
            return {"ok": False, "error": err, "job_url": job_url, "to": to_email}
        sent_templates.append(tpl_name)

    else:  # freelancer
        tpl_name = template_name_for("personal", stage)
        smtp_cfg = get_outreach_smtp_config(lead_type="personal")
        ok, err = _send_single_template(tpl_name, lead, to_email, person_name, role, smtp_cfg, "Shani")
        if not ok:
            return {"ok": False, "error": err, "job_url": job_url, "to": to_email}
        sent_templates.append(tpl_name)

    today = date.today()
    location = lead.get("location") or lead.get("target_location")

    resolved_tz = lead.get("resolved_timezone") or None
    if not resolved_tz and location:
        resolved_tz = resolve_timezone(location)

    next_stage, next_send, new_state = advance_after_send(
        stage, today, location=location, resolved_tz=resolved_tz
    )

    update_kwargs: dict = dict(
        outreach_stage=next_stage,
        outreach_state=new_state,
        next_send_at=next_send,
        last_sent_at=datetime.utcnow().isoformat(),
        clear_next_send_at=(next_send is None),
    )
    if resolved_tz and not lead.get("resolved_timezone"):
        update_kwargs["resolved_timezone"] = resolved_tz

    sqlite_manager.update_lead_outreach(job_url, **update_kwargs)
    return {
        "ok": True,
        "job_url": job_url,
        "to": to_email,
        "templates": sent_templates,
        "strategy": strategy,
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
    target = getattr(settings, "outreach_schedule_daily_time", "09:00")
    hour, minute = _parse_schedule_time(target)
    logger.info(f"Outreach scheduler started. Daily fire at {hour:02d}:{minute:02d}")

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


if __name__ == "__main__":
    assert template_name_for("company", 1) == "Company - Initial Outreach"
    assert template_name_for("personal", 3) == "Freelancer - Final Follow-up"
    assert template_name_for("freelancer", 1) == "Freelancer - Initial Outreach"
    assert template_name_for("others", 1) is None
    assert normalize_next_send_at("2026-09-23") == "2026-09-23T09:00:00"
    assert normalize_next_send_at("2026-09-23T14:30") == "2026-09-23T14:00:00"
    assert parse_send_hour("2026-09-23T16:00:00") == 16
    s, d, st = advance_after_send(1, date(2026, 9, 10), gap_days=6, send_hour=14)
    assert (s, d, st) == (2, "2026-09-16T14:00:00", "open")
    s, d, st = advance_after_send(3, date(2026, 9, 10), gap_days=6)
    assert (s, d, st) == (3, None, "closed")
    assert has_verified_email([{"email": "a@b.com", "is_verified": True}]) is True
    assert has_verified_email([{"email": "a@b.com", "is_verified": False}]) is False
    print("outreach_automation self-check OK")
