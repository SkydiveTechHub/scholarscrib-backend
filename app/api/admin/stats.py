from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import StatsOut
from app.database.db import AnSession
from app.database.models import Admin
from app.services.console import GetAdminStatsService

router = APIRouter(prefix="/stats", tags=["Admin / Stats"])


@router.get("", response_model=StatsOut)
async def admin_stats(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetAdminStatsService(session).process()
