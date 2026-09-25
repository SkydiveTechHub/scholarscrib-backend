from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import OverviewOut, StatsOut
from app.database.db import AnSession
from app.database.models import Admin
from app.services.console import GetAdminOverviewService, GetAdminStatsService

router = APIRouter(prefix="/stats", tags=["Admin / Stats"])
overview_router = APIRouter(prefix="/overview", tags=["Admin / Overview"])


@router.get("", response_model=StatsOut)
async def admin_stats(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetAdminStatsService(session).process()


@overview_router.get("", response_model=OverviewOut)
async def admin_overview(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetAdminOverviewService(session).process()
