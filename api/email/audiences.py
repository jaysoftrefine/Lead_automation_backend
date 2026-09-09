import json
import uuid
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException

from schemas import (
    AudienceCreate,
    AudienceUpdate,
)
from email_campaigns.db import get_connection
from email_campaigns.campaign_runner import (
    count_recipients,
    _get_recipients_from_sqlite,
    _get_recipients_from_mongo,
)

router = APIRouter()


@router.get("/recipients/browse")
def browse_recipients(
    sources: str = "sqlite,mongo",
    country: str = "",
    category: str = "",
    search: str = "",
    lead_type: str = "",
    date_preset: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "any",
    sort_by: str = "date",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 25,
) -> Dict[str, Any]:
    """Browse, search, and paginate available contacts from centralized SQLite (EU Startups & Job Leads) for audience selection (ImHUB style)."""
    src_list = [s.strip().lower() for s in sources.split(",") if s.strip()]
    filters: Dict[str, Any] = {}
    if country.strip():
        filters["country"] = country.strip()
    if category.strip():
        filters["category"] = category.strip()
    if lead_type.strip() and lead_type.strip().lower() != "all":
        filters["lead_type"] = lead_type.strip().lower()
    if date_preset.strip():
        filters["date_preset"] = date_preset.strip()
    if date_from.strip():
        filters["date_from"] = date_from.strip()
    if date_to.strip():
        filters["date_to"] = date_to.strip()
    if date_field.strip():
        filters["date_field"] = date_field.strip()

    all_recipients: List[Dict[str, Any]] = []
    seen = set()

    if "sqlite" in src_list:
        for r in _get_recipients_from_sqlite(filters):
            e = (r.get("email") or "").lower().strip()
            if e and e not in seen:
                seen.add(e)
                scraped_date = r.get("date") or (r.get("created_at") or "")[:10]
                all_recipients.append({
                    "id": f"sqlite-{e}",
                    "person_name": r.get("person_name") or "Founder / Leadership",
                    "role": r.get("role") or "Leadership",
                    "email": r.get("email") or "",
                    "company_name": r.get("company_name") or "Startup",
                    "website": r.get("website") or "",
                    "city": r.get("city") or "",
                    "country": r.get("country") or "",
                    "category": r.get("category") or "",
                    "source": "sqlite",
                    "lead_type": "company",
                    "date": scraped_date,
                    "scraped_at": r.get("scraped_at") or r.get("created_at"),
                    "created_at": r.get("created_at"),
                    "date_posted": None,
                })

    if "mongo" in src_list or "job_leads" in src_list:
        try:
            for r in _get_recipients_from_mongo(filters):
                e = (r.get("email") or "").lower().strip()
                if e and e not in seen:
                    seen.add(e)
                    scraped_date = r.get("date") or (r.get("scraped_at") or "")[:10] or (r.get("created_at") or "")[:10]
                    all_recipients.append({
                        "id": f"leads-{e}",
                        "person_name": r.get("person_name") or "Contact",
                        "role": r.get("role") or "Professional",
                        "email": r.get("email") or "",
                        "company_name": r.get("company_name") or "Company",
                        "website": r.get("website") or "",
                        "city": r.get("city") or "",
                        "country": r.get("country") or "",
                        "category": r.get("category") or "Job Lead",
                        "source": "mongo" if "mongo" in src_list else "job_leads",
                        "lead_type": r.get("lead_type") or "others",
                        "date": scraped_date,
                        "scraped_at": r.get("scraped_at") or r.get("created_at"),
                        "created_at": r.get("created_at") or None,
                        "date_posted": r.get("date_posted") or None,
                    })
        except Exception:
            pass

    # Search filter
    q = search.strip().lower()
    if q:
        filtered = []
        for r in all_recipients:
            haystack = f"{r['person_name']} {r['company_name']} {r['email']} {r['role']} {r['country']} {r['category']}".lower()
            if q in haystack:
                filtered.append(r)
        all_recipients = filtered

    # Sort recipients (default: newest scraped date first)
    def get_sort_key(item: Dict[str, Any]) -> str:
        if sort_by == "name":
            return (item.get("person_name") or "").lower()
        elif sort_by == "company":
            return (item.get("company_name") or "").lower()
        else:  # "date"
            return str(item.get("scraped_at") or item.get("created_at") or item.get("date") or "")

    is_desc = (sort_dir.lower() != "asc")
    all_recipients.sort(key=get_sort_key, reverse=is_desc)

    total = len(all_recipients)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = all_recipients[start:end]

    return {
        "status": "success",
        "data": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "items": page_items,
        },
    }


@router.get("/audiences")
def list_audiences() -> Dict[str, Any]:
    """List all saved audiences."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM email_audiences ORDER BY updated_at DESC").fetchall()
    conn.close()
    audiences = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d["sources"] or "[]")
        except Exception:
            d["sources"] = ["sqlite"]
        try:
            d["filters"] = json.loads(d["filters"] or "{}")
        except Exception:
            d["filters"] = {}
        try:
            d["manual_recipients"] = json.loads(d["manual_recipients"] or "[]")
        except Exception:
            d["manual_recipients"] = []
        try:
            d["selected_recipients"] = json.loads(d.get("selected_recipients") or "[]")
        except Exception:
            d["selected_recipients"] = []
        audiences.append(d)
    return {"status": "success", "data": audiences}


@router.post("/audiences")
def create_audience(payload: AudienceCreate) -> Dict[str, Any]:
    """Create a new saved audience."""
    aud_id = str(uuid.uuid4())
    sources = payload.sources or ["sqlite"]
    filters = payload.filters or {}
    manual = payload.manual_recipients or []
    selected = payload.selected_recipients or []

    raw_manual_emails = [
        m if isinstance(m, str) else (m.get("email") or "")
        for m in manual
    ]
    raw_manual_emails = [e for e in raw_manual_emails if e]

    count = count_recipients(sources, filters, raw_manual_emails, selected_recipients=selected)

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO email_audiences (id, name, description, sources, filters, manual_recipients, selected_recipients, contact_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aud_id,
            payload.name.strip(),
            payload.description or "",
            json.dumps(sources),
            json.dumps(filters),
            json.dumps(manual),
            json.dumps(selected),
            count,
        ),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Audience saved.", "data": {"id": aud_id, "contact_count": count}}


@router.get("/audiences/{aud_id}")
def get_audience(aud_id: str) -> Dict[str, Any]:
    """Get single audience."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_audiences WHERE id = ?", (aud_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Audience not found.")
    d = dict(row)
    try:
        d["sources"] = json.loads(d["sources"] or "[]")
    except Exception:
        d["sources"] = ["sqlite"]
    try:
        d["filters"] = json.loads(d["filters"] or "{}")
    except Exception:
        d["filters"] = {}
    try:
        d["manual_recipients"] = json.loads(d["manual_recipients"] or "[]")
    except Exception:
        d["manual_recipients"] = []
    try:
        d["selected_recipients"] = json.loads(d.get("selected_recipients") or "[]")
    except Exception:
        d["selected_recipients"] = []
    return {"status": "success", "data": d}


@router.put("/audiences/{aud_id}")
def update_audience(aud_id: str, payload: AudienceUpdate) -> Dict[str, Any]:
    """Update saved audience."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM email_audiences WHERE id = ?", (aud_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Audience not found.")

    current = dict(row)
    name = payload.name if payload.name is not None else current["name"]
    desc = payload.description if payload.description is not None else current["description"]
    sources = payload.sources if payload.sources is not None else json.loads(current["sources"] or "[]")
    filters = payload.filters if payload.filters is not None else json.loads(current["filters"] or "{}")
    manual = payload.manual_recipients if payload.manual_recipients is not None else json.loads(current["manual_recipients"] or "[]")
    selected = payload.selected_recipients if payload.selected_recipients is not None else json.loads(current.get("selected_recipients") or "[]")

    raw_manual_emails = [
        m if isinstance(m, str) else (m.get("email") or "")
        for m in manual
    ]
    raw_manual_emails = [e for e in raw_manual_emails if e]

    count = count_recipients(sources, filters, raw_manual_emails, selected_recipients=selected)

    conn.execute(
        """
        UPDATE email_audiences
        SET name = ?, description = ?, sources = ?, filters = ?, manual_recipients = ?, selected_recipients = ?, contact_count = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (name.strip(), desc, json.dumps(sources), json.dumps(filters), json.dumps(manual), json.dumps(selected), count, aud_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Audience updated.", "data": {"id": aud_id, "contact_count": count}}


@router.delete("/audiences/{aud_id}")
def delete_audience(aud_id: str) -> Dict[str, Any]:
    """Delete saved audience."""
    conn = get_connection()
    res = conn.execute("DELETE FROM email_audiences WHERE id = ?", (aud_id,))
    conn.commit()
    conn.close()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Audience not found.")
    return {"status": "success", "message": "Audience deleted."}
