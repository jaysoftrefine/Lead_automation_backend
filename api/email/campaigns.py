import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from email_campaigns.ws_manager import campaign_ws_manager

from schemas import (
    CampaignCreate,
    CampaignUpdate,
    CampaignPreviewGeneratedRequest,
    SequenceStep,
    SequenceStepUpdate,
)
from email_campaigns.db import (
    get_connection,
    get_smtp_config,
    create_sequence_steps,
    list_sequence_steps,
    get_sequence_step,
    update_sequence_step,
)
from email_campaigns.smtp_sender import test_smtp_connection
from email_campaigns.template_engine import (
    get_sample_context,
    resolve_variables,
    text_to_html_email,
    build_context,
)
from email_campaigns.campaign_runner import (
    launch_campaign,
    count_recipients,
    collect_recipients,
)
from api.email.shared import _enrich_recipient_company_info

router = APIRouter()


@router.get("/campaigns")
def list_campaigns() -> Dict[str, Any]:
    """List all email campaigns ordered by newest first, enriched with sequence status."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM email_campaigns ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    campaigns = [dict(r) for r in rows]

    for c in campaigns:
        if c.get("campaign_type") == "sequence":
            steps = list_sequence_steps(c["id"])
            c["total_steps"] = len(steps)
            c["completed_steps"] = sum(1 for s in steps if s.get("status") == "completed")
            active_steps = [s for s in steps if s.get("status") in ("pending", "running", "paused")]
            c["next_step"] = active_steps[0] if active_steps else None

    return {"status": "success", "data": campaigns}


@router.post("/campaigns")
def create_campaign(body: CampaignCreate) -> Dict[str, Any]:
    """
    Create a new email campaign.
    If body.draft=True, saves with status='draft' without sending.
    If body.draft=False, validates SMTP and immediately launches the campaign in a background thread.
    """
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
    campaign_cc = (body.cc if body.cc is not None else (tpl.get("cc") or "")).strip()

    # Pack full configuration into audience_filter JSON
    config_payload = {
        "audience_sources": body.audience_sources,
        "audience_filters": body.audience_filters,
        "manual_emails": body.manual_emails or [],
        "selected_recipients": body.selected_recipients or [],
        "delay_seconds": body.delay_seconds,
    }

    campaign_type = body.campaign_type or "one_shot"
    is_sequence    = campaign_type == "sequence" and body.steps and len(body.steps) > 0
    start_date     = datetime.now(timezone.utc).isoformat()

    if body.draft:
        # Save as draft without launching
        conn.execute("""
            INSERT INTO email_campaigns
                (id, name, template_id, template_name, subject, attachment_path, attachment_name,
                 status, audience_filter, smtp_account_id, cc,
                 campaign_type, start_date, reminder_email, reminder_hours_before)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
        """, (
            cid, body.name.strip(), body.template_id,
            tpl["name"], tpl["subject"],
            attachment_path, attachment_name,
            json.dumps(config_payload),
            body.smtp_account_id,
            campaign_cc,
            campaign_type, start_date,
            body.reminder_email or "",
            body.reminder_hours_before or 24,
        ))
        conn.commit()
        row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (cid,)).fetchone()
        conn.close()

        # Persist sequence steps for draft sequence campaigns
        steps_data = []
        if is_sequence:
            steps_data = create_sequence_steps(
                cid,
                [s.dict() for s in body.steps],
                start_date,
            )

        return {
            "status": "success",
            "message": "Campaign saved as draft successfully.",
            "data": dict(row),
            "steps": steps_data,
        }

    # Validate SMTP before launching
    smtp_cfg = get_smtp_config(body.smtp_account_id)
    smtp_ok, smtp_msg = test_smtp_connection(smtp_cfg)
    if not smtp_ok:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"SMTP not configured or invalid ({smtp_cfg.get('smtp_user') or 'default'}): {smtp_msg}. Please check SMTP credentials.",
        )

    conn.execute("""
        INSERT INTO email_campaigns
            (id, name, template_id, template_name, subject, attachment_path, attachment_name,
             status, audience_filter, smtp_account_id, cc,
             campaign_type, start_date, reminder_email, reminder_hours_before)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
    """, (
        cid, body.name.strip(), body.template_id,
        tpl["name"], tpl["subject"],
        attachment_path, attachment_name,
        json.dumps(config_payload),
        body.smtp_account_id,
        campaign_cc,
        campaign_type, start_date,
        body.reminder_email or "",
        body.reminder_hours_before or 24,
    ))
    conn.commit()
    conn.close()

    # Sequence campaign — persist steps; scheduler will fire them on schedule
    if is_sequence:
        steps_data = create_sequence_steps(
            cid,
            [s.dict() for s in body.steps],
            start_date,
        )
        # Mark overall campaign as 'scheduled'
        conn2 = get_connection()
        conn2.execute(
            "UPDATE email_campaigns SET status = 'scheduled' WHERE id = ?", (cid,)
        )
        conn2.commit()
        conn2.close()

        step_count = len(steps_data)
        return {
            "status": "success",
            "message": f"Sequence campaign created with {step_count} step(s). Step 1 will fire immediately; follow-ups will run automatically.",
            "data": {"campaign_id": cid, "steps": steps_data},
        }

    # One-shot campaign — launch immediately in background
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
        smtp_account_id=body.smtp_account_id,
        cc=campaign_cc,
    )

    return {
        "status": "success",
        "message": f"Campaign launched via {smtp_cfg.get('smtp_user')}! Sending to {total} recipient(s){' with PDF attachment' if attachment_path else ''}.",
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

    if body.smtp_account_id is not None:
        updates["smtp_account_id"] = body.smtp_account_id

    if body.cc is not None:
        updates["cc"] = body.cc.strip()

    if body.reminder_email is not None:
        updates["reminder_email"] = body.reminder_email

    if body.reminder_hours_before is not None:
        updates["reminder_hours_before"] = body.reminder_hours_before

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

    # Update sequence steps if provided
    steps_data = []
    if body.steps is not None:
        camp_row = dict(row)
        start_date = camp_row.get("start_date") or datetime.now(timezone.utc).isoformat()
        steps_data = create_sequence_steps(
            campaign_id,
            [s.dict() for s in body.steps],
            start_date,
        )
        conn.execute(
            "UPDATE email_campaigns SET campaign_type = 'sequence' WHERE id = ?",
            (campaign_id,)
        )
        conn.commit()

    conn.close()

    return {
        "status": "success",
        "message": "Campaign updated successfully.",
        "data": dict(row),
        "steps": steps_data,
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

    camp_dict = dict(camp)
    smtp_account_id = camp_dict.get("smtp_account_id")
    smtp_cfg = get_smtp_config(smtp_account_id)
    smtp_ok, smtp_msg = test_smtp_connection(smtp_cfg)
    if not smtp_ok:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"SMTP not configured or invalid ({smtp_cfg.get('smtp_user') or 'default'}): {smtp_msg}. Please configure SMTP first.",
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
    cc = (camp_dict.get("cc") if "cc" in camp_dict else None) or tpl.get("cc") or ""

    if camp_dict.get("campaign_type") == "sequence":
        from datetime import timedelta
        from email_campaigns.scheduler import wake_scheduler
        start_date = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE email_campaigns SET status = 'scheduled', start_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (start_date, campaign_id),
        )
        conn.commit()
        conn.close()

        # Recalculate scheduled_at for pending steps based on launch start_date
        steps = list_sequence_steps(campaign_id)
        base = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        for s in steps:
            if s.get("status") == "pending":
                days = int(s.get("days_after") or 0)
                new_scheduled_at = (base + timedelta(days=days)).isoformat()
                update_sequence_step(s["id"], scheduled_at=new_scheduled_at)

        wake_scheduler()
        return {
            "status": "success",
            "message": f"Sequence campaign launched! Step 1 will fire immediately; follow-ups will run on schedule.",
            "data": {"campaign_id": campaign_id, "status": "scheduled"},
        }

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
        smtp_account_id=smtp_account_id,
        cc=cc,
    )

    return {
        "status": "success",
        "message": f"Campaign launched! Sending to {total} recipient(s).",
        "data": {"campaign_id": campaign_id, "total_recipients": total},
    }


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str) -> Dict[str, Any]:
    """Delete a campaign and its associated delivery logs and sequence steps."""
    conn = get_connection()
    conn.execute("DELETE FROM email_campaign_logs WHERE campaign_id = ?", (campaign_id,))
    conn.execute("DELETE FROM campaign_sequence_recipients WHERE campaign_id = ?", (campaign_id,))
    conn.execute("DELETE FROM campaign_sequences WHERE campaign_id = ?", (campaign_id,))
    res = conn.execute("DELETE FROM email_campaigns WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()

    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    return {"status": "success", "message": "Campaign deleted successfully."}


@router.get("/campaigns/{campaign_id}/steps")
def get_campaign_steps(campaign_id: str) -> Dict[str, Any]:
    """List all sequence steps for a campaign with parent campaign context."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    steps = list_sequence_steps(campaign_id)
    return {"status": "success", "data": steps, "campaign": dict(row)}


@router.patch("/campaigns/{campaign_id}/steps/{step_id}")
def update_campaign_single_step(
    campaign_id: str,
    step_id: str,
    body: SequenceStepUpdate,
) -> Dict[str, Any]:
    """
    Update a single sequence step: pause/resume (status), reschedule (scheduled_at, days_after),
    or change template/subject.
    """
    from email_campaigns.ws_manager import campaign_ws_manager
    from email_campaigns.scheduler import wake_scheduler

    step = get_sequence_step(step_id)
    if not step or step.get("campaign_id") != campaign_id:
        raise HTTPException(status_code=404, detail="Sequence step not found.")

    updates: Dict[str, Any] = {}
    if body.status is not None:
        valid_statuses = {"pending", "paused", "skipped", "completed", "failed"}
        if body.status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status '{body.status}'. Valid: {valid_statuses}")
        updates["status"] = body.status

    if body.scheduled_at is not None:
        updates["scheduled_at"] = body.scheduled_at

    if body.days_after is not None:
        updates["days_after"] = int(body.days_after)

    if body.template_id is not None:
        conn = get_connection()
        tpl = conn.execute("SELECT * FROM email_templates WHERE id = ?", (body.template_id,)).fetchone()
        conn.close()
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found.")
        updates["template_id"] = body.template_id
        updates["template_name"] = tpl["name"]
        if body.subject is None:
            updates["subject"] = tpl["subject"]

    if body.subject is not None:
        updates["subject"] = body.subject

    if not updates:
        return {"status": "success", "message": "No updates provided.", "data": step}

    updated = update_sequence_step(step_id, **updates)
    wake_scheduler()

    # Broadcast campaign progress update via websocket
    conn = get_connection()
    camp_row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()
    if camp_row:
        campaign_ws_manager.broadcast_campaign_update(dict(camp_row))

    return {
        "status": "success",
        "message": f"Step #{step['step_number']} updated successfully.",
        "data": updated,
    }


@router.post("/campaigns/{campaign_id}/steps/{step_id}/fire-now")
def fire_sequence_step_immediately(campaign_id: str, step_id: str) -> Dict[str, Any]:
    """Immediately trigger a sequence step in the background."""
    from email_campaigns.scheduler import fire_sequence_step_now
    try:
        updated = fire_sequence_step_now(step_id)
        return {
            "status": "success",
            "message": f"Step #{updated.get('step_number', 1)} triggered immediately.",
            "data": updated,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fire step: {e}")


@router.post("/campaigns/{campaign_id}/toggle-pause")
def toggle_campaign_pause_state(campaign_id: str) -> Dict[str, Any]:
    """Pause or resume a sequence campaign."""
    from email_campaigns.ws_manager import campaign_ws_manager
    from email_campaigns.scheduler import wake_scheduler

    conn = get_connection()
    row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found.")

    curr = row["status"]
    new_status = "scheduled" if curr == "paused" else "paused"

    conn.execute(
        "UPDATE email_campaigns SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_status, campaign_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()

    updated_dict = dict(updated)
    campaign_ws_manager.broadcast_campaign_update(updated_dict)
    wake_scheduler()

    return {
        "status": "success",
        "message": f"Campaign is now {new_status}.",
        "data": updated_dict,
    }


@router.put("/campaigns/{campaign_id}/steps")
def replace_campaign_steps(
    campaign_id: str,
    body: List[SequenceStep],
) -> Dict[str, Any]:
    """Replace all sequence steps for a campaign (idempotent)."""
    conn = get_connection()
    camp = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    camp_dict = dict(camp)
    start_date = camp_dict.get("start_date") or datetime.now(timezone.utc).isoformat()
    steps = create_sequence_steps(campaign_id, [s.dict() for s in body], start_date)

    # Ensure campaign_type is set to sequence
    conn2 = get_connection()
    conn2.execute("UPDATE email_campaigns SET campaign_type = 'sequence' WHERE id = ?", (campaign_id,))
    conn2.commit()
    conn2.close()

    from email_campaigns.scheduler import wake_scheduler
    wake_scheduler()

    return {"status": "success", "message": f"{len(steps)} step(s) saved.", "data": steps}


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
            "company_description": "Poetry brings AI into talent acquisition workflows, from talent intelligence to recruiter enablement.",
            "company_tags": "HR Tech, AI, Recruiting",
            "is_sample": True,
        }]

    cfg = get_smtp_config()
    sender_name = cfg.get("from_name", "Stephan Arnas")

    eu_conn = None
    try:
        from eu_startups.db import get_connection as get_eu_connection
        eu_conn = get_eu_connection()
    except Exception:
        pass

    items = []
    try:
        for r in recipients:
            r = _enrich_recipient_company_info(r, conn=conn, eu_conn=eu_conn)
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
    finally:
        if eu_conn:
            try:
                eu_conn.close()
            except Exception:
                pass

    conn.close()

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


@router.websocket("/campaigns/ws")
async def websocket_campaigns_progress(websocket: WebSocket, campaign_id: Optional[str] = Query(None)):
    """Real-time WebSocket endpoint streaming campaign progress and status updates."""
    await campaign_ws_manager.connect(websocket)
    try:
        # If campaign_id was passed in query, immediately push its current state
        if campaign_id:
            conn = get_connection()
            row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
            conn.close()
            if row:
                await websocket.send_json({
                    "type": "campaign_progress",
                    "campaign_id": campaign_id,
                    "data": dict(row),
                })

        # Keep connection open and process pings/subscriptions
        while True:
            msg_text = await websocket.receive_text()
            if msg_text == "ping":
                await websocket.send_text("pong")
            else:
                try:
                    payload = json.loads(msg_text)
                    if payload.get("action") == "subscribe" and payload.get("campaign_id"):
                        cid = payload["campaign_id"]
                        conn = get_connection()
                        row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (cid,)).fetchone()
                        conn.close()
                        if row:
                            await websocket.send_json({
                                "type": "campaign_progress",
                                "campaign_id": cid,
                                "data": dict(row),
                            })
                except Exception:
                    pass
    except (WebSocketDisconnect, Exception):
        campaign_ws_manager.disconnect(websocket)

