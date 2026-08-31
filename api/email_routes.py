"""
FastAPI Router — Email Templates & Bulk Campaign Endpoints.
Prefix: /api/email
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from email.db import get_connection, get_smtp_config, save_smtp_config
from email.smtp_sender import test_smtp_connection, send_email
from email.template_engine import get_sample_context, resolve_variables, AVAILABLE_VARIABLES
from email.campaign_runner import launch_campaign, count_recipients

router = APIRouter(prefix="/api/email", tags=["Email Campaigns"])


# ─────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────

class TemplateCreate(BaseModel):
    name: str
    subject: str
    body: str
    tags: Optional[str] = ""


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[str] = None


class SMTPConfigBody(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    from_name: str = "LeadPulse AI"
    use_ssl: bool = False
    use_tls: bool = True


class CampaignCreate(BaseModel):
    name: str
    template_id: str
    audience_sources: List[str] = ["sqlite"]   # "sqlite", "mongo", "manual"
    audience_filters: Dict[str, Any] = {}       # country, category
    manual_emails: Optional[List[str]] = None
    delay_seconds: float = 0.8


class TestEmailBody(BaseModel):
    to_email: str
    template_id: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None


# ─────────────────────────────────────────────
# SMTP Config Endpoints
# ─────────────────────────────────────────────

@router.get("/smtp/config")
def get_smtp() -> Dict[str, Any]:
    """Return current SMTP configuration (password masked)."""
    cfg = get_smtp_config()
    cfg["smtp_pass"] = "••••••••" if cfg.get("smtp_pass") else ""
    return {"status": "success", "data": cfg}


@router.post("/smtp/config")
def save_smtp(body: SMTPConfigBody) -> Dict[str, Any]:
    """Save SMTP configuration to the database."""
    save_smtp_config(
        host=body.smtp_host,
        port=body.smtp_port,
        user=body.smtp_user,
        password=body.smtp_pass,
        from_name=body.from_name,
        use_ssl=body.use_ssl,
        use_tls=body.use_tls,
    )
    return {"status": "success", "message": "SMTP configuration saved successfully."}


@router.post("/smtp/test")
def test_smtp(body: Optional[SMTPConfigBody] = None) -> Dict[str, Any]:
    """Test SMTP connection with provided or saved credentials."""
    if body:
        cfg = body.model_dump()
        cfg["smtp_host"] = cfg.pop("smtp_host")
        cfg["smtp_port"] = cfg.pop("smtp_port")
        cfg["smtp_user"] = cfg.pop("smtp_user")
        cfg["smtp_pass"] = cfg.pop("smtp_pass")
    else:
        cfg = get_smtp_config()
    ok, msg = test_smtp_connection(cfg)
    return {"status": "success" if ok else "failed", "message": msg, "connected": ok}


# ─────────────────────────────────────────────
# Template Endpoints
# ─────────────────────────────────────────────

@router.get("/templates")
def list_templates() -> Dict[str, Any]:
    """List all saved email templates."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM email_templates ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return {"status": "success", "data": [dict(r) for r in rows]}


@router.post("/templates")
def create_template(body: TemplateCreate) -> Dict[str, Any]:
    """Create a new email template."""
    tid = str(uuid.uuid4())
    conn = get_connection()
    conn.execute("""
        INSERT INTO email_templates (id, name, subject, body, tags)
        VALUES (?, ?, ?, ?, ?)
    """, (tid, body.name.strip(), body.subject.strip(), body.body, body.tags or ""))
    conn.commit()
    row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (tid,)).fetchone()
    conn.close()
    return {"status": "success", "message": "Template created.", "data": dict(row)}


@router.get("/templates/variables")
def get_variables() -> Dict[str, Any]:
    """Return list of supported {{variables}} for template authoring."""
    return {
        "status": "success",
        "data": [{"variable": v, "description": d} for v, d in AVAILABLE_VARIABLES],
    }


@router.get("/templates/{template_id}")
def get_template(template_id: str) -> Dict[str, Any]:
    """Fetch a single template by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (template_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found.")
    return {"status": "success", "data": dict(row)}


@router.put("/templates/{template_id}")
def update_template(template_id: str, body: TemplateUpdate) -> Dict[str, Any]:
    """Update an existing template."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Template not found.")

    updates = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.subject is not None:
        updates["subject"] = body.subject.strip()
    if body.body is not None:
        updates["body"] = body.body
    if body.tags is not None:
        updates["tags"] = body.tags

    if updates:
        set_sql = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE email_templates SET {set_sql}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            list(updates.values()) + [template_id],
        )
        conn.commit()

    row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (template_id,)).fetchone()
    conn.close()
    return {"status": "success", "message": "Template updated.", "data": dict(row)}


@router.delete("/templates/{template_id}")
def delete_template(template_id: str) -> Dict[str, Any]:
    """Delete a template."""
    conn = get_connection()
    row = conn.execute("SELECT id FROM email_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Template not found.")
    conn.execute("DELETE FROM email_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Template deleted."}


@router.post("/templates/{template_id}/preview")
def preview_template(template_id: str) -> Dict[str, Any]:
    """Render template with sample data for a live preview."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (template_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found.")

    cfg = get_smtp_config()
    ctx = get_sample_context(sender_name=cfg.get("from_name", "Your Name"))
    subj, body = resolve_variables(row["subject"], row["body"], ctx)
    return {
        "status": "success",
        "data": {
            "template_id": template_id,
            "rendered_subject": subj,
            "rendered_body": body,
            "sample_context": ctx,
        },
    }


@router.post("/templates/preview-raw")
def preview_raw(body: Dict[str, str]) -> Dict[str, Any]:
    """Render a raw subject + body (not yet saved) with sample data for live preview."""
    cfg = get_smtp_config()
    ctx = get_sample_context(sender_name=cfg.get("from_name", "Your Name"))
    subj, rendered_body = resolve_variables(
        body.get("subject", ""), body.get("body", ""), ctx
    )
    return {
        "status": "success",
        "data": {"rendered_subject": subj, "rendered_body": rendered_body, "sample_context": ctx},
    }


@router.post("/send-test")
def send_test_email(body: TestEmailBody) -> Dict[str, Any]:
    """Send a single test email to verify SMTP and template rendering."""
    cfg = get_smtp_config()
    ctx = get_sample_context(sender_name=cfg.get("from_name", "Your Name"))

    if body.template_id:
        conn = get_connection()
        row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (body.template_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found.")
        subject, html_body = resolve_variables(row["subject"], row["body"], ctx)
    elif body.subject and body.body:
        subject, html_body = resolve_variables(body.subject, body.body, ctx)
    else:
        raise HTTPException(status_code=400, detail="Provide template_id or subject+body.")

    ok, err = send_email(body.to_email, subject, html_body, cfg)
    if not ok:
        return {"status": "failed", "message": f"Failed to send: {err}"}
    return {"status": "success", "message": f"Test email sent to {body.to_email} ✓"}


# ─────────────────────────────────────────────
# Campaign Endpoints
# ─────────────────────────────────────────────

@router.get("/campaigns")
def list_campaigns() -> Dict[str, Any]:
    """List all past email campaigns."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM email_campaigns ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return {"status": "success", "data": [dict(r) for r in rows]}


@router.post("/campaigns")
def create_campaign(body: CampaignCreate) -> Dict[str, Any]:
    """Create and immediately launch a bulk email campaign."""
    # Validate template exists
    conn = get_connection()
    tpl = conn.execute(
        "SELECT * FROM email_templates WHERE id = ?", (body.template_id,)
    ).fetchone()
    if not tpl:
        conn.close()
        raise HTTPException(status_code=404, detail="Template not found.")

    # Validate SMTP before queueing
    smtp_ok, smtp_msg = test_smtp_connection()
    if not smtp_ok:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"SMTP not configured or invalid: {smtp_msg}. Please configure SMTP first.",
        )

    # Create campaign record
    cid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO email_campaigns
            (id, name, template_id, template_name, subject, status, audience_filter)
        VALUES (?, ?, ?, ?, ?, 'queued', ?)
    """, (
        cid, body.name.strip(), body.template_id,
        tpl["name"], tpl["subject"],
        json.dumps(body.audience_filters),
    ))
    conn.commit()
    conn.close()

    # Launch in background
    total = launch_campaign(
        campaign_id=cid,
        subject_template=tpl["subject"],
        body_template=tpl["body"],
        audience_sources=body.audience_sources,
        audience_filters=body.audience_filters,
        manual_emails=body.manual_emails,
        delay_seconds=body.delay_seconds,
    )

    return {
        "status": "success",
        "message": f"Campaign launched! Sending to {total} recipient(s).",
        "data": {"campaign_id": cid, "total_recipients": total},
    }


@router.get("/campaigns/estimate")
def estimate_recipients(
    sources: str = Query("sqlite", description="Comma-separated: sqlite,mongo,manual"),
    country: str = Query("", description="Filter by country"),
    category: str = Query("", description="Filter by category"),
    manual_emails: str = Query("", description="Comma-separated manual emails"),
) -> Dict[str, Any]:
    """Estimate how many recipients a campaign would have before launching."""
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    filters = {"country": country, "category": category}
    emails = [e.strip() for e in manual_emails.split(",") if e.strip()] if manual_emails else []
    total = count_recipients(source_list, filters, emails)
    return {"status": "success", "data": {"estimated_recipients": total}}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str) -> Dict[str, Any]:
    """Get campaign status and progress counters."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return {"status": "success", "data": dict(row)}


@router.get("/campaigns/{campaign_id}/logs")
def get_campaign_logs(
    campaign_id: str,
    status: str = Query("", description="Filter: sent, failed, or empty for all"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """Get per-recipient delivery logs for a campaign."""
    conn = get_connection()
    # Check campaign exists
    row = conn.execute(
        "SELECT id FROM email_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found.")

    where = "WHERE campaign_id = ?"
    params: List[Any] = [campaign_id]
    if status in ("sent", "failed", "pending"):
        where += " AND status = ?"
        params.append(status)

    total = conn.execute(
        f"SELECT COUNT(*) FROM email_campaign_logs {where}", params
    ).fetchone()[0]

    offset = (page - 1) * per_page
    logs = conn.execute(
        f"SELECT * FROM email_campaign_logs {where} ORDER BY sent_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()
    conn.close()

    return {
        "status": "success",
        "data": [dict(l) for l in logs],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
