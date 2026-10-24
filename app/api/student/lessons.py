from typing import Annotated

from fastapi import APIRouter, Depends

from app.ai.tutor.service import LessonTutorService
from app.api.deps import StudentPrincipal, require_feature, require_student
from app.api.responses import ProgressOut, TutorThreadOut
from app.api.schemas import ProgressIn, TutorThreadCreateIn
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


@router.post(
    "/{lesson_id}/tutor",
    status_code=201,
    response_model=TutorThreadOut,
    dependencies=[Depends(require_feature("aiTutor"))],
)
async def lesson_tutor(
    lesson_id: str,
    body: TutorThreadCreateIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """Thin wrapper: start a lesson-scoped tutor thread (lesson-engine contract)."""
    return await LessonTutorService(session, student.id, lesson_id, body).process()
