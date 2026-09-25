from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import ProgressOut
from app.api.schemas import ProgressIn
from app.database.db import AnSession
from app.services.learning import SaveLessonProgressService

router = APIRouter(prefix="/lessons", tags=["Student / Lessons"])


@router.patch("/{lesson_id}/progress", response_model=ProgressOut)
async def lesson_progress(
    lesson_id: str,
    body: ProgressIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await SaveLessonProgressService(
        session, student.id, lesson_id, body
    ).process()
