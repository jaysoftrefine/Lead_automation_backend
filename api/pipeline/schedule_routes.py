"""FastAPI Router for Scheduled Scraping Jobs & Batch File Uploads."""

import csv
import io
import re
from datetime import datetime
from typing import Optional, Any
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
import pandas as pd

from core.logging import logger
from db.sqlite import sqlite_manager
from pipeline.scheduler import run_scheduled_job_now

router = APIRouter()


def _normalize_header(col_name: str) -> str:
    """Normalize column header: lowercase and strip spaces/underscores/special chars."""
    return re.sub(r"[^a-z0-9]", "", str(col_name).lower())


def _parse_scheduled_date(val: Any) -> str:
    """Parse various date representations into YYYY-MM-DD."""
    if not val or pd.isna(val):
        return datetime.utcnow().strftime("%Y-%m-%d")

    # If it's already a pandas Timestamp or datetime
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.strftime("%Y-%m-%d")

    val_str = str(val).strip()
    # Try multiple standard formats
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(val_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Fallback to today if unparseable
    return val_str[:10] if len(val_str) >= 10 else datetime.utcnow().strftime("%Y-%m-%d")


@router.post("/pipeline/schedule/upload")
async def upload_schedule_file(file: UploadFile = File(...)):
    """
    Upload a CSV or Excel (.xlsx, .xls) file with scheduled scraping jobs.
    Expected columns:
      ID | JobTitle | Target Location | Company Size | Scraping Limit | Scheduled date
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    filename = file.filename.lower()
    content = await file.read()

    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file format. Please upload a CSV or Excel (.xlsx, .xls) file."
            )
    except Exception as e:
        logger.error(f"Error reading schedule file: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    if df.empty:
        raise HTTPException(status_code=400, detail="The uploaded file contains no data rows.")

    # Map normalized headers to our standard fields
    col_mapping = {}
    for col in df.columns:
        norm = _normalize_header(col)
        if norm in ("id", "jobid"):
            col_mapping[col] = "id"
        elif norm in ("jobtitle", "title", "role", "searchterm", "job"):
            col_mapping[col] = "job_title"
        elif norm in ("targetlocation", "location", "geo", "city"):
            col_mapping[col] = "target_location"
        elif norm in ("companysize", "size", "companysizerange"):
            col_mapping[col] = "company_size"
        elif norm in ("scrapinglimit", "limit", "maxjobs", "results", "resultswanted"):
            col_mapping[col] = "scraping_limit"
        elif norm in ("scheduleddate", "date", "schedule", "scheduledat"):
            col_mapping[col] = "scheduled_date"

    if "job_title" not in col_mapping.values():
        raise HTTPException(
            status_code=400,
            detail="Missing required column 'JobTitle' (or 'Job Title' / 'Title'). Please check the file format."
        )

    sqlite_manager.connect()
    imported_jobs = []
    skipped_rows = 0

    for idx, row in df.iterrows():
        mapped_row = {}
        for original_col, standard_key in col_mapping.items():
            mapped_row[standard_key] = row[original_col]

        job_title = str(mapped_row.get("job_title") or "").strip()
        if not job_title or job_title.lower() == "nan":
            skipped_rows += 1
            continue

        raw_id = mapped_row.get("id")
        if pd.isna(raw_id) or not str(raw_id).strip():
            job_id = f"JOB-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{idx + 1}"
        else:
            job_id = str(raw_id).strip()

        target_loc = mapped_row.get("target_location")
        target_location = "Worldwide (Remote)" if (pd.isna(target_loc) or not str(target_loc).strip()) else str(target_loc).strip()

        comp_size = mapped_row.get("company_size")
        company_size = "small" if (pd.isna(comp_size) or not str(comp_size).strip()) else str(comp_size).strip()

        raw_limit = mapped_row.get("scraping_limit")
        try:
            scraping_limit = int(raw_limit) if (not pd.isna(raw_limit) and raw_limit > 0) else 10
        except (ValueError, TypeError):
            scraping_limit = 10

        scheduled_date = _parse_scheduled_date(mapped_row.get("scheduled_date"))

        job_dict = {
            "id": job_id,
            "job_title": job_title,
            "target_location": target_location,
            "company_size": company_size,
            "scraping_limit": scraping_limit,
            "scheduled_date": scheduled_date,
            "status": "pending",
        }

        try:
            saved = sqlite_manager.create_or_upsert_scheduled_job(job_dict)
            imported_jobs.append(saved)
        except Exception as err:
            logger.error(f"Error saving job row {idx}: {err}")
            skipped_rows += 1

    return {
        "success": True,
        "message": f"Successfully imported {len(imported_jobs)} scraping tasks into SQLite.",
        "imported_count": len(imported_jobs),
        "skipped_count": skipped_rows,
        "jobs": imported_jobs,
    }


@router.get("/pipeline/schedule")
def list_scheduled_jobs(
    status: Optional[str] = Query(None, description="Filter by job status (pending, running, completed, failed, all)"),
    scheduled_date: Optional[str] = Query(None, description="Filter by scheduled date (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search by ID, Job Title or Location"),
    limit: int = Query(100, ge=1, le=500),
    page: int = Query(1, ge=1),
):
    """List scheduled scraping jobs."""
    try:
        sqlite_manager.connect()
        return sqlite_manager.list_scheduled_jobs(
            status=status,
            scheduled_date=scheduled_date,
            search=search,
            limit=limit,
            page=page,
        )
    except Exception as e:
        logger.error(f"Error listing scheduled jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pipeline/schedule/{job_id}")
def get_scheduled_job(job_id: str):
    """Get single scheduled job by ID."""
    try:
        sqlite_manager.connect()
        job = sqlite_manager.get_scheduled_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Scheduled job not found")
        return job
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipeline/schedule/{job_id}/run")
def trigger_scheduled_job_now(job_id: str):
    """Immediately trigger the autonomous pipeline for a scheduled job."""
    try:
        return run_scheduled_job_now(job_id)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=409, detail=str(re))
    except Exception as e:
        logger.error(f"Error running scheduled job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pipeline/schedule/{job_id}")
def delete_scheduled_job(job_id: str):
    """Delete a scheduled job."""
    try:
        sqlite_manager.connect()
        success = sqlite_manager.delete_scheduled_job(job_id)
        if not success:
            raise HTTPException(status_code=404, detail="Scheduled job not found")
        return {"success": True, "message": f"Deleted job {job_id}."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pipeline/schedule-template")
def download_sample_schedule_template(format: str = Query("csv", pattern="^(csv|xlsx)$")):
    """Download sample CSV or Excel template for uploading scheduled scraping jobs."""
    sample_data = [
        {
            "ID": "JOB-101",
            "JobTitle": "Senior Full Stack Python Developer",
            "Target Location": "Worldwide (Remote)",
            "Company Size": "Small / Startup (1-50)",
            "Scraping Limit": 10,
            "Scheduled date": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        {
            "ID": "JOB-102",
            "JobTitle": "React Native Mobile Engineer",
            "Target Location": "United States",
            "Company Size": "11-50",
            "Scraping Limit": 15,
            "Scheduled date": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        {
            "ID": "JOB-103",
            "JobTitle": "DevOps / SRE Architect",
            "Target Location": "Worldwide (Remote)",
            "Company Size": "51-200",
            "Scraping Limit": 10,
            "Scheduled date": datetime.utcnow().strftime("%Y-%m-%d"),
        },
    ]

    df = pd.DataFrame(sample_data)

    if format == "csv":
        output = io.StringIO()
        df.to_csv(output, index=False)
        csv_bytes = output.getvalue().encode("utf-8")
        return Response(
            content=csv_bytes,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=hirepilot_scraping_schedule_template.csv"}
        )
    else:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Scraping_Schedule")
        output.seek(0)
        return Response(
            content=output.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=hirepilot_scraping_schedule_template.xlsx"}
        )


@router.get("/pipeline/automations/overview")
def get_automations_overview():
    """Unified overview of upcoming automations and execution history across scraping & outreach."""
    try:
        from config.settings import settings
        import json
        import sqlite3
        from pathlib import Path

        sqlite_manager.connect()
        conn = sqlite_manager.get_connection()
        cur = conn.cursor()

        # 1. Scraping jobs: split into upcoming and history
        cur.execute("SELECT * FROM scheduled_scraping_jobs ORDER BY created_at DESC")
        all_jobs = [dict(r) for r in cur.fetchall()]

        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        upcoming_scraping = []
        history_scraping = []

        for job in all_jobs:
            status = (job.get("status") or "").lower()
            sched_date = str(job.get("scheduled_date") or "")[:10]
            if status in ("pending", "running") or (sched_date >= today_str and status != "completed"):
                upcoming_scraping.append(job)
            else:
                history_scraping.append(job)

        # 2. Upcoming Outreach Drips: auto + open + next_send_at
        cur.execute("""
            SELECT id, job_url, title, company, company_domain, lead_type, location,
                   company_summary, lead_summary, hiring_urgency, key_technologies, company_size,
                   outreach_mode, outreach_state, outreach_stage, next_send_at, last_sent_at, contacts,
                   created_at, scraped_at, scheduled_job_id
            FROM enriched_leads
            WHERE is_valid_lead = 1
              AND COALESCE(LOWER(outreach_mode), 'manual') = 'auto'
              AND COALESCE(LOWER(outreach_state), 'open') = 'open'
              AND next_send_at IS NOT NULL
            ORDER BY next_send_at ASC
            LIMIT 100
        """)
        from email_campaigns.outreach_automation import has_verified_email

        raw_upcoming_outreach = [dict(r) for r in cur.fetchall()]
        upcoming_outreach = []
        for r in raw_upcoming_outreach:
            if isinstance(r.get("contacts"), str):
                try:
                    r["contacts"] = json.loads(r["contacts"])
                except Exception:
                    r["contacts"] = []
            # Only count drips that can actually send (verified email)
            if has_verified_email(r.get("contacts")):
                upcoming_outreach.append(r)

        # 3. History Outreach: sent emails (last_sent_at IS NOT NULL)
        cur.execute("""
            SELECT id, job_url, title, company, company_domain, lead_type, location,
                   company_summary, lead_summary, hiring_urgency, key_technologies, company_size,
                   outreach_mode, outreach_state, outreach_stage, next_send_at, last_sent_at, contacts,
                   created_at, scraped_at, scheduled_job_id
            FROM enriched_leads
            WHERE is_valid_lead = 1
              AND last_sent_at IS NOT NULL
            ORDER BY last_sent_at DESC
            LIMIT 100
        """)
        raw_history_outreach = [dict(r) for r in cur.fetchall()]
        history_outreach = []
        for r in raw_history_outreach:
            if isinstance(r.get("contacts"), str):
                try:
                    r["contacts"] = json.loads(r["contacts"])
                except Exception:
                    r["contacts"] = []
            history_outreach.append(r)

        conn.close()

        # 4. Email Campaigns DB (campaign sequences, delivery logs, queue items)
        upcoming_campaign_steps = []
        campaign_logs = []
        pending_queue = []
        sent_queue = []

        email_db_path = Path(__file__).resolve().parent.parent.parent / "data" / "email_campaigns.db"
        if email_db_path.exists():
            try:
                econn = sqlite3.connect(str(email_db_path))
                econn.row_factory = sqlite3.Row
                ecur = econn.cursor()

                try:
                    ecur.execute("""
                        SELECT cs.*, ec.name as campaign_name
                        FROM campaign_sequences cs
                        LEFT JOIN email_campaigns ec ON cs.campaign_id = ec.id
                        WHERE cs.status = 'scheduled'
                        ORDER BY cs.scheduled_at ASC
                        LIMIT 50
                    """)
                    upcoming_campaign_steps = [dict(r) for r in ecur.fetchall()]
                except Exception:
                    upcoming_campaign_steps = []

                try:
                    ecur.execute("""
                        SELECT l.*, ec.name as campaign_name
                        FROM email_campaign_logs l
                        LEFT JOIN email_campaigns ec ON l.campaign_id = ec.id
                        ORDER BY l.sent_at DESC
                        LIMIT 100
                    """)
                    campaign_logs = [dict(r) for r in ecur.fetchall()]
                except Exception:
                    campaign_logs = []

                try:
                    ecur.execute("""
                        SELECT id, template_name, recipient_name, recipient_email, company_name,
                               subject, status, created_at, updated_at
                        FROM email_queue_items
                        WHERE status IN ('draft', 'pending')
                        ORDER BY created_at DESC
                        LIMIT 50
                    """)
                    pending_queue = [dict(r) for r in ecur.fetchall()]
                except Exception:
                    pending_queue = []

                try:
                    ecur.execute("""
                        SELECT id, template_name, recipient_name, recipient_email, company_name,
                               subject, status, error_message, sent_at
                        FROM email_queue_items
                        WHERE status IN ('sent', 'failed')
                        ORDER BY sent_at DESC
                        LIMIT 100
                    """)
                    sent_queue = [dict(r) for r in ecur.fetchall()]
                except Exception:
                    sent_queue = []

                econn.close()
            except Exception as edb_err:
                logger.warning(f"Could not read email campaigns db in automations overview: {edb_err}")

        schedulers = {
            "scraping": {
                "name": "Autonomous Scraping Engine",
                "daily_time": getattr(settings, "scraping_schedule_daily_time", "22:00"),
                "status": "active",
                "description": "Scrapes and enriches leads for scheduled job postings daily",
            },
            "outreach": {
                "name": "Daily Outreach Drip Mailer",
                "daily_time": getattr(settings, "outreach_schedule_daily_time", "09:00"),
                "status": "active",
                "description": "Sends personalized cold outreach & follow-up drips daily",
            },
        }

        total_upcoming_emails = len(upcoming_outreach) + len(upcoming_campaign_steps) + len(pending_queue)
        total_history_emails = len(history_outreach) + len(campaign_logs) + len(sent_queue)

        counts = {
            "upcoming_scraping": len(upcoming_scraping),
            "upcoming_outreach": len(upcoming_outreach),
            "upcoming_campaign_steps": len(upcoming_campaign_steps),
            "pending_queue": len(pending_queue),
            "upcoming_emails": total_upcoming_emails,
            "total_upcoming": len(upcoming_scraping) + total_upcoming_emails,
            "history_scraping": len(history_scraping),
            "history_outreach": len(history_outreach),
            "campaign_logs": len(campaign_logs),
            "sent_queue": len(sent_queue),
            "history_emails": total_history_emails,
            "total_history": len(history_scraping) + total_history_emails,
        }

        return {
            "success": True,
            "counts": counts,
            "jobs": all_jobs,
            "upcoming": {
                "scraping_jobs": upcoming_scraping,
                "outreach_drips": upcoming_outreach,
                "campaign_steps": upcoming_campaign_steps,
                "queue_items": pending_queue,
            },
            "history": {
                "scraping_runs": history_scraping,
                "outreach_sent": history_outreach,
                "campaign_logs": campaign_logs,
                "queue_sent": sent_queue,
            },
            "schedulers": schedulers,
        }
    except Exception as e:
        logger.error(f"Error fetching automations overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))

