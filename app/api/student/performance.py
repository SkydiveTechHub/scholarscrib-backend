from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import PerformanceOut
from app.database.db import AnSession
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
        session, student.id, student.tier, max(page, 1), subjectId
    ).process()
