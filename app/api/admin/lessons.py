from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.ai.rag.ingest import schedule_index
from app.api.deps import require_admin
from app.api.responses import (
    LessonBrowseOut,
    LessonImportOut,
    LessonTopicOut,
    LessonTreeOut,
)
from app.api.schemas import LessonImportIn
from app.database.db import AnSession
from app.database.models import Admin
from app.domain import ClassLevel, Term
from app.services.console import (
    GetLessonBrowseService,
    GetLessonForTopicService,
    GetLessonTreeService,
    ImportLessonService,
)

router = APIRouter(prefix="/lessons", tags=["Admin / Lessons"])
tree_router = APIRouter(prefix="/lesson-tree", tags=["Admin / Lessons"])
browse_router = APIRouter(prefix="/lesson-browse", tags=["Admin / Lessons"])


@tree_router.get("", response_model=LessonTreeOut)
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
    background: BackgroundTasks,
):
    result = await ImportLessonService(session, admin.id, body).process()
    lesson_id = result.get("lessonId")
    if lesson_id:
        schedule_index(background, "lesson", lesson_id)
    return result


@browse_router.get("", response_model=LessonBrowseOut)
async def lesson_browse(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    subjectId: str | None = None,
    classLevel: ClassLevel | None = None,
    term: Term | None = None,
):
    return await GetLessonBrowseService(session, subjectId, classLevel, term).process()
