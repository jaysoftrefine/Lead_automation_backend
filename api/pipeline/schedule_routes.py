"""FastAPI Router for Scheduled Scraping Jobs & Batch File Uploads."""

import csv
import io
import re
from datetime import datetime
from typing import Optional
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
