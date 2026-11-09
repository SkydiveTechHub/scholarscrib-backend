from fastapi import APIRouter

from app.api.cron.billing import router as billing_router
from app.api.cron.email import router as email_router
from app.api.cron.push import router as push_router
from app.api.cron.rag import router as rag_router

router = APIRouter(prefix="/api/cron")
router.include_router(push_router)
router.include_router(rag_router)
router.include_router(billing_router)
router.include_router(email_router)
