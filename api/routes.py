"""FastAPI Router for Lead Generation Engine."""

import asyncio
import csv
import io
import json
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Response, WebSocket, WebSocketDisconnect, Request

from schemas import (
    RunPipelineRequest,
    TestEnrichmentRequest,
    UpdateLeadStatusRequest,
    UpdateLeadTypeRequest,
    InstantResearchRequest,
    ManualContactInput,
    CreateManualLeadRequest,
    CheckPresenceItem,
    CheckPresenceRequest,
    AddExtractedLeadRequest,
    BatchAddExtractedLeadsRequest,
)

from config.settings import settings
from core.logging import logger
from core.company_filter import is_matching_company_size, classify_company_size, detect_job_type_filter
from db.sqlite import sqlite_manager
from db.models import RawJobPosting, EnrichedLead, ContactPerson
from enrichment.agent import LeadEnrichmentAgent
from pipeline.orchestrator import LeadGenOrchestrator, PipelineMetrics
from llm.registry import LLMProviderRegistry

router = APIRouter(prefix="/api")


# --- WebSocket Manager for Real-Time Streaming ---
class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self._lock:
            self.active_connections.append(websocket)
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def disconnect(self, websocket: WebSocket):
        with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    def broadcast(self, data: Dict[str, Any]):
        with self._lock:
            if not self.active_connections:
                return
            loop = self.loop
            if not loop or loop.is_closed():
                try:
                    loop = asyncio.get_running_loop()
                    self.loop = loop
                except RuntimeError:
                    return
        try:
            asyncio.run_coroutine_threadsafe(self._send_all(data), loop)
        except Exception as e:
            logger.debug(f"WebSocket broadcast error: {e}")

    async def _send_all(self, data: Dict[str, Any]):
        with self._lock:
            conns = list(self.active_connections)
        dead = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        if dead:
            with self._lock:
                for d in dead:
                    if d in self.active_connections:
                        self.active_connections.remove(d)


ws_manager = WebSocketManager()


# --- Pipeline State Management ---
class PipelineState:
    def __init__(self):
        self.is_running: bool = False
        self.status: str = "idle"  # idle, scraping, enriching, completed, error
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.current_job_title: Optional[str] = None
        self.current_company: Optional[str] = None
        self.processed_count: int = 0
        self.total_count: int = 0
        self.logs: List[Dict[str, Any]] = []
        self.metrics: Optional[Dict[str, Any]] = None
        self.error_message: Optional[str] = None
        self._stop_requested: bool = False
        self._lock = threading.RLock()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "is_running": self.is_running,
                "status": self.status,
                "processed_count": self.processed_count,
                "total_count": self.total_count,
                "current_job_title": self.current_job_title,
                "current_company": self.current_company,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "logs": list(self.logs),
                "metrics": self.metrics,
                "error_message": self.error_message,
            }

    def notify(self):
        ws_manager.broadcast(self.to_dict())

    def add_log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.logs.append({"time": timestamp, "message": message, "level": level})
            if len(self.logs) > 200:
                self.logs.pop(0)
        self.notify()

    def reset(self, total: int = 0):
        with self._lock:
            self.is_running = True
            self.status = "initializing"
            self.started_at = time.time()
            self.finished_at = None
            self.current_job_title = None
            self.current_company = None
            self.processed_count = 0
            self.total_count = total
            self.logs = []
            self.metrics = None
            self.error_message = None
            self._stop_requested = False
        self.notify()

    def finish(self, metrics: Optional[PipelineMetrics] = None, error: Optional[str] = None):
        with self._lock:
            self.is_running = False
            self.finished_at = time.time()
            if error:
                self.status = "error"
                self.error_message = error
                self.logs.append({"time": datetime.now().strftime("%H:%M:%S"), "message": f"Pipeline error: {error}", "level": "error"})
            else:
                self.status = "completed"
                self.logs.append({"time": datetime.now().strftime("%H:%M:%S"), "message": "Pipeline completed successfully!", "level": "success"})
            if metrics:
                self.metrics = {
                    "search_term": metrics.search_term,
                    "location": metrics.location,
                    "sites": metrics.sites,
                    "target_company_size": getattr(metrics, "target_company_size", "small"),
                    "total_scraped": metrics.total_scraped,
                    "already_existing": metrics.already_existing,
                    "processed_by_agent": metrics.processed_by_agent,
                    "saved_to_db": metrics.saved_to_db,
                    "rejected_by_llm": metrics.rejected_by_llm,
                    "total_contacts_discovered": metrics.total_contacts_discovered,
                    "duration_seconds": metrics.duration_seconds,
                }
        self.notify()


pipeline_state = PipelineState()


# --- Worker Function ---
def _execute_pipeline_task(req: RunPipelineRequest):
    sites = req.sites or req.platforms or ["linkedin"]  # ["linkedin", "indeed"]
    target_goal = req.limit or req.results_wanted or 10
    target_size = req.company_size or "small"
    detected_job_type, effective_search_term = detect_job_type_filter(req.search_term, explicit_job_type=req.job_type)
    
    remote_tag = " [REMOTE ONLY]" if req.is_remote else ""
    size_label = "ALL SIZES" if target_size == "all" else f"{target_size.upper()} [Max 50]"
    pipeline_state.add_log(
        f"🎯 GOAL: Scrape & evaluate {target_goal} leads for '{req.search_term}' (Location: '{req.location}'{remote_tag}, Size: {size_label}, Min Score: {req.min_score})",
        "info"
    )
    pipeline_state.add_log(f"Target platforms: {', '.join(sites)} in '{req.location}'", "info")

    try:
        # Connect to DB
        sqlite_manager.connect()
        pipeline_state.add_log("Connected to centralized SQLite database successfully.", "info")

        # Initialize Agent
        provider_name = req.provider or req.llm_provider or settings.default_llm_provider
        model_name = req.model or req.model_name
        agent = LeadEnrichmentAgent(
            provider_name=provider_name,
            model_name=model_name,
        )

        orchestrator = LeadGenOrchestrator(
            agent=agent,
            db=sqlite_manager,
            min_relevance_score=req.min_score,
        )

        pipeline_state.status = "scraping"
        
        # Scrape raw candidate listings
        raw_to_fetch = min(max(target_goal * 2, 10), 50)
        
        # 1. Scrape Job Boards (JobSpy)
        pipeline_state.add_log(f"📡 Scraping candidate jobs from {', '.join(sites)} in '{req.location}' (Remote: {req.is_remote})...", "info")
        raw_postings = orchestrator.scraper.scrape(
            search_term=effective_search_term,
            location=req.location,
            results_wanted=raw_to_fetch,
            hours_old=req.hours_old,
            sites=sites,
            job_type=detected_job_type,
            is_remote=req.is_remote,
        )

        unique_comps = sorted(list({p.company.strip() for p in raw_postings if p.company and p.company.strip()}))
        metrics = PipelineMetrics(
            search_term=req.search_term,
            location=req.location,
            sites=req.sites,
            target_company_size=target_size,
            target_job_type=detected_job_type or "all",
            total_scraped=len(raw_postings),
            unique_companies_count=len(unique_comps),
            unique_companies=unique_comps,
        )

        pipeline_state.total_count = target_goal
        pipeline_state.add_log(
            f"📥 Fetched {len(raw_postings)} candidate postings across {len(unique_comps)} unique companies. Starting qualification loop...",
            "success"
        )
        if unique_comps:
            sample_preview = ", ".join(unique_comps[:6]) + ("..." if len(unique_comps) > 6 else "")
            pipeline_state.add_log(f"🏢 Discovered companies: {sample_preview}", "info")

        if not raw_postings:
            pipeline_state.add_log("⚠️ No job postings found matching search parameters.", "warning")
            metrics.end_time = time.time()
            pipeline_state.finish(metrics=metrics)
            return

        pipeline_state.status = "enriching"
        pipeline_state.add_log(f"🧠 [2/2] Starting autonomous AI research on {len(raw_postings)} postings (Filtering for {target_size.upper()} [Max 50] companies & {str(detected_job_type or 'all').upper()} jobs)...", "info")

        for idx, job in enumerate(raw_postings, start=1):
            if pipeline_state._stop_requested:
                pipeline_state.add_log("🛑 Pipeline run manually stopped by user.", "warning")
                break

            pct = round((idx / len(raw_postings)) * 100)
            pipeline_state.processed_count = idx
            pipeline_state.current_job_title = job.title
            pipeline_state.current_company = job.company

            pipeline_state.add_log(
                f"🔍 [{idx}/{len(raw_postings)} | {pct}%] Processing: '{job.title}' @ '{job.company}' ({job.site.upper()})",
                "info"
            )

            # Save raw job
            sqlite_manager.save_raw_job(job)

            # Check duplicate (if skip_existing is True)
            if req.skip_existing and sqlite_manager.job_exists(job.job_url):
                pipeline_state.add_log(
                    f"⏭️ SKIPPED (Duplicate): '{job.company}' - already exists in SQLite database. (Uncheck 'Skip Duplicates' to force re-enrich)",
                    "warning"
                )
                metrics.already_existing += 1
                continue

            metrics.processed_by_agent += 1
            try:
                pipeline_state.add_log(f"🤖 Agent researching '{job.company}' (Domain, Company Size & Contacts)...", "info")
                enriched_lead = agent.enrich_job(
                    job,
                    target_company_size=target_size,
                    target_job_type=detected_job_type or "all",
                )

                if not enriched_lead.is_valid_lead:
                    pipeline_state.add_log(
                        f"❌ REJECTED (Invalid Lead): '{job.company}' - {enriched_lead.lead_summary}",
                        "warning"
                    )
                    metrics.rejected_by_llm += 1
                    sqlite_manager.upsert_enriched_lead(enriched_lead)
                    continue

                if enriched_lead.relevance_score < req.min_score:
                    pipeline_state.add_log(
                        f"❌ REJECTED (Low Score): '{job.company}' scored {enriched_lead.relevance_score}/100 < required {req.min_score}",
                        "warning"
                    )
                    metrics.rejected_by_llm += 1
                    continue

                # Strict Company Size Validation (Max 50 for small)
                if target_size != "all" and not is_matching_company_size(enriched_lead.company_size, target_filter=target_size):
                    pipeline_state.add_log(
                        f"❌ REJECTED (Size Mismatch): '{job.company}' size is '{enriched_lead.company_size or 'Unknown'}' (Target was '{target_size}' max 50)",
                        "warning"
                    )
                    metrics.rejected_by_size += 1
                    metrics.rejected_by_llm += 1
                    continue

                sqlite_manager.upsert_enriched_lead(enriched_lead)
                metrics.saved_to_db += 1
                pipeline_state.processed_count = metrics.saved_to_db
                contacts_found = len(enriched_lead.contacts)
                metrics.total_contacts_discovered += contacts_found

                contact_preview = ", ".join([f"{c.name or 'Executive'} ({c.email or 'Domain'})" for c in enriched_lead.contacts[:2]])
                pipeline_state.add_log(
                    f"✅ [{metrics.saved_to_db}/{target_goal} Target Qualified Leads] SAVED: '{job.company}' ({enriched_lead.company_size or 'Small'}) [{enriched_lead.job_type or 'Contract'}] | Score: {enriched_lead.relevance_score}/100 | Contacts ({contacts_found}): {contact_preview or 'Company Domain'}",
                    "success"
                )

                # Check if we have fulfilled the user's exact requested target count!
                if metrics.saved_to_db >= target_goal:
                    pipeline_state.add_log(
                        f"🎉 TARGET REACHED: Successfully discovered and saved all {target_goal} qualified leads matching all your filter criteria!",
                        "success"
                    )
                    break

            except Exception as item_err:
                logger.error(f"Error enriching {job.job_url}: {item_err}")
                pipeline_state.add_log(f"⚠️ Error researching {job.company}: {str(item_err)}", "error")

        metrics.end_time = time.time()
        
        # Log final summary
        pipeline_state.add_log("=" * 60, "info")
        pipeline_state.add_log(
            f"📊 SUMMARY: Goal: {target_goal} Qualified Leads | Found & Saved: {metrics.saved_to_db} | Scraped: {metrics.total_scraped} | Processed: {metrics.processed_by_agent} | Rejected: {metrics.rejected_by_llm} | Contacts: {metrics.total_contacts_discovered}",
            "success"
        )
        pipeline_state.finish(metrics=metrics)

    except Exception as e:
        logger.exception("Pipeline execution failed")
        pipeline_state.finish(error=str(e))
    finally:
        pipeline_state.is_running = False


# --- API Routes ---

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


@router.get("/leads")
def get_leads(
    search: Optional[str] = Query(None, description="Search term for title or company"),
    site: Optional[str] = Query(None, description="Platform filter (linkedin, naukri, etc.)"),
    status: Optional[str] = Query(None, description="Lead status filter"),
    lead_type: Optional[str] = Query(None, description="Lead type filter ('company', 'personal', 'others', 'all')"),
    company_size: Optional[str] = Query(None, description="Company size filter ('small', 'medium', 'large', 'all')"),
    job_type: Optional[str] = Query(None, description="Job type filter ('contract', 'fulltime', 'parttime', 'all')"),
    hours_old: Optional[int] = Query(None, description="Filter leads found within last N hours (e.g. 24, 72, 168, 720)"),
    min_score: int = Query(0, ge=0, le=100, description="Minimum relevance score"),
    has_contacts: Optional[bool] = Query(None, description="Filter for leads with found contacts"),
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
            min_score=min_score,
            has_contacts=has_contacts,
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
        import uuid
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
        import uuid
        sqlite_manager.connect()
        conn = sqlite_manager.get_connection()
        cur = conn.cursor()

        company = req.company.strip()
        name = req.name.strip() if req.name else "Decision Maker"
        role = req.role.strip() if req.role else "Executive"
        email = req.email.strip() if req.email else None

        # Check if company lead already exists
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


@router.post("/pipeline/run")
@router.post("/pipeline/start")
def trigger_pipeline(req: RunPipelineRequest, background_tasks: BackgroundTasks):
    """Trigger the scraping and enrichment pipeline asynchronously."""
    # Safety: Auto-clear stale running state if > 3 minutes
    if pipeline_state.is_running and pipeline_state.started_at and (time.time() - pipeline_state.started_at) > 180:
        logger.warning("Auto-clearing stale running pipeline state.")
        pipeline_state.is_running = False

    if pipeline_state.is_running:
        raise HTTPException(status_code=409, detail="A pipeline task is already currently running. Click Stop or wait a moment.")

    target_goal = req.limit or req.results_wanted or 10
    pipeline_state.reset(total=target_goal)
    pipeline_state.add_log(f"🚀 Initializing autonomous pipeline for '{req.search_term}'...", "info")

    thread = threading.Thread(target=_execute_pipeline_task, args=(req,), daemon=True)
    thread.start()

    return {
        "success": True,
        "message": "Pipeline started in background.",
        "search_term": req.search_term,
        "limit": target_goal,
    }


@router.websocket("/pipeline/ws")
async def websocket_pipeline_status(websocket: WebSocket):
    """Real-time WebSocket endpoint streaming pipeline status, progress, and terminal logs."""
    await ws_manager.connect(websocket)
    try:
        # Immediately send current state snapshot upon connection
        await websocket.send_json(pipeline_state.to_dict())
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if msg == "ping":
                    await websocket.send_text("pong")
                elif msg in ("status", "get_status"):
                    await websocket.send_json(pipeline_state.to_dict())
            except asyncio.TimeoutError:
                # Keep-alive heartbeat and state sync while running
                if pipeline_state.is_running:
                    await websocket.send_json(pipeline_state.to_dict())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        ws_manager.disconnect(websocket)


@router.get("/pipeline/status")
def get_pipeline_status():
    """Get the current live status and logs of the pipeline."""
    return pipeline_state.to_dict()


@router.post("/pipeline/stop")
def stop_pipeline():
    """Request pipeline to cancel current run."""
    if not pipeline_state.is_running:
        return {"success": False, "message": "No active pipeline running."}
    pipeline_state._stop_requested = True
    pipeline_state.add_log("Stop request received, winding down...", "warning")
    return {"success": True, "message": "Stop requested."}


@router.post("/pipeline/test-enrichment")
def test_enrichment_direct(req: TestEnrichmentRequest):
    """Directly test the autonomous LLM thinking agent & Tavily research on a single custom job without scraping."""
    try:
        sample_job = RawJobPosting(
            title=req.title,
            company=req.company,
            location=req.location or "Remote",
            job_url=req.job_url or f"https://example.com/jobs/{int(time.time())}",
            site="direct_test",
            description=req.job_description or f"Job opening for {req.title} at {req.company}.",
        )

        agent = LeadEnrichmentAgent(
            provider_name=req.provider or settings.default_llm_provider,
            model_name=req.model,
        )

        lead: EnrichedLead = agent.enrich_job(
            sample_job,
            target_company_size=req.target_company_size or "small",
            target_job_type=req.target_job_type or "all",
        )

        # Save to DB if requested
        if req.save_to_db:
            try:
                sqlite_manager.connect()
                sqlite_manager.upsert_enriched_lead(lead)
            except Exception as db_err:
                logger.warning(f"Could not persist test lead to SQLite: {db_err}")

        lead_dict = lead.model_dump()
        lead_dict["created_at"] = lead.created_at.isoformat()
        lead_dict["updated_at"] = lead.updated_at.isoformat()

        return {
            "success": True,
            "lead": lead_dict,
        }
    except Exception as e:
        logger.exception("Direct test enrichment failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/instant-research")
def run_instant_research(req: InstantResearchRequest):
    """Execute autonomous real-time web research, Tavily search, and LLM synthesis based on a custom prompt."""
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Research prompt cannot be empty.")

    try:
        # 1. Tavily Search for live information
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

        # 2. Invoke LLM for synthesis and structured contact extraction
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
            "  ]\n"
            "}\n"
            "Do not wrap in anything other than valid JSON or markdown json block."
        )

        user_content = (
            f"Research Objective:\n{prompt}\n\n"
            f"Web Search Results Context:\n{search_context if search_context else 'No live search results returned. Use your general knowledge.'}"
        )

        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content=system_instruction),
            HumanMessage(content=user_content),
        ]

        response = chat_model.invoke(messages)
        raw_text = response.content if hasattr(response, "content") else str(response)

        # Parse JSON from response
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
            # Fallback if json parsing fails
            report_text = raw_text

        if not report_text:
            report_text = raw_text

        # Annotate extracted leads with already_exists status
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




        
