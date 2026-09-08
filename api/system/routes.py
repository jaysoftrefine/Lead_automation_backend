"""FastAPI Router for System statistics, export, database maintenance, and instant research."""

import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Request, Response

from config.settings import settings
from core.logging import logger
from db.sqlite import sqlite_manager
from llm.registry import LLMProviderRegistry
from schemas import InstantResearchRequest
from api.pipeline.state import pipeline_state

router = APIRouter()


@router.post("/database/clear")
def clear_database_api():
    """Clear all enriched leads and raw jobs from centralized SQLite database."""
    try:
        sqlite_manager.connect()
        res = sqlite_manager.clear_database()
        return {
            "success": True,
            "leads_deleted": res.get("leads_deleted", 0),
            "raw_jobs_deleted": res.get("raw_jobs_deleted", 0),
            "message": f"Database cleared: {res.get('leads_deleted', 0)} leads deleted.",
        }
    except Exception as e:
        logger.error(f"Error clearing database: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/database/backfill")
async def backfill_database_api(request: Request):
    """
    Backfill database records.
    - If called with a JSON payload of leads/raw_jobs, it imports and upserts them into SQLite.
    - Runs relational backfill to sync date_posted and scraped_at between raw jobs and enriched leads.
    """
    try:
        sqlite_manager.connect()
        payload = None
        raw_body = await request.body()
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except Exception as pe:
                logger.warning(f"Could not parse request body as JSON: {pe}")

        result = sqlite_manager.import_and_backfill(payload)
        return result
    except Exception as e:
        logger.error(f"Error in backfill API: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/database/export")
def export_database_api():
    """Export all enriched leads and raw jobs from centralized SQLite database as JSON."""
    try:
        sqlite_manager.connect()
        return sqlite_manager.export_all_data()
    except Exception as e:
        logger.error(f"Error exporting database: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def get_stats():
    """Get system statistics, database counts, and configuration status."""
    sqlite_manager.connect()
    db_stats = sqlite_manager.get_stats()

    return {
        "db_connected": db_stats.get("db_connected", True),
        "database_name": db_stats.get("database_name", "SQLite"),
        "default_provider": settings.default_llm_provider,
        "gemini_configured": bool(settings.google_api_key and settings.google_api_key != "your_google_api_key_here"),
        "nvidia_configured": bool(settings.nvidia_api_key and settings.nvidia_api_key != "your_nvidia_api_key_here"),
        "tavily_configured": bool(settings.tavily_api_key and settings.tavily_api_key != "your_tavily_api_key_here"),
        "leads_count": db_stats.get("leads_count", 0),
        "raw_jobs_count": db_stats.get("raw_jobs_count", 0),
        "total_contacts_discovered": db_stats.get("total_contacts_discovered", 0),
        "avg_relevance_score": db_stats.get("avg_relevance_score", 0.0),
        "is_pipeline_running": pipeline_state.is_running,
    }


@router.post("/instant-research")
def run_instant_research(req: InstantResearchRequest):
    """Execute autonomous real-time web research, Tavily search, and LLM synthesis based on a custom prompt."""
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Research prompt cannot be empty.")

    try:
        from enrichment.tools.web_search import TavilySearchTool
        tavily = TavilySearchTool()
        search_results = tavily.search(query=prompt, max_results=req.max_search_results or 5, search_depth="advanced")

        sources: List[str] = []
        search_context_snippets: List[str] = []
        for res in search_results:
            url = res.get("url")
            if url and url not in sources:
                sources.append(url)
            title = res.get("title", "")
            content = res.get("content", "")
            search_context_snippets.append(f"Title: {title}\nURL: {url}\nContent: {content}\n")

        search_context = "\n---\n".join(search_context_snippets)

        provider = LLMProviderRegistry.get_provider()
        chat_model = provider.get_chat_model()

        system_instruction = (
            "You are an elite B2B Intelligence and Lead Research Agent.\n"
            "Analyze the real-time web search findings to answer the user's objective comprehensively.\n"
            "You must return your output strictly formatted as a JSON object with this structure:\n"
            "{\n"
            '  "report": "A detailed, executive markdown summary of your findings, market analysis, company overviews, and strategic insights.",\n'
            '  "extracted_leads": [\n'
            '    {\n'
            '      "name": "Full Name",\n'
            '      "role": "Founder / CEO / Head of ...",\n'
            '      "company": "Company Name",\n'
            '      "email": "verified or deduced email (e.g. name@company.com) or empty if unknown"\n'
            '    }\n'
            '  ]\n'
            "}\n"
            "Do not wrap in anything other than valid JSON or markdown json block."
        )

        user_content = (
            f"Research Objective:\n{prompt}\n\n"
            f"Web Search Results Context:\n{search_context if search_context else 'No live search results returned. Use your general knowledge.'}"
        )

        from langchain_core.messages import HumanMessage, SystemMessage
        messages = [
            SystemMessage(content=system_instruction),
            HumanMessage(content=user_content),
        ]

        response = chat_model.invoke(messages)
        raw_text = response.content if hasattr(response, "content") else str(response)

        cleaned_text = raw_text.strip()
        if "```json" in cleaned_text:
            cleaned_text = cleaned_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned_text:
            cleaned_text = cleaned_text.split("```", 1)[1].split("```", 1)[0].strip()

        report_text = ""
        extracted_leads: List[Dict[str, Any]] = []

        try:
            parsed = json.loads(cleaned_text)
            if isinstance(parsed, dict):
                report_text = parsed.get("report") or parsed.get("summary") or ""
                extracted_leads = parsed.get("extracted_leads") or []
        except Exception:
            report_text = raw_text

        if not report_text:
            report_text = raw_text

        try:
            sqlite_manager.connect()
            conn = sqlite_manager.get_connection()
            cur = conn.cursor()
            for lead in extracted_leads:
                email = (lead.get("email") or "").strip().lower()
                company = (lead.get("company") or "").strip()
                name = (lead.get("name") or "").strip().lower()

                already_exists = False
                matched_company = False

                if company:
                    cur.execute("SELECT id, contacts FROM enriched_leads WHERE LOWER(company) = LOWER(?)", (company,))
                    c_row = cur.fetchone()
                    if c_row:
                        matched_company = True
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
                    if cur.fetchone():
                        already_exists = True

                lead["already_exists"] = already_exists
                lead["company_exists"] = matched_company
        except Exception as err:
            logger.warning(f"Error checking presence of extracted leads: {err}")

        return {
            "success": True,
            "report": report_text,
            "extracted_leads": extracted_leads,
            "sources": sources,
        }

    except Exception as e:
        logger.exception("Instant agent research failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/csv")
def export_leads_csv(
    min_score: int = Query(0),
    site: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    company_size: Optional[str] = Query(None),
    job_type: Optional[str] = Query(None),
    hours_old: Optional[int] = Query(None),
):
    """Export enriched leads to a downloadable CSV file."""
    try:
        sqlite_manager.connect()
        res = sqlite_manager.get_leads(
            site=site,
            status=status,
            company_size=company_size,
            job_type=job_type,
            hours_old=hours_old,
            min_score=min_score,
            limit=10000,
        )
        leads = res.get("leads", [])

        output = io.StringIO()
        writer = csv.writer(output)

        # Headers
        writer.writerow([
            "Company",
            "Company Size",
            "Job Title",
            "Job Type",
            "Domain",
            "Location",
            "Platform",
            "Relevance Score",
            "Status",
            "Urgency",
            "Contacts Found",
            "Primary Contact Name",
            "Primary Contact Role",
            "Primary Contact Email",
            "Primary Contact Phone",
            "Primary Contact LinkedIn",
            "Job URL",
            "Created At",
        ])

        for lead in leads:
            contacts = lead.get("contacts", [])
            primary = contacts[0] if contacts else {}
            writer.writerow([
                lead.get("company", ""),
                lead.get("company_size", "11-50 employees"),
                lead.get("title", ""),
                lead.get("job_type", "Contract"),
                lead.get("company_domain", ""),
                lead.get("location", ""),
                lead.get("site", ""),
                lead.get("relevance_score", 0),
                lead.get("status", "new"),
                lead.get("hiring_urgency", ""),
                len(contacts),
                primary.get("name", ""),
                primary.get("role", ""),
                primary.get("email", ""),
                primary.get("phone", ""),
                primary.get("linkedin_url", ""),
                lead.get("job_url", ""),
                str(lead.get("created_at", "")),
            ])

        output.seek(0)
        filename = f"leads_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error(f"CSV export error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
