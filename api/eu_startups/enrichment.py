"""Enrichment endpoints for EU Startups."""

from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


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
