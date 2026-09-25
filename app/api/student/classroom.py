from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import (
    ClassroomSubjectsOut,
    PracticeResultOut,
    SubjectPageOut,
    TopicPageOut,
)
from app.database.db import AnSession
from app.services.pages import (
    GetClassroomSubjectsService,
    GetPracticeResultService,
    GetSubjectPageService,
    GetTopicPageService,
)

router = APIRouter(prefix="/classroom", tags=["Student / Classroom"])


@router.get("/subjects", response_model=ClassroomSubjectsOut)
async def classroom_subjects(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetClassroomSubjectsService(
        session, student.id, student.track
    ).process()


@router.get("/subjects/{slug}", response_model=SubjectPageOut)
async def classroom_subject(
    slug: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetSubjectPageService(session, student.id, slug).process()


@router.get(
    "/subjects/{slug}/topics/{topic_slug}",
    response_model=TopicPageOut,
)
async def classroom_topic(
    slug: str,
    topic_slug: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetTopicPageService(
        session, student.id, slug, topic_slug, "overview"
    ).process()


@router.get(
    "/subjects/{slug}/topics/{topic_slug}/study",
    response_model=TopicPageOut,
)
async def classroom_study(
    slug: str,
    topic_slug: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetTopicPageService(
        session, student.id, slug, topic_slug, "study"
    ).process()


@router.get(
    "/subjects/{slug}/topics/{topic_slug}/quiz",
    response_model=TopicPageOut,
)
async def classroom_quiz(
    slug: str,
    topic_slug: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetTopicPageService(
        session, student.id, slug, topic_slug, "quiz"
    ).process()


@router.get(
    "/subjects/{slug}/topics/{topic_slug}/practice",
    response_model=TopicPageOut,
)
async def classroom_practice(
    slug: str,
    topic_slug: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetTopicPageService(
        session, student.id, slug, topic_slug, "practice"
    ).process()


@router.get(
    "/subjects/{slug}/topics/{topic_slug}/practice/result",
    response_model=PracticeResultOut,
)
async def classroom_practice_result(
    slug: str,
    topic_slug: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetPracticeResultService(
        session, student.id, slug, topic_slug
    ).process()
