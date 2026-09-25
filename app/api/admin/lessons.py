from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import LessonImportOut, LessonTopicOut, LessonTreeOut
from app.api.schemas import LessonImportIn
from app.database.db import AnSession
from app.database.models import Admin
from app.services.console import (
    GetLessonForTopicService,
    GetLessonTreeService,
    ImportLessonService,
)

router = APIRouter(prefix="/lessons", tags=["Admin / Lessons"])


@router.get("", response_model=LessonTreeOut)
async def lesson_tree(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetLessonTreeService(session).process()


@router.get("/{topic_id}", response_model=LessonTopicOut)
async def lesson_for_topic(
    topic_id: str, session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetLessonForTopicService(session, topic_id).process()


@router.post("/import", response_model=LessonImportOut)
async def import_lesson(
    body: LessonImportIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await ImportLessonService(session, admin.id, body).process()
