from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import AchievementsOut, AwardOut
from app.database.db import AnSession
from app.services.learning import AwardAchievementsService, GetAchievementsService

router = APIRouter(prefix="/achievements", tags=["Student / Achievements"])


@router.get("", response_model=AchievementsOut)
async def achievements(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetAchievementsService(session, student.id).process()


@router.post("", response_model=AwardOut)
async def award_achievements(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await AwardAchievementsService(session, student.id).process()
