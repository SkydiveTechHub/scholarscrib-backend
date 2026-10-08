from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import AnalyticsOut, OverviewOut, StatsOut
from app.database.db import AnSession
from app.database.models import Admin
from app.services.analytics import GetAdminAnalyticsService
from app.services.console import GetAdminOverviewService, GetAdminStatsService

router = APIRouter(prefix="/stats", tags=["Admin / Stats"])
overview_router = APIRouter(prefix="/overview", tags=["Admin / Overview"])
analytics_router = APIRouter(prefix="/analytics", tags=["Admin / Analytics"])


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


@analytics_router.get("", response_model=AnalyticsOut)
async def admin_analytics(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    now: datetime,
    since: datetime,
    currentStart: datetime,
    previousStart: datetime,
    previousEnd: datetime,
    lastMonth: datetime,
    cohortStart: datetime,
):
    """Raw counts for the overview's analytics. The caller owns the windows."""
    return await GetAdminAnalyticsService(
        session,
        now=now,
        since=since,
        current_start=currentStart,
        previous_start=previousStart,
        previous_end=previousEnd,
        last_month=lastMonth,
        cohort_start=cohortStart,
    ).process()
