"""Lead Generation and Enrichment Pipeline Orchestrator."""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
import time

from core.logging import logger
from core.company_filter import is_matching_company_size, classify_company_size, detect_job_type_filter
from config.settings import settings
from db.mongo import MongoManager, mongo_manager
from db.models import RawJobPosting, EnrichedLead
from scraper.base import BaseScraper
from scraper.jobspy_scraper import JobSpyScraper
from enrichment.agent import LeadEnrichmentAgent


@dataclass
class PipelineMetrics:
    """Statistics tracking for a pipeline execution run."""
    search_term: str = ""
    location: str = ""
    sites: List[str] = field(default_factory=list)
    target_company_size: str = "small"
    target_job_type: str = "all"
    total_scraped: int = 0
    unique_companies_count: int = 0
    unique_companies: List[str] = field(default_factory=list)
    already_existing: int = 0
    processed_by_agent: int = 0
    saved_to_db: int = 0
    rejected_by_llm: int = 0
    rejected_by_size: int = 0
    total_contacts_discovered: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    @property
    def duration_seconds(self) -> float:
        end = self.end_time or time.time()
        return round(end - self.start_time, 2)


class LeadGenOrchestrator:
    """
    Coordinates the full end-to-end workflow:
    1. Scrapes job listings from LinkedIn & Naukri via JobSpy with job_type filtering.
    2. Persists raw jobs and performs deduplication checks.
    3. Feeds new jobs one-by-one to the LLM thinking agent with target company size (max 50) and job type heuristics.
    4. Executes live Tavily web research to locate company size, recruiters, and contact details.
    5. Filters leads based on qualification, company size, and job type criteria and stores strictly structured leads into MongoDB.
    """

    def __init__(
        self,
        scraper: Optional[BaseScraper] = None,
        agent: Optional[LeadEnrichmentAgent] = None,
        db: Optional[MongoManager] = None,
        min_relevance_score: int = 30,
    ):
        self.scraper = scraper or JobSpyScraper()
        self.agent = agent or LeadEnrichmentAgent()
        self.db = db or mongo_manager
        self.min_relevance_score = min_relevance_score

    def run(
        self,
        search_term: str,
        location: str = "Remote",
        sites: Optional[List[str]] = None,
        company_size: str = "small",
        job_type: Optional[str] = None,
        results_limit: int = 20,
        hours_old: int = 72,
        skip_existing: bool = True,
        save_raw: bool = True,
    ) -> PipelineMetrics:
        """
        Execute the end-to-end lead generation pipeline with granular progress logging.
        """
        target_sites = sites or ["linkedin", "naukri"]
        target_size = company_size or "small"
        
        # Detect or apply explicit job type (e.g. freelance / contract)
        detected_job_type, effective_search_term = detect_job_type_filter(search_term, explicit_job_type=job_type)
        
        metrics = PipelineMetrics(
            search_term=search_term,
            location=location,
            sites=target_sites,
            target_company_size=target_size,
            target_job_type=detected_job_type or "all",
        )

        logger.info("=" * 70)
        logger.info(f"🚀 STARTING LEAD GEN PIPELINE")
        logger.info(f"🎯 Target Query    : '{search_term}'")
        logger.info(f"📍 Location        : '{location}'")
        logger.info(f"🏢 Company Size Req: '{target_size.upper()}' (Max 50 employees)")
        logger.info(f"🤝 Job Type Req    : '{str(detected_job_type or 'all').upper()}'")
        logger.info(f"🌐 Platforms       : {', '.join(target_sites)}")
        logger.info(f"🔢 Target Limit    : {results_limit} postings")
        logger.info(f"⭐ Min Score Req   : {self.min_relevance_score}/100")
        logger.info("=" * 70)

        # 1. Connect DB
        self.db.connect()

        # 2. Scrape jobs
        raw_postings: List[RawJobPosting] = []
        try:
            logger.info(f"📡 [Phase 1/2] Fetching job listings across {', '.join(target_sites)} (Job Type: {detected_job_type or 'All'})...")
            raw_postings = self.scraper.scrape(
                search_term=effective_search_term,
                location=location,
                results_wanted=results_limit,
                hours_old=hours_old,
                sites=target_sites,
                job_type=detected_job_type,
            )
            metrics.total_scraped = len(raw_postings)
            
            # Extract unique companies
            unique_comps = sorted(list({p.company.strip() for p in raw_postings if p.company and p.company.strip()}))
            metrics.unique_companies_count = len(unique_comps)
            metrics.unique_companies = unique_comps

            logger.info("=" * 70)
            logger.info(f"📥 SCRAPE COMPLETE: Fetched {metrics.total_scraped} job postings across {metrics.unique_companies_count} unique companies.")
            if unique_comps:
                sample_companies = ", ".join(unique_comps[:8]) + ("..." if len(unique_comps) > 8 else "")
                logger.info(f"🏢 Companies discovered: {sample_companies}")
            logger.info("=" * 70)

        except Exception as scrape_err:
            logger.error(f"❌ Scraping stage encountered error: {scrape_err}")
            metrics.end_time = time.time()
            return metrics

        if not raw_postings:
            logger.warning("⚠️ No postings found matching the search criteria. Pipeline exiting.")
            metrics.end_time = time.time()
            return metrics

        # 3. Process each job 1 by 1
        logger.info(f"🧠 [Phase 2/2] Starting autonomous AI research & qualification loop (Filter: {target_size.upper()} companies, {str(detected_job_type or 'all').upper()} jobs)...")
        total_jobs = len(raw_postings)

        for idx, job in enumerate(raw_postings, start=1):
            pct = round((idx / total_jobs) * 100)
            logger.info("-" * 65)
            logger.info(f"🔍 [{idx}/{total_jobs} | {pct}%] Processing: '{job.title}' @ '{job.company}' ({job.site.upper()})")

            if save_raw:
                self.db.save_raw_job(job)

            # Deduplication check
            if skip_existing and self.db.job_exists(job.job_url):
                logger.info(f"⏭️  SKIPPED [Duplicate]: '{job.company}' - already exists in MongoDB database.")
                metrics.already_existing += 1
                continue

            # Send to LLM Thinking & Research Agent
            metrics.processed_by_agent += 1
            try:
                logger.info(f"🤖 Agent researching '{job.company}' (Domain, Company Size, Decision Makers, Tech Stack)...")
                enriched_lead: EnrichedLead = self.agent.enrich_job(
                    job,
                    target_company_size=target_size,
                    target_job_type=detected_job_type or "all",
                )

                # Qualification Filtering
                if not enriched_lead.is_valid_lead:
                    logger.warning(
                        f"❌ REJECTED [Invalid Lead]: '{job.company}' - {enriched_lead.lead_summary}"
                    )
                    metrics.rejected_by_llm += 1
                    self.db.upsert_enriched_lead(enriched_lead)
                    continue

                if enriched_lead.relevance_score < self.min_relevance_score:
                    logger.warning(
                        f"❌ REJECTED [Low Score]: '{job.company}' scored {enriched_lead.relevance_score}/100 (Below required {self.min_relevance_score})"
                    )
                    metrics.rejected_by_llm += 1
                    continue

                # Company Size Validation (Strictly max 50 for small)
                if target_size != "all" and not is_matching_company_size(enriched_lead.company_size, target_filter=target_size):
                    logger.warning(
                        f"❌ REJECTED [Company Size Mismatch]: '{job.company}' ({enriched_lead.company_size or 'Unknown'}) does not match requested '{target_size}' size filter."
                    )
                    metrics.rejected_by_size += 1
                    metrics.rejected_by_llm += 1
                    continue

                # Save qualified enriched lead to MongoDB
                self.db.upsert_enriched_lead(enriched_lead)
                metrics.saved_to_db += 1
                contacts_count = len(enriched_lead.contacts)
                metrics.total_contacts_discovered += contacts_count

                contact_details = ", ".join([f"{c.name or 'Recruiter'} ({c.email or 'No email'})" for c in enriched_lead.contacts[:2]])
                logger.info(
                    f"✅ QUALIFIED LEAD SAVED: '{job.company}' ({enriched_lead.company_size or 'Small'}) [{enriched_lead.job_type or 'Contract'}] | Score: {enriched_lead.relevance_score}/100 | Contacts ({contacts_count}): {contact_details or 'Company Domain'}"
                )

            except Exception as item_err:
                logger.error(f"⚠️ Error during enrichment of job {job.job_url}: {item_err}")
                continue

        metrics.end_time = time.time()

        # Print Final Summary Banner
        logger.info("=" * 70)
        logger.info("📊 PIPELINE RUN FINAL SUMMARY")
        logger.info(f"⏱️  Duration            : {metrics.duration_seconds} seconds")
        logger.info(f"📥 Total Jobs Scraped  : {metrics.total_scraped}")
        logger.info(f"🏢 Unique Companies    : {metrics.unique_companies_count}")
        logger.info(f"🎯 Target Company Size : {target_size.upper()}")
        logger.info(f"🤝 Target Job Type     : {str(detected_job_type or 'all').upper()}")
        logger.info(f"⏭️  Already In DB       : {metrics.already_existing} (Skipped)")
        logger.info(f"🧠 Processed by AI     : {metrics.processed_by_agent}")
        logger.info(f"✅ Qualified Leads     : {metrics.saved_to_db} ({(metrics.saved_to_db/max(1, metrics.processed_by_agent)*100):.1f}% yield)")
        logger.info(f"❌ Rejected / Non-match: {metrics.rejected_by_llm} (Size Mismatch: {metrics.rejected_by_size})")
        logger.info(f"👥 Verified Contacts   : {metrics.total_contacts_discovered}")
        logger.info("=" * 70)

        return metrics
