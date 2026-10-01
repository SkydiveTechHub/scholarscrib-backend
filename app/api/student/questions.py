from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from redis_fastapi import cache

from app.api.responses import CoverageOut, DiscoveryOut, PapersOut, QuestionPageOut
from app.api.schemas import ExamType
from app.database.db import AnSession
from app.database.repositories.curriculum import subjects_repository
from app.services.catalogue import ListPastPapersService
from app.services.provider import (
    DiscoveryResource,
    ProviderCoverageService,
    ProviderDiscoveryService,
    SearchProviderQuestionsService,
)

router = APIRouter(prefix="/questions", tags=["Student / Questions"])

SubjectKey = Annotated[
    str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
]


QUESTIONS_CACHE_TTL = 24 * 60 * 60


def _page_limit(limit: int) -> int:
    return min(max(limit, 1), 50)


def questions_cache_key(request: Request, eviction_group: str = "", prefix: str = ""):
    query = request.query_params
    limit = query.get("limit", "")
    parts = {
        "subject": query.get("subjectId", "").strip().lower(),
        "exam": query.get("examType", "").strip().upper(),
        "year": query.get("examYear", "").strip(),
        "limit": str(_page_limit(int(limit))) if limit.isdigit() else "20",
        "cursor": query.get("cursor", "").strip(),
    }
    normalized = ":".join(f"{key}={value}" for key, value in parts.items() if value)
    return f"{prefix}:{{{eviction_group}}}:questions:{normalized}"


@router.get(
    "",
    response_model=QuestionPageOut,
    dependencies=[
        Depends(
            cache(
                ttl=QUESTIONS_CACHE_TTL,
                eviction_group="questions",
                key_builder=questions_cache_key,
            )
        )
    ],
)
async def list_questions(
    session: AnSession,
    subjectId: str | None = None,
    examType: ExamType | None = None,
    examYear: int | None = None,
    limit: int = 20,
    cursor: str | None = None,
):
    return await SearchProviderQuestionsService(
        session,
        subject_key=subjectId,
        exam_type=examType,
        exam_year=examYear,
        limit=_page_limit(limit),
        cursor=cursor,
    ).process()
    # Database-backed listing, paused while questions come straight from ALOC.
    # Restore the topicId, difficulty and page params above when re-enabling.
    # limit = _page_limit(limit)
    # page = max(page, 1)
    # if subjectId:
    #     subjectId = await subjects_repository.resolve_id(session, subjectId)
    # rows, total = await questions_repository.list_page(
    #     session,
    #     subject_id=subjectId,
    #     topic_id=topicId,
    #     exam_type=examType,
    #     exam_year=examYear,
    #     difficulty=difficulty,
    #     page=page,
    #     limit=limit,
    # )
    # pages = max(1, (total + limit - 1) // limit)
    # return {
    #     "questions": [public_question(row, include_answers=True) for row in rows],
    #     "pagination": {
    #         "page": page,
    #         "limit": limit,
    #         "total": total,
    #         "totalPages": pages,
    #     },
    # }


@router.get("/past-papers", response_model=PapersOut)
async def past_papers(
    session: AnSession,
    examType: ExamType | None = None,
    subjectId: str | None = None,
):
    if subjectId:
        subjectId = await subjects_repository.resolve_id(session, subjectId)
    return await ListPastPapersService(session, examType, subjectId).process()


@router.get("/coverage", response_model=CoverageOut)
async def provider_coverage():
    return await ProviderCoverageService().process()


@router.get("/coverage/subjects", response_model=DiscoveryOut)
async def provider_subjects():
    return await ProviderDiscoveryService(DiscoveryResource.SUBJECTS).process()


@router.get("/coverage/subjects/{subject}", response_model=DiscoveryOut)
async def provider_subject(subject: SubjectKey):
    return await ProviderDiscoveryService(DiscoveryResource.SUBJECT, subject).process()


@router.get("/coverage/subjects/{subject}/topics", response_model=DiscoveryOut)
async def provider_subject_topics(subject: SubjectKey):
    return await ProviderDiscoveryService(
        DiscoveryResource.SUBJECT_TOPICS, subject
    ).process()


@router.get("/coverage/subjects/{subject}/years", response_model=DiscoveryOut)
async def provider_subject_years(subject: SubjectKey):
    return await ProviderDiscoveryService(
        DiscoveryResource.SUBJECT_YEARS, subject
    ).process()


@router.get("/coverage/years/{year}", response_model=DiscoveryOut)
async def provider_year_subjects(year: Annotated[int, Path(ge=1980, le=2100)]):
    return await ProviderDiscoveryService(
        DiscoveryResource.YEAR_SUBJECTS, year
    ).process()
