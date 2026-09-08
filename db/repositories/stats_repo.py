"""Stats Repository for SQLite dashboard metrics and analytics."""

import json
from pathlib import Path
from typing import Any, Callable, Dict
import sqlite3

from core.logging import logger


class StatsRepository:
    def __init__(self, get_connection: Callable[[], sqlite3.Connection], db_path: str, ensure_tables: Callable[[], None]):
        self.get_connection = get_connection
        self.db_path = db_path
        self.ensure_tables = ensure_tables

    def get_stats(self) -> Dict[str, Any]:
        """Calculate dashboard statistics from SQLite tables."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()

            # Check table existence
            cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='enriched_leads'")
            if cur.fetchone()[0] == 0:
                self.ensure_tables()

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
