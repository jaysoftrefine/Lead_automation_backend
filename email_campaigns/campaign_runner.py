"""
Campaign Runner — fetches recipients from centralized SQLite (EU Startups & Job Leads), renders templates,
and dispatches bulk emails in a background thread with per-recipient logging.
"""

import json
import time
import uuid
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from email_campaigns.db import get_connection, get_smtp_config
from email_campaigns.smtp_sender import send_email
from email_campaigns.template_engine import build_context, resolve_variables


def _resolve_date_range(filters: Dict[str, Any]) -> tuple[Optional[str], Optional[str], str]:
    """Resolves date_from, date_to, and date_field from filters dictionary."""
    preset = (filters.get("date_preset") or "").strip().lower()
    date_from = (filters.get("date_from") or "").strip()
    date_to = (filters.get("date_to") or "").strip()
    date_field = (filters.get("date_field") or "any").strip().lower()

    if preset and preset not in ("custom", "all"):
        today = datetime.utcnow().date()
        if preset in ("today", "24h"):
            date_from = today.isoformat()
            date_to = today.isoformat()
        elif preset == "7d":
            date_from = (today - timedelta(days=7)).isoformat()
            date_to = today.isoformat()
        elif preset == "14d":
            date_from = (today - timedelta(days=14)).isoformat()
            date_to = today.isoformat()
        elif preset == "30d":
            date_from = (today - timedelta(days=30)).isoformat()
            date_to = today.isoformat()
        elif preset == "90d":
            date_from = (today - timedelta(days=90)).isoformat()
            date_to = today.isoformat()

    return date_from or None, date_to or None, date_field


def _get_recipients_from_sqlite(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Fetch enriched EU Startups people with valid emails as campaign recipients.
    Applies optional country / category / date filters.
    """
    from eu_startups.db import get_connection as get_eu_connection
    conn = get_eu_connection()
    cur = conn.cursor()

    wheres = [
        "p.email IS NOT NULL",
        "TRIM(p.email) != ''",
    ]
    params: List[Any] = []

    country = (filters.get("country") or "").strip()
    category = (filters.get("category") or "").strip()
    if country:
        wheres.append("LOWER(TRIM(s.country)) = LOWER(TRIM(?))")
        params.append(country)
    if category:
        wheres.append("LOWER(TRIM(s.category)) = LOWER(TRIM(?))")
        params.append(category)

    date_from, date_to, date_field = _resolve_date_range(filters)
    # Startups don't have job postings, so apply if any or scraped/added
    if date_field in ("any", "scraped", "added"):
        if date_from:
            wheres.append("substr(COALESCE(s.created_at, s.updated_at), 1, 10) >= ?")
            params.append(date_from)
        if date_to:
            wheres.append("substr(COALESCE(s.created_at, s.updated_at), 1, 10) <= ?")
            params.append(date_to)

    where_sql = "WHERE " + " AND ".join(wheres)

    rows = cur.execute(f"""
        SELECT
            p.name        AS person_name,
            p.role        AS role,
            p.email       AS email,
            s.company_name,
            s.website,
            s.city,
            s.country,
            s.category,
            s.description AS company_description,
            s.tags        AS company_tags,
            s.created_at,
            s.updated_at
        FROM people p
        JOIN startups s ON s.id = p.startup_id
        {where_sql}
        ORDER BY p.id
    """, params).fetchall()

    conn.close()
    recipients = []
    for r in rows:
        d = dict(r)
        d["date"] = (d.get("created_at") or "")[:10] or (d.get("updated_at") or "")[:10]
        d["date_posted"] = None
        d["scraped_at"] = d.get("created_at")
        recipients.append(d)
    return recipients


def _get_recipients_from_mongo(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Fetch LinkedIn-scraped leads from centralized SQLite database as campaign recipients.
    (Preserved with original name and alias for backward compatibility).
    """
    try:
        from db.sqlite import sqlite_manager
        sqlite_manager.connect()
        return sqlite_manager.get_recipients(filters)
    except Exception as e:
        logger.warning(f"Error getting recipients from SQLite job leads: {e}")
        return []


_get_recipients_from_job_leads = _get_recipients_from_mongo


def _get_manual_recipients(manual_emails: List[str]) -> List[Dict[str, Any]]:
    """Convert a list of raw email strings or structured entries to recipient dicts.
    
    Supported manual formats:
      1. Simple email: 'adam@poetry.hr'
      2. Name and email: 'Adam Smith <adam@poetry.hr>'
      3. Comma/Pipe-separated: 'Adam, Poetry, adam@poetry.hr' or 'Adam | Poetry | adam@poetry.hr | CEO'
    """
    recipients = []
    for item in manual_emails:
        item = item.strip()
        if not item:
            continue

        # Check for pipe or comma separated multi-field entry
        if "|" in item or ("," in item and not item.startswith("http")):
            delimiter = "|" if "|" in item else ","
            parts = [p.strip() for p in item.split(delimiter)]
            # Find which part contains '@'
            email_idx = -1
            for i, p in enumerate(parts):
                if "@" in p and "." in p:
                    email_idx = i
                    break
            
            if email_idx != -1:
                email_val = parts[email_idx]
                person_name = parts[0] if email_idx != 0 and len(parts) > 0 else ""
                company_name = parts[1] if len(parts) > 1 and email_idx != 1 else (parts[0] if email_idx != 0 else "")
                role = parts[3] if len(parts) > 3 and email_idx != 3 else ""
                website = parts[4] if len(parts) > 4 and email_idx != 4 else ""

                recipients.append({
                    "person_name":   person_name,
                    "role":          role,
                    "email":         email_val,
                    "company_name":  company_name,
                    "website":       website,
                    "city":          "",
                    "country":       "",
                    "category":      "",
                })
                continue

        # Check for 'Name <email@example.com>' format
        match = re.search(r'^(.*?)\s*<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>', item)
        if match:
            recipients.append({
                "person_name":   match.group(1).strip(),
                "role":          "",
                "email":         match.group(2).strip(),
                "company_name":  "",
                "website":       "",
                "city":          "",
                "country":       "",
                "category":      "",
            })
            continue

        # Fallback to pure email extraction
        if "@" in item:
            # Extract clean email
            email_clean = item.strip().strip("<>\"',;")
            recipients.append({
                "person_name":   "",
                "role":          "",
                "email":         email_clean,
                "company_name":  "",
                "website":       "",
                "city":          "",
                "country":       "",
                "category":      "",
            })

    return recipients


def _log_recipient(campaign_id: str, recipient: Dict, status: str, error: str = "", sender_email: Optional[str] = None) -> None:
    """Save one delivery log row to SQLite."""
    try:
        conn = get_connection()
        conn.execute("""
            INSERT INTO email_campaign_logs
                (id, campaign_id, recipient_name, recipient_email, company_name, status, error_message, sender_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            campaign_id,
            recipient.get("person_name") or "",
            recipient.get("email") or "",
            recipient.get("company_name") or "",
            status,
            error,
            sender_email or "",
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _update_campaign(campaign_id: str, **fields) -> None:
    """Update campaign row with arbitrary fields."""
    if not fields:
        return
    set_clauses = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [campaign_id]
    try:
        conn = get_connection()
        conn.execute(
            f"UPDATE email_campaigns SET {set_clauses}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_campaign_in_background(
    campaign_id: str,
    subject_template: str,
    body_template: str,
    recipients: List[Dict[str, Any]],
    delay_seconds: float = 0.8,
    attachment_path: Optional[str] = None,
    attachment_name: Optional[str] = None,
    smtp_account_id: Optional[str] = None,
    cc: Optional[str] = None,
) -> None:
    """
    Background thread worker: iterates recipients, renders per-person,
    sends email with optional attachment and CC, logs result, and updates counters.
    Uses designated SMTP account if provided, or default account.
    """
    smtp_cfg = get_smtp_config(smtp_account_id)
    sender_name = smtp_cfg.get("from_name", "HirePilot AI")
    sender_email = smtp_cfg.get("smtp_user", "")

    _update_campaign(campaign_id, status="running", total=len(recipients))

    sent = 0
    failed = 0

    for r in recipients:
        ctx = build_context(
            person_name=r.get("person_name"),
            role=r.get("role"),
            company_name=r.get("company_name"),
            website=r.get("website"),
            city=r.get("city"),
            country=r.get("country"),
            category=r.get("category"),
            sender_name=sender_name,
            email=r.get("email"),
        )
        rendered_subject, rendered_body = resolve_variables(subject_template, body_template, ctx)
        ok, err = send_email(
            r["email"], rendered_subject, rendered_body, smtp_cfg,
            attachment_path=attachment_path,
            attachment_name=attachment_name,
            cc=cc or "",
        )

        if ok:
            sent += 1
            _log_recipient(campaign_id, r, "sent", sender_email=sender_email)
        else:
            failed += 1
            _log_recipient(campaign_id, r, "failed", error=err, sender_email=sender_email)

        _update_campaign(campaign_id, sent=sent, failed_count=failed)
        time.sleep(delay_seconds)

    _update_campaign(
        campaign_id,
        status="completed",
        sent=sent,
        failed_count=failed,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


def collect_recipients(
    audience_sources: List[str],
    audience_filters: Dict[str, Any],
    manual_emails: Optional[List[str]] = None,
    selected_recipients: Optional[List[Dict[str, Any]]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Gather unique recipients across requested audience sources with optional limit."""
    recipients: List[Dict[str, Any]] = []
    seen_emails: set[str] = set()

    if selected_recipients:
        for r in selected_recipients:
            e = (r.get("email") or "").lower().strip()
            if e and e not in seen_emails:
                seen_emails.add(e)
                recipients.append(r)
                if limit and len(recipients) >= limit:
                    return recipients

    if "manual" in audience_sources and manual_emails:
        for r in _get_manual_recipients(manual_emails):
            e = (r.get("email") or "").lower().strip()
            if e and e not in seen_emails:
                seen_emails.add(e)
                recipients.append(r)
                if limit and len(recipients) >= limit:
                    return recipients

    # Only pull entire database leads if no specific handpicked recipients were chosen
    if not selected_recipients:
        if "sqlite" in audience_sources:
            for r in _get_recipients_from_sqlite(audience_filters):
                e = (r.get("email") or "").lower().strip()
                if e and e not in seen_emails:
                    seen_emails.add(e)
                    recipients.append(r)
                    if limit and len(recipients) >= limit:
                        return recipients

        if "mongo" in audience_sources or "job_leads" in audience_sources:
            for r in _get_recipients_from_mongo(audience_filters):
                e = (r.get("email") or "").lower().strip()
                if e and e not in seen_emails:
                    seen_emails.add(e)
                    recipients.append(r)
                    if limit and len(recipients) >= limit:
                        return recipients

    return recipients


def launch_campaign(
    campaign_id: str,
    subject_template: str,
    body_template: str,
    audience_sources: List[str],
    audience_filters: Dict[str, Any],
    manual_emails: Optional[List[str]] = None,
    selected_recipients: Optional[List[Dict[str, Any]]] = None,
    delay_seconds: float = 0.8,
    attachment_path: Optional[str] = None,
    attachment_name: Optional[str] = None,
    smtp_account_id: Optional[str] = None,
    cc: Optional[str] = None,
) -> int:
    """
    Build recipient list from requested sources and launch the campaign.
    Returns total recipient count.
    """
    recipients = collect_recipients(
        audience_sources=audience_sources,
        audience_filters=audience_filters,
        manual_emails=manual_emails,
        selected_recipients=selected_recipients,
    )
    total = len(recipients)
    _update_campaign(campaign_id, total=total, status="queued")

    t = threading.Thread(
        target=run_campaign_in_background,
        args=(campaign_id, subject_template, body_template, recipients, delay_seconds, attachment_path, attachment_name, smtp_account_id, cc),
        daemon=True,
    )
    t.start()
    return total


def count_recipients(
    audience_sources: List[str],
    audience_filters: Dict[str, Any],
    manual_emails: Optional[List[str]] = None,
    selected_recipients: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Estimate recipient count without launching the campaign."""
    seen_emails: set[str] = set()
    count = 0

    if selected_recipients:
        for r in selected_recipients:
            e = (r.get("email") or "").lower().strip()
            if e and e not in seen_emails:
                seen_emails.add(e)
                count += 1

    if "manual" in audience_sources and manual_emails:
        for e in manual_emails:
            e = e.strip().lower()
            if e and "@" in e and e not in seen_emails:
                seen_emails.add(e)
                count += 1

    # Only count entire database leads if no specific handpicked recipients were chosen
    if not selected_recipients:
        if "sqlite" in audience_sources:
            for r in _get_recipients_from_sqlite(audience_filters):
                e = (r.get("email") or "").lower().strip()
                if e and e not in seen_emails:
                    seen_emails.add(e)
                    count += 1

        if "mongo" in audience_sources or "job_leads" in audience_sources:
            for r in _get_recipients_from_mongo(audience_filters):
                e = (r.get("email") or "").lower().strip()
                if e and e not in seen_emails:
                    seen_emails.add(e)
                    count += 1

    return count
