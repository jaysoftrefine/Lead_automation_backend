import datetime as dt
from datetime import datetime
from typing import List, Optional, Dict, Any
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

try:
    import dns.resolver
    # Fix macOS / IPv6 DNS timeout on mongodb+srv SRV lookups
    dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
    dns.resolver.default_resolver.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1"]
    dns.resolver.default_resolver.timeout = 4.0
    dns.resolver.default_resolver.lifetime = 4.0
except Exception:
    pass

from config.settings import settings
from core.logging import logger
from core.exceptions import DatabaseException
from db.models import EnrichedLead, RawJobPosting


def sanitize_mongo_document(obj: Any) -> Any:
    """Recursively converts objects (like datetime.date) into BSON/PyMongo friendly formats."""
    if isinstance(obj, dict):
        return {str(k): sanitize_mongo_document(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [sanitize_mongo_document(item) for item in obj]
    elif isinstance(obj, dt.datetime):
        return obj
    elif isinstance(obj, dt.date):
        return dt.datetime.combine(obj, dt.time.min)
    return obj


class MongoManager:
    """MongoDB connection manager with automatic index creation and upsert helpers."""

    def __init__(
        self,
        uri: Optional[str] = None,
        db_name: Optional[str] = None,
        collection_name: Optional[str] = None,
        raw_collection_name: Optional[str] = None,
    ):
        self.uri = uri or settings.mongodb_uri
        self.db_name = db_name or settings.mongodb_db_name
        self.collection_name = collection_name or settings.mongodb_collection_name
        self.raw_collection_name = raw_collection_name or settings.mongodb_raw_collection_name

        self._client: Optional[MongoClient] = None
        self._db = None
        self._leads_collection = None
        self._raw_jobs_collection = None

    def connect(self) -> None:
        """Establish connection to MongoDB and ensure indexes exist."""
        if self._client is not None:
            try:
                self._client.admin.command("ping")
                return
            except Exception:
                self._client = None

        try:
            logger.info(f"Connecting to MongoDB at {self.uri} (Database: {self.db_name})...")
            self._client = MongoClient(
                self.uri,
                serverSelectionTimeoutMS=20000,
                connectTimeoutMS=20000,
                retryWrites=True,
            )
            # Test connection
            self._client.admin.command("ping")
            self._db = self._client[self.db_name]
            self._leads_collection = self._db[self.collection_name]
            self._raw_jobs_collection = self._db[self.raw_collection_name]

            self._ensure_indexes()
            logger.info("MongoDB connected and indexes verified successfully.")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise DatabaseException(f"MongoDB connection failed: {e}") from e

    def _ensure_indexes(self) -> None:
        """Create necessary indexes for deduplication and fast queries."""
        try:
            # Unique index on job_url for leads
            self._leads_collection.create_index(
                [("job_url", ASCENDING)],
                unique=True,
                name="idx_leads_job_url_unique",
            )
            # Query indexes
            self._leads_collection.create_index([("company", ASCENDING)], name="idx_leads_company")
            self._leads_collection.create_index([("site", ASCENDING)], name="idx_leads_site")
            self._leads_collection.create_index([("is_valid_lead", ASCENDING)], name="idx_leads_is_valid")
            self._leads_collection.create_index([("relevance_score", DESCENDING)], name="idx_leads_relevance")
            self._leads_collection.create_index([("created_at", DESCENDING)], name="idx_leads_created_at")

            # Unique index on job_url for raw jobs
            self._raw_jobs_collection.create_index(
                [("job_url", ASCENDING)],
                unique=True,
                name="idx_raw_job_url_unique",
            )
            self._raw_jobs_collection.create_index([("scraped_at", DESCENDING)], name="idx_raw_scraped_at")
        except PyMongoError as e:
            logger.warning(f"Error while creating MongoDB indexes: {e}")

    @property
    def leads_collection(self):
        if self._leads_collection is None:
            self.connect()
        return self._leads_collection

    @property
    def raw_jobs_collection(self):
        if self._raw_jobs_collection is None:
            self.connect()
        return self._raw_jobs_collection

    def job_exists(self, job_url: str) -> bool:
        """Check if a qualified lead with the given job_url has already been enriched."""
        try:
            count = self.leads_collection.count_documents({
                "job_url": job_url,
                "relevance_score": {"$gt": 0},
                "status": {"$ne": "failed"}
            }, limit=1)
            return count > 0
        except Exception as e:
            logger.error(f"Error checking job existence for {job_url}: {e}")
            return False

    def save_raw_job(self, job: RawJobPosting) -> bool:
        """Save a raw scraped job posting with upsert."""
        try:
            job_dict = sanitize_mongo_document(job.model_dump())
            self.raw_jobs_collection.update_one(
                {"job_url": job.job_url},
                {"$set": job_dict},
                upsert=True,
            )
            return True
        except Exception as e:
            logger.error(f"Error saving raw job {job.job_url}: {e}")
            return False

    def upsert_enriched_lead(self, lead: EnrichedLead) -> bool:
        """Save or update an enriched lead in MongoDB."""
        try:
            lead_dict = sanitize_mongo_document(lead.model_dump())
            created_at_val = lead_dict.pop("created_at", datetime.utcnow())
            lead_dict["updated_at"] = datetime.utcnow()

            result = self.leads_collection.update_one(
                {"job_url": lead.job_url},
                {
                    "$set": lead_dict,
                    "$setOnInsert": {"created_at": created_at_val},
                },
                upsert=True,
            )
            if result.upserted_id:
                logger.info(f"Inserted new lead in Mongo: '{lead.title}' at {lead.company}")
            else:
                logger.info(f"Updated existing lead in Mongo: '{lead.title}' at {lead.company}")
            return True
        except Exception as e:
            logger.error(f"Error upserting lead {lead.job_url}: {e}")
            raise DatabaseException(f"Failed to upsert lead: {e}") from e

    def get_leads(
        self,
        filter_query: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        sort_by: str = "created_at",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieve enriched leads from MongoDB."""
        try:
            query = filter_query or {}
            sort_order = ASCENDING if ascending else DESCENDING
            cursor = self.leads_collection.find(query).sort(sort_by, sort_order).limit(limit)
            return list(cursor)
        except Exception as e:
            logger.error(f"Error retrieving leads from MongoDB: {e}")
            raise DatabaseException(f"Failed to query leads: {e}") from e

    def clear_database(self) -> Dict[str, int]:
        """Clear all documents from enriched_leads and raw_jobs collections while preserving indexes."""
        try:
            if self._db is None:
                self.connect()
            leads_deleted = self.leads_collection.delete_many({}).deleted_count
            raw_deleted = self.raw_jobs_collection.delete_many({}).deleted_count
            
            # Re-ensure indexes exist
            self._ensure_indexes()
            logger.info(f"Database '{self.db_name}' cleared: {leads_deleted} leads and {raw_deleted} raw jobs deleted.")
            return {
                "leads_deleted": leads_deleted,
                "raw_jobs_deleted": raw_deleted,
                "total_deleted": leads_deleted + raw_deleted,
            }
        except Exception as e:
            logger.error(f"Error clearing database '{self.db_name}': {e}")
            raise DatabaseException(f"Failed to clear database: {e}") from e

    def drop_database(self) -> None:
        """Drop the entire database."""
        try:
            if self._client is None:
                self.connect()
            self._client.drop_database(self.db_name)
            logger.info(f"Database '{self.db_name}' dropped successfully.")
        except Exception as e:
            logger.error(f"Error dropping database '{self.db_name}': {e}")
            raise DatabaseException(f"Failed to drop database: {e}") from e

    def close(self) -> None:
        """Close MongoDB connection."""
        if self._client:
            self._client.close()
            logger.info("MongoDB connection closed.")


# Default instance
mongo_manager = MongoManager()
