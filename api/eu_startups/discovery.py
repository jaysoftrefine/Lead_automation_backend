"""Web discovery endpoints for EU Startups."""

from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


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
