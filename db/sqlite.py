"""SQLite Database Manager for Autonomous Lead Generation Engine."""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from config.settings import settings
from core.exceptions import DatabaseException
from core.logging import logger
from db.models import EnrichedLead, RawJobPosting
from db.repositories.jobs_repo import JobsRepository
from db.repositories.leads_repo import LeadsRepository
from db.repositories.maintenance_repo import MaintenanceRepository
from db.repositories.stats_repo import StatsRepository
from db.repositories.scheduled_jobs_repo import ScheduledJobsRepository
from db.schema import ensure_database_schema


class SqliteManager:
    """Manages SQLite database connections, schema setup, and modular repositories."""

    def __init__(self, db_path: Optional[str] = None):
        raw_path = db_path or getattr(settings, "sqlite_db_path", "data/leads.db")
        self._db_path = Path(raw_path).resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[sqlite3.Connection] = None

        # Repositories
        self.jobs_repo = JobsRepository(self.get_connection)
        self.leads_repo = LeadsRepository(self.get_connection)
        self.stats_repo = StatsRepository(self.get_connection, str(self._db_path), self._ensure_tables)
        self.maintenance_repo = MaintenanceRepository(self.get_connection, self._format_lead_row)
        self.scheduled_jobs_repo = ScheduledJobsRepository(self.get_connection)

    @property
    def db_path(self) -> str:
        return str(self._db_path)

    def get_connection(self) -> sqlite3.Connection:
        """Create and return a new thread-safe SQLite connection with WAL mode and dict-like rows."""
        conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def connect(self) -> None:
        """Verify database connectivity and ensure schema initialization."""
        conn = self.get_connection()
        try:
            conn.execute("SELECT 1")
            logger.info(f"SQLite centralized database connected at: {self._db_path}")
            self._ensure_tables()
        finally:
            conn.close()

    def _ensure_tables(self) -> None:
        """Create enriched_leads, raw_jobs, and job_leads tables if they do not exist."""
        conn = self.get_connection()
        try:
            ensure_database_schema(conn)
        finally:
            conn.close()

    # --- Delegated Jobs Repository Methods ---
    def job_exists(self, job_url: str) -> bool:
        return self.jobs_repo.job_exists(job_url)

    def save_raw_job(self, job: RawJobPosting) -> bool:
        return self.jobs_repo.save_raw_job(job)

    # --- Delegated Leads Repository Methods ---
    def _format_lead_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        return self.leads_repo._format_lead_row(r)

    def upsert_enriched_lead(self, lead: EnrichedLead) -> bool:
        return self.leads_repo.upsert_enriched_lead(lead)

    def get_leads(
        self,
        search: Optional[str] = None,
        site: Optional[str] = None,
        status: Optional[str] = None,
        lead_type: Optional[str] = None,
        company_size: Optional[str] = None,
        job_type: Optional[str] = None,
        hours_old: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_field: Optional[str] = "any",
        min_score: int = 0,
        has_contacts: Optional[bool] = None,
        scheduled_job_id: Optional[str] = None,
        limit: int = 50,
        page: int = 1,
    ) -> Dict[str, Any]:
        return self.leads_repo.get_leads(
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

    # --- Delegated Scheduled Jobs Repository Methods ---
    def create_or_upsert_scheduled_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        return self.scheduled_jobs_repo.create_or_upsert_job(job)

    def list_scheduled_jobs(
        self,
        status: Optional[str] = None,
        scheduled_date: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        page: int = 1,
    ) -> Dict[str, Any]:
        return self.scheduled_jobs_repo.list_jobs(
            status=status,
            scheduled_date=scheduled_date,
            search=search,
            limit=limit,
            page=page,
        )

    def get_scheduled_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.scheduled_jobs_repo.get_job_by_id(job_id)

    def update_scheduled_job_status(
        self,
        job_id: str,
        status: str,
        result_count: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        return self.scheduled_jobs_repo.update_job_status(
            job_id, status, result_count=result_count, error_message=error_message
        )

    def delete_scheduled_job(self, job_id: str) -> bool:
        return self.scheduled_jobs_repo.delete_job(job_id)

    def get_due_pending_jobs(self, today_date_str: str) -> List[Dict[str, Any]]:
        return self.scheduled_jobs_repo.get_due_pending_jobs(today_date_str)

    def get_lead_by_url(self, job_url: str) -> Optional[Dict[str, Any]]:
        return self.leads_repo.get_lead_by_url(job_url)

    def update_lead_status(self, job_url: str, status: str) -> bool:
        return self.leads_repo.update_lead_status(job_url, status)

    def update_lead_type(self, job_url: str, lead_type: str) -> bool:
        return self.leads_repo.update_lead_type(job_url, lead_type)

    def update_lead_outreach(self, job_url: str, **kwargs) -> bool:
        return self.leads_repo.update_lead_outreach(job_url, **kwargs)

    def get_due_outreach_leads(self, today_date_str: str) -> List[Dict[str, Any]]:
        return self.leads_repo.get_due_outreach_leads(today_date_str)

    def delete_lead(self, job_url: str) -> bool:
        return self.leads_repo.delete_lead(job_url)

    def get_recipients(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self.leads_repo.get_recipients(filters)

    # --- Delegated Stats Repository Methods ---
    def get_stats(self) -> Dict[str, Any]:
        return self.stats_repo.get_stats()

    # --- Delegated Maintenance Repository Methods ---
    def clear_database(self) -> Dict[str, int]:
        return self.maintenance_repo.clear_database()

    def backfill_dates(self) -> int:
        return self.maintenance_repo.backfill_dates()

    def export_all_data(self) -> Dict[str, Any]:
        return self.maintenance_repo.export_all_data()

    def import_and_backfill(self, payload: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
        return self.maintenance_repo.import_and_backfill(payload)

    def close(self) -> None:
        """Close connection if explicitly opened."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def verify_database_health(self) -> Dict[str, Any]:
        """
        Verify database file connectivity, run PRAGMA quick_check,
        and verify that all expected tables exist across active modular databases.
        Raises DatabaseException if verification fails.
        """
        if not self._db_path.parent.exists():
            raise DatabaseException(f"Database directory does not exist: {self._db_path.parent}")

        table_counts: Dict[str, int] = {}
        databases_info: Dict[str, Any] = {}

        # 1. Integrity and schema verification for Leads database
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA quick_check")
            check_res = cur.fetchone()
            if not check_res or check_res[0] != "ok":
                raise DatabaseException(f"SQLite PRAGMA quick_check failed on leads db: {check_res[0] if check_res else 'Unknown'}")

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            existing_tables = set(r[0] for r in cur.fetchall())

            core_tables = {"enriched_leads", "raw_jobs", "job_leads"}
            missing_core = core_tables - existing_tables
            if missing_core:
                raise DatabaseException(f"Database is missing critical core tables: {sorted(list(missing_core))}")

            for tbl in sorted(existing_tables):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    table_counts[tbl] = cur.fetchone()[0]
                except Exception:
                    table_counts[tbl] = -1

            databases_info["leads"] = {
                "path": str(self._db_path),
                "integrity": "ok",
                "tables": sorted(list(existing_tables))
            }
        except Exception as e:
            if isinstance(e, DatabaseException):
                raise
            raise DatabaseException(f"Database health verification failed: {e}") from e
        finally:
            conn.close()

        # 2. Integrity and schema verification for EU Startups database
        try:
            from eu_startups.db import DB_PATH as EU_DB_PATH, get_connection as get_eu_conn
            eu_path = Path(EU_DB_PATH)
            if eu_path.resolve() != self._db_path.resolve() and eu_path.exists():
                eu_conn = get_eu_conn()
                try:
                    eu_cur = eu_conn.cursor()
                    eu_cur.execute("PRAGMA quick_check")
                    eu_check = eu_cur.fetchone()
                    if not eu_check or eu_check[0] != "ok":
                        raise DatabaseException(f"SQLite PRAGMA quick_check failed on EU startups db: {eu_check[0] if eu_check else 'Unknown'}")

                    eu_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                    eu_existing = set(r[0] for r in eu_cur.fetchall())

                    eu_tables = {"startups", "people", "contacts", "crawl_status"}
                    eu_missing = eu_tables - eu_existing
                    if eu_missing:
                        raise DatabaseException(f"EU startups database is missing critical tables: {sorted(list(eu_missing))}")

                    for tbl in sorted(eu_existing):
                        if tbl not in table_counts:
                            try:
                                eu_cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                                table_counts[tbl] = eu_cur.fetchone()[0]
                            except Exception:
                                table_counts[tbl] = -1

                    databases_info["eu_startups"] = {
                        "path": str(eu_path),
                        "integrity": "ok",
                        "tables": sorted(list(eu_existing))
                    }
                finally:
                    eu_conn.close()
        except ImportError:
            pass
        except Exception as e:
            if isinstance(e, DatabaseException):
                raise
            logger.warning(f"Could not verify EU startups database health: {e}")

        # 3. Integrity and schema verification for Email Campaigns database
        try:
            from email_campaigns.db import DB_PATH as EMAIL_DB_PATH, get_connection as get_email_conn
            email_path = Path(EMAIL_DB_PATH)
            if email_path.resolve() != self._db_path.resolve() and email_path.exists():
                email_conn = get_email_conn()
                try:
                    email_cur = email_conn.cursor()
                    email_cur.execute("PRAGMA quick_check")
                    email_check = email_cur.fetchone()
                    if not email_check or email_check[0] != "ok":
                        raise DatabaseException(f"SQLite PRAGMA quick_check failed on email campaigns db: {email_check[0] if email_check else 'Unknown'}")

                    email_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                    email_existing = set(r[0] for r in email_cur.fetchall())

                    email_tables = {"email_templates", "email_campaigns", "email_campaign_logs", "smtp_config", "email_audiences", "email_queue_items"}
                    email_missing = email_tables - email_existing
                    if email_missing:
                        raise DatabaseException(f"Email campaigns database is missing critical tables: {sorted(list(email_missing))}")

                    for tbl in sorted(email_existing):
                        if tbl not in table_counts:
                            try:
                                email_cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                                table_counts[tbl] = email_cur.fetchone()[0]
                            except Exception:
                                table_counts[tbl] = -1

                    databases_info["email_campaigns"] = {
                        "path": str(email_path),
                        "integrity": "ok",
                        "tables": sorted(list(email_existing))
                    }
                finally:
                    email_conn.close()
        except ImportError:
            pass
        except Exception as e:
            if isinstance(e, DatabaseException):
                raise
            logger.warning(f"Could not verify Email campaigns database health: {e}")

        return {
            "status": "healthy",
            "db_path": str(self._db_path),
            "databases": databases_info,
            "integrity": "ok",
            "table_count": len(table_counts),
            "tables": table_counts
        }


# Default singleton instance
sqlite_manager = SqliteManager()
