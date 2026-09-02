import os
import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from email_campaigns.db import get_connection, get_smtp_config, save_smtp_config
from email_campaigns.smtp_sender import test_smtp_connection, send_email
from email_campaigns.template_engine import (
    get_sample_context,
    resolve_variables,
    AVAILABLE_VARIABLES,
    text_to_html_email,
    build_context,
)
from email_campaigns.campaign_runner import (
    launch_campaign,
    count_recipients,
    collect_recipients,
)

router = APIRouter(prefix="/api/email", tags=["Email Campaigns"])

ROOT_DIR = Path(__file__).resolve().parent.parent
ATTACHMENTS_DIR = ROOT_DIR / "uploads" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────

class TemplateCreate(BaseModel):
    name: str
    subject: str
    body: str
    tags: Optional[str] = ""
    attachment_path: Optional[str] = None
    attachment_name: Optional[str] = None


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[str] = None
    attachment_path: Optional[str] = None
    attachment_name: Optional[str] = None


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
    audience_sources: List[str] = ["sqlite"]   # "sqlite", "mongo", "manual", "selected"
    audience_filters: Dict[str, Any] = {}       # country, category
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    delay_seconds: float = 0.8
    draft: bool = False


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    template_id: Optional[str] = None
    audience_sources: Optional[List[str]] = None
    audience_filters: Optional[Dict[str, Any]] = None
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    delay_seconds: Optional[float] = None
    status: Optional[str] = None


class TestEmailBody(BaseModel):
    to_email: str
    template_id: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    attachment_path: Optional[str] = None
    attachment_name: Optional[str] = None


class CampaignPreviewGeneratedRequest(BaseModel):
    template_id: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    audience_sources: List[str] = ["sqlite"]
    audience_filters: Dict[str, Any] = {}
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    limit: int = 10


class AudienceCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    sources: List[str] = ["sqlite"]
    filters: Dict[str, Any] = {}
    manual_recipients: Optional[List[Any]] = None


class AudienceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sources: Optional[List[str]] = None
    filters: Optional[Dict[str, Any]] = None
    manual_recipients: Optional[List[Any]] = None


class QueueGenerateRequest(BaseModel):
    template_id: str
    audience_id: Optional[str] = None
    audience_sources: List[str] = ["sqlite"]
    audience_filters: Dict[str, Any] = {}
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    limit: int = 50


class QueueItemUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_email: Optional[str] = None
    company_name: Optional[str] = None


# ─────────────────────────────────────────────
# Attachment Upload Endpoint
# ─────────────────────────────────────────────

@router.post("/attachments/upload")
async def upload_attachment(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Upload a PDF attachment and store it in uploads/attachments/."""
    original_filename = file.filename or "document.pdf"
    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files (.pdf) are allowed as attachments.")

    file_uuid = uuid.uuid4().hex[:10]
    safe_filename = f"{file_uuid}_{original_filename.replace(' ', '_')}"
    destination = ATTACHMENTS_DIR / safe_filename

    try:
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save attachment file: {str(e)}")

    file_size_kb = round(destination.stat().st_size / 1024, 1)

    return {
        "status": "success",
        "message": f"Attached {original_filename} ({file_size_kb} KB)",
        "data": {
            "attachment_name": original_filename,
            "attachment_path": str(destination),
            "file_size_kb": file_size_kb,
        },
    }


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
    """Create a new email template with optional attachment."""
    tid = str(uuid.uuid4())
    conn = get_connection()
    conn.execute("""
        INSERT INTO email_templates (id, name, subject, body, tags, attachment_path, attachment_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (tid, body.name.strip(), body.subject.strip(), body.body, body.tags or "", body.attachment_path, body.attachment_name))
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
    if body.attachment_path is not None:
        updates["attachment_path"] = body.attachment_path
    if body.attachment_name is not None:
        updates["attachment_name"] = body.attachment_name

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
    html_preview = text_to_html_email(body)
    return {
        "status": "success",
        "data": {
            "template_id": template_id,
            "rendered_subject": subj,
            "rendered_body": html_preview,
            "sample_context": ctx,
            "attachment_name": row["attachment_name"] if "attachment_name" in row.keys() else None,
            "attachment_path": row["attachment_path"] if "attachment_path" in row.keys() else None,
        },
    }


@router.post("/templates/preview-raw")
def preview_raw(body: Dict[str, Any]) -> Dict[str, Any]:
    """Render a raw subject + body (not yet saved) with sample data for live preview."""
    cfg = get_smtp_config()
    ctx = get_sample_context(sender_name=cfg.get("from_name", "Your Name"))
    subj, rendered_body = resolve_variables(
        body.get("subject", ""), body.get("body", ""), ctx
    )
    html_preview = text_to_html_email(rendered_body)
    return {
        "status": "success",
        "data": {
            "rendered_subject": subj,
            "rendered_body": html_preview,
            "sample_context": ctx,
            "attachment_name": body.get("attachment_name"),
            "attachment_path": body.get("attachment_path"),
        },
    }


@router.post("/send-test")
def send_test_email(body: TestEmailBody) -> Dict[str, Any]:
    """Send a single test email to verify SMTP and template rendering with optional PDF attachment."""
    cfg = get_smtp_config()
    ctx = get_sample_context(sender_name=cfg.get("from_name", "Your Name"))

    attachment_path = body.attachment_path
    attachment_name = body.attachment_name

    if body.template_id:
        conn = get_connection()
        row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (body.template_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found.")
        subject, html_body = resolve_variables(row["subject"], row["body"], ctx)
        if not attachment_path and "attachment_path" in row.keys():
            attachment_path = row["attachment_path"]
            attachment_name = row["attachment_name"]
    elif body.subject and body.body:
        subject, html_body = resolve_variables(body.subject, body.body, ctx)
    else:
        raise HTTPException(status_code=400, detail="Provide template_id or subject+body.")

    ok, err = send_email(
        body.to_email, subject, html_body, cfg,
        attachment_path=attachment_path,
        attachment_name=attachment_name
    )
    if not ok:
        return {"status": "failed", "message": f"Failed to send: {err}"}
    return {
        "status": "success",
        "message": f"Test email sent to {body.to_email}{' with attachment' if attachment_path else ''} ✓"
    }


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
    """Create a campaign as draft or immediately launch it."""
    conn = get_connection()
    tpl = conn.execute(
        "SELECT * FROM email_templates WHERE id = ?", (body.template_id,)
    ).fetchone()
    if not tpl:
        conn.close()
        raise HTTPException(status_code=404, detail="Template not found.")

    cid = str(uuid.uuid4())
    attachment_path = tpl["attachment_path"] if "attachment_path" in tpl.keys() else None
    attachment_name = tpl["attachment_name"] if "attachment_name" in tpl.keys() else None

    # Pack full configuration into audience_filter JSON
    config_payload = {
        "audience_sources": body.audience_sources,
        "audience_filters": body.audience_filters,
        "manual_emails": body.manual_emails or [],
        "selected_recipients": body.selected_recipients or [],
        "delay_seconds": body.delay_seconds,
    }

    if body.draft:
        # Save as draft without launching
        conn.execute("""
            INSERT INTO email_campaigns
                (id, name, template_id, template_name, subject, attachment_path, attachment_name, status, audience_filter)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)
        """, (
            cid, body.name.strip(), body.template_id,
            tpl["name"], tpl["subject"],
            attachment_path, attachment_name,
            json.dumps(config_payload),
        ))
        conn.commit()
        row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (cid,)).fetchone()
        conn.close()
        return {
            "status": "success",
            "message": "Campaign saved as draft successfully.",
            "data": dict(row),
        }

    # Validate SMTP before launching
    smtp_ok, smtp_msg = test_smtp_connection()
    if not smtp_ok:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"SMTP not configured or invalid: {smtp_msg}. Please configure SMTP first.",
        )

    conn.execute("""
        INSERT INTO email_campaigns
            (id, name, template_id, template_name, subject, attachment_path, attachment_name, status, audience_filter)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)
    """, (
        cid, body.name.strip(), body.template_id,
        tpl["name"], tpl["subject"],
        attachment_path, attachment_name,
        json.dumps(config_payload),
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
        selected_recipients=body.selected_recipients,
        delay_seconds=body.delay_seconds,
        attachment_path=attachment_path,
        attachment_name=attachment_name,
    )

    return {
        "status": "success",
        "message": f"Campaign launched! Sending to {total} recipient(s){' with PDF attachment' if attachment_path else ''}.",
        "data": {"campaign_id": cid, "total_recipients": total},
    }


@router.put("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, body: CampaignUpdate) -> Dict[str, Any]:
    """Update an existing campaign draft or configuration."""
    conn = get_connection()
    camp = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not camp:
        conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found.")

    updates = {}
    if body.name is not None:
        updates["name"] = body.name.strip()

    if body.template_id is not None:
        tpl = conn.execute("SELECT * FROM email_templates WHERE id = ?", (body.template_id,)).fetchone()
        if tpl:
            updates["template_id"] = body.template_id
            updates["template_name"] = tpl["name"]
            updates["subject"] = tpl["subject"]
            if "attachment_path" in tpl.keys():
                updates["attachment_path"] = tpl["attachment_path"]
                updates["attachment_name"] = tpl["attachment_name"]

    if body.status is not None:
        updates["status"] = body.status

    # Merge audience filter configuration
    existing_filter = {}
    try:
        if camp["audience_filter"]:
            existing_filter = json.loads(camp["audience_filter"])
    except Exception:
        existing_filter = {}

    if body.audience_sources is not None:
        existing_filter["audience_sources"] = body.audience_sources
    if body.audience_filters is not None:
        existing_filter["audience_filters"] = body.audience_filters
    if body.manual_emails is not None:
        existing_filter["manual_emails"] = body.manual_emails
    if body.selected_recipients is not None:
        existing_filter["selected_recipients"] = body.selected_recipients
    if body.delay_seconds is not None:
        existing_filter["delay_seconds"] = body.delay_seconds

    updates["audience_filter"] = json.dumps(existing_filter)

    set_clauses = [f"{k} = ?" for k in updates.keys()]
    values = list(updates.values()) + [campaign_id]

    conn.execute(
        f"UPDATE email_campaigns SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        values,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()

    return {
        "status": "success",
        "message": "Campaign updated successfully.",
        "data": dict(row),
    }


@router.post("/campaigns/{campaign_id}/launch")
def launch_campaign_by_id(campaign_id: str) -> Dict[str, Any]:
    """Launch a previously saved/draft campaign."""
    conn = get_connection()
    camp = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not camp:
        conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found.")

    tpl = conn.execute("SELECT * FROM email_templates WHERE id = ?", (camp["template_id"],)).fetchone()
    if not tpl:
        conn.close()
        raise HTTPException(status_code=404, detail="Associated email template not found.")

    smtp_ok, smtp_msg = test_smtp_connection()
    if not smtp_ok:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"SMTP not configured or invalid: {smtp_msg}. Please configure SMTP first.",
        )

    # Parse config
    config = {}
    try:
        if camp["audience_filter"]:
            config = json.loads(camp["audience_filter"])
    except Exception:
        config = {}

    audience_sources = config.get("audience_sources", ["sqlite"])
    audience_filters = config.get("audience_filters", {})
    manual_emails = config.get("manual_emails", [])
    selected_recipients = config.get("selected_recipients", [])
    delay_seconds = config.get("delay_seconds", 0.8)

    attachment_path = camp["attachment_path"] if "attachment_path" in camp.keys() else tpl.get("attachment_path")
    attachment_name = camp["attachment_name"] if "attachment_name" in camp.keys() else tpl.get("attachment_name")

    conn.execute(
        "UPDATE email_campaigns SET status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (campaign_id,),
    )
    conn.commit()
    conn.close()

    total = launch_campaign(
        campaign_id=campaign_id,
        subject_template=tpl["subject"],
        body_template=tpl["body"],
        audience_sources=audience_sources,
        audience_filters=audience_filters,
        manual_emails=manual_emails,
        selected_recipients=selected_recipients,
        delay_seconds=delay_seconds,
        attachment_path=attachment_path,
        attachment_name=attachment_name,
    )

    return {
        "status": "success",
        "message": f"Campaign launched! Sending to {total} recipient(s).",
        "data": {"campaign_id": campaign_id, "total_recipients": total},
    }


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str) -> Dict[str, Any]:
    """Delete a campaign and its associated delivery logs."""
    conn = get_connection()
    conn.execute("DELETE FROM email_campaign_logs WHERE campaign_id = ?", (campaign_id,))
    res = conn.execute("DELETE FROM email_campaigns WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()

    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    return {"status": "success", "message": "Campaign deleted successfully."}


@router.post("/campaigns/preview-generated")
def preview_campaign_generated(body: CampaignPreviewGeneratedRequest) -> Dict[str, Any]:
    """Generate and preview the exact emails that will be sent to audience recipients."""
    conn = get_connection()
    subj_template = ""
    body_template = ""
    tpl_name = ""
    attachment_name = None

    if body.template_id:
        tpl = conn.execute("SELECT * FROM email_templates WHERE id = ?", (body.template_id,)).fetchone()
        if not tpl:
            conn.close()
            raise HTTPException(status_code=404, detail="Email template not found.")
        subj_template = tpl["subject"]
        body_template = tpl["body"]
        tpl_name = tpl["name"]
        attachment_name = tpl["attachment_name"] if "attachment_name" in tpl.keys() else None
    elif body.subject and body.body:
        subj_template = body.subject
        body_template = body.body
        tpl_name = "Custom Template"
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Provide template_id or subject+body.")

    conn.close()

    # Get sample recipients from targeted audience
    recipients = collect_recipients(
        audience_sources=body.audience_sources,
        audience_filters=body.audience_filters,
        manual_emails=body.manual_emails,
        selected_recipients=body.selected_recipients,
        limit=max(1, min(body.limit, 20)),
    )

    # Fallback to rich sample if no recipients found in audience
    if not recipients:
        cfg = get_smtp_config()
        sample_ctx = get_sample_context(sender_name=cfg.get("from_name", "Stephan Arnas"))
        recipients = [{
            "person_name": sample_ctx["name"],
            "role": sample_ctx["role"],
            "email": sample_ctx["email"],
            "company_name": sample_ctx["company_name"],
            "website": sample_ctx["company_website"],
            "city": sample_ctx["city"],
            "country": sample_ctx["country"],
            "category": sample_ctx["category"],
            "is_sample": True,
        }]

    cfg = get_smtp_config()
    sender_name = cfg.get("from_name", "Stephan Arnas")

    items = []
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
        rendered_subj, rendered_body = resolve_variables(subj_template, body_template, ctx)
        rendered_html = text_to_html_email(rendered_body)

        items.append({
            "recipient": {
                "person_name": r.get("person_name") or "",
                "role": r.get("role") or "",
                "email": r.get("email") or "",
                "company_name": r.get("company_name") or "",
                "website": r.get("website") or "",
                "city": r.get("city") or "",
                "country": r.get("country") or "",
                "category": r.get("category") or "",
                "is_sample": r.get("is_sample", False),
            },
            "rendered_subject": rendered_subj,
            "rendered_body": rendered_html,
            "raw_body": rendered_body,
            "context": ctx,
        })

    return {
        "status": "success",
        "data": {
            "template_name": tpl_name,
            "attachment_name": attachment_name,
            "total_previewed": len(items),
            "items": items,
        },
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


# ─────────────────────────────────────────────
# Audience Management Endpoints
# ─────────────────────────────────────────────

@router.get("/audiences")
def list_audiences() -> Dict[str, Any]:
    """List all saved audiences."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM email_audiences ORDER BY updated_at DESC").fetchall()
    conn.close()
    audiences = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d["sources"] or "[]")
        except Exception:
            d["sources"] = ["sqlite"]
        try:
            d["filters"] = json.loads(d["filters"] or "{}")
        except Exception:
            d["filters"] = {}
        try:
            d["manual_recipients"] = json.loads(d["manual_recipients"] or "[]")
        except Exception:
            d["manual_recipients"] = []
        audiences.append(d)
    return {"status": "success", "data": audiences}


@router.post("/audiences")
def create_audience(payload: AudienceCreate) -> Dict[str, Any]:
    """Create a new saved audience."""
    aud_id = str(uuid.uuid4())
    sources = payload.sources or ["sqlite"]
    filters = payload.filters or {}
    manual = payload.manual_recipients or []

    raw_manual_emails = [
        m if isinstance(m, str) else (m.get("email") or "")
        for m in manual
    ]
    raw_manual_emails = [e for e in raw_manual_emails if e]

    count = count_recipients(sources, filters, raw_manual_emails)

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO email_audiences (id, name, description, sources, filters, manual_recipients, contact_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aud_id,
            payload.name.strip(),
            payload.description or "",
            json.dumps(sources),
            json.dumps(filters),
            json.dumps(manual),
            count,
        ),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Audience saved.", "data": {"id": aud_id, "contact_count": count}}


@router.get("/audiences/{aud_id}")
def get_audience(aud_id: str) -> Dict[str, Any]:
    """Get single audience."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_audiences WHERE id = ?", (aud_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Audience not found.")
    d = dict(row)
    try:
        d["sources"] = json.loads(d["sources"] or "[]")
    except Exception:
        d["sources"] = ["sqlite"]
    try:
        d["filters"] = json.loads(d["filters"] or "{}")
    except Exception:
        d["filters"] = {}
    try:
        d["manual_recipients"] = json.loads(d["manual_recipients"] or "[]")
    except Exception:
        d["manual_recipients"] = []
    return {"status": "success", "data": d}


@router.put("/audiences/{aud_id}")
def update_audience(aud_id: str, payload: AudienceUpdate) -> Dict[str, Any]:
    """Update saved audience."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_audiences WHERE id = ?", (aud_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Audience not found.")

    current = dict(row)
    name = payload.name if payload.name is not None else current["name"]
    desc = payload.description if payload.description is not None else current["description"]
    sources = payload.sources if payload.sources is not None else json.loads(current["sources"] or "[]")
    filters = payload.filters if payload.filters is not None else json.loads(current["filters"] or "{}")
    manual = payload.manual_recipients if payload.manual_recipients is not None else json.loads(current["manual_recipients"] or "[]")

    raw_manual_emails = [
        m if isinstance(m, str) else (m.get("email") or "")
        for m in manual
    ]
    raw_manual_emails = [e for e in raw_manual_emails if e]

    count = count_recipients(sources, filters, raw_manual_emails)

    conn.execute(
        """
        UPDATE email_audiences
        SET name = ?, description = ?, sources = ?, filters = ?, manual_recipients = ?, contact_count = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (name.strip(), desc, json.dumps(sources), json.dumps(filters), json.dumps(manual), count, aud_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Audience updated.", "data": {"id": aud_id, "contact_count": count}}


@router.delete("/audiences/{aud_id}")
def delete_audience(aud_id: str) -> Dict[str, Any]:
    """Delete saved audience."""
    conn = get_connection()
    res = conn.execute("DELETE FROM email_audiences WHERE id = ?", (aud_id,))
    conn.commit()
    conn.close()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Audience not found.")
    return {"status": "success", "message": "Audience deleted."}


# ─────────────────────────────────────────────
# 1-by-1 Review Queue Endpoints
# ─────────────────────────────────────────────

@router.post("/queue/generate")
def generate_review_queue(body: QueueGenerateRequest) -> Dict[str, Any]:
    """Generate personalized outreach emails into the 1-by-1 review queue."""
    conn = get_connection()
    tpl = conn.execute("SELECT * FROM email_templates WHERE id = ?", (body.template_id,)).fetchone()
    if not tpl:
        conn.close()
        raise HTTPException(status_code=404, detail="Email template not found.")

    sources = body.audience_sources
    filters = body.audience_filters
    manual = body.manual_emails
    selected = body.selected_recipients

    if body.audience_id:
        aud = conn.execute("SELECT * FROM email_audiences WHERE id = ?", (body.audience_id,)).fetchone()
        if aud:
            sources = json.loads(aud["sources"] or "[]")
            filters = json.loads(aud["filters"] or "{}")
            raw_recips = json.loads(aud["manual_recipients"] or "[]")
            manual = [m if isinstance(m, str) else (m.get("email") or "") for m in raw_recips]

    recipients = collect_recipients(
        audience_sources=sources,
        audience_filters=filters,
        manual_emails=manual,
        selected_recipients=selected,
        limit=max(1, min(body.limit, 200)),
    )

    if not recipients:
        conn.close()
        raise HTTPException(status_code=400, detail="No recipients found for this audience. Check filters or manual contacts.")

    cfg = get_smtp_config()
    sender_name = cfg.get("from_name", "Stephan Arnas")

    created_items = []
    for r in recipients:
        item_id = str(uuid.uuid4())
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
        rendered_subj, rendered_body = resolve_variables(tpl["subject"], tpl["body"], ctx)
        rendered_html = text_to_html_email(rendered_body)

        conn.execute(
            """
            INSERT INTO email_queue_items (
                id, template_id, template_name, audience_id,
                recipient_name, recipient_email, company_name, role, website, city, country, category,
                subject, body, raw_body, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                item_id,
                tpl["id"],
                tpl["name"],
                body.audience_id,
                r.get("person_name") or "",
                r.get("email") or "",
                r.get("company_name") or "",
                r.get("role") or "",
                r.get("website") or "",
                r.get("city") or "",
                r.get("country") or "",
                r.get("category") or "",
                rendered_subj,
                rendered_html,
                rendered_body,
            ),
        )
        created_items.append({
            "id": item_id,
            "recipient_name": r.get("person_name") or "",
            "recipient_email": r.get("email") or "",
            "company_name": r.get("company_name") or "",
            "subject": rendered_subj,
            "status": "draft",
        })

    conn.commit()
    conn.close()

    return {
        "status": "success",
        "message": f"Generated {len(created_items)} emails into the review queue.",
        "data": {
            "count": len(created_items),
            "items": created_items,
        },
    }


@router.get("/queue")
def list_queue(
    status: str = Query("", description="Filter: draft, sent, failed, or empty for all"),
    q: str = Query("", description="Search recipient name, company, email, or subject"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """List 1-by-1 generated outreach queue items."""
    conn = get_connection()
    status_str = status if isinstance(status, str) else ""
    q_str = q if isinstance(q, str) else ""
    page_num = page if isinstance(page, int) else 1
    per_page_num = per_page if isinstance(per_page, int) else 50

    wheres = []
    params: List[Any] = []

    if status_str in ("draft", "sent", "failed"):
        wheres.append("status = ?")
        params.append(status_str)

    if q_str.strip():
        search = f"%{q_str.strip()}%"
        wheres.append("(recipient_name LIKE ? OR recipient_email LIKE ? OR company_name LIKE ? OR subject LIKE ?)")
        params.extend([search, search, search, search])

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    total = conn.execute(f"SELECT COUNT(*) FROM email_queue_items {where_sql}", params).fetchone()[0]

    offset = (page_num - 1) * per_page_num
    rows = conn.execute(
        f"SELECT * FROM email_queue_items {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page_num, offset],
    ).fetchall()
    conn.close()

    return {
        "status": "success",
        "data": [dict(r) for r in rows],
        "total": total,
        "page": page_num,
        "per_page": per_page_num,
    }


@router.get("/queue/{item_id}")
def get_queue_item(item_id: str) -> Dict[str, Any]:
    """Get a single queue item."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_queue_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    return {"status": "success", "data": dict(row)}


@router.put("/queue/{item_id}")
def update_queue_item(item_id: str, body: QueueItemUpdate) -> Dict[str, Any]:
    """Edit an individual queue item's subject, body, or recipient before sending."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_queue_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Queue item not found.")

    current = dict(row)
    new_subj = body.subject if body.subject is not None else current["subject"]
    new_name = body.recipient_name if body.recipient_name is not None else current["recipient_name"]
    new_email = body.recipient_email if body.recipient_email is not None else current["recipient_email"]
    new_company = body.company_name if body.company_name is not None else current["company_name"]

    new_body = current["body"]
    new_raw = current["raw_body"]
    if body.body is not None:
        new_raw = body.body
        new_body = text_to_html_email(body.body)

    conn.execute(
        """
        UPDATE email_queue_items
        SET subject = ?, body = ?, raw_body = ?, recipient_name = ?, recipient_email = ?, company_name = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_subj, new_body, new_raw, new_name, new_email, new_company, item_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Email draft updated."}


@router.post("/queue/{item_id}/send")
def send_queue_item(item_id: str) -> Dict[str, Any]:
    """Send this single individual email from the queue via SMTP."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_queue_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Queue item not found.")

    item = dict(row)
    to_email = (item["recipient_email"] or "").strip()
    if not to_email:
        conn.close()
        raise HTTPException(status_code=400, detail="Recipient email is empty.")

    attachment_path = None
    attachment_name = None
    if item.get("template_id"):
        tpl = conn.execute("SELECT attachment_path, attachment_name FROM email_templates WHERE id = ?", (item["template_id"],)).fetchone()
        if tpl:
            attachment_path = tpl["attachment_path"]
            attachment_name = tpl["attachment_name"]

    cfg = get_smtp_config()
    success, err_msg = send_email(
        to_email=to_email,
        subject=item["subject"],
        body=item["body"],
        smtp_cfg=cfg,
        attachment_path=attachment_path,
        attachment_name=attachment_name,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    if success:
        conn.execute(
            "UPDATE email_queue_items SET status = 'sent', sent_at = ?, error_message = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (now_iso, item_id),
        )
        log_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO email_campaign_logs (id, campaign_id, recipient_name, recipient_email, company_name, status, sent_at)
            VALUES (?, ?, ?, ?, ?, 'sent', ?)
            """,
            (log_id, "1-by-1-queue", item["recipient_name"], to_email, item["company_name"], now_iso),
        )
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Email successfully sent to {to_email}!"}
    else:
        conn.execute(
            "UPDATE email_queue_items SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (err_msg, item_id),
        )
        conn.commit()
        conn.close()
        return {"status": "error", "message": f"Failed to send email: {err_msg}"}


@router.delete("/queue/{item_id}")
def delete_queue_item(item_id: str) -> Dict[str, Any]:
    """Delete single item from queue."""
    conn = get_connection()
    res = conn.execute("DELETE FROM email_queue_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    return {"status": "success", "message": "Item deleted from queue."}


@router.post("/queue/clear")
def clear_queue(status: str = Query("all", description="all, sent, or draft")) -> Dict[str, Any]:
    """Clear items from queue."""
    conn = get_connection()
    if status == "sent":
        res = conn.execute("DELETE FROM email_queue_items WHERE status = 'sent'")
    elif status == "draft":
        res = conn.execute("DELETE FROM email_queue_items WHERE status = 'draft'")
    else:
        res = conn.execute("DELETE FROM email_queue_items")
    deleted = res.rowcount
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Cleared {deleted} items from queue."}
