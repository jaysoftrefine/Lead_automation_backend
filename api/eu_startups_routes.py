"""FastAPI Router for EU Startups Explorer & Lead Intelligence."""

import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Query, HTTPException

from eu_startups.db import get_connection

router = APIRouter(prefix="/api/eu-startups", tags=["EU Startups"])


@router.get("/stats")
def get_eu_stats() -> Dict[str, Any]:
    """Retrieve overview metrics from the EU Startups SQLite database."""
    try:
        conn = get_connection()
        cur = conn.cursor()

        total = cur.execute("SELECT COUNT(*) FROM startups").fetchone()[0]
        countries = cur.execute(
            "SELECT COUNT(DISTINCT NULLIF(TRIM(country), '')) FROM startups"
        ).fetchone()[0]
        categories = cur.execute(
            "SELECT COUNT(DISTINCT NULLIF(TRIM(category), '')) FROM startups"
        ).fetchone()[0]
        people = cur.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        emails = cur.execute(
            "SELECT COUNT(*) FROM people WHERE email IS NOT NULL AND TRIM(email) <> ''"
        ).fetchone()[0]

        conn.close()
        return {
            "total": total,
            "countries": countries,
            "categories": categories,
            "people": people,
            "emails": emails,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch EU Startups stats: {str(e)}")


@router.get("/options")
def get_eu_options() -> Dict[str, List[str]]:
    """Retrieve distinct dropdown filter choices (countries, states, cities, categories, roles)."""
    try:
        conn = get_connection()
        cur = conn.cursor()

        def values(sql: str) -> List[str]:
            return [
                row[0] for row in cur.execute(sql).fetchall()
                if row[0] and str(row[0]).strip()
            ]

        result = {
            "countries": values("""
                SELECT DISTINCT country FROM startups
                WHERE country IS NOT NULL AND TRIM(country) <> ''
                ORDER BY country COLLATE NOCASE
            """),
            "states": values("""
                SELECT DISTINCT state FROM startups
                WHERE state IS NOT NULL AND TRIM(state) <> ''
                ORDER BY state COLLATE NOCASE
            """),
            "cities": values("""
                SELECT DISTINCT city FROM startups
                WHERE city IS NOT NULL AND TRIM(city) <> ''
                ORDER BY city COLLATE NOCASE
            """),
            "categories": values("""
                SELECT DISTINCT category FROM startups
                WHERE category IS NOT NULL AND TRIM(category) <> ''
                ORDER BY category COLLATE NOCASE
            """),
            "roles": values("""
                SELECT DISTINCT role FROM people
                WHERE role IS NOT NULL AND TRIM(role) <> ''
                ORDER BY role COLLATE NOCASE
            """),
        }

        conn.close()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch EU Startups options: {str(e)}")


@router.get("/startups")
def get_eu_startups(
    search: str = Query("", description="Search term across company, description, tag, person, email"),
    country: str = Query("", description="Filter by exact country"),
    state: str = Query("", description="Filter by exact state"),
    city: str = Query("", description="Filter by exact city"),
    category: str = Query("", description="Filter by exact category"),
    founded_min: str = Query("", description="Minimum founded year"),
    founded_max: str = Query("", description="Maximum founded year"),
    has_website: str = Query("", description="Filter by website availability (yes/no/any)"),
    has_email: str = Query("", description="Filter by email availability (yes/no/any)"),
    role: str = Query("", description="Filter by person role"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(25, ge=1, le=100, description="Items per page"),
    sort: str = Query("updated_at", description="Sort column"),
    direction: str = Query("desc", description="Sort direction (asc/desc)"),
) -> Dict[str, Any]:
    """Search, filter, and paginate startups from EU Startups database."""
    try:
        search = search.strip()
        country = country.strip()
        state = state.strip()
        city = city.strip()
        category = category.strip()
        founded_min = founded_min.strip()
        founded_max = founded_max.strip()
        has_website = has_website.strip()
        has_email = has_email.strip()
        role = role.strip()
        direction = direction.lower()

        allowed_sort = {
            "company_name": "s.company_name",
            "country": "s.country",
            "state": "s.state",
            "city": "s.city",
            "category": "s.category",
            "founded_year": "s.founded_year",
            "created_at": "s.created_at",
            "updated_at": "s.updated_at",
        }
        order_by = allowed_sort.get(sort, "s.updated_at")
        order_dir = "ASC" if direction == "asc" else "DESC"

        where: List[str] = []
        params: List[Any] = []

        if search:
            like = f"%{search}%"
            where.append("""
                (
                    s.company_name LIKE ?
                    OR s.description LIKE ?
                    OR s.website LIKE ?
                    OR s.country LIKE ?
                    OR s.state LIKE ?
                    OR s.city LIKE ?
                    OR s.category LIKE ?
                    OR s.tags LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM people p2
                        WHERE p2.startup_id = s.id
                        AND (
                            p2.name LIKE ?
                            OR p2.role LIKE ?
                            OR p2.email LIKE ?
                        )
                    )
                )
            """)
            params.extend([like] * 11)

        exact_filters = [
            ("s.country", country),
            ("s.state", state),
            ("s.city", city),
            ("s.category", category),
        ]

        for column, value in exact_filters:
            if value:
                where.append(f"LOWER(TRIM({column})) = LOWER(TRIM(?))")
                params.append(value)

        if founded_min:
            try:
                where.append("s.founded_year >= ?")
                params.append(int(founded_min))
            except ValueError:
                pass

        if founded_max:
            try:
                where.append("s.founded_year <= ?")
                params.append(int(founded_max))
            except ValueError:
                pass

        if has_website == "yes":
            where.append("s.website IS NOT NULL AND TRIM(s.website) <> ''")
        elif has_website == "no":
            where.append("(s.website IS NULL OR TRIM(s.website) = '')")

        if has_email == "yes":
            where.append("""
                EXISTS (
                    SELECT 1 FROM people p3
                    WHERE p3.startup_id = s.id
                    AND p3.email IS NOT NULL
                    AND TRIM(p3.email) <> ''
                )
            """)
        elif has_email == "no":
            where.append("""
                NOT EXISTS (
                    SELECT 1 FROM people p3
                    WHERE p3.startup_id = s.id
                    AND p3.email IS NOT NULL
                    AND TRIM(p3.email) <> ''
                )
            """)

        if role:
            where.append("""
                EXISTS (
                    SELECT 1 FROM people p4
                    WHERE p4.startup_id = s.id
                    AND LOWER(TRIM(p4.role)) = LOWER(TRIM(?))
                )
            """)
            params.append(role)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        conn = get_connection()
        cur = conn.cursor()

        total = cur.execute(
            f"SELECT COUNT(*) FROM startups s {where_sql}",
            params
        ).fetchone()[0]

        offset = (page - 1) * per_page

        rows = cur.execute(f"""
            SELECT
                s.id,
                s.company_name,
                s.description,
                s.website,
                s.eu_startups_url,
                s.country,
                s.state,
                s.city,
                s.founded_year,
                s.category,
                s.tags,
                s.company_linkedin,
                s.created_at,
                s.updated_at,
                (
                    SELECT COUNT(*)
                    FROM people p
                    WHERE p.startup_id = s.id
                ) AS people_count,
                (
                    SELECT COUNT(*)
                    FROM people p
                    WHERE p.startup_id = s.id
                    AND p.email IS NOT NULL
                    AND TRIM(p.email) <> ''
                ) AS email_count
            FROM startups s
            {where_sql}
            ORDER BY {order_by} {order_dir}, s.id DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset]).fetchall()

        startup_ids = [row["id"] for row in rows]
        people_map: Dict[int, List[Dict[str, Any]]] = {}

        if startup_ids:
            placeholders = ",".join("?" for _ in startup_ids)
            people_rows = cur.execute(f"""
                SELECT id, startup_id, name, role, email, linkedin, source_url
                FROM people
                WHERE startup_id IN ({placeholders})
                ORDER BY startup_id, id
            """, startup_ids).fetchall()

            for person in people_rows:
                people_map.setdefault(person["startup_id"], []).append(dict(person))

        data = []
        for row in rows:
            item = dict(row)
            item["people"] = people_map.get(row["id"], [])
            data.append(item)

        conn.close()

        return {
            "data": data,
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if per_page > 0 else 1,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query EU Startups: {str(e)}")


@router.post("/enrich")
def trigger_enrichment(
    limit: int = Query(10, ge=1, le=100, description="Number of startups to enrich"),
    startup_id: Optional[int] = Query(None, description="Enrich specific startup ID"),
) -> Dict[str, Any]:
    """Trigger background or inline lead enrichment for EU startups."""
    try:
        from eu_startups.enrich import run_enrichment, enrich_startup, get_connection

        if startup_id:
            conn = get_connection()
            row = conn.cursor().execute("SELECT * FROM startups WHERE id = ?", (startup_id,)).fetchone()
            if not row:
                conn.close()
                raise HTTPException(status_code=404, detail="Startup not found")
            success = enrich_startup(conn, row)
            conn.close()
            return {"status": "success" if success else "no_data", "startup_id": startup_id}
        else:
            run_enrichment(limit=limit)
            return {"status": "success", "limit": limit}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Enrichment error: {str(e)}")


@router.post("/startups/{startup_id}/enrich")
def enrich_single_startup(startup_id: int) -> Dict[str, Any]:
    """Enrich a single EU startup by ID and return updated record."""
    try:
        from eu_startups.enrich import enrich_startup, get_connection

        conn = get_connection()
        cur = conn.cursor()
        row = cur.execute("SELECT * FROM startups WHERE id = ?", (startup_id,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail=f"Startup with ID {startup_id} not found")
        
        success = enrich_startup(conn, row)

        # Fetch freshly enriched startup data and people
        updated_startup = cur.execute("SELECT * FROM startups WHERE id = ?", (startup_id,)).fetchone()
        people_rows = cur.execute("""
            SELECT id, startup_id, name, role, email, linkedin, source_url
            FROM people
            WHERE startup_id = ?
            ORDER BY id ASC
        """, (startup_id,)).fetchall()
        conn.close()

        people_list = [dict(p) for p in people_rows]
        startup_dict = dict(updated_startup) if updated_startup else {}
        startup_dict["people"] = people_list
        startup_dict["people_count"] = len(people_list)
        startup_dict["email_count"] = sum(1 for p in people_list if p.get("email"))

        return {
            "status": "success" if len(people_list) > 0 else "no_data",
            "startup_id": startup_id,
            "people_found": len(people_list),
            "data": startup_dict,
            "message": f"Found {len(people_list)} verified decision-maker(s)" if people_list else "No public executive profiles discovered for this startup.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Enrichment error: {str(e)}")


@router.post("/discover")
def trigger_discovery(
    topic: str = Query("", description="Industry, keywords, or topic (e.g. 'AI', 'Fintech', 'SaaS')"),
    country: str = Query("", description="Target European country"),
    limit: int = Query(5, ge=1, le=25, description="Number of leads to discover"),
) -> Dict[str, Any]:
    """Discover new European startups, extract real founders, and auto-enrich deliverable emails."""
    try:
        from eu_startups.discovery import run_discovery
        result = run_discovery(topic=topic, country=country, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discovery error: {str(e)}")


