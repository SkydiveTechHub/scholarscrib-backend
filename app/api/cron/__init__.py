from fastapi import APIRouter

from app.api.cron.push import router as push_router

router = APIRouter(prefix="/api/cron")
router.include_router(push_router)
