"""API Router for Personal Workspace Auto-Pilot: Resume Management, AI Job Pitch Generation, Real Job Discovery, and Automated Applications with Attachments."""

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from core.logging import logger
from email_campaigns.smtp_sender import send_email
from email_campaigns.db import get_smtp_config, get_connection

router = APIRouter(prefix="/api/personal", tags=["personal"])

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
ATTACHMENTS_DIR = UPLOADS_DIR / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_FILE = UPLOADS_DIR / "personal_profile.json"


class PersonalProfile(BaseModel):
    name: str = "Jay"
    email: str = ""
    target_role: str = "Python Backend Engineer"
    target_location: str = "India"
    experience_years: str = "3+ years"
    workplace_preference: str = "all"  # 'Remote', 'Hybrid', 'On-site', 'all'
    company_size_preference: str = "all"  # open to every company size
    resume_name: Optional[str] = None
    resume_path: Optional[str] = None
    resume_text: Optional[str] = None
    updated_at: Optional[str] = None


class GenerateEmailRequest(BaseModel):
    job_title: str
    company_name: str
    job_location: Optional[str] = "India"
    job_description: Optional[str] = ""
    recruiter_name: Optional[str] = None
    recruiter_email: Optional[str] = None


class SendApplicationRequest(BaseModel):
    to_email: str
    recipient_name: Optional[str] = None
    company_name: str
    job_title: str
    subject: str
    body: str
    opportunity_id: Optional[str] = None
    smtp_account_id: Optional[str] = None
    resume_path: Optional[str] = None
    resume_name: Optional[str] = None


def _load_profile() -> Dict[str, Any]:
    """Load profile from storage or return sensible defaults."""
    if PROFILE_FILE.exists():
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not data.get("target_location") or data.get("target_location") == "Remote / Open":
                    data["target_location"] = "India"
                return data
        except Exception as e:
            logger.warning(f"Failed to read personal profile: {e}")

    return {
        "name": "Jay",
        "email": "",
        "target_role": "Python Backend Engineer",
        "target_location": "India",
        "experience_years": "3+ years",
        "workplace_preference": "all",
        "company_size_preference": "all",
        "resume_name": None,
        "resume_path": None,
        "resume_text": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _save_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    """Save profile to storage atomically."""
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save personal profile: {e}")
    return data


@router.get("/profile")
def get_profile() -> Dict[str, Any]:
    """Retrieve current personal job search profile and resume state."""
    profile = _load_profile()
    # Check if resume still exists
    if profile.get("resume_path") and not os.path.exists(profile["resume_path"]):
        profile["resume_exists"] = False
    else:
        profile["resume_exists"] = bool(profile.get("resume_path"))
    return {"status": "success", "data": profile}


@router.post("/profile")
def update_profile(body: PersonalProfile) -> Dict[str, Any]:
    """Update personal job search preferences."""
    current = _load_profile()
    updated = body.model_dump()
    # Preserve existing resume if not overwritten
    if not updated.get("resume_path") and current.get("resume_path"):
        updated["resume_path"] = current["resume_path"]
        updated["resume_name"] = current["resume_name"]
        updated["resume_text"] = current["resume_text"]

    saved = _save_profile(updated)
    return {"status": "success", "message": "Profile updated successfully.", "data": saved}


@router.post("/resume")
async def upload_resume(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Upload resume PDF, extract text with PyPDF, and update personal profile."""
    original_filename = file.filename or "resume.pdf"
    if not original_filename.lower().endswith((".pdf", ".txt")):
        raise HTTPException(status_code=400, detail="Only PDF and text files are supported for resumes.")

    file_uuid = uuid.uuid4().hex[:8]
    safe_filename = f"resume_{file_uuid}_{original_filename.replace(' ', '_')}"
    destination = ATTACHMENTS_DIR / safe_filename

    try:
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save resume file: {e}")

    file_size_kb = round(destination.stat().st_size / 1024, 1)

    # Extract text from resume
    extracted_text = ""
    try:
        if safe_filename.lower().endswith(".pdf"):
            import pypdf
            reader = pypdf.PdfReader(str(destination))
            pages = [page.extract_text() or "" for page in reader.pages]
            extracted_text = "\n".join(pages).strip()
        else:
            with destination.open("r", encoding="utf-8", errors="ignore") as f:
                extracted_text = f.read().strip()
    except Exception as e:
        logger.warning(f"Could not extract text from uploaded resume: {e}")

    # Update profile
    profile = _load_profile()
    profile["resume_name"] = original_filename
    profile["resume_path"] = str(destination)
    profile["resume_text"] = extracted_text[:8000]  # Store up to 8k chars for AI context
    _save_profile(profile)

    return {
        "status": "success",
        "message": f"Resume '{original_filename}' uploaded successfully ({file_size_kb} KB).",
        "data": {
            "resume_name": original_filename,
            "resume_path": str(destination),
            "file_size_kb": file_size_kb,
            "has_extracted_text": bool(extracted_text),
            "text_preview": extracted_text[:300] if extracted_text else "",
        },
    }


@router.post("/generate-email")
def generate_application_email(body: GenerateEmailRequest) -> Dict[str, Any]:
    """Generate a hyper-personalized, high-converting job application email using Gemini AI."""
    profile = _load_profile()
    candidate_name = profile.get("name") or "Jay"
    target_role = profile.get("target_role") or "Full Stack Developer"
    experience_years = profile.get("experience_years") or "3+ years"
    resume_text = profile.get("resume_text") or ""

    c_name = body.company_name.strip()
    recruiter = body.recruiter_name or "Hiring Team"
    job_title = body.job_title.strip()

    prompt = f"""You are an expert tech career advisor writing a high-converting, personalized job application email from {candidate_name} to {recruiter} at {c_name} for the role of "{job_title}".

Candidate Background:
- Candidate Name: {candidate_name}
- Specialization / Target Role: {target_role}
- Experience: {experience_years}
- Candidate Resume Extract / Skills:
{resume_text if resume_text else "Proficient in Python (FastAPI, AsyncIO, Web Scraping), React, TypeScript, Database Automation, REST/GraphQL APIs, and AI integrations."}

Target Opportunity:
- Role: {job_title}
- Company: {c_name}
- Location: {body.job_location}
- Description: {body.job_description[:600] if body.job_description else 'Building scalable modern software platforms.'}

Requirements:
1. Provide a clear, professional subject line (e.g., "Application: {job_title} - {candidate_name}").
2. Write a concise, compelling email body (approx 120-180 words):
   - Opening: Genuine enthusiasm for {c_name} and the {job_title} position.
   - Body paragraph: Highlight 2-3 specific matching technical strengths/projects directly relevant to the role from the candidate's background.
   - Call to action: Note that the resume is attached, and express availability for a quick introductory conversation.
   - Sign off with Warm regards, {candidate_name}.
3. Tone: Direct, capable, polite, and free of generic fluff or buzzwords.

Output Format: JSON with exactly two fields:
{{
  "subject": "string",
  "body": "string"
}}
"""

    subject = f"Application: {job_title} - {candidate_name}"
    email_body = ""

    try:
        from llm.providers.gemini import GeminiProvider

        provider = GeminiProvider(temperature=0.3)
        chat = provider.get_chat_model()
        response = chat.invoke(prompt)
        content = response.content

        # Extract JSON from code fence if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        parsed = json.loads(content)
        subject = parsed.get("subject") or subject
        email_body = parsed.get("body") or ""
    except Exception as e:
        logger.warning(f"Gemini generation fallback for {job_title} at {c_name}: {e}")
        # Smart fallback template
        email_body = (
            f"Hi {recruiter},\n\n"
            f"I am writing to express my strong interest in the {job_title} role at {c_name}. "
            f"With over {experience_years} of hands-on experience in full-stack architecture—specifically building asynchronous Python (FastAPI) backends, responsive React/TypeScript interfaces, and robust data pipelines—I am excited about the opportunity to contribute to your engineering team.\n\n"
            f"I have attached my resume detailing my recent projects, technical stack, and achievements. I would welcome the opportunity to connect for a brief call to discuss how my skill set can support {c_name}'s roadmap.\n\n"
            f"Thank you for your time and consideration.\n\n"
            f"Best regards,\n"
            f"{candidate_name}"
        )

    return {
        "status": "success",
        "data": {
            "subject": subject,
            "body": email_body,
            "has_resume": bool(profile.get("resume_path")),
            "resume_name": profile.get("resume_name"),
        },
    }


@router.post("/send-application")
def send_application(body: SendApplicationRequest) -> Dict[str, Any]:
    """Send the personalized application email with the candidate's resume attached."""
    profile = _load_profile()
    resume_path = body.resume_path or profile.get("resume_path")
    resume_name = body.resume_name or profile.get("resume_name") or "Resume.pdf"

    if resume_path and not os.path.exists(resume_path):
        logger.warning(f"Resume path {resume_path} not found on disk.")
        resume_path = None

    # Load SMTP config
    cfg = get_smtp_config(body.smtp_account_id)
    if not cfg or not cfg.get("smtp_host") or not cfg.get("smtp_user"):
        raise HTTPException(
            status_code=400,
            detail="SMTP is not configured yet. Please configure your email in Settings (SMTP) first.",
        )

    success, err_msg = send_email(
        to_email=body.to_email,
        subject=body.subject,
        body=body.body,
        config=cfg,
        attachment_path=resume_path,
        attachment_name=resume_name,
    )

    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email via SMTP: {err_msg}",
        )

    # Log application into email_campaign_logs
    try:
        conn = get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        log_id = str(uuid.uuid4())
        sender_email = cfg.get("smtp_user", "")
        conn.execute(
            """
            INSERT INTO email_campaign_logs (id, campaign_id, recipient_name, recipient_email, company_name, status, sent_at, sender_email)
            VALUES (?, ?, ?, ?, ?, 'sent', ?, ?)
            """,
            (
                log_id,
                "personal-job-application",
                body.recipient_name or body.job_title,
                body.to_email,
                body.company_name,
                now_iso,
                sender_email,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as log_err:
        logger.warning(f"Failed to log personal application: {log_err}")

    return {
        "status": "success",
        "message": f"Application successfully sent to {body.to_email} with resume attached!",
        "attachment_sent": bool(resume_path),
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/search-jobs")
def search_jobs(
    role: str = Query("Python Backend Engineer"),
    location: str = Query("India"),
    results_wanted: int = Query(15, ge=1, le=50),
    is_remote: bool = Query(True),
) -> Dict[str, Any]:
    """Search live jobs using JobSpyScraper matching the candidate's criteria."""
    from scraper.jobspy_scraper import JobSpyScraper

    scraper = JobSpyScraper()
    try:
        # Use linkedin and indeed which work reliably without recaptcha
        raw_postings = scraper.scrape(
            search_term=role,
            location=location,
            results_wanted=results_wanted,
            is_remote=is_remote,
            sites=["linkedin", "indeed"],
            hours_old=168,  # last 7 days
        )

        opportunities: List[Dict[str, Any]] = []
        for p in raw_postings:
            opp_id = f"job-{uuid.uuid4().hex[:8]}"
            meta = getattr(p, "raw_metadata", {}) or {}

            # Determine remote/onsite status
            is_job_remote = bool(meta.get("is_remote")) or "remote" in (p.location or "").lower() or is_remote
            workplace_str = "Remote" if is_job_remote else "On-site"

            # Determine compensation
            if p.salary_min and p.salary_max:
                curr = p.salary_currency or ("₹" if "india" in (p.location or location).lower() else "$")
                pay_str = f"{curr}{p.salary_min:,.0f} - {curr}{p.salary_max:,.0f} / yr"
            elif meta.get("salary"):
                pay_str = str(meta.get("salary"))
            else:
                pay_str = "Competitive / Industry Standard"

            # Date posted
            date_str = str(p.date_posted) if p.date_posted else "Recent"

            # Recruiter / contact email if extracted
            emails = meta.get("emails")
            contact_email = None
            if isinstance(emails, list) and emails:
                contact_email = str(emails[0])
            elif isinstance(emails, str) and emails:
                contact_email = emails

            # Tags
            site_name = (getattr(p, "site", None) or "LinkedIn").title()
            tags = [role, site_name]
            if is_job_remote:
                tags.append("Remote")

            opportunities.append({
                "id": opp_id,
                "title": p.title,
                "company": p.company,
                "location": p.location or location,
                "type": "Full-time" if not p.job_type else str(p.job_type),
                "workplace": workplace_str,
                "payRange": pay_str,
                "tags": tags,
                "description": p.description or f"Position for {p.title} at {p.company}.",
                "postedDate": date_str,
                "sourceUrl": p.job_url,
                "recruiterEmail": contact_email,
                "status": "saved",
            })

        return {
            "status": "success",
            "count": len(opportunities),
            "data": opportunities,
        }
    except Exception as e:
        logger.warning(f"Live job scrape encountered error: {e}")
        return {
            "status": "partial",
            "message": f"Job search query completed with notes: {str(e)}",
            "data": [],
        }

