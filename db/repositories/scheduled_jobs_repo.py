"""Scheduled Scraping Jobs Repository for SQLite."""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import sqlite3

from core.logging import logger


class ScheduledJobsRepository:
    def __init__(self, get_connection: Callable[[], sqlite3.Connection]):
        self.get_connection = get_connection

    def _format_job_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": r["id"],
            "job_title": r["job_title"],
            "target_location": r["target_location"] or "Worldwide (Remote)",
            "company_size": r["company_size"] or "all",
            "scraping_limit": r["scraping_limit"] or 10,
            "scheduled_date": r["scheduled_date"],
            "status": r["status"] or "pending",
            "result_count": r["result_count"] or 0,
            "last_run_at": r["last_run_at"],
            "error_message": r["error_message"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }

    def create_or_upsert_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update a scheduled scraping job."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            job_id = str(job.get("id") or f"JOB-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
            job_title = str(job.get("job_title") or job.get("jobtitle") or "Software Engineer").strip()
            target_location = str(job.get("target_location") or "Worldwide (Remote)").strip()
            company_size = str(job.get("company_size") or "small").strip()
            scraping_limit = int(job.get("scraping_limit") or 10)
            scheduled_date = str(job.get("scheduled_date") or datetime.utcnow().strftime("%Y-%m-%d")).strip()
            status = str(job.get("status") or "pending").strip()

            now_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            cur.execute("""
                INSERT INTO scheduled_scraping_jobs (
                    id, job_title, target_location, company_size, scraping_limit,
                    scheduled_date, status, result_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    job_title = excluded.job_title,
                    target_location = excluded.target_location,
                    company_size = excluded.company_size,
                    scraping_limit = excluded.scraping_limit,
                    scheduled_date = excluded.scheduled_date,
                    updated_at = excluded.updated_at
            """, (
                job_id, job_title, target_location, company_size, scraping_limit,
                scheduled_date, status, now_iso, now_iso
            ))
            conn.commit()

            cur.execute("SELECT * FROM scheduled_scraping_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return self._format_job_row(row) if row else {}
        except Exception as e:
            logger.error(f"Error creating/upserting scheduled job: {e}")
            raise
        finally:
            conn.close()

    def list_jobs(
        self,
        status: Optional[str] = None,
        scheduled_date: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        page: int = 1,
    ) -> Dict[str, Any]:
        """List scheduled scraping jobs with filters and pagination."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            conditions = []
            params: List[Any] = []

            if status and status != "all":
                conditions.append("status = ?")
                params.append(status)

            if scheduled_date:
                conditions.append("scheduled_date = ?")
                params.append(scheduled_date)

            if search:
                term = f"%{search.strip()}%"
                conditions.append("(id LIKE ? OR job_title LIKE ? OR target_location LIKE ?)")
                params.extend([term, term, term])

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            count_query = f"SELECT COUNT(*) FROM scheduled_scraping_jobs {where_clause}"
            cur.execute(count_query, params)
            total = cur.fetchone()[0]

            offset = (page - 1) * limit
            select_query = f"""
                SELECT s.*, 
                       (SELECT COUNT(*) FROM enriched_leads el WHERE el.scheduled_job_id = s.id) as actual_leads_count
                FROM scheduled_scraping_jobs s
                {where_clause}
                ORDER BY 
                    CASE 
                        WHEN status = 'running' THEN 1
                        WHEN status = 'pending' THEN 2
                        WHEN status = 'completed' THEN 3
                        ELSE 4
                    END,
                    scheduled_date DESC,
                    created_at DESC
                LIMIT ? OFFSET ?
            """
            cur.execute(select_query, params + [limit, offset])
            rows = cur.fetchall()

            jobs = []
            for r in rows:
                formatted = self._format_job_row(r)
                # Keep result_count in sync with actual leads if present
                actual_cnt = r["actual_leads_count"]
                if actual_cnt > 0 and formatted["result_count"] != actual_cnt:
                    formatted["result_count"] = actual_cnt
                jobs.append(formatted)

            return {
                "jobs": jobs,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": max(1, (total + limit - 1) // limit),
            }
        finally:
            conn.close()

    def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single scheduled job by ID."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT s.*, 
                       (SELECT COUNT(*) FROM enriched_leads el WHERE el.scheduled_job_id = s.id) as actual_leads_count
                FROM scheduled_scraping_jobs s
                WHERE s.id = ?
            """, (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            res = self._format_job_row(row)
            if row["actual_leads_count"] > 0:
                res["result_count"] = row["actual_leads_count"]
            return res
        finally:
            conn.close()

    def update_job_status(
        self,
        job_id: str,
        status: str,
        result_count: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update the execution status of a scheduled job."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            now_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            updates = ["status = ?", "updated_at = ?"]
            params: List[Any] = [status, now_iso]

            if status in ("running", "completed", "failed"):
                updates.append("last_run_at = ?")
                params.append(now_iso)

            if result_count is not None:
                updates.append("result_count = ?")
                params.append(result_count)

            if error_message is not None:
                updates.append("error_message = ?")
                params.append(error_message)

            params.append(job_id)
            cur.execute(f"UPDATE scheduled_scraping_jobs SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_job(self, job_id: str) -> bool:
        """Delete a scheduled job."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM scheduled_scraping_jobs WHERE id = ?", (job_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_due_pending_jobs(self, today_date_str: str) -> List[Dict[str, Any]]:
        """Get all pending scheduled jobs due on or before today."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM scheduled_scraping_jobs
                WHERE scheduled_date <= ? AND status = 'pending'
                ORDER BY scheduled_date ASC, created_at ASC
            """, (today_date_str,))
            rows = cur.fetchall()
            return [self._format_job_row(r) for r in rows]
        finally:
            conn.close()
