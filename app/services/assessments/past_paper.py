"""Past papers sat from the question provider, recorded like any other attempt.

Each page is fetched from the provider on the server, stored in our bank
(ProviderQuestion keeps the raw item, Question the mapped row) and appended to
a real PAST_PAPER attempt. The browser only ever sees the questions without
their answers; grading, responses and learning events then go through the
ordinary `POST /api/assessments/submit`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    Question,
    Subject,
)
from app.database.repositories.assessment import (
    assessment_questions_repository,
    assessments_repository,
    attempts_repository,
)
from app.database.repositories.curriculum import subjects_repository
from app.services.assessments.service import (
    _is_stale,
    _paper_questions,
    _payload,
    _persist,
    reap_stale,
)
from app.services.catalogue import public_question
from app.services.provider.base import DiscoveryResource, QuestionProvider
from app.services.provider.factory import ProviderFactory
from app.services.provider.ingest import ProviderPaperIngest
from app.services.provider.mapping import exam_type_for, subject_slug_candidates
from app.services.provider.service import ProviderDiscoveryService

# Questions per provider page (the provider caps search at 15).
PAST_PAPER_PAGE_SIZE = 15
# Past papers run at a minute a question; each extra page adds its minutes.
MINUTES_PER_QUESTION = 1


class PastPaperIngest(ProviderPaperIngest):
    """Past-paper ingest with the fixed past-paper page size."""

    def __init__(
        self,
        session: AsyncSession,
        provider: QuestionProvider,
        *,
        provider_subject: str,
        subject: Subject,
        exam_type: str,
        exam_year: int,
    ) -> None:
        super().__init__(
            session,
            provider,
            provider_subject=provider_subject,
            subject=subject,
            exam_type=exam_type,
            exam_year=exam_year,
            page_limit=PAST_PAPER_PAGE_SIZE,
        )


def paper_question(question: Question, item: dict | None) -> dict:
    """The question as the exam surface gets it: no answer, context attached.

    ALOC's `section` means two things (checked against live English papers):
    with `hasPassage` it is the comprehension passage itself (its `passage`
    field stays empty); without, it is the instruction shared by a group of
    questions ("select the option that best explains ..."), which the
    question text alone does not make sense without.
    """
    payload = public_question(question, include_answers=False)
    section = item.get("section") if item else None
    if not isinstance(section, str) or not section.strip():
        return payload
    if item and item.get("hasPassage"):
        payload["hasPassage"] = True
        payload["passage"] = section
        payload["passageGroup"] = item.get("category")
    else:
        payload["instruction"] = section
    return payload


async def resolve_subject(
    session: AsyncSession, provider: QuestionProvider, key: str
) -> Subject:
    """Our Subject for an ALOC subject key, or 404.

    The key alone (plus the one known alias) is tried first; only when that
    misses is ALOC asked for its record of the subject, whose display name and
    aliases usually name our subject outright.
    """
    for slug in subject_slug_candidates(key):
        subject = await subjects_repository.by_slug(session, slug)
        if subject is not None:
            return subject
    try:
        discovered = await ProviderDiscoveryService(
            DiscoveryResource.SUBJECT, key, provider=provider
        )._fetch()
    except ApiError:
        discovered = None
    if isinstance(discovered, dict):
        for slug in subject_slug_candidates(key, discovered):
            subject = await subjects_repository.by_slug(session, slug)
            if subject is not None:
                return subject
    raise ApiError(404, "This subject isn't available for past papers yet.")


async def _subject_and_exam(
    session: AsyncSession, provider: QuestionProvider, subject_key: str, exam: str
) -> tuple[Subject, str]:
    exam_type = exam_type_for(exam)
    if exam_type is None:
        raise ApiError(400, "This exam isn't available for past papers.")
    if not provider.configured:
        raise ApiError(503, "Questions are unavailable right now.")
    return await resolve_subject(session, provider, subject_key), exam_type


class StartPastPaperService:
    """Starts a recorded past-paper attempt with the paper's first page."""

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        *,
        subject_key: str,
        exam: str,
        exam_year: int,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_key = subject_key
        self.exam = exam
        self.exam_year = exam_year
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        subject, exam_type = await _subject_and_exam(
            self.session, self.provider, self.subject_key, self.exam
        )
        await reap_stale(self.session, self.student_id)
        stored, next_cursor = await PastPaperIngest(
            self.session,
            self.provider,
            provider_subject=self.subject_key,
            subject=subject,
            exam_type=exam_type,
            exam_year=self.exam_year,
        ).page_with_questions()
        if not stored:
            raise ApiError(404, "No questions found for this paper yet.")
        questions = _unique([question for question, _item in stored])
        payload = await _persist(
            self.session,
            student_id=self.student_id,
            subject=subject,
            questions=questions,
            assessment_type="PAST_PAPER",
            exam_type=exam_type,
            exam_year=self.exam_year,
            title=f"{exam_type} {subject.name} {self.exam_year}",
            untimed=False,
            time_override=len(questions) * MINUTES_PER_QUESTION,
        )
        items = {question.id: item for question, item in stored}
        payload["questions"] = [
            paper_question(question, items.get(question.id)) for question in questions
        ]
        payload["nextCursor"] = next_cursor
        return payload


class ContinuePastPaperService:
    """Appends the paper's next page to an in-progress past-paper attempt."""

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        *,
        attempt_id: str,
        subject_key: str,
        cursor: str,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id
        self.subject_key = subject_key
        self.cursor = cursor
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        attempt, assessment = await self._load()
        subject = await resolve_subject(self.session, self.provider, self.subject_key)
        if subject.id != assessment.subject_id:
            raise ApiError(400, "That subject doesn't match this paper.")
        if assessment.exam_type is None or assessment.exam_year is None:
            raise ApiError(400, "This attempt is not a past paper.")
        on_paper = {
            question.id
            for question in await _paper_questions(self.session, assessment.id)
        }
        stored, next_cursor = await PastPaperIngest(
            self.session,
            self.provider,
            provider_subject=self.subject_key,
            subject=subject,
            exam_type=assessment.exam_type,
            exam_year=assessment.exam_year,
        ).page_with_questions(self.cursor, exclude=on_paper)
        added = [
            question
            for question in _unique([question for question, _item in stored])
            if question.id not in on_paper
        ]
        for offset, question in enumerate(added):
            await assessment_questions_repository.add(
                self.session,
                AssessmentQuestion(
                    id=cuid(),
                    assessment_id=assessment.id,
                    question_id=question.id,
                    order_index=len(on_paper) + offset,
                ),
            )
        assessment.total_marks = (assessment.total_marks or 0) + sum(
            question.marks for question in added
        )
        assessment.time_limit_minutes = (assessment.time_limit_minutes or 0) + len(
            added
        ) * MINUTES_PER_QUESTION
        await self.session.flush()
        items = {question.id: item for question, item in stored}
        body = _payload(assessment, attempt, added)
        return {
            "questions": [
                paper_question(question, items.get(question.id)) for question in added
            ],
            "nextCursor": next_cursor,
            "timeLimitMinutes": assessment.time_limit_minutes,
            "deadlineAt": body.get("deadlineAt"),
        }

    async def _load(self) -> tuple[AssessmentAttempt, Assessment]:
        attempt = await attempts_repository.by_id(self.session, self.attempt_id)
        if attempt is None or attempt.student_id != self.student_id:
            raise ApiError(404, "Attempt not found")
        assessment = await assessments_repository.by_id(
            self.session, attempt.assessment_id
        )
        if assessment is None or assessment.assessment_type != "PAST_PAPER":
            raise ApiError(404, "Attempt not found")
        if attempt.status != "IN_PROGRESS" or _is_stale(attempt, assessment, utcnow()):
            raise ApiError(409, "This attempt is no longer in progress")
        return attempt, assessment


def _unique(questions: list[Question]) -> list[Question]:
    """Drops a question the provider returned twice on one page."""
    seen: set[str] = set()
    out = []
    for question in questions:
        if question.id not in seen:
            seen.add(question.id)
            out.append(question)
    return out


class GetPastPaperHistoryService:
    """A student's completed sittings of one subject's past papers, by year."""

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        *,
        subject_key: str,
        exam: str,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_key = subject_key
        self.exam = exam
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        exam_type = exam_type_for(self.exam)
        if exam_type is None:
            raise ApiError(400, "This exam isn't available for past papers.")
        try:
            subject = await resolve_subject(
                self.session, self.provider, self.subject_key
            )
        except ApiError:
            # A subject we can't map has nothing sat against it.
            return {"years": []}
        rows = await attempts_repository.completed_past_papers(
            self.session,
            self.student_id,
            subject_id=subject.id,
            exam_type=exam_type,
        )
        by_year: dict[int, list[dict]] = {}
        for attempt, year in rows:
            if attempt.completed_at is None:
                continue
            by_year.setdefault(year, []).append(
                {
                    "attemptId": attempt.id,
                    "completedAt": attempt.completed_at.isoformat(),
                    "percentage": attempt.percentage,
                    "score": attempt.score,
                    "totalMarks": attempt.total_marks,
                }
            )
        return {
            "years": [
                {"year": year, "attempts": attempts}
                for year, attempts in sorted(by_year.items(), reverse=True)
            ]
        }
