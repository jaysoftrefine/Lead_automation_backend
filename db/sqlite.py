"""
SQLite Database Manager for Autonomous Lead Generation Engine.
Centralized storage for enriched leads, raw jobs, and outreach recipients.
"""

import json
import sqlite3
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from core.exceptions import DatabaseException
from core.logging import logger
from db.models import EnrichedLead, RawJobPosting


def _resolve_db_path(configured_path: Optional[str] = None) -> Path:
    """Resolve SQLite database file path relative to project root if not absolute."""
    path_str = configured_path or getattr(settings, "sqlite_db_path", "data/eu_startups.db")
    p = Path(path_str)
    if not p.is_absolute():
        root_dir = Path(__file__).resolve().parent.parent
        p = root_dir / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class SqliteManager:
    """Centralized SQLite connection and repository manager."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path_raw = db_path
        self._db_path = _resolve_db_path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def db_path(self) -> str:
        return str(self._db_path)

    def get_connection(self) -> sqlite3.Connection:
        """Create or return a thread-safe connection to SQLite with WAL mode."""
        conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def connect(self) -> None:
        """Establish connection and ensure tables and indexes exist."""
        try:
            self._ensure_tables()
            logger.info(f"SQLite centralized database connected at: {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to connect / initialize SQLite database: {e}")
            raise DatabaseException(f"SQLite initialization failed: {e}") from e

    def _ensure_tables(self) -> None:
        """Create enriched_leads, raw_jobs, and job_leads tables if they do not exist."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()

            # 1. Enriched Leads Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS enriched_leads (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_url                 TEXT UNIQUE NOT NULL,
                    title                   TEXT NOT NULL,
                    company                 TEXT NOT NULL,
                    site                    TEXT,
                    location                TEXT,
                    job_type                TEXT,
                    job_description         TEXT,
                    is_valid_lead           INTEGER DEFAULT 1,
                    relevance_score         INTEGER DEFAULT 50,
                    company_domain          TEXT,
                    company_summary         TEXT,
                    company_size            TEXT,
                    contacts                TEXT DEFAULT '[]',
                    key_technologies        TEXT DEFAULT '[]',
                    hiring_urgency          TEXT,
                    lead_summary            TEXT,
                    agent_thinking_process  TEXT,
                    search_queries_used     TEXT DEFAULT '[]',
                    status                  TEXT DEFAULT 'new',
                    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Raw Scraped Jobs Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS raw_jobs (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_id                  TEXT,
                    job_url                 TEXT UNIQUE NOT NULL,
                    title                   TEXT NOT NULL,
                    company                 TEXT NOT NULL,
                    location                TEXT,
                    site                    TEXT,
                    description             TEXT,
                    job_type                TEXT,
                    salary_min              REAL,
                    salary_max              REAL,
                    salary_currency         TEXT,
                    date_posted             TEXT,
                    scraped_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    raw_metadata            TEXT DEFAULT '{}'
                )
            """)

            # 3. Job Leads Table (Historical / Legacy Scraped LinkedIn Leads)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS job_leads (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_url                 TEXT UNIQUE NOT NULL,
                    title                   TEXT,
                    company                 TEXT,
                    location                TEXT,
                    site                    TEXT,
                    company_website         TEXT,
                    company_url             TEXT,
                    company_phone           TEXT,
                    company_email           TEXT,
                    company_industry        TEXT,
                    company_num_employees   TEXT,
                    emails                  TEXT,
                    phones                  TEXT,
                    recruiter_name          TEXT,
                    ai_notes                TEXT,
                    is_remote               INTEGER DEFAULT 0,
                    enriched_by_ai          INTEGER DEFAULT 0,
                    date_posted             TEXT,
                    description             TEXT,
                    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Indexes for high performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_job_url ON enriched_leads(job_url)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_company ON enriched_leads(company)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_site ON enriched_leads(site)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_score ON enriched_leads(relevance_score)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_status ON enriched_leads(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_created ON enriched_leads(created_at)")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_jobs_job_url ON raw_jobs(job_url)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_jobs_scraped ON raw_jobs(scraped_at)")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_job_leads_job_url ON job_leads(job_url)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_job_leads_company ON job_leads(company)")

            conn.commit()
        finally:
            conn.close()

    def job_exists(self, job_url: str) -> bool:
        """Check if a lead with job_url has already been enriched and saved."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 1 FROM enriched_leads 
                WHERE job_url = ? AND relevance_score > 0 AND status != 'failed'
                LIMIT 1
            """, (job_url,))
            row = cur.fetchone()
            if row:
                return True

            # Also check job_leads table
            cur.execute("SELECT 1 FROM job_leads WHERE job_url = ? LIMIT 1", (job_url,))
            return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"Error checking job existence in SQLite for {job_url}: {e}")
            return False
        finally:
            conn.close()

    def save_raw_job(self, job: RawJobPosting) -> bool:
        """Save raw scraped job posting with upsert."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            raw_meta_str = json.dumps(job.raw_metadata or {})
            scraped_at_str = job.scraped_at.isoformat() if isinstance(job.scraped_at, datetime) else str(job.scraped_at or datetime.utcnow().isoformat())

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
                    date_posted=excluded.date_posted,
                    raw_metadata=excluded.raw_metadata
            """, (
                job.id,
                job.job_url,
                job.title,
                job.company,
                job.location,
                job.site,
                job.description,
                job.job_type,
                job.salary_min,
                job.salary_max,
                job.salary_currency,
                job.date_posted,
                scraped_at_str,
                raw_meta_str,
            ))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving raw job {job.job_url} to SQLite: {e}")
            return False
        finally:
            conn.close()

    def upsert_enriched_lead(self, lead: EnrichedLead) -> bool:
        """Save or update enriched lead in SQLite."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            contacts_json = json.dumps([c.model_dump() for c in lead.contacts])
            tech_json = json.dumps(lead.key_technologies or [])
            queries_json = json.dumps(lead.search_queries_used or [])
            now_iso = datetime.utcnow().isoformat()
            created_at_iso = lead.created_at.isoformat() if isinstance(lead.created_at, datetime) else now_iso

            cur.execute("""
                INSERT INTO enriched_leads (
                    job_url, title, company, site, location, job_type, job_description,
                    is_valid_lead, relevance_score, company_domain, company_summary,
                    company_size, contacts, key_technologies, hiring_urgency,
                    lead_summary, agent_thinking_process, search_queries_used,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at=?
            """, (
                lead.job_url,
                lead.title,
                lead.company,
                lead.site,
                lead.location,
                lead.job_type,
                lead.job_description,
                1 if lead.is_valid_lead else 0,
                lead.relevance_score,
                lead.company_domain,
                lead.company_summary,
                lead.company_size,
                contacts_json,
                tech_json,
                lead.hiring_urgency,
                lead.lead_summary,
                lead.agent_thinking_process,
                queries_json,
                lead.status,
                created_at_iso,
                now_iso,
                now_iso,
            ))
            conn.commit()
            logger.info(f"Saved enriched lead in SQLite: '{lead.title}' at {lead.company}")
            return True
        except Exception as e:
            logger.error(f"Error upserting lead {lead.job_url} to SQLite: {e}")
            raise DatabaseException(f"Failed to upsert lead to SQLite: {e}") from e
        finally:
            conn.close()

    def get_leads(
        self,
        search: Optional[str] = None,
        site: Optional[str] = None,
        status: Optional[str] = None,
        company_size: Optional[str] = None,
        job_type: Optional[str] = None,
        hours_old: Optional[int] = None,
        min_score: int = 0,
        has_contacts: Optional[bool] = None,
        limit: int = 50,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Retrieve filtered, paginated list of enriched leads from SQLite."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            conditions = []
            params = []

            if min_score > 0:
                conditions.append("relevance_score >= ?")
                params.append(min_score)

            if site and site.lower() != "all":
                conditions.append("LOWER(site) = ?")
                params.append(site.lower().strip())

            if status and status.lower() != "all":
                conditions.append("LOWER(status) = ?")
                params.append(status.lower().strip())

            if has_contacts is True:
                conditions.append("(contacts IS NOT NULL AND contacts != '[]' AND contacts != '')")

            if hours_old and hours_old > 0:
                cutoff = (datetime.utcnow() - timedelta(hours=hours_old)).isoformat()
                conditions.append("created_at >= ?")
                params.append(cutoff)

            if company_size and company_size.lower() != "all":
                c_size = company_size.lower().strip()
                if c_size == "small":
                    conditions.append("""(
                        (
                            company_size LIKE '%1-10%' OR
                            company_size LIKE '%11-50%' OR
                            company_size LIKE '%1-50%' OR
                            company_size LIKE '%1-20%' OR
                            LOWER(company_size) LIKE '%startup%' OR
                            LOWER(company_size) LIKE '%seed%' OR
                            LOWER(company_size) LIKE '%micro%' OR
                            LOWER(company_size) LIKE '%boutique%' OR
                            company_size IS NULL OR
                            company_size = '' OR
                            company_size = 'Unspecified' OR
                            company_size = 'Unknown'
                        ) AND NOT (
                            company_size LIKE '%51-200%' OR
                            company_size LIKE '%201-500%' OR
                            company_size LIKE '%501-1000%' OR
                            company_size LIKE '%500+%' OR
                            company_size LIKE '%1000+%' OR
                            LOWER(company_size) LIKE '%enterprise%'
                        )
                    )""")
                elif c_size == "medium":
                    conditions.append("""(
                        company_size LIKE '%51-200%' OR
                        company_size LIKE '%201-500%' OR
                        company_size LIKE '%501-1000%' OR
                        company_size LIKE '%201-1000%' OR
                        company_size LIKE '%200-500%' OR
                        LOWER(company_size) LIKE '%medium%' OR
                        LOWER(company_size) LIKE '%mid%'
                    )""")
                elif c_size == "large":
                    conditions.append("""(
                        company_size LIKE '%500+%' OR
                        company_size LIKE '%1000+%' OR
                        company_size LIKE '%5000+%' OR
                        company_size LIKE '%10000+%' OR
                        LOWER(company_size) LIKE '%enterprise%' OR
                        LOWER(company_size) LIKE '%corporation%' OR
                        LOWER(company_size) LIKE '%corporate%' OR
                        LOWER(company_size) LIKE '%fortune%'
                    )""")

            if job_type and job_type.lower() != "all":
                jt = job_type.lower().strip()
                if jt in ("contract", "freelance"):
                    conditions.append("""(
                        LOWER(COALESCE(job_type, '')) LIKE '%contract%' OR
                        LOWER(COALESCE(job_type, '')) LIKE '%freelance%' OR
                        LOWER(COALESCE(job_type, '')) LIKE '%c2c%' OR
                        LOWER(COALESCE(job_type, '')) LIKE '%corp%' OR
                        LOWER(COALESCE(job_type, '')) LIKE '%gig%' OR
                        LOWER(COALESCE(job_type, '')) LIKE '%part-time%' OR
                        LOWER(COALESCE(job_type, '')) LIKE '%outside ir35%' OR
                        LOWER(title) LIKE '%contract%' OR
                        LOWER(title) LIKE '%freelance%' OR
                        LOWER(title) LIKE '%c2c%' OR
                        LOWER(title) LIKE '%gig%' OR
                        LOWER(title) LIKE '%outside ir35%'
                    )""")
                else:
                    conditions.append("LOWER(COALESCE(job_type, '')) LIKE ?")
                    params.append(f"%{jt}%")

            if search and search.strip():
                term = f"%{search.strip().lower()}%"
                conditions.append("""(
                    LOWER(title) LIKE ? OR
                    LOWER(company) LIKE ? OR
                    LOWER(COALESCE(key_technologies, '')) LIKE ? OR
                    LOWER(COALESCE(contacts, '')) LIKE ?
                )""")
                params.extend([term, term, term, term])

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            # Count total matching
            count_sql = f"SELECT COUNT(*) FROM enriched_leads {where_clause}"
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            # Fetch page items
            offset = (page - 1) * limit
            data_sql = f"""
                SELECT * FROM enriched_leads 
                {where_clause}
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """
            cur.execute(data_sql, params + [limit, offset])
            rows = cur.fetchall()

            leads = []
            for r in rows:
                leads.append(self._format_lead_row(r))

            return {
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit if limit else 1,
                "leads": leads,
            }
        except Exception as e:
            logger.error(f"Error fetching leads from SQLite: {e}")
            raise DatabaseException(f"Failed to query leads: {e}") from e
        finally:
            conn.close()

    def get_lead_by_url(self, job_url: str) -> Optional[Dict[str, Any]]:
        """Retrieve single enriched lead by job_url."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM enriched_leads WHERE job_url = ? LIMIT 1", (job_url,))
            row = cur.fetchone()
            if not row:
                return None
            return self._format_lead_row(row)
        finally:
            conn.close()

    def update_lead_status(self, job_url: str, status: str) -> bool:
        """Update lead status in SQLite."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            now_iso = datetime.utcnow().isoformat()
            cur.execute("""
                UPDATE enriched_leads 
                SET status = ?, updated_at = ? 
                WHERE job_url = ?
            """, (status, now_iso, job_url))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_lead(self, job_url: str) -> bool:
        """Delete lead from enriched_leads table."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM enriched_leads WHERE job_url = ?", (job_url,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Calculate dashboard statistics from SQLite tables."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()

            # Check table existence
            cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='enriched_leads'")
            if cur.fetchone()[0] == 0:
                self._ensure_tables()

            cur.execute("SELECT COUNT(*) FROM enriched_leads")
            leads_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM raw_jobs")
            raw_count = cur.fetchone()[0]

            # Average score and total contacts
            cur.execute("SELECT AVG(relevance_score) FROM enriched_leads WHERE relevance_score IS NOT NULL")
            avg_score_row = cur.fetchone()
            avg_score = round(avg_score_row[0] or 0.0, 1) if avg_score_row and avg_score_row[0] is not None else 0.0

            # Count total contacts across leads
            cur.execute("SELECT contacts FROM enriched_leads WHERE contacts IS NOT NULL AND contacts != '[]'")
            all_contacts_raw = cur.fetchall()
            total_contacts = 0
            for r in all_contacts_raw:
                try:
                    c_list = json.loads(r[0])
                    if isinstance(c_list, list):
                        total_contacts += len(c_list)
                except Exception:
                    pass

            return {
                "db_connected": True,
                "database_name": f"SQLite ({Path(self.db_path).name})",
                "leads_count": leads_count,
                "raw_jobs_count": raw_count,
                "total_contacts_discovered": total_contacts,
                "avg_relevance_score": avg_score,
            }
        except Exception as e:
            logger.error(f"Error calculating SQLite stats: {e}")
            return {
                "db_connected": False,
                "database_name": f"SQLite ({Path(self.db_path).name})",
                "leads_count": 0,
                "raw_jobs_count": 0,
                "total_contacts_discovered": 0,
                "avg_relevance_score": 0.0,
            }
        finally:
            conn.close()

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

    def get_recipients(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Fetch email campaign recipients from enriched_leads and job_leads in SQLite."""
        conn = self.get_connection()
        filters = filters or {}
        country_filter = (filters.get("country") or "").strip().lower()
        recipients = []
        seen_emails = set()

        try:
            cur = conn.cursor()
            # 1. From enriched_leads
            cur.execute("""
                SELECT company, company_domain, location, contacts 
                FROM enriched_leads 
                WHERE contacts IS NOT NULL AND contacts != '[]'
            """)
            for row in cur.fetchall():
                loc = row["location"] or ""
                if country_filter and country_filter not in loc.lower():
                    continue

                try:
                    contacts = json.loads(row["contacts"])
                except Exception:
                    contacts = []

                for c in contacts:
                    email = (c.get("email") or "").strip()
                    if email and email.lower() not in seen_emails:
                        seen_emails.add(email.lower())
                        domain = row["company_domain"] or ""
                        recipients.append({
                            "person_name":  c.get("name") or "Leadership / Contact",
                            "role":         c.get("role") or "Professional",
                            "email":        email,
                            "company_name": row["company"] or "",
                            "website":      f"https://{domain}" if domain and not domain.startswith("http") else domain,
                            "city":         "",
                            "country":      loc,
                            "category":     "Job Lead",
                            "source":       "job_leads",
                        })

            # 2. From job_leads (historical / legacy scraped data)
            cur.execute("""
                SELECT company, company_website, location, emails, phones, recruiter_name, title
                FROM job_leads
                WHERE emails IS NOT NULL AND emails != '' AND emails != 'None'
            """)
            for row in cur.fetchall():
                loc = row["location"] or ""
                if country_filter and country_filter not in loc.lower():
                    continue

                raw_emails = row["emails"] or ""
                for em in re.split(r"[,;\s]+", raw_emails):
                    email = em.strip()
                    if email and "@" in email and email.lower() not in seen_emails:
                        seen_emails.add(email.lower())
                        site = row["company_website"] or ""
                        recipients.append({
                            "person_name":  row["recruiter_name"] or "Hiring Manager",
                            "role":         f"Recruiter / Hiring for {row['title']}" if row['title'] else "Hiring Manager",
                            "email":        email,
                            "company_name": row["company"] or "",
                            "website":      site if site.startswith("http") else (f"https://{site}" if site else ""),
                            "city":         "",
                            "country":      loc,
                            "category":     "Job Lead",
                            "source":       "job_leads",
                        })

            return recipients
        except Exception as e:
            logger.warning(f"Error fetching recipients from SQLite job leads: {e}")
            return recipients
        finally:
            conn.close()

    def close(self) -> None:
        """Gracefully release connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def verify_database_health(self) -> Dict[str, Any]:
        """
        Verify database file connectivity, run PRAGMA quick_check,
        and verify that all expected tables exist across all modules.
        Raises DatabaseException if verification fails.
        """
        if not self._db_path.parent.exists():
            raise DatabaseException(f"Database directory does not exist: {self._db_path.parent}")

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            # 1. Integrity check
            cur.execute("PRAGMA quick_check")
            check_res = cur.fetchone()
            if not check_res or check_res[0] != "ok":
                raise DatabaseException(f"SQLite PRAGMA quick_check failed: {check_res[0] if check_res else 'Unknown'}")

            # 2. Enumerate existing tables
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            existing_tables = set(r[0] for r in cur.fetchall())

            # 3. Expected table groups
            core_tables = {"enriched_leads", "raw_jobs", "job_leads"}
            eu_tables = {"startups", "people", "contacts", "crawl_status"}
            email_tables = {"email_templates", "email_campaigns", "email_campaign_logs", "smtp_config", "email_audiences", "email_queue_items"}
            
            all_expected = core_tables | eu_tables | email_tables
            missing = all_expected - existing_tables
            if missing:
                raise DatabaseException(f"Database is missing critical tables: {sorted(list(missing))}")

            # 4. Count records in tables for health report
            table_counts: Dict[str, int] = {}
            for tbl in sorted(existing_tables):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    table_counts[tbl] = cur.fetchone()[0]
                except Exception:
                    table_counts[tbl] = -1

            return {
                "status": "healthy",
                "db_path": str(self._db_path),
                "integrity": "ok",
                "table_count": len(existing_tables),
                "tables": table_counts
            }
        except Exception as e:
            if isinstance(e, DatabaseException):
                raise
            raise DatabaseException(f"Database health verification failed: {e}") from e
        finally:
            conn.close()

    def _format_lead_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        """Convert a SQLite Row into a dictionary compatible with EnrichedLead JSON structure."""
        doc = dict(r)
        # Parse JSON fields
        for json_col in ("contacts", "key_technologies", "search_queries_used"):
            val = doc.get(json_col)
            if isinstance(val, str):
                try:
                    doc[json_col] = json.loads(val)
                except Exception:
                    doc[json_col] = []
            elif val is None:
                doc[json_col] = []

        # Boolean conversion
        doc["is_valid_lead"] = bool(doc.get("is_valid_lead", 1))
        doc["_id"] = str(doc.get("id", ""))
        return doc


# Default singleton instance
sqlite_manager = SqliteManager()
