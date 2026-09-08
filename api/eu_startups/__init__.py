"""Master EU Startups API router module."""

from fastapi import APIRouter

from api.eu_startups.directory import router as directory_router
from api.eu_startups.enrichment import router as enrichment_router
from api.eu_startups.discovery import router as discovery_router
from api.eu_startups.agent_leads import router as agent_leads_router

router = APIRouter(prefix="/api/eu-startups", tags=["EU Startups"])

router.include_router(directory_router)
router.include_router(enrichment_router)
router.include_router(discovery_router)
router.include_router(agent_leads_router)

__all__ = ["router"]
