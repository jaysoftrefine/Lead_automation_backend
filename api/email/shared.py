from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ATTACHMENTS_DIR = ROOT_DIR / "uploads" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def _enrich_recipient_company_info(r: Dict[str, Any], conn) -> Dict[str, Any]:
    """Look up company details (description, tags, website) from startups table if missing."""
    c_name = (r.get("company_name") or "").strip()
    if not c_name:
        return r
    if not r.get("company_description") or not r.get("company_tags"):
        row = conn.execute(
            "SELECT description, tags, website, city, country, category FROM startups WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(?)) LIMIT 1",
            (c_name,)
        ).fetchone()
        if row:
            if not r.get("company_description") and row["description"]:
                r["company_description"] = row["description"]
            if not r.get("company_tags") and row["tags"]:
                r["company_tags"] = row["tags"]
            if not r.get("website") and row["website"]:
                r["website"] = row["website"]
            if not r.get("city") and row["city"]:
                r["city"] = row["city"]
            if not r.get("country") and row["country"]:
                r["country"] = row["country"]
            if not r.get("category") and row["category"]:
                r["category"] = row["category"]
    return r
