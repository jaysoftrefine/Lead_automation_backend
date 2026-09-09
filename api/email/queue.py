import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query

from schemas import (
    QueueGenerateRequest,
    QueueItemUpdate,
    QueueSendRequest,
)
from email_campaigns.db import (
    get_connection,
    get_smtp_config,
)
from email_campaigns.smtp_sender import send_email
from email_campaigns.template_engine import (
    resolve_variables,
    text_to_html_email,
    build_context,
)
from email_campaigns.campaign_runner import collect_recipients
from email_campaigns.ai_personalizer import generate_ai_hook_and_pitch
from api.email.shared import _enrich_recipient_company_info

router = APIRouter()


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
            aud_dict = dict(aud)
            sources = json.loads(aud_dict.get("sources") or "[]")
            filters = json.loads(aud_dict.get("filters") or "{}")
            raw_recips = json.loads(aud_dict.get("manual_recipients") or "[]")
            manual = [m if isinstance(m, str) else (m.get("email") or "") for m in raw_recips]
            if not selected:
                try:
                    selected = json.loads(aud_dict.get("selected_recipients") or "[]")
                except Exception:
                    selected = None

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

    cfg = get_smtp_config(body.smtp_account_id)
    sender_name = cfg.get("from_name", "HirePilot AI")

    created_items = []
    for r in recipients:
        r = _enrich_recipient_company_info(r, conn)
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
            company_description=r.get("company_description"),
            company_tags=r.get("company_tags"),
            use_ai=True,
        )
        rendered_subj, rendered_body = resolve_variables(tpl["subject"], tpl["body"], ctx)
        rendered_html = text_to_html_email(rendered_body)

        conn.execute(
            """
            INSERT INTO email_queue_items (
                id, template_id, template_name, audience_id,
                recipient_name, recipient_email, company_name, role, website, city, country, category,
                subject, body, raw_body, status, smtp_account_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
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
                body.smtp_account_id,
            ),
        )
        created_items.append({
            "id": item_id,
            "recipient_name": r.get("person_name") or "",
            "recipient_email": r.get("email") or "",
            "company_name": r.get("company_name") or "",
            "subject": rendered_subj,
            "status": "draft",
            "smtp_account_id": body.smtp_account_id,
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
    """Edit an individual queue item's subject, body, sender account, or recipient before sending."""
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
    new_smtp_id = body.smtp_account_id if body.smtp_account_id is not None else current.get("smtp_account_id")

    new_body = current["body"]
    new_raw = current["raw_body"]
    if body.body is not None:
        new_raw = body.body
        new_body = text_to_html_email(body.body)

    conn.execute(
        """
        UPDATE email_queue_items
        SET subject = ?, body = ?, raw_body = ?, recipient_name = ?, recipient_email = ?, company_name = ?, smtp_account_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_subj, new_body, new_raw, new_name, new_email, new_company, new_smtp_id, item_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Email draft updated."}


@router.post("/queue/{item_id}/send")
def send_queue_item(
    item_id: str,
    body: Optional[QueueSendRequest] = None,
    smtp_account_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
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

    target_smtp_id = (body.smtp_account_id if body and body.smtp_account_id else None) or smtp_account_id or item.get("smtp_account_id")
    cfg = get_smtp_config(target_smtp_id)
    sender_email = cfg.get("smtp_user", "")

    success, err_msg = send_email(
        to_email=to_email,
        subject=item["subject"],
        body=item["body"],
        config=cfg,
        attachment_path=attachment_path,
        attachment_name=attachment_name,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    if success:
        conn.execute(
            "UPDATE email_queue_items SET status = 'sent', sent_at = ?, error_message = NULL, smtp_account_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (now_iso, target_smtp_id, item_id),
        )
        log_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO email_campaign_logs (id, campaign_id, recipient_name, recipient_email, company_name, status, sent_at, sender_email)
            VALUES (?, ?, ?, ?, ?, 'sent', ?, ?)
            """,
            (log_id, "1-by-1-queue", item["recipient_name"], to_email, item["company_name"], now_iso, sender_email),
        )
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Email successfully sent to {to_email} (via {sender_email})!"}
    else:
        conn.execute(
            "UPDATE email_queue_items SET status = 'failed', error_message = ?, smtp_account_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (err_msg, target_smtp_id, item_id),
        )
        conn.commit()
        conn.close()
        return {"status": "error", "message": f"Failed to send email: {err_msg}"}


@router.post("/queue/{item_id}/regenerate-ai")
def regenerate_queue_item_ai(item_id: str) -> Dict[str, Any]:
    """Regenerate personalized ai_company_hook and ai_value_pitch using Gemini for a single queue item."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_queue_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Queue item not found.")

    item = dict(row)
    c_name = item.get("company_name") or ""
    desc = None
    tags = None
    website = item.get("website")
    category = item.get("category")

    if c_name:
        s_row = conn.execute(
            "SELECT description, tags, website, category FROM startups WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(?)) LIMIT 1",
            (c_name.strip(),)
        ).fetchone()
        if s_row:
            desc = s_row["description"]
            tags = s_row["tags"]
            if not website and s_row["website"]:
                website = s_row["website"]
            if not category and s_row["category"]:
                category = s_row["category"]

    ai_data = generate_ai_hook_and_pitch(
        company_name=c_name,
        recipient_name=item.get("recipient_name"),
        role=item.get("role"),
        website=website,
        description=desc,
        tags=tags,
        category=category,
    )

    subj_tpl = item["subject"]
    body_tpl = item.get("raw_body") or item["body"]
    if item.get("template_id"):
        tpl = conn.execute("SELECT subject, body FROM email_templates WHERE id = ?", (item["template_id"],)).fetchone()
        if tpl:
            subj_tpl = tpl["subject"]
            body_tpl = tpl["body"]

    cfg = get_smtp_config()
    sender_name = cfg.get("from_name", "Stephan Arnas")

    ctx = build_context(
        person_name=item.get("recipient_name"),
        role=item.get("role"),
        company_name=c_name,
        website=website,
        city=item.get("city"),
        country=item.get("country"),
        category=category,
        sender_name=sender_name,
        email=item.get("recipient_email"),
        company_description=desc,
        company_tags=tags,
        ai_company_hook_temp1=ai_data.get("ai_company_hook_temp1"),
        ai_value_pitch_temp1=ai_data.get("ai_value_pitch_temp1"),
        ai_company_hook_temp2=ai_data.get("ai_company_hook_temp2"),
        ai_value_pitch_temp2=ai_data.get("ai_value_pitch_temp2"),
        ai_company_hook=ai_data.get("ai_company_hook"),
        ai_value_pitch=ai_data.get("ai_value_pitch"),
        use_ai=False,
    )

    new_subj, new_body = resolve_variables(subj_tpl, body_tpl, ctx)
    new_html = text_to_html_email(new_body)

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE email_queue_items
        SET subject = ?, body = ?, raw_body = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_subj, new_html, new_body, now_iso, item_id),
    )
    conn.commit()

    updated = conn.execute("SELECT * FROM email_queue_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()

    return {
        "status": "success",
        "message": f"Successfully regenerated personalized AI email for {c_name or item.get('recipient_name')}.",
        "data": dict(updated),
        "ai_hook": ai_data.get("ai_company_hook_temp1") or ai_data.get("ai_company_hook"),
        "ai_pitch": ai_data.get("ai_value_pitch_temp1") or ai_data.get("ai_value_pitch"),
        "ai_hook_temp1": ai_data.get("ai_company_hook_temp1"),
        "ai_pitch_temp1": ai_data.get("ai_value_pitch_temp1"),
        "ai_hook_temp2": ai_data.get("ai_company_hook_temp2"),
        "ai_pitch_temp2": ai_data.get("ai_value_pitch_temp2"),
    }


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
