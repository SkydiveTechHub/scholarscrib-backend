from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from redis_fastapi import AsyncRedisDep, cache

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import (
    CoverageOut,
    DiscoveryOut,
    ExplanationOut,
    PapersOut,
    QuestionPageOut,
    RecordedOut,
    TopicQuizQuestionsOut,
)
from app.api.schemas import ExamType, TopicAnswersIn
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.database.repositories.curriculum import subjects_repository
from app.services.catalogue import ListPastPapersService
from app.services.learning import RecordTopicAnswersService
from app.services.provider import (
    DiscoveryResource,
    GetQuestionExplanationService,
    ProviderCoverageService,
    ProviderDiscoveryService,
    SearchProviderQuestionsService,
    TopicQuizQuestionsService,
)

router = APIRouter(prefix="/questions", tags=["Student / Questions"])

SubjectKey = Annotated[
    str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
]
QuestionKey = SubjectKey


QUESTIONS_CACHE_TTL = 24 * 60 * 60
EXPLANATION_CACHE_TTL = 7 * 24 * 60 * 60


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
    return f"{prefix}:{{{eviction_group}}}:questions:v2:{normalized}"


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
    redis: AsyncRedisDep,
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
        redis=redis,
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


@router.get("/topic-quiz", response_model=TopicQuizQuestionsOut)
async def topic_quiz_questions(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    subjectId: str,
    topic: str,
    examType: ExamType = "WAEC",
    limit: int = 10,
    random: bool = True,
):
    """A topic's quick-quiz questions, answers included.

    Not cached, unlike the listing above: every draw is meant to differ.
    Falls back to JAMB when the requested exam has nothing for the topic.
    """
    hit(f"topic-quiz:{student.id}", 20, 60)
    return await TopicQuizQuestionsService(
        session,
        subject_key=subjectId,
        topic_key=topic,
        exam_type=examType,
        limit=_page_limit(limit),
        random=random,
    ).process()


@router.post("/topic-answers", response_model=RecordedOut)
async def record_topic_answers(
    body: TopicAnswersIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """Records a student's answers to a topic's practice or quick-quiz questions
    as mastery evidence."""
    hit(f"topic-answers:{student.id}", 60, 60)
    return await RecordTopicAnswersService(session, student.id, body).process()


@router.get(
    "/{question_id}/explanation",
    response_model=ExplanationOut,
    dependencies=[
        Depends(require_student),
        Depends(cache(ttl=EXPLANATION_CACHE_TTL, eviction_group="explanations")),
    ],
)
async def question_explanation(
    question_id: QuestionKey,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"explain:{student.id}", 20, 60)
    return await GetQuestionExplanationService(session, question_id).process()


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
