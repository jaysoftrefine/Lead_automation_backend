"""FastAPI Router for Lead Generation Engine."""

import csv
import io
import json
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Response
from pydantic import BaseModel, Field

from config.settings import settings
from core.logging import logger
from core.company_filter import is_matching_company_size, classify_company_size, detect_job_type_filter
from db.mongo import mongo_manager
from db.models import RawJobPosting, EnrichedLead
from enrichment.agent import LeadEnrichmentAgent
from pipeline.orchestrator import LeadGenOrchestrator, PipelineMetrics
from llm.registry import LLMProviderRegistry

router = APIRouter(prefix="/api")


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
        self._lock = threading.Lock()

    def add_log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.logs.append({"time": timestamp, "message": message, "level": level})
            if len(self.logs) > 200:
                self.logs.pop(0)

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

    def finish(self, metrics: Optional[PipelineMetrics] = None, error: Optional[str] = None):
        with self._lock:
            self.is_running = False
            self.finished_at = time.time()
            if error:
                self.status = "error"
                self.error_message = error
                self.add_log(f"Pipeline error: {error}", level="error")
            else:
                self.status = "completed"
                self.add_log("Pipeline completed successfully!", level="success")
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


pipeline_state = PipelineState()


# --- Request & Response Models ---
class RunPipelineRequest(BaseModel):
    search_term: str = Field(..., example="Python Backend Developer")
    location: str = Field("Remote", example="Remote")
    sites: List[str] = Field(default_factory=lambda: ["linkedin", "naukri"])
    company_size: str = Field("small", example="small", description="Target company size: 'small' (1-50 employees), 'medium' (51-500), 'large' (500+), or 'all'")
    job_type: Optional[str] = Field("all", example="contract", description="Target job type: 'all', 'contract' (freelance/C2C), 'fulltime', 'parttime', 'internship'")
    limit: int = Field(10, ge=1, le=100)
    provider: Optional[str] = Field(None, example="gemini")
    model: Optional[str] = Field(None, example="gemini-2.5-flash")
    min_score: int = Field(30, ge=0, le=100)
    hours_old: int = Field(72, ge=1)


class TestEnrichmentRequest(BaseModel):
    title: str = Field(..., example="Senior Software Engineer")
    company: str = Field(..., example="Stripe")
    location: Optional[str] = Field("Remote")
    job_description: Optional[str] = Field(None)
    job_url: Optional[str] = Field(None)
    provider: Optional[str] = Field(None)
    model: Optional[str] = Field(None)


class UpdateLeadStatusRequest(BaseModel):
    job_url: str
    status: str = Field(..., example="qualified")  # new, contacted, qualified, rejected, archived


# --- Worker Function ---
def _execute_pipeline_task(req: RunPipelineRequest):
    pipeline_state.reset()
    target_size = req.company_size or "small"
    detected_job_type, effective_search_term = detect_job_type_filter(req.search_term, explicit_job_type=req.job_type)
    
    pipeline_state.add_log(
        f"Starting pipeline for '{req.search_term}' in '{req.location}' (Size Target: {target_size.upper()} [Max 50], Job Type: {str(detected_job_type or 'all').upper()})",
        "info"
    )
    pipeline_state.add_log(f"Target platforms: {', '.join(req.sites)} (Limit: {req.limit})", "info")

    try:
        # Connect to DB
        mongo_manager.connect()
        pipeline_state.add_log("Connected to MongoDB successfully.", "info")

        # Initialize Agent
        agent = LeadEnrichmentAgent(
            provider_name=req.provider or settings.default_llm_provider,
            model_name=req.model,
        )

        orchestrator = LeadGenOrchestrator(
            agent=agent,
            db=mongo_manager,
            min_relevance_score=req.min_score,
        )

        pipeline_state.status = "scraping"
        pipeline_state.add_log(f"📡 [1/2] Scraping job postings from {', '.join(req.sites)} (Job Type: {str(detected_job_type or 'all').upper()}, Location: '{req.location}')...", "info")

        # Scrape jobs
        raw_postings = orchestrator.scraper.scrape(
            search_term=effective_search_term,
            location=req.location,
            results_wanted=req.limit,
            hours_old=req.hours_old,
            sites=req.sites,
            job_type=detected_job_type,
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

        pipeline_state.total_count = len(raw_postings)
        pipeline_state.add_log(
            f"📥 Fetched {len(raw_postings)} job postings across {len(unique_comps)} unique companies.",
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
            mongo_manager.save_raw_job(job)

            # Check duplicate
            if mongo_manager.job_exists(job.job_url):
                pipeline_state.add_log(
                    f"⏭️ SKIPPED (Duplicate): '{job.company}' - already exists in MongoDB database.",
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
                    mongo_manager.upsert_enriched_lead(enriched_lead)
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

                mongo_manager.upsert_enriched_lead(enriched_lead)
                metrics.saved_to_db += 1
                contacts_found = len(enriched_lead.contacts)
                metrics.total_contacts_discovered += contacts_found

                contact_preview = ", ".join([f"{c.name or 'Recruiter'} ({c.email or 'Domain'})" for c in enriched_lead.contacts[:2]])
                pipeline_state.add_log(
                    f"✅ QUALIFIED LEAD SAVED: '{job.company}' ({enriched_lead.company_size or 'Small'}) [{enriched_lead.job_type or 'Contract'}] | Score: {enriched_lead.relevance_score}/100 | Contacts ({contacts_found}): {contact_preview or 'Company Domain'}",
                    "success"
                )

            except Exception as item_err:
                logger.error(f"Error enriching {job.job_url}: {item_err}")
                pipeline_state.add_log(f"⚠️ Error researching {job.company}: {str(item_err)}", "error")

        metrics.end_time = time.time()
        
        # Log final summary
        pipeline_state.add_log("=" * 60, "info")
        pipeline_state.add_log(
            f"📊 SUMMARY: Scraped: {metrics.total_scraped} | Unique Companies: {metrics.unique_companies_count} | Target Size: {target_size.upper()} (Max 50) | Processed: {metrics.processed_by_agent} | Qualified Leads Saved: {metrics.saved_to_db} | Contacts Found: {metrics.total_contacts_discovered}",
            "success"
        )
        pipeline_state.finish(metrics=metrics)

    except Exception as e:
        logger.exception("Pipeline execution failed")
        pipeline_state.finish(error=str(e))


# --- API Routes ---

@router.get("/stats")
def get_stats():
    """Get system statistics, database counts, and configuration status."""
    db_connected = False
    leads_count = 0
    raw_count = 0
    total_contacts = 0
    avg_score = 0.0

    try:
        mongo_manager.connect()
        if mongo_manager.leads_collection is not None and mongo_manager.raw_jobs_collection is not None:
            db_connected = True
            leads_count = mongo_manager.leads_collection.count_documents({})
            raw_count = mongo_manager.raw_jobs_collection.count_documents({})

            # Aggregate total contacts and average score
            pipeline = [
                {
                    "$group": {
                        "_id": None,
                        "avg_score": {"$avg": "$relevance_score"},
                        "total_contacts": {"$sum": {"$size": {"$ifNull": ["$contacts", []]}}}
                    }
                }
            ]
            agg_result = list(mongo_manager.leads_collection.aggregate(pipeline))
            if agg_result:
                avg_score = round(agg_result[0].get("avg_score") or 0.0, 1)
                total_contacts = agg_result[0].get("total_contacts") or 0
    except Exception as e:
        logger.warning(f"Stats check error: {e}")

    return {
        "db_connected": db_connected,
        "database_name": settings.mongodb_db_name,
        "default_provider": settings.default_llm_provider,
        "gemini_configured": bool(settings.google_api_key and settings.google_api_key != "your_google_api_key_here"),
        "nvidia_configured": bool(settings.nvidia_api_key and settings.nvidia_api_key != "your_nvidia_api_key_here"),
        "tavily_configured": bool(settings.tavily_api_key and settings.tavily_api_key != "your_tavily_api_key_here"),
        "leads_count": leads_count,
        "raw_jobs_count": raw_count,
        "total_contacts_discovered": total_contacts,
        "avg_relevance_score": avg_score,
        "is_pipeline_running": pipeline_state.is_running,
    }


@router.get("/leads")
def get_leads(
    search: Optional[str] = Query(None, description="Search term for title or company"),
    site: Optional[str] = Query(None, description="Platform filter (linkedin, naukri, etc.)"),
    status: Optional[str] = Query(None, description="Lead status filter"),
    company_size: Optional[str] = Query(None, description="Company size filter ('small', 'medium', 'large', 'all')"),
    job_type: Optional[str] = Query(None, description="Job type filter ('contract', 'fulltime', 'parttime', 'all')"),
    hours_old: Optional[int] = Query(None, description="Filter leads found within last N hours (e.g. 24, 72, 168, 720)"),
    min_score: int = Query(0, ge=0, le=100, description="Minimum relevance score"),
    has_contacts: Optional[bool] = Query(None, description="Filter for leads with found contacts"),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
):
    """Retrieve filtered, paginated list of enriched leads."""
    try:
        mongo_manager.connect()
        query: Dict[str, Any] = {}

        if min_score > 0:
            query["relevance_score"] = {"$gte": min_score}

        if site and site.lower() != "all":
            query["site"] = site.lower()

        if status and status.lower() != "all":
            query["status"] = status.lower()

        if has_contacts is True:
            query["contacts.0"] = {"$exists": True}

        # Date / Recency Filter
        if hours_old and hours_old > 0:
            time_cutoff = datetime.utcnow() - timedelta(hours=hours_old)
            if "$and" not in query:
                query["$and"] = []
            query["$and"].append({"created_at": {"$gte": time_cutoff}})

        # Strict Company Size Filter (Small = MAX 50 employees: 1-10, 11-50)
        if company_size and company_size.lower() != "all":
            c_size = company_size.lower().strip()
            if c_size == "small":
                size_cond = {
                    "$and": [
                        {
                            "$or": [
                                {"company_size": {"$regex": r"\b(1-10|11-50|1-50|1-20|startup|seed|micro|boutique)\b", "$options": "i"}},
                                {"company_size": None},
                                {"company_size": "Unspecified"},
                                {"company_size": "Unknown"},
                            ]
                        },
                        {
                            "company_size": {"$not": {"$regex": r"\b(51-200|201-500|501-1000|500\+|1000\+|enterprise)\b", "$options": "i"}}
                        }
                    ]
                }
            elif c_size == "medium":
                size_cond = {
                    "company_size": {"$regex": r"\b(51-200|201-500|501-1000|201-1000|200-500|medium|mid)\b", "$options": "i"}
                }
            elif c_size == "large":
                size_cond = {
                    "company_size": {"$regex": r"\b(500\+|1000\+|5000\+|10000\+|enterprise|corporation|corporate|fortune)\b", "$options": "i"}
                }
            else:
                size_cond = None

            if size_cond:
                if "$and" not in query:
                    query["$and"] = []
                query["$and"].append(size_cond)

        # Job Type Filter
        if job_type and job_type.lower() != "all":
            jt = job_type.lower().strip()
            if jt in ("contract", "freelance"):
                jt_cond = {
                    "$or": [
                        {"job_type": {"$regex": "contract|freelance|c2c|corp|gig|part-time|outside ir35", "$options": "i"}},
                        {"title": {"$regex": "contract|freelance|c2c|gig|outside ir35", "$options": "i"}}
                    ]
                }
            else:
                jt_cond = {"job_type": {"$regex": jt, "$options": "i"}}
            if "$and" not in query:
                query["$and"] = []
            query["$and"].append(jt_cond)

        if search:
            regex_search = {"$regex": search, "$options": "i"}
            search_cond = {
                "$or": [
                    {"title": regex_search},
                    {"company": regex_search},
                    {"key_technologies": regex_search},
                    {"contacts.name": regex_search},
                    {"contacts.email": regex_search},
                ]
            }
            if "$and" not in query:
                query["$and"] = []
            query["$and"].append(search_cond)

        if mongo_manager.leads_collection is None:
            return {
                "total": 0,
                "page": page,
                "limit": limit,
                "total_pages": 0,
                "leads": [],
            }

        total_matching = mongo_manager.leads_collection.count_documents(query)
        skip = (page - 1) * limit

        cursor = (
            mongo_manager.leads_collection.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )

        leads = []
        for doc in cursor:
            doc["_id"] = str(doc.get("_id", ""))
            if "created_at" in doc and isinstance(doc["created_at"], datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            if "updated_at" in doc and isinstance(doc["updated_at"], datetime):
                doc["updated_at"] = doc["updated_at"].isoformat()
            leads.append(doc)

        return {
            "total": total_matching,
            "page": page,
            "limit": limit,
            "total_pages": (total_matching + limit - 1) // limit if limit else 1,
            "leads": leads,
        }

    except Exception as e:
        logger.error(f"Error fetching leads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lead")
def get_lead_by_url(url: str = Query(..., description="Job URL of the lead")):
    """Get single enriched lead details."""
    try:
        mongo_manager.connect()
        doc = mongo_manager.leads_collection.find_one({"job_url": url})
        if not doc:
            raise HTTPException(status_code=404, detail="Lead not found")

        doc["_id"] = str(doc.get("_id", ""))
        if "created_at" in doc and isinstance(doc["created_at"], datetime):
            doc["created_at"] = doc["created_at"].isoformat()
        if "updated_at" in doc and isinstance(doc["updated_at"], datetime):
            doc["updated_at"] = doc["updated_at"].isoformat()

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
        mongo_manager.connect()
        res = mongo_manager.leads_collection.update_one(
            {"job_url": req.job_url},
            {"$set": {"status": req.status, "updated_at": datetime.utcnow()}}
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Lead not found")
        return {"success": True, "status": req.status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating lead status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/lead")
def delete_lead(url: str = Query(..., description="Job URL of the lead")):
    """Delete an enriched lead from database."""
    try:
        mongo_manager.connect()
        res = mongo_manager.leads_collection.delete_one({"job_url": url})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Lead not found")
        return {"success": True, "message": "Lead deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting lead: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipeline/run")
def trigger_pipeline(req: RunPipelineRequest, background_tasks: BackgroundTasks):
    """Trigger the scraping and enrichment pipeline asynchronously."""
    if pipeline_state.is_running:
        raise HTTPException(status_code=409, detail="A pipeline task is already currently running.")

    thread = threading.Thread(target=_execute_pipeline_task, args=(req,), daemon=True)
    thread.start()

    return {
        "success": True,
        "message": "Pipeline started in background.",
        "search_term": req.search_term,
        "limit": req.limit,
    }


@router.get("/pipeline/status")
def get_pipeline_status():
    """Get the current live status and logs of the pipeline."""
    return {
        "is_running": pipeline_state.is_running,
        "status": pipeline_state.status,
        "processed_count": pipeline_state.processed_count,
        "total_count": pipeline_state.total_count,
        "current_job_title": pipeline_state.current_job_title,
        "current_company": pipeline_state.current_company,
        "started_at": pipeline_state.started_at,
        "finished_at": pipeline_state.finished_at,
        "logs": pipeline_state.logs,
        "metrics": pipeline_state.metrics,
        "error_message": pipeline_state.error_message,
    }


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

        lead: EnrichedLead = agent.enrich_job(sample_job)

        # Save to DB
        try:
            mongo_manager.connect()
            mongo_manager.upsert_enriched_lead(lead)
        except Exception as db_err:
            logger.warning(f"Could not persist test lead to Mongo: {db_err}")

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
        mongo_manager.connect()
        query: Dict[str, Any] = {}
        if min_score > 0:
            query["relevance_score"] = {"$gte": min_score}
        if site and site.lower() != "all":
            query["site"] = site.lower()
        if status and status.lower() != "all":
            query["status"] = status.lower()

        if hours_old and hours_old > 0:
            time_cutoff = datetime.utcnow() - timedelta(hours=hours_old)
            if "$and" not in query:
                query["$and"] = []
            query["$and"].append({"created_at": {"$gte": time_cutoff}})

        if company_size and company_size.lower() != "all":
            c_size = company_size.lower().strip()
            if c_size == "small":
                size_cond = {
                    "$and": [
                        {
                            "$or": [
                                {"company_size": {"$regex": r"\b(1-10|11-50|1-50|1-20|startup|seed|micro|boutique)\b", "$options": "i"}},
                                {"company_size": None},
                                {"company_size": "Unspecified"},
                                {"company_size": "Unknown"},
                            ]
                        },
                        {
                            "company_size": {"$not": {"$regex": r"\b(51-200|201-500|501-1000|500\+|1000\+|enterprise)\b", "$options": "i"}}
                        }
                    ]
                }
                if "$and" not in query:
                    query["$and"] = []
                query["$and"].append(size_cond)
            elif c_size == "medium":
                query["company_size"] = {"$regex": r"\b(51-200|201-500|501-1000|201-1000|200-500|medium|mid)\b", "$options": "i"}
            elif c_size == "large":
                query["company_size"] = {"$regex": r"\b(500\+|1000\+|5000\+|10000\+|enterprise|corporation|corporate|fortune)\b", "$options": "i"}

        if job_type and job_type.lower() != "all":
            jt = job_type.lower().strip()
            if jt in ("contract", "freelance"):
                jt_cond = {
                    "$or": [
                        {"job_type": {"$regex": "contract|freelance|c2c|corp|gig|part-time|outside ir35", "$options": "i"}},
                        {"title": {"$regex": "contract|freelance|c2c|gig|outside ir35", "$options": "i"}}
                    ]
                }
            else:
                jt_cond = {"job_type": {"$regex": jt, "$options": "i"}}
            if "$and" not in query:
                query["$and"] = []
            query["$and"].append(jt_cond)

        leads = list(mongo_manager.leads_collection.find(query).sort("created_at", -1))

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
