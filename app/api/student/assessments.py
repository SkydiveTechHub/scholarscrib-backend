from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import (
    AttemptResultOut,
    BoardsOut,
    JambOptionsOut,
    JambPrepareOut,
    MockOptionsOut,
    PastPaperHistoryOut,
    PastPaperPageOut,
    QuizOut,
)
from app.api.schemas import (
    GenerateQuizIn,
    JambIn,
    PastPaperIn,
    PastPaperMoreIn,
    ScopedMockIn,
    SubmitIn,
)
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.database.db import AnSession, get_session
from app.database.repositories.assessment import (
    assessments_repository,
    attempts_repository,
)
from app.services.assessments import (
    ContinuePastPaperService,
    GenerateJambPaperService,
    GenerateQuizService,
    GenerateScopedMockService,
    GetAttemptResultService,
    GetBoardReadinessService,
    GetJambCatalogueService,
    GetJambHistoryService,
    GetMockOptionsService,
    GetPastPaperHistoryService,
    StartPastPaperService,
    SubmitAttemptService,
    SyncJambPaperService,
)
from app.services.assessments.past_paper import resolve_subject
from app.services.learning import AwardAchievementsService
from app.services.planner import MarkStudyPlanItemService
from app.services.practice_exit import (
    CompletePracticeExitService,
    StartPracticeExitService,
)
from app.services.provider import ProviderFactory, schedule_provider_bank_page
from app.services.provider.mapping import exam_type_for

router = APIRouter(prefix="/assessments", tags=["Student / Assessments"])


@router.post("/generate", response_model=QuizOut)
async def generate_quiz(
    body: GenerateQuizIn,
    background: BackgroundTasks,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"generate:{student.id}", 20, 60)

    if body.practiceExit:
        return await StartPracticeExitService(session, student.id, body).process()

    async def schedule(subject, exam_type, exam_year):
        return schedule_provider_bank_page(
            background, subject.slug, exam_type, exam_year
        )

    return await GenerateQuizService(session, student.id, body, schedule).process()


@router.post("/past-paper", response_model=QuizOut)
async def start_past_paper(
    body: PastPaperIn,
    background: BackgroundTasks,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"past-paper:{student.id}", 20, 60)
    payload = await StartPastPaperService(
        session,
        student.id,
        subject_key=body.subject,
        exam=body.examType,
        exam_year=body.examYear,
    ).process()
    try:
        subject = await resolve_subject(session, ProviderFactory.create(), body.subject)
        schedule_provider_bank_page(
            background,
            subject.slug,
            exam_type_for(body.examType) or body.examType,
            body.examYear,
        )
    except ApiError:
        pass
    return payload


@router.get("/past-paper/history", response_model=PastPaperHistoryOut)
async def past_paper_history(
    subject: str,
    examType: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetPastPaperHistoryService(
        session, student.id, subject_key=subject, exam=examType
    ).process()


@router.post("/past-paper/{attempt_id}/more", response_model=PastPaperPageOut)
async def continue_past_paper(
    attempt_id: str,
    body: PastPaperMoreIn,
    background: BackgroundTasks,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"past-paper:{student.id}", 20, 60)
    payload = await ContinuePastPaperService(
        session,
        student.id,
        attempt_id=attempt_id,
        subject_key=body.subject,
        cursor=body.cursor,
    ).process()
    try:
        subject = await resolve_subject(session, ProviderFactory.create(), body.subject)
        attempt = await attempts_repository.by_id(session, attempt_id)
        assessment = (
            await assessments_repository.by_id(session, attempt.assessment_id)
            if attempt is not None
            else None
        )
        if assessment is not None:
            schedule_provider_bank_page(
                background,
                subject.slug,
                assessment.exam_type,
                assessment.exam_year,
            )
    except ApiError:
        pass
    return payload


@router.post("/submit", response_model=AttemptResultOut)
async def submit_attempt(
    body: SubmitIn,
    background: BackgroundTasks,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    payload, created = await SubmitAttemptService(session, student.id, body).process()
    exit_passed = None
    if created and body.practiceExit:
        # Judged here, in the request, so completion commits with the attempt.
        exit_passed = await CompletePracticeExitService(
            session,
            student.id,
            body.attemptId,
            float(payload.get("percentage") or 0),
            body.practiceExit,
        ).process()
    if created:
        background.add_task(
            _after_submit, student.id, payload.get("assessmentType"), exit_passed
        )
    return payload


async def _after_submit(
    student_id: str, assessment_type: str | None, exit_passed: bool | None
) -> None:
    async with get_session() as session:
        try:
            await AwardAchievementsService(session, student_id).process()
            activity = {
                "PAST_PAPER": "PAST_QUESTIONS",
                "MOCK_EXAM": "MOCK_EXAM",
                "CBT_PRACTICE": "MOCK_EXAM",
            }.get(assessment_type or "", "PRACTICE")
            # A plan lesson is done only by passing its exit; a failed exit
            # counts for nothing, and anything else is ordinary practice.
            if exit_passed is True:
                await MarkStudyPlanItemService(session, student_id, "LESSON").process()
            elif exit_passed is None:
                await MarkStudyPlanItemService(session, student_id, activity).process()
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
    student: Annotated[StudentPrincipal, Depends(require_student)],
    examType: str | None = None,
):
    """Subjects in the student's track, with question counts per class and term.

    Without an examType the counts span every board.
    """
    if examType:
        examType = examType.strip().upper()
        if examType not in {"WAEC", "JAMB", "NECO"}:
            raise ApiError(400, "examType must be WAEC, JAMB, or NECO")
    return await GetMockOptionsService(session, examType, student.track).process()


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


@router.get("/jamb-cbt/history", response_model=PastPaperHistoryOut)
async def jamb_history(
    subjectIds: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    # Comma-separated: the three chosen subjects besides English.
    ids = [part for part in subjectIds.split(",") if part]
    return await GetJambHistoryService(session, student.id, ids).process()


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
