from fastapi import APIRouter

from api.email.attachments import router as attachments_router
from api.email.smtp import router as smtp_router
from api.email.templates import router as templates_router
from api.email.audiences import router as audiences_router
from api.email.campaigns import router as campaigns_router
from api.email.queue import router as queue_router

router = APIRouter(prefix="/api/email", tags=["Email Campaigns"])

router.include_router(attachments_router)
router.include_router(smtp_router)
router.include_router(templates_router)
router.include_router(audiences_router)
router.include_router(campaigns_router)
router.include_router(queue_router)

__all__ = ["router"]
