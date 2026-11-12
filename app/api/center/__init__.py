from fastapi import APIRouter

from app.api.center.assignments import router as assignments_router
from app.api.center.astra import router as astra_router
from app.api.center.auth import router as auth_router
from app.api.center.centers import router as centers_router
from app.api.center.privacy import router as privacy_router
from app.api.center.recommendations import router as recommendations_router
from app.api.center.results import router as results_router
from app.api.center.tokens import router as tokens_router
from app.api.center.wallet import router as wallet_router

router = APIRouter(prefix="/center/api")
router.include_router(auth_router)
router.include_router(centers_router)
router.include_router(assignments_router)
router.include_router(results_router)
router.include_router(recommendations_router)
router.include_router(astra_router)
router.include_router(tokens_router)
router.include_router(wallet_router)
router.include_router(privacy_router)


@router.get("/health")
async def center_health():
    return {"ok": True, "realm": "tutor"}
