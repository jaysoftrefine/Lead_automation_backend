"""Background Pipeline Worker & Execution Logic."""

import time
from datetime import datetime

from config.settings import settings
from core.company_filter import detect_job_type_filter, is_matching_company_size
from core.logging import logger
from db.sqlite import sqlite_manager
from enrichment.agent import LeadEnrichmentAgent
from pipeline.orchestrator import LeadGenOrchestrator, PipelineMetrics
from schemas import RunPipelineRequest
from api.pipeline.state import pipeline_state


def execute_pipeline_task(req: RunPipelineRequest):
    sites = req.sites or req.platforms or ["linkedin"]
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
        sqlite_manager.connect()
        pipeline_state.add_log("Connected to centralized SQLite database successfully.", "info")

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

        pipeline_state.total_count = target_goal
        pipeline_state.processed_count = 0

        metrics = PipelineMetrics(
            search_term=req.search_term,
            location=req.location,
            sites=req.sites,
            target_company_size=target_size,
            target_job_type=detected_job_type or "all",
            total_scraped=0,
            unique_companies_count=0,
            unique_companies=[],
        )

        seen_job_urls = set()
        offset = 0
        round_idx = 1
        MAX_ROUNDS = 8
        consecutive_empty_batches = 0

        while metrics.saved_to_db < target_goal and round_idx <= MAX_ROUNDS:
            if pipeline_state._stop_requested:
                pipeline_state.add_log("🛑 Pipeline run manually stopped by user.", "warning")
                break

            remaining_needed = target_goal - metrics.saved_to_db
            raw_to_fetch = min(max(remaining_needed * 3, 20), 40)

            round_tag = f"Round {round_idx}" if round_idx > 1 else "Initial Round"
            pipeline_state.status = "scraping"
            pipeline_state.add_log(
                f"📡 [{round_tag}] Scraping candidate jobs from {', '.join(sites)} in '{req.location}' "
                f"(Remote: {req.is_remote}, Offset: {offset}, Requesting: {raw_to_fetch})...",
                "info"
            )

            try:
                raw_postings = orchestrator.scraper.scrape(
                    search_term=effective_search_term,
                    location=req.location,
                    results_wanted=raw_to_fetch,
                    hours_old=req.hours_old,
                    sites=sites,
                    job_type=detected_job_type,
                    is_remote=req.is_remote,
                    offset=offset,
                )
            except Exception as scrape_err:
                logger.error(f"Scraper round {round_idx} failed: {scrape_err}")
                pipeline_state.add_log(f"⚠️ Scraping error during {round_tag}: {scrape_err}", "warning")
                raw_postings = []

            # Deduplicate against already-seen URLs in this run
            new_postings = []
            for p in raw_postings:
                if p.job_url:
                    if p.job_url not in seen_job_urls:
                        seen_job_urls.add(p.job_url)
                        new_postings.append(p)
                else:
                    new_postings.append(p)

            if not new_postings:
                consecutive_empty_batches += 1
                if consecutive_empty_batches >= 2:
                    pipeline_state.add_log(
                        f"⚠️ No additional job postings found matching search parameters across {', '.join(sites)}.",
                        "warning"
                    )
                    break
                else:
                    offset += raw_to_fetch
                    round_idx += 1
                    continue

            consecutive_empty_batches = 0
            metrics.total_scraped += len(new_postings)
            batch_companies = sorted(list({p.company.strip() for p in new_postings if p.company and p.company.strip()}))
            for c in batch_companies:
                if c not in metrics.unique_companies:
                    metrics.unique_companies.append(c)
            metrics.unique_companies_count = len(metrics.unique_companies)

            pipeline_state.add_log(
                f"📥 [{round_tag}] Fetched {len(new_postings)} candidate postings across {len(batch_companies)} companies. "
                f"(Current Progress: {metrics.saved_to_db}/{target_goal} qualified leads)",
                "success"
            )
            if batch_companies and round_idx == 1:
                sample_preview = ", ".join(batch_companies[:6]) + ("..." if len(batch_companies) > 6 else "")
                pipeline_state.add_log(f"🏢 Discovered companies: {sample_preview}", "info")

            pipeline_state.status = "enriching"
            pipeline_state.add_log(
                f"🧠 [{round_tag}] Researching {len(new_postings)} candidates (Filtering for {target_size.upper()} [Max 50] & {str(detected_job_type or 'all').upper()})...",
                "info"
            )

            for idx, job in enumerate(new_postings, start=1):
                if pipeline_state._stop_requested:
                    pipeline_state.add_log("🛑 Pipeline run manually stopped by user.", "warning")
                    break

                pipeline_state.current_job_title = job.title
                pipeline_state.current_company = job.company

                overall_pct = min(round((metrics.saved_to_db / target_goal) * 100), 100)
                pipeline_state.add_log(
                    f"🔍 [Batch {idx}/{len(new_postings)} | Goal: {metrics.saved_to_db}/{target_goal} ({overall_pct}%)] "
                    f"Processing: '{job.title}' @ '{job.company}' ({job.site.upper()})",
                    "info"
                )

                sqlite_manager.save_raw_job(job)

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
                        f"✅ [{metrics.saved_to_db}/{target_goal} Target Qualified Leads] SAVED: '{job.company}' "
                        f"({enriched_lead.company_size or 'Small'}) [{enriched_lead.job_type or 'Contract'}] | "
                        f"Score: {enriched_lead.relevance_score}/100 | Contacts ({contacts_found}): {contact_preview or 'Company Domain'}",
                        "success"
                    )

                    if metrics.saved_to_db >= target_goal:
                        pipeline_state.add_log(
                            f"🎉 TARGET REACHED: Successfully discovered and saved all {target_goal} qualified leads matching all your filter criteria!",
                            "success"
                        )
                        break

                except Exception as item_err:
                    logger.error(f"Error enriching {job.job_url}: {item_err}")
                    pipeline_state.add_log(f"⚠️ Error researching {job.company}: {str(item_err)}", "error")

            # Check termination after this batch
            if pipeline_state._stop_requested or metrics.saved_to_db >= target_goal:
                break

            offset += len(raw_postings)
            round_idx += 1
            if round_idx > MAX_ROUNDS:
                pipeline_state.add_log(
                    f"ℹ️ Reached maximum search rounds ({MAX_ROUNDS}). Finished with {metrics.saved_to_db}/{target_goal} qualified leads.",
                    "info"
                )
                break

            remaining = target_goal - metrics.saved_to_db
            pipeline_state.add_log(
                f"🔄 [Goal: {target_goal} | Qualified So Far: {metrics.saved_to_db}] Need {remaining} more qualified leads. "
                f"Auto-fetching next candidate batch (Round {round_idx})...",
                "info"
            )

        metrics.end_time = time.time()
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
