"""PostgreSQL / Supabase Database Manager for Autonomous Lead Generation Engine."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import psycopg
from psycopg_pool import ConnectionPool

from config.settings import settings
from core.exceptions import DatabaseException
from core.logging import logger
from db.models import EnrichedLead, RawJobPosting
from db.repositories.jobs_repo import JobsRepository
from db.repositories.leads_repo import LeadsRepository
from db.repositories.maintenance_repo import MaintenanceRepository
from db.repositories.stats_repo import StatsRepository
from db.repositories.scheduled_jobs_repo import ScheduledJobsRepository


def convert_placeholders(sql: str) -> str:
    """Convert SQLite '?' parameter placeholders to PostgreSQL '%s', ignoring string literals."""
    out = []
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == '?' and not in_single and not in_double:
            out.append('%s')
        else:
            out.append(ch)
    return "".join(out)


def adapt_query_for_postgres(query: str) -> str:
    """Adapt SQLite syntax to PostgreSQL: placeholders, introspection, and boolean literal comparisons."""
    adapted = convert_placeholders(query)
    if "sqlite_master" in adapted:
        adapted = adapted.replace(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='enriched_leads'",
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='enriched_leads'",
        )
    # Adapt boolean columns (e.g. is_default, use_ssl, use_tls) from integer 1/0 to Postgres boolean TRUE/FALSE
    for col in ("is_default", "use_ssl", "use_tls"):
        if col in adapted:
            adapted = re.sub(rf'\b{col}\s*=\s*1\b', f'{col} = TRUE', adapted, flags=re.IGNORECASE)
            adapted = re.sub(rf'\b{col}\s*=\s*0\b', f'{col} = FALSE', adapted, flags=re.IGNORECASE)
    # Strip contacts != '' comparisons since contacts is JSONB in Postgres and empty string is invalid JSON syntax
    adapted = re.sub(r"\s+AND\s+([\w\.]*contacts\s*!=\s*'')", "", adapted, flags=re.IGNORECASE)
    return adapted


class SmartRow(dict):
    """Row adapter that allows dictionary-style and integer-index access like sqlite3.Row."""

    def __init__(self, data: Dict[str, Any], values: tuple):
        super().__init__(data)
        self._values = values

    def __getitem__(self, key: Union[str, int]) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def smart_row_factory(cursor):
    """Custom row factory producing SmartRow instances."""
    desc = cursor.description
    if not desc:
        return lambda values: values
    col_names = [col.name for col in desc]

    def make_row(values):
        data = {col_names[i]: values[i] for i in range(len(values))}
        return SmartRow(data, values)

    return make_row


class PostgresCursorWrapper:
    """Wraps a psycopg cursor to adapt parameter placeholders and sqlite compatibility."""

    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def execute(self, query: str, params: Any = None):
        adapted_query = adapt_query_for_postgres(query)

        if params is not None:
            if isinstance(params, (list, tuple)):
                self._cursor.execute(adapted_query, params)
            else:
                self._cursor.execute(adapted_query, (params,))
        else:
            self._cursor.execute(adapted_query)
        return self

    def executemany(self, query: str, params_seq: Any):
        adapted_query = adapt_query_for_postgres(query)
        self._cursor.executemany(adapted_query, params_seq)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size: int = 1):
        return self._cursor.fetchmany(size)

    def close(self):
        self._cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


from psycopg.pq import TransactionStatus


class PostgresConnectionWrapper:
    """Wraps a psycopg connection from the pool, adapting cursor creation and life cycle."""

    def __init__(self, raw_conn, pool: ConnectionPool):
        self._raw_conn = raw_conn
        self._pool = pool
        self._closed = False
        self.is_postgres = True

    def cursor(self):
        cur = self._raw_conn.cursor(row_factory=smart_row_factory)
        return PostgresCursorWrapper(cur)

    def execute(self, query: str, params: Any = None):
        cur = self.cursor()
        return cur.execute(query, params)

    def commit(self):
        if not self._raw_conn.closed:
            self._raw_conn.commit()

    def rollback(self):
        if not self._raw_conn.closed:
            try:
                self._raw_conn.rollback()
            except Exception:
                pass

    def close(self):
        if not self._closed:
            if not self._raw_conn.closed:
                try:
                    if self._raw_conn.info.transaction_status == TransactionStatus.INTRANS:
                        self._raw_conn.rollback()
                except Exception:
                    pass
            self._pool.putconn(self._raw_conn)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        self.close()


class PostgresManager:
    """Manages Supabase / PostgreSQL database connections, schema setup, and modular repositories."""

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = (database_url or settings.database_url or "").strip()
        if not self.database_url:
            raise DatabaseException("DATABASE_URL is required to initialize PostgresManager.")

        # Ensure sslmode for Supabase/cloud connections if not specified
        if "sslmode=" not in self.database_url and ("supabase" in self.database_url or "aws" in self.database_url or "pooler" in self.database_url):
            delimiter = "&" if "?" in self.database_url else "?"
            self.database_url = f"{self.database_url}{delimiter}sslmode=require"

        # Add TCP keepalives to prevent AWS/Supabase pooler from silently dropping idle sockets
        if "keepalives=" not in self.database_url:
            delimiter = "&" if "?" in self.database_url else "?"
            self.database_url = f"{self.database_url}{delimiter}keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"

        self._pool = ConnectionPool(
            conninfo=self.database_url,
            min_size=1,
            max_size=15,
            timeout=10.0,
            max_idle=45.0,
            max_lifetime=300.0,
            check=ConnectionPool.check_connection,
            open=True,
        )

        # Fail-fast check to ensure database is reachable
        try:
            test_conn = self._pool.getconn(timeout=5.0)
            self._pool.putconn(test_conn)
        except Exception as e:
            self._pool.close()
            raise DatabaseException(f"Failed to connect to Supabase PostgreSQL at {self.database_url[:35]}...: {e}") from e

        # Repositories
        self.jobs_repo = JobsRepository(self.get_connection)
        self.leads_repo = LeadsRepository(self.get_connection)
        self.stats_repo = StatsRepository(self.get_connection, "Supabase PostgreSQL", self._ensure_tables)
        self.maintenance_repo = MaintenanceRepository(self.get_connection, self._format_lead_row)
        self.scheduled_jobs_repo = ScheduledJobsRepository(self.get_connection)

    @property
    def db_path(self) -> str:
        return "Supabase (Cloud PostgreSQL)"

    def get_connection(self) -> PostgresConnectionWrapper:
        """Borrow a connection from the pool and wrap it."""
        raw_conn = self._pool.getconn()
        return PostgresConnectionWrapper(raw_conn, self._pool)

    def connect(self) -> None:
        """Verify database connectivity and ensure schema initialization."""
        conn = self.get_connection()
        try:
            conn.execute("SELECT 1")
            logger.info("Supabase PostgreSQL cloud database connected successfully.")
            self._ensure_tables()
        finally:
            conn.close()

    def _ensure_tables(self) -> None:
        """Run supabase_schema.sql if tables do not exist."""
        schema_path = Path(__file__).resolve().parent / "supabase_schema.sql"
        if not schema_path.exists():
            logger.warning(f"supabase_schema.sql not found at {schema_path}")
            return

        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()

        conn = self.get_connection()
        try:
            # Check if enriched_leads already exists
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='enriched_leads'")
            exists = cur.fetchone()[0] > 0
            if not exists:
                logger.info("Initializing Supabase tables from supabase_schema.sql...")
                for statement in sql.split(";"):
                    stmt = statement.strip()
                    if stmt:
                        try:
                            cur.execute(stmt)
                        except Exception as se:
                            logger.debug(f"Schema statement notice: {se}")
                conn.commit()
                logger.info("Supabase tables initialized successfully.")
        except Exception as e:
            logger.error(f"Error ensuring Supabase database schema: {e}")
            conn.rollback()
        finally:
            conn.close()

    # --- Delegated Jobs Repository Methods ---
    def job_exists(self, job_url: str) -> bool:
        return self.jobs_repo.job_exists(job_url)

    def save_raw_job(self, job: RawJobPosting) -> bool:
        return self.jobs_repo.save_raw_job(job)

    # --- Delegated Leads Repository Methods ---
    def _format_lead_row(self, r: Any) -> Dict[str, Any]:
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

    def update_scheduled_job(self, job_id: str, **kwargs) -> bool:
        return self.scheduled_jobs_repo.update_scheduled_job(job_id, **kwargs)

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
        """Close connection pool."""
        try:
            self._pool.close()
        except Exception:
            pass

    def verify_database_health(self) -> Dict[str, Any]:
        """Verify database connectivity and table counts."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")

            # Discover all tables in public schema
            cur.execute("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            table_names = [r[0] for r in cur.fetchall()]

            table_counts: Dict[str, int] = {}
            for t in table_names:
                try:
                    cur.execute(f"SELECT count(*) FROM {t}")
                    table_counts[t] = cur.fetchone()[0]
                except Exception:
                    table_counts[t] = 0

            return {
                "status": "healthy",
                "db_path": "Supabase (Cloud PostgreSQL)",
                "database_type": "Supabase PostgreSQL",
                "integrity": "ok",
                "table_count": len(table_counts),
                "tables": table_counts,
            }
        except Exception as e:
            raise DatabaseException(f"Supabase database health check failed: {e}") from e
        finally:
            conn.close()
