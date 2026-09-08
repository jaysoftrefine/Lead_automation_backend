import uuid
from typing import Any, Dict
from fastapi import APIRouter, HTTPException

from schemas import (
    TemplateCreate,
    TemplateUpdate,
    TestEmailBody,
)
from email_campaigns.db import (
    get_connection,
    get_smtp_config,
)
from email_campaigns.smtp_sender import send_email
from email_campaigns.template_engine import (
    get_sample_context,
    resolve_variables,
    AVAILABLE_VARIABLES,
    text_to_html_email,
)

router = APIRouter()


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
    cfg = get_smtp_config(body.smtp_account_id)
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
        "message": f"Test email sent to {body.to_email} (from {cfg.get('smtp_user')}){' with attachment' if attachment_path else ''} ✓"
    }
