"""Jobs Repository for SQLite raw job operations."""

import json
from datetime import datetime
from typing import Callable
import sqlite3

from core.logging import logger
from db.models import RawJobPosting


class JobsRepository:
    def __init__(self, get_connection: Callable[[], sqlite3.Connection]):
        self.get_connection = get_connection

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
            raw_meta_str = json.dumps(job.raw_metadata or {}, default=str)
            scraped_at_str = job.scraped_at.isoformat() if isinstance(job.scraped_at, datetime) else str(job.scraped_at or datetime.utcnow().isoformat())
            date_posted_str = job.date_posted.isoformat() if hasattr(job.date_posted, "isoformat") else (str(job.date_posted) if job.date_posted else None)

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
                date_posted_str,
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
