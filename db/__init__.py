"""Database package initialization."""

from db.models import RawJobPosting, ContactPerson, EnrichedLead
from db.sqlite import SqliteManager, sqlite_manager

# Backward-compatibility alias
MongoManager = SqliteManager
mongo_manager = sqlite_manager

__all__ = [
    "RawJobPosting",
    "ContactPerson",
    "EnrichedLead",
    "SqliteManager",
    "sqlite_manager",
    "MongoManager",
    "mongo_manager",
]
