"""Agent leads ingestion and duplicate checking for EU Startups."""

import urllib.parse
import uuid
from typing import Any, Dict
from fastapi import APIRouter, HTTPException

from eu_startups.db import get_connection
from api.eu_startups.schemas import (
    AgentStartupPersonInput,
    BatchAddAgentStartupsRequest,
    CheckStartupPresenceRequest,
)

router = APIRouter()


@router.post("/check-presence")
def check_startups_presence(req: CheckStartupPresenceRequest):
    """Check which researched startups or founders are already in the EU Startups SQLite database."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        results: Dict[str, Any] = {}

        for item in req.leads:
            company = (item.company or "").strip()
            email = (item.email or "").strip().lower()
            name = (item.name or "").strip().lower()
            key = email if email else f"{company.lower()}::{name}"

            already_exists = False
            startup_exists = False
            startup_id = None

            if company:
                s_row = cur.execute(
                    "SELECT id FROM startups WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(?))",
                    (company,)
                ).fetchone()
                if s_row:
                    startup_exists = True
                    startup_id = s_row[0]
                    if email:
                        p_row = cur.execute(
                            "SELECT id FROM people WHERE startup_id = ? AND LOWER(TRIM(email)) = ?",
                            (startup_id, email)
                        ).fetchone()
                        if p_row:
                            already_exists = True
                    elif name:
                        p_row = cur.execute(
                            "SELECT id FROM people WHERE startup_id = ? AND LOWER(TRIM(name)) = ?",
                            (startup_id, name)
                        ).fetchone()
                        if p_row:
                            already_exists = True
                    else:
                        already_exists = True

            if not already_exists and email:
                p_row = cur.execute(
                    "SELECT id, startup_id FROM people WHERE LOWER(TRIM(email)) = ?",
                    (email,)
                ).fetchone()
                if p_row:
                    already_exists = True
                    startup_id = p_row[1]

            results[key] = {
                "already_exists": already_exists,
                "startup_exists": startup_exists,
                "startup_id": startup_id,
            }

        conn.close()
        return {"status": "success", "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check startup presence: {str(e)}")


@router.post("/add-from-agent")
def add_startup_from_agent(req: AgentStartupPersonInput):
    """Add a researched European startup and founder to EU Startups Explorer database."""
    try:
        conn = get_connection()
        cur = conn.cursor()

        company_name = req.company.strip()
        if not company_name:
            raise HTTPException(status_code=400, detail="Company name is required")

        email = req.email.strip() if req.email else None
        name = req.name.strip() if req.name else "Founder / Leadership"
        role = req.role.strip() if req.role else "Co-Founder & CEO"

        website = req.website
        if not website and email and "@" in email:
            dom = email.split("@")[-1].lower()
            if not any(dom.endswith(pub) for pub in ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"]):
                website = f"https://{dom}"
        elif not website:
            clean_dom = company_name.lower().replace(" ", "").replace("&", "and")
            website = f"https://{clean_dom}.com"

        linkedin = req.linkedin
        if not linkedin and name and company_name:
            linkedin = f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(f'{name} {company_name}')}"

        company_linkedin = f"https://www.linkedin.com/search/results/all/?keywords={urllib.parse.quote(company_name)}"

        existing = cur.execute(
            "SELECT id FROM startups WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(?))",
            (company_name,)
        ).fetchone()

        if existing:
            startup_id = existing[0]
            if website:
                cur.execute("UPDATE startups SET website = COALESCE(website, ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?", (website, startup_id))
        else:
            eu_url = f"agent://{uuid.uuid4().hex[:12]}"
            country = req.country or "Europe"
            city = req.city or ""
            c_low = company_name.lower()
            if "mistral" in c_low:
                country, city = "France", "Paris"
            elif "osapiens" in c_low:
                country, city = "Germany", "Mannheim"
            elif "deepset" in c_low or "n8n" in c_low:
                country, city = "Germany", "Berlin"

            cur.execute("""
                INSERT INTO startups (
                    company_name, description, website, eu_startups_url,
                    country, city, category, tags, company_linkedin,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (
                company_name,
                f"Researched via Autonomous Research Agent. Objective: {req.research_prompt or 'European B2B Tech Startups'}",
                website,
                eu_url,
                country,
                city,
                req.category or "Artificial Intelligence & B2B SaaS",
                "AI, SaaS, Autonomous Agent",
                company_linkedin,
            ))
            startup_id = cur.lastrowid

        # Check if person is already attached
        is_duplicate = False
        if email:
            p_check = cur.execute(
                "SELECT id FROM people WHERE startup_id = ? AND LOWER(TRIM(email)) = ?",
                (startup_id, email.lower())
            ).fetchone()
            if p_check:
                is_duplicate = True
        elif name:
            p_check = cur.execute(
                "SELECT id FROM people WHERE startup_id = ? AND LOWER(TRIM(name)) = ?",
                (startup_id, name.lower())
            ).fetchone()
            if p_check:
                is_duplicate = True

        if is_duplicate:
            conn.close()
            return {
                "success": True,
                "already_existed": True,
                "startup_id": startup_id,
                "company_name": company_name,
                "message": f"'{name}' is already attached to '{company_name}' in EU Startups Explorer.",
            }

        cur.execute("""
            INSERT INTO people (
                startup_id, name, role, email, linkedin, source_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            startup_id,
            name,
            role,
            email,
            linkedin,
            "instant_agent_lab",
        ))

        conn.commit()
        conn.close()

        return {
            "success": True,
            "startup_id": startup_id,
            "company_name": company_name,
            "action": "person_added" if existing else "startup_created",
            "message": f"Added '{name}' ({company_name}) to EU Startups Explorer!",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add startup from agent: {str(e)}")


@router.post("/add-batch-from-agent")
def add_batch_startups_from_agent(req: BatchAddAgentStartupsRequest):
    """Batch add multiple researched European startups and founders to EU Startups Explorer."""
    added_count = 0
    skipped_count = 0
    errors = []

    for item in req.startups:
        try:
            if not item.research_prompt and req.research_prompt:
                item.research_prompt = req.research_prompt
            res = add_startup_from_agent(item)
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
        "message": f"Successfully added {added_count} startup(s) to EU Startups Explorer! ({skipped_count} already present)",
    }
