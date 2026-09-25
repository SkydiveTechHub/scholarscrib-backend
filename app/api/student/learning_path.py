from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import PretestOut
from app.api.schemas import PretestIn
from app.database.db import AnSession
from app.services.assessments import GradePretestService, StartPretestService

router = APIRouter(prefix="/learning-path", tags=["Student / Learning path"])


@router.post("/topics/{topic_id}/pretest", response_model=PretestOut)
async def pretest(
    topic_id: str,
    body: PretestIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    if body.attemptId:
        return await GradePretestService(session, student.id, topic_id, body).process()
    return await StartPretestService(session, student.id, topic_id).process()
