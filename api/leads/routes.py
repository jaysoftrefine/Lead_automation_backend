"""FastAPI Router for Leads search, CRUD, manual addition, presence check, and agent extraction."""

import json
import urllib.parse
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query

from core.logging import logger
from db.models import ContactPerson, EnrichedLead
from db.sqlite import sqlite_manager
from schemas import (
    AddExtractedLeadRequest,
    BatchAddExtractedLeadsRequest,
    CheckPresenceRequest,
    CreateManualLeadRequest,
    UpdateLeadStatusRequest,
    UpdateLeadTypeRequest,
)

router = APIRouter()


@router.get("/leads")
def get_leads(
    search: Optional[str] = Query(None, description="Search term for title or company"),
    site: Optional[str] = Query(None, description="Platform filter (linkedin, naukri, etc.)"),
    status: Optional[str] = Query(None, description="Lead status filter"),
    lead_type: Optional[str] = Query(None, description="Lead type filter ('company', 'personal', 'others', 'all')"),
    company_size: Optional[str] = Query(None, description="Company size filter ('small', 'medium', 'large', 'all')"),
    job_type: Optional[str] = Query(None, description="Job type filter ('contract', 'fulltime', 'parttime', 'all')"),
    hours_old: Optional[int] = Query(None, description="Filter leads found within last N hours (e.g. 24, 72, 168, 720)"),
    date_from: Optional[str] = Query(None, description="Filter leads posted/scraped from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter leads posted/scraped to date (YYYY-MM-DD)"),
    date_field: Optional[str] = Query("any", description="Filter field: 'any', 'posted', 'scraped'"),
    min_score: int = Query(0, ge=0, le=100, description="Minimum relevance score"),
    has_contacts: Optional[bool] = Query(None, description="Filter for leads with found contacts"),
    scheduled_job_id: Optional[str] = Query(None, description="Filter leads by scheduled batch/scraping job ID"),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
):
    """Retrieve filtered, paginated list of enriched leads from SQLite."""
    try:
        sqlite_manager.connect()
        return sqlite_manager.get_leads(
            search=search,
            site=site,
            status=status,
            lead_type=lead_type,
            company_size=company_size,
            job_type=job_type,
            hours_old=hours_old,
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
            min_score=min_score,
            has_contacts=has_contacts,
            scheduled_job_id=scheduled_job_id,
            limit=limit,
            page=page,
        )
    except Exception as e:
        logger.error(f"Error fetching leads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lead")
def get_lead_by_url(url: str = Query(..., description="Job URL of the lead")):
    """Get single enriched lead details."""
    try:
        sqlite_manager.connect()
        doc = sqlite_manager.get_lead_by_url(url)
        if not doc:
            raise HTTPException(status_code=404, detail="Lead not found")
        return doc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching lead: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leads/update-status")
def update_lead_status(req: UpdateLeadStatusRequest):
    """Update lead status (new, contacted, qualified, rejected, archived)."""
    try:
        sqlite_manager.connect()
        updated = sqlite_manager.update_lead_status(req.job_url, req.status)
        if not updated:
            raise HTTPException(status_code=404, detail="Lead not found")
        return {"success": True, "status": req.status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating lead status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leads/update-lead-type")
def update_lead_type(req: UpdateLeadTypeRequest):
    """Update lead type classification (company, personal, others)."""
    valid_types = {"company", "personal", "others"}
    if req.lead_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid lead_type. Must be one of: {valid_types}")
    try:
        sqlite_manager.connect()
        updated = sqlite_manager.update_lead_type(req.job_url, req.lead_type)
        if not updated:
            raise HTTPException(status_code=404, detail="Lead not found")
        return {"success": True, "lead_type": req.lead_type}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating lead type: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/lead")
def delete_lead(url: str = Query(..., description="Job URL of the lead")):
    """Delete an enriched lead from SQLite database."""
    try:
        sqlite_manager.connect()
        deleted = sqlite_manager.delete_lead(url)
        if not deleted:
            raise HTTPException(status_code=404, detail="Lead not found")
        return {"success": True, "message": "Lead deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting lead: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leads")
@router.post("/leads/manual")
def create_manual_lead(req: CreateManualLeadRequest):
    """Add a lead manually to the SQLite database."""
    try:
        sqlite_manager.connect()
        job_url = req.job_url.strip() if req.job_url and req.job_url.strip() else f"manual://{uuid.uuid4().hex[:12]}"

        parsed_contacts = []
        for c in req.contacts or []:
            if (c.name and c.name.strip()) or (c.email and c.email.strip()):
                parsed_contacts.append(ContactPerson(
                    name=c.name.strip() if c.name else None,
                    role=c.role.strip() if c.role else None,
                    email=c.email.strip() if c.email else None,
                    phone=c.phone.strip() if c.phone else None,
                    linkedin_url=c.linkedin_url.strip() if c.linkedin_url else None,
                    confidence_score=85,
                    is_verified=bool(c.email and "@" in c.email),
                    verification_status="valid" if (c.email and "@" in c.email) else "unverified"
                ))

        lead = EnrichedLead(
            job_url=job_url,
            title=req.title.strip(),
            company=req.company.strip(),
            site="manual",
            location=req.location.strip() if req.location else "Remote",
            job_type=req.job_type or "fulltime",
            job_description=req.lead_summary or f"Manual entry for {req.company}",
            is_valid_lead=True,
            relevance_score=req.relevance_score or 80,
            company_domain=req.company_domain.strip() if req.company_domain else None,
            company_summary=req.lead_summary,
            company_size=req.company_size or "Small (1-50)",
            lead_type=req.lead_type or "others",
            contacts=parsed_contacts,
            key_technologies=[t.strip() for t in req.key_technologies if t and t.strip()] if req.key_technologies else [],
            hiring_urgency="Normal",
            lead_summary=req.lead_summary,
            status="new",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        saved = sqlite_manager.upsert_enriched_lead(lead)
        if not saved:
            raise HTTPException(status_code=500, detail="Failed to save manual lead to database")

        return {
            "success": True,
            "message": f"Lead for '{req.company}' successfully added to database",
            "job_url": job_url,
            "lead": sqlite_manager.get_lead_by_url(job_url),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating manual lead: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leads/check-presence")
def check_leads_presence(req: CheckPresenceRequest):
    """Check which leads / contacts are already present in the SQLite database."""
    try:
        sqlite_manager.connect()
        conn = sqlite_manager.get_connection()
        cur = conn.cursor()
        results: Dict[str, Any] = {}

        for item in req.leads:
            company = (item.company or "").strip()
            email = (item.email or "").strip().lower()
            name = (item.name or "").strip().lower()
            key = email if email else f"{company.lower()}::{name}"

            already_exists = False
            matched_company = False
            lead_id = None

            if company:
                cur.execute("SELECT id, contacts FROM enriched_leads WHERE LOWER(company) = LOWER(?)", (company,))
                c_row = cur.fetchone()
                if c_row:
                    matched_company = True
                    lead_id = c_row["id"]
                    try:
                        contacts_list = json.loads(c_row["contacts"] or "[]")
                        for c in contacts_list:
                            c_email = (c.get("email") or "").lower().strip()
                            c_name = (c.get("name") or "").lower().strip()
                            if (email and c_email == email) or (name and c_name == name):
                                already_exists = True
                                break
                    except Exception:
                        pass

            if not already_exists and email and "@" in email:
                cur.execute("SELECT id FROM enriched_leads WHERE LOWER(contacts) LIKE ?", (f"%{email}%",))
                row = cur.fetchone()
                if row:
                    already_exists = True
                    lead_id = row["id"]

            results[key] = {
                "already_exists": already_exists,
                "company_exists": matched_company,
                "lead_id": lead_id,
            }

        return {"status": "success", "results": results}
    except Exception as e:
        logger.error(f"Error checking presence: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leads/add-from-agent")
def add_lead_from_agent(req: AddExtractedLeadRequest):
    """Add an extracted contact from Autonomous Research Agent to leads.
    If company lead exists: appends contact to its contacts list (if not duplicate).
    If company lead does not exist: creates a new EnrichedLead.
    """
    try:
        sqlite_manager.connect()
        conn = sqlite_manager.get_connection()
        cur = conn.cursor()

        company = req.company.strip()
        name = req.name.strip() if req.name else "Decision Maker"
        role = req.role.strip() if req.role else "Executive"
        email = req.email.strip() if req.email else None

        cur.execute("SELECT id, job_url, company, contacts FROM enriched_leads WHERE LOWER(company) = LOWER(?)", (company,))
        existing_lead = cur.fetchone()

        contact_linkedin = req.linkedin_url
        if not contact_linkedin and name and company:
            contact_linkedin = f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(f'{name} {company}')}"

        if existing_lead:
            contacts_list = []
            try:
                contacts_list = json.loads(existing_lead["contacts"] or "[]")
            except Exception:
                contacts_list = []

            is_duplicate = False
            for c in contacts_list:
                c_email = (c.get("email") or "").strip().lower()
                c_name = (c.get("name") or "").strip().lower()
                if (email and c_email == email.lower()) or (name.lower() == c_name):
                    is_duplicate = True
                    break

            if is_duplicate:
                return {
                    "success": True,
                    "already_existed": True,
                    "message": f"'{name}' is already attached to lead '{company}'.",
                    "lead_id": existing_lead["id"],
                }

            new_contact = {
                "name": name,
                "role": role,
                "email": email,
                "phone": None,
                "linkedin_url": contact_linkedin,
                "confidence_score": 85,
                "source_url": "instant_agent_lab",
                "is_verified": bool(email and "@" in email),
                "verification_status": "valid" if (email and "@" in email) else "unverified",
                "verification_details": "Extracted by Autonomous Research Agent",
            }
            contacts_list.append(new_contact)
            cur.execute(
                "UPDATE enriched_leads SET contacts = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(contacts_list), existing_lead["id"]),
            )
            conn.commit()

            return {
                "success": True,
                "action": "contact_appended",
                "message": f"Added '{name}' ({role}) to existing lead '{company}'!",
                "lead_id": existing_lead["id"],
            }
        else:
            job_url = f"agent://{uuid.uuid4().hex[:12]}"
            domain = None
            if email and "@" in email:
                dom = email.split("@")[-1].lower()
                if not any(dom.endswith(pub) for pub in ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"]):
                    domain = dom

            parsed_contact = ContactPerson(
                name=name,
                role=role,
                email=email,
                phone=None,
                linkedin_url=contact_linkedin,
                confidence_score=85,
                source_url="instant_agent_lab",
                is_verified=bool(email and "@" in email),
                verification_status="valid" if (email and "@" in email) else "unverified",
                verification_details="Extracted by Autonomous Research Agent",
            )

            prompt_desc = (
                f"Discovered via Autonomous Research Agent. Objective: {req.research_prompt}"
                if req.research_prompt
                else f"Discovered via Autonomous Research Agent for {company}."
            )

            lead = EnrichedLead(
                job_url=job_url,
                title=f"{role} at {company}",
                company=company,
                site="instant_agent",
                location=req.location or "Europe / Global",
                job_type="fulltime",
                job_description=prompt_desc,
                is_valid_lead=True,
                relevance_score=85,
                company_domain=domain,
                company_summary=f"{company} - identified during agent intelligence research.",
                company_size="Small (1-50)",
                lead_type=req.lead_type or "company",
                contacts=[parsed_contact],
                key_technologies=[],
                hiring_urgency="Normal",
                lead_summary=f"Extracted contact {name} ({role}) from {company}.",
                status="new",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

            saved = sqlite_manager.upsert_enriched_lead(lead)
            if not saved:
                raise HTTPException(status_code=500, detail="Failed to save lead to database")

            return {
                "success": True,
                "action": "lead_created",
                "message": f"Successfully created new lead for '{company}' ({name})!",
                "job_url": job_url,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding lead from agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leads/add-batch-from-agent")
def add_batch_leads_from_agent(req: BatchAddExtractedLeadsRequest):
    """Batch add multiple extracted contacts from Autonomous Research Agent."""
    added_count = 0
    skipped_count = 0
    errors = []

    for item in req.leads:
        try:
            if not item.research_prompt and req.research_prompt:
                item.research_prompt = req.research_prompt
            res = add_lead_from_agent(item)
            if res.get("already_existed"):
                skipped_count += 1
            else:
                added_count += 1
        except Exception as e:
            errors.append(f"{item.company}: {str(e)}")

    return {
        "success": True,
        "added_count": added_count,
        "skipped_count": skipped_count,
        "errors": errors,
        "message": f"Successfully added {added_count} lead(s) to database! ({skipped_count} already present)",
    }
