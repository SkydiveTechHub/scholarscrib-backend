from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from redis_fastapi import AsyncRedisDep

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import (
    AttemptResultOut,
    BoardsOut,
    JambOptionsOut,
    JambPrepareOut,
    MockOptionsOut,
    PastPaperPageOut,
    QuizOut,
)
from app.api.schemas import (
    GenerateQuizIn,
    JambIn,
    PastPaperIn,
    PastPaperMoreIn,
    PracticeExitIn,
    ScopedMockIn,
    SubmitIn,
)
from app.core.config import settings
from app.core.errors import ApiError, RateLimited
from app.core.logger import logger
from app.core.rate_limit import hit
from app.database.db import AnSession, get_session
from app.services.assessments import (
    ContinuePastPaperService,
    GenerateJambPaperService,
    GenerateQuizService,
    GenerateScopedMockService,
    GetAttemptResultService,
    GetBoardReadinessService,
    GetJambCatalogueService,
    GetMockOptionsService,
    StartPastPaperService,
    SubmitAttemptService,
    SyncJambPaperService,
)
from app.services.learning import AwardAchievementsService
from app.services.planner import MarkStudyPlanItemService
from app.services.provider import EnsureProviderQuestionsService
from app.services.provider.cache import AsyncRedisClient

router = APIRouter(prefix="/assessments", tags=["Student / Assessments"])


@router.post("/generate", response_model=QuizOut)
async def generate_quiz(
    body: GenerateQuizIn,
    background: BackgroundTasks,
    session: AnSession,
    redis: AsyncRedisDep,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"generate:{student.id}", 20, 60)

    async def schedule(subject, exam_type, exam_year):
        if not settings.provider_enabled:
            return False
        try:
            hit("provider:outbound", 30, 60)
        except RateLimited:
            return False
        background.add_task(_provider_job, subject.slug, exam_type, exam_year, redis)
        return True

    return await GenerateQuizService(session, student.id, body, schedule).process()


async def _provider_job(
    slug: str,
    exam_type: str,
    exam_year: int,
    redis: AsyncRedisClient,
) -> None:
    async with get_session() as session:
        try:
            await EnsureProviderQuestionsService(
                session, slug, exam_type, exam_year, redis=redis
            ).process()
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Provider question backfill failed")


@router.post("/past-paper", response_model=QuizOut)
async def start_past_paper(
    body: PastPaperIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"past-paper:{student.id}", 20, 60)
    return await StartPastPaperService(
        session,
        student.id,
        subject_key=body.subject,
        exam=body.examType,
        exam_year=body.examYear,
    ).process()


@router.post("/past-paper/{attempt_id}/more", response_model=PastPaperPageOut)
async def continue_past_paper(
    attempt_id: str,
    body: PastPaperMoreIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"past-paper:{student.id}", 20, 60)
    return await ContinuePastPaperService(
        session,
        student.id,
        attempt_id=attempt_id,
        subject_key=body.subject,
        cursor=body.cursor,
    ).process()


@router.post("/submit", response_model=AttemptResultOut)
async def submit_attempt(
    body: SubmitIn,
    background: BackgroundTasks,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    payload, created = await SubmitAttemptService(session, student.id, body).process()
    if created:
        background.add_task(
            _after_submit, student.id, payload.get("assessmentType"), body.practiceExit
        )
    return payload


async def _after_submit(
    student_id: str, assessment_type: str | None, practice_exit: PracticeExitIn | None
) -> None:
    async with get_session() as session:
        try:
            await AwardAchievementsService(session, student_id).process()
            activity = {
                "PAST_PAPER": "PAST_QUESTIONS",
                "MOCK_EXAM": "MOCK_EXAM",
                "CBT_PRACTICE": "MOCK_EXAM",
            }.get(assessment_type or "", "PRACTICE")
            if not practice_exit:
                await MarkStudyPlanItemService(session, student_id, activity).process()
            elif practice_exit:
                await MarkStudyPlanItemService(session, student_id, "LESSON").process()
            await session.commit()
        except Exception:
            await session.rollback()


@router.get("/attempts/{attempt_id}", response_model=AttemptResultOut)
async def get_attempt(
    attempt_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetAttemptResultService(session, student.id, attempt_id).process()


@router.get("/mock-exam/boards", response_model=BoardsOut)
async def mock_boards(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetBoardReadinessService(session).process()


@router.get("/mock-exam/options", response_model=MockOptionsOut)
async def mock_options(
    session: AnSession,
    examType: str,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    examType = examType.strip().upper()
    if examType not in {"WAEC", "JAMB", "NECO"}:
        raise ApiError(400, "examType must be WAEC, JAMB, or NECO")
    return await GetMockOptionsService(session, examType).process()


@router.post("/mock-exam/scoped", response_model=QuizOut)
async def scoped_mock(
    body: ScopedMockIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"scoped-mock:{student.id}", 12, 60)
    return await GenerateScopedMockService(session, student.id, body).process()


@router.get("/jamb-cbt/options", response_model=JambOptionsOut)
async def jamb_options(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetJambCatalogueService(session).process()


@router.post("/jamb-cbt/prepare", response_model=JambPrepareOut)
async def jamb_prepare(
    body: JambIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    # Synchronous on purpose: the year's four papers are pulled from the
    # provider and stored before this answers, so "ready" means ready.
    hit(f"jamb-cbt-prepare:{student.id}", 10, 60)
    return await SyncJambPaperService(session, body.subjectIds, body.examYear).process()


@router.post("/jamb-cbt/generate", response_model=QuizOut)
async def jamb_generate(
    body: JambIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"jamb-cbt:{student.id}", 6, 60)
    return await GenerateJambPaperService(
        session, student.id, body.subjectIds, body.examYear
    ).process()
