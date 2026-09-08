"""Master API Router for Lead Generation Engine.

This router aggregates the modular sub-routers:
- `api.leads`: Lead search, CRUD, manual entry, presence checks, agent extraction
- `api.pipeline`: Pipeline runner, status, stop, test-enrichment, and WebSocket streaming
- `api.system`: Statistics, CSV export, instant research, and database maintenance
"""

from fastapi import APIRouter

from api.leads.routes import router as leads_router
from api.pipeline.routes import router as pipeline_router, websocket_pipeline_status
from api.system.routes import router as system_router

router = APIRouter(prefix="/api")

router.include_router(leads_router)
router.include_router(pipeline_router)
router.include_router(system_router)

__all__ = ["router", "websocket_pipeline_status"]
