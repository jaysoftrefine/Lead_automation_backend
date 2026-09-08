"""Backward-compatibility alias for legacy MongoManager.

HirePilot has fully transitioned to the centralized SQLite database.
All database operations are handled directly by `db.sqlite.sqlite_manager`.
"""

from db.sqlite import SqliteManager as MongoManager, sqlite_manager as mongo_manager

__all__ = ["MongoManager", "mongo_manager"]
