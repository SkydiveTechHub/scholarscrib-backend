from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import PerformanceOut, TopicStandingsOut
from app.database.db import AnSession
from app.services.learning import GetTopicStandingsService
from app.services.pages import GetPerformanceService

router = APIRouter(prefix="/performance", tags=["Student / Performance"])


@router.get("", response_model=PerformanceOut)
async def performance(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    page: int = 1,
    subjectId: str | None = None,
):
    return await GetPerformanceService(
        session, student.id, student.tier, max(page, 1), subjectId, student.track
    ).process()


@router.get("/topics", response_model=TopicStandingsOut)
async def topic_standings(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    status: Literal["all", "strong", "weak", "normal"] = "all",
    subjectId: Annotated[list[str] | None, Query()] = None,
    limit: int = 50,
):
    return await GetTopicStandingsService(
        session,
        student.id,
        status=status,
        subject_ids=subjectId,
        limit=limit,
    ).process()
