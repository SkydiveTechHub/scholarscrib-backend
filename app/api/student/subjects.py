from fastapi import APIRouter

from app.api.responses import SubjectsOut, TopicSummaryOut
from app.core.errors import ApiError
from app.database.db import AnSession
from app.services.catalogue import (
    GetTopicSummaryService,
    ListSubjectsService,
    visible_subjects,
)

router = APIRouter(prefix="/subjects", tags=["Student / Subjects"])


@router.get("", response_model=SubjectsOut)
async def subjects(
    session: AnSession, track: str | None = None, examType: str | None = None
):
    rows = await ListSubjectsService(session).process()
    return {"subjects": visible_subjects(rows, track, examType)}


@router.get(
    "/{subject_slug}/topics/{topic_slug}",
    response_model=TopicSummaryOut,
)
async def topic_summary(subject_slug: str, topic_slug: str, session: AnSession):
    found = await GetTopicSummaryService(session, subject_slug, topic_slug).process()
    if found is None:
        raise ApiError(404, "Subject or topic not found")
    return found
