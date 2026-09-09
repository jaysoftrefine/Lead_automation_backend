import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ATTACHMENTS_DIR = ROOT_DIR / "uploads" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def get_startup_info(company_name: str, eu_conn=None) -> Optional[Dict[str, Any]]:
    """Look up company details (description, tags, website, city, country, category) from startups table in eu_startups.db."""
    c_name = (company_name or "").strip()
    if not c_name:
        return None

    close_db = False
    conn_to_use = eu_conn

    if conn_to_use is None:
        try:
            from eu_startups.db import get_connection as get_eu_connection
            conn_to_use = get_eu_connection()
            close_db = True
        except Exception:
            return None

    try:
        row = conn_to_use.execute(
            "SELECT company_name, description, tags, website, city, country, category FROM startups WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(?)) LIMIT 1",
            (c_name,)
        ).fetchone()
        if row:
            return dict(row)
    except sqlite3.OperationalError:
        return None
    finally:
        if close_db and conn_to_use:
            try:
                conn_to_use.close()
            except Exception:
                pass

    return None


def _enrich_recipient_company_info(r: Dict[str, Any], conn=None, eu_conn=None) -> Dict[str, Any]:
    """Look up company details (description, tags, website) from startups table if missing."""
    c_name = (r.get("company_name") or "").strip()
    if not c_name:
        return r

    if not r.get("company_description") or not r.get("company_tags") or not r.get("website"):
        active_eu_conn = eu_conn
        if active_eu_conn is None and conn is not None:
            try:
                has_startups = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='startups'"
                ).fetchone()
                if has_startups:
                    active_eu_conn = conn
            except Exception:
                pass

        info = get_startup_info(c_name, eu_conn=active_eu_conn)
        if info:
            if not r.get("company_description") and info.get("description"):
                r["company_description"] = info["description"]
            if not r.get("company_tags") and info.get("tags"):
                r["company_tags"] = info["tags"]
            if not r.get("website") and info.get("website"):
                r["website"] = info["website"]
            if not r.get("city") and info.get("city"):
                r["city"] = info["city"]
            if not r.get("country") and info.get("country"):
                r["country"] = info["country"]
            if not r.get("category") and info.get("category"):
                r["category"] = info["category"]

    return r

