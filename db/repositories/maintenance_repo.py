"""Maintenance Repository for SQLite data clearing, import, export, and backfills."""

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union
import sqlite3

from core.exceptions import DatabaseException
from core.logging import logger


class MaintenanceRepository:
    def __init__(
        self,
        get_connection: Callable[[], sqlite3.Connection],
        format_lead_row: Callable[[sqlite3.Row], Dict[str, Any]],
    ):
        self.get_connection = get_connection
        self.format_lead_row = format_lead_row

    def clear_database(self) -> Dict[str, int]:
        """Clear enriched_leads and raw_jobs tables while preserving indexes & schema."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM enriched_leads")
            leads_deleted = cur.rowcount

            cur.execute("DELETE FROM raw_jobs")
            raw_jobs_deleted = cur.rowcount

            conn.commit()
            logger.info(f"SQLite database cleared: {leads_deleted} leads and {raw_jobs_deleted} raw jobs deleted.")
            return {
                "leads_deleted": leads_deleted,
                "raw_jobs_deleted": raw_jobs_deleted,
                "total_deleted": leads_deleted + raw_jobs_deleted,
            }
        except Exception as e:
            logger.error(f"Error clearing SQLite database: {e}")
            raise DatabaseException(f"Failed to clear SQLite database: {e}") from e
        finally:
            conn.close()

    def backfill_dates(self) -> int:
        """Run SQL query to backfill date_posted and scraped_at on enriched_leads from raw_jobs/job_leads."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE enriched_leads
                SET 
                    date_posted = COALESCE(
                        date_posted, 
                        (SELECT rj.date_posted FROM raw_jobs rj WHERE rj.job_url = enriched_leads.job_url),
                        (SELECT jl.date_posted FROM job_leads jl WHERE jl.job_url = enriched_leads.job_url)
                    ),
                    scraped_at = COALESCE(
                        scraped_at,
                        (SELECT rj.scraped_at FROM raw_jobs rj WHERE rj.job_url = enriched_leads.job_url),
                        (SELECT jl.created_at FROM job_leads jl WHERE jl.job_url = enriched_leads.job_url),
                        created_at
                    )
                WHERE date_posted IS NULL OR scraped_at IS NULL
            """)
            conn.commit()
            return cur.rowcount
        except Exception as e:
            logger.error(f"Error during backfill_dates: {e}")
            raise DatabaseException(f"Failed to backfill dates: {e}") from e
        finally:
            conn.close()

    def export_all_data(self) -> Dict[str, Any]:
        """Export all enriched leads and raw jobs as a JSON-serializable dictionary."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    enriched_leads.*,
                    COALESCE(enriched_leads.date_posted, rj.date_posted, jl.date_posted) AS resolved_date_posted,
                    COALESCE(enriched_leads.scraped_at, rj.scraped_at, jl.created_at, enriched_leads.created_at) AS resolved_scraped_at
                FROM enriched_leads 
                LEFT JOIN raw_jobs rj ON enriched_leads.job_url = rj.job_url
                LEFT JOIN job_leads jl ON enriched_leads.job_url = jl.job_url
                ORDER BY enriched_leads.created_at DESC
            """)
            leads = [self.format_lead_row(r) for r in cur.fetchall()]

            cur.execute("SELECT * FROM raw_jobs ORDER BY scraped_at DESC")
            raw_jobs = []
            for r in cur.fetchall():
                d = dict(r)
                if isinstance(d.get("raw_metadata"), str):
                    try:
                        d["raw_metadata"] = json.loads(d["raw_metadata"])
                    except Exception:
                        d["raw_metadata"] = {}
                raw_jobs.append(d)

            return {
                "exported_at": datetime.utcnow().isoformat(),
                "leads_count": len(leads),
                "raw_jobs_count": len(raw_jobs),
                "leads": leads,
                "raw_jobs": raw_jobs,
            }
        finally:
            conn.close()

    def import_and_backfill(self, payload: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
        """Import leads and raw_jobs from JSON payload (if provided) and run date backfill."""
        imported_leads_count = 0
        imported_jobs_count = 0

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            now_iso = datetime.utcnow().isoformat()

            if payload:
                leads_data = []
                jobs_data = []

                if isinstance(payload, list):
                    leads_data = payload
                elif isinstance(payload, dict):
                    leads_data = payload.get("leads") or []
                    jobs_data = payload.get("raw_jobs") or []

                # Import raw jobs
                for j in jobs_data:
                    if not isinstance(j, dict) or not j.get("job_url"):
                        continue
                    try:
                        raw_meta = json.dumps(j.get("raw_metadata") or {}, default=str)
                        cur.execute("""
                            INSERT INTO raw_jobs (
                                raw_id, job_url, title, company, location, site, description,
                                job_type, salary_min, salary_max, salary_currency, date_posted,
                                scraped_at, raw_metadata
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(job_url) DO UPDATE SET
                                raw_id=excluded.raw_id,
                                title=excluded.title,
                                company=excluded.company,
                                location=excluded.location,
                                site=excluded.site,
                                description=excluded.description,
                                job_type=excluded.job_type,
                                salary_min=excluded.salary_min,
                                salary_max=excluded.salary_max,
                                salary_currency=excluded.salary_currency,
                                date_posted=COALESCE(excluded.date_posted, raw_jobs.date_posted),
                                scraped_at=COALESCE(excluded.scraped_at, raw_jobs.scraped_at),
                                raw_metadata=excluded.raw_metadata
                        """, (
                            j.get("raw_id") or j.get("id"),
                            j.get("job_url"),
                            j.get("title") or "Unknown Title",
                            j.get("company") or "Unknown Company",
                            j.get("location"),
                            j.get("site"),
                            j.get("description"),
                            j.get("job_type"),
                            j.get("salary_min"),
                            j.get("salary_max"),
                            j.get("salary_currency"),
                            str(j.get("date_posted")) if j.get("date_posted") else None,
                            str(j.get("scraped_at")) if j.get("scraped_at") else now_iso,
                            raw_meta,
                        ))
                        imported_jobs_count += 1
                    except Exception as je:
                        logger.warning(f"Skipping job import error for {j.get('job_url')}: {je}")

                # Import enriched leads
                for l in leads_data:
                    if not isinstance(l, dict) or not l.get("job_url"):
                        continue
                    try:
                        contacts = json.dumps(l.get("contacts") or [], default=str)
                        tech = json.dumps(l.get("key_technologies") or [], default=str)
                        queries = json.dumps(l.get("search_queries_used") or [], default=str)

                        cur.execute("""
                            INSERT INTO enriched_leads (
                                job_url, title, company, site, location, job_type, job_description,
                                is_valid_lead, relevance_score, company_domain, company_summary,
                                company_size, contacts, key_technologies, hiring_urgency,
                                lead_summary, agent_thinking_process, search_queries_used,
                                status, date_posted, scraped_at, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(job_url) DO UPDATE SET
                                title=excluded.title,
                                company=excluded.company,
                                site=excluded.site,
                                location=excluded.location,
                                job_type=excluded.job_type,
                                job_description=excluded.job_description,
                                is_valid_lead=excluded.is_valid_lead,
                                relevance_score=excluded.relevance_score,
                                company_domain=excluded.company_domain,
                                company_summary=excluded.company_summary,
                                company_size=excluded.company_size,
                                contacts=excluded.contacts,
                                key_technologies=excluded.key_technologies,
                                hiring_urgency=excluded.hiring_urgency,
                                lead_summary=excluded.lead_summary,
                                agent_thinking_process=excluded.agent_thinking_process,
                                search_queries_used=excluded.search_queries_used,
                                status=excluded.status,
                                date_posted=COALESCE(excluded.date_posted, enriched_leads.date_posted),
                                scraped_at=COALESCE(excluded.scraped_at, enriched_leads.scraped_at),
                                updated_at=excluded.updated_at
                        """, (
                            l.get("job_url"),
                            l.get("title") or "Unknown Title",
                            l.get("company") or "Unknown Company",
                            l.get("site"),
                            l.get("location"),
                            l.get("job_type"),
                            l.get("job_description"),
                            1 if l.get("is_valid_lead", True) else 0,
                            l.get("relevance_score", 70),
                            l.get("company_domain"),
                            l.get("company_summary"),
                            l.get("company_size"),
                            contacts,
                            tech,
                            l.get("hiring_urgency") or "Medium",
                            l.get("lead_summary"),
                            l.get("agent_thinking_process"),
                            queries,
                            l.get("status") or "new",
                            str(l.get("date_posted")) if l.get("date_posted") else None,
                            str(l.get("scraped_at")) if l.get("scraped_at") else (l.get("created_at") or now_iso),
                            l.get("created_at") or now_iso,
                            now_iso,
                        ))
                        imported_leads_count += 1
                    except Exception as le:
                        logger.warning(f"Skipping lead import error for {l.get('job_url')}: {le}")

                conn.commit()

            # Execute relational date backfill
            cur.execute("""
                UPDATE enriched_leads
                SET 
                    date_posted = COALESCE(
                        date_posted, 
                        (SELECT rj.date_posted FROM raw_jobs rj WHERE rj.job_url = enriched_leads.job_url),
                        (SELECT jl.date_posted FROM job_leads jl WHERE jl.job_url = enriched_leads.job_url)
                    ),
                    scraped_at = COALESCE(
                        scraped_at,
                        (SELECT rj.scraped_at FROM raw_jobs rj WHERE rj.job_url = enriched_leads.job_url),
                        (SELECT jl.created_at FROM job_leads jl WHERE jl.job_url = enriched_leads.job_url),
                        created_at
                    )
                WHERE date_posted IS NULL OR scraped_at IS NULL
            """)
            conn.commit()
            backfilled_count = cur.rowcount

            cur.execute("SELECT COUNT(*) FROM enriched_leads")
            total_leads = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM raw_jobs")
            total_jobs = cur.fetchone()[0]

            return {
                "success": True,
                "imported_leads": imported_leads_count,
                "imported_raw_jobs": imported_jobs_count,
                "backfilled_rows": backfilled_count,
                "total_leads_in_db": total_leads,
                "total_raw_jobs_in_db": total_jobs,
                "message": f"Backfill completed: {imported_leads_count} leads imported, {imported_jobs_count} raw jobs imported, {backfilled_count} rows backfilled.",
            }
        except Exception as e:
            logger.error(f"Error in import_and_backfill: {e}")
            raise DatabaseException(f"Failed to import and backfill data: {e}") from e
        finally:
            conn.close()
