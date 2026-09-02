"""
Backward-compatibility adapter for legacy MongoManager calls.
Redirects all database operations to the centralized SQLite database manager.
"""

from typing import Any, Dict, List, Optional
from db.sqlite import sqlite_manager, SqliteManager
from db.models import EnrichedLead, RawJobPosting


class MongoCollectionMock:
    """Mock collection for any direct mongo_manager.leads_collection calls."""

    def __init__(self, manager: SqliteManager):
        self.manager = manager

    def count_documents(self, filter_query: Optional[Dict[str, Any]] = None, limit: Optional[int] = None) -> int:
        stats = self.manager.get_stats()
        return stats.get("leads_count", 0)

    def find(self, *args, **kwargs):
        res = self.manager.get_leads(limit=1000)
        return res.get("leads", [])

    def find_one(self, query: Dict[str, Any], *args, **kwargs):
        job_url = query.get("job_url")
        if job_url:
            return self.manager.get_lead_by_url(job_url)
        return None

    def update_one(self, filter_query: Dict[str, Any], update_doc: Dict[str, Any], *args, **kwargs):
        job_url = filter_query.get("job_url")
        if job_url and "$set" in update_doc and "status" in update_doc["$set"]:
            ok = self.manager.update_lead_status(job_url, update_doc["$set"]["status"])
            class MockUpdateResult:
                matched_count = 1 if ok else 0
                modified_count = 1 if ok else 0
            return MockUpdateResult()
        return None

    def delete_one(self, filter_query: Dict[str, Any]):
        job_url = filter_query.get("job_url")
        if job_url:
            ok = self.manager.delete_lead(job_url)
            class MockDeleteResult:
                deleted_count = 1 if ok else 0
            return MockDeleteResult()
        return None

    def delete_many(self, *args, **kwargs):
        res = self.manager.clear_database()
        class MockDeleteManyResult:
            deleted_count = res.get("leads_deleted", 0)
        return MockDeleteManyResult()


class MongoManager:
    """Compatibility shim delegating to SqliteManager."""

    def __init__(self, *args, **kwargs):
        self.sqlite = sqlite_manager
        self._leads_col = MongoCollectionMock(self.sqlite)
        self._raw_col = MongoCollectionMock(self.sqlite)

    def connect(self) -> None:
        self.sqlite.connect()

    @property
    def leads_collection(self):
        return self._leads_col

    @property
    def raw_jobs_collection(self):
        return self._raw_col

    def job_exists(self, job_url: str) -> bool:
        return self.sqlite.job_exists(job_url)

    def save_raw_job(self, job: RawJobPosting) -> bool:
        return self.sqlite.save_raw_job(job)

    def upsert_enriched_lead(self, lead: EnrichedLead) -> bool:
        return self.sqlite.upsert_enriched_lead(lead)

    def get_leads(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return self.sqlite.get_leads(*args, **kwargs).get("leads", [])

    def clear_database(self) -> Dict[str, int]:
        return self.sqlite.clear_database()

    def drop_database(self) -> None:
        self.sqlite.clear_database()

    def close(self) -> None:
        self.sqlite.close()


# Default instance
mongo_manager = MongoManager()
