from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import (
    DailyQuestsOut,
    ImprovementOut,
    StreakOut,
    XpProfileOut,
)
from app.database.db import AnSession
from app.services.gamification.service import (
    GetDailyQuestsService,
    GetImprovementService,
    GetStreakService,
    GetXpProfileService,
)

router = APIRouter(prefix="/gamification", tags=["Student / Gamification"])


@router.get("/profile", response_model=XpProfileOut)
async def xp_profile(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetXpProfileService(session, student.id).process()


@router.get("/quests", response_model=DailyQuestsOut)
async def daily_quests(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetDailyQuestsService(session, student.id).process()


@router.get("/streak", response_model=StreakOut)
async def streak(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetStreakService(session, student.id).process()


@router.get("/improvement", response_model=ImprovementOut)
async def improvement(
    attemptId: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetImprovementService(session, student.id, attemptId).process()
