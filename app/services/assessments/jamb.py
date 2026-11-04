"""JAMB UTME simulation built on the question provider's real past papers.

A sitting is English (60 questions) plus three chosen subjects (40 each), all
from the same year, under one 120-minute clock. What can be offered comes from
the provider's own catalogue: a subject is listed when the provider carries it
for JAMB, and a year is sittable for a subject when the provider holds at least
that subject's full complement for the year.

Picking a year syncs its four papers into our bank (`SyncJambPaperService`):
each subject's paper is walked page by page from the provider — the four
concurrently, since the provider is slow — and stored. A paper walked to its
end is marked SATURATED and never fetched again. Starting the exam then draws
each subject's questions in the order they were set, so the sitting is the
paper as it was, comprehension passages and all.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.models import AssessmentAttempt, Question, Subject
from app.database.repositories.assessment import (
    assessment_questions_repository,
    assessments_repository,
    attempts_repository,
)
from app.database.repositories.curriculum import subjects_repository
from app.database.repositories.provider import (
    fetches_repository,
    provider_questions_repository,
)
from app.domain import (
    JAMB_DURATION_MINUTES,
    JAMB_ENGLISH_CODE,
    JAMB_ENGLISH_QUESTIONS,
    JAMB_OTHER_SUBJECTS,
    JAMB_SUBJECT_QUESTIONS,
    JAMB_TOTAL_MARKS,
)
from app.services.assessments.past_paper import (
    PAST_PAPER_PAGE_SIZE,
    PastPaperIngest,
    paper_question,
)
from app.services.assessments.service import (
    _paper_questions,
    _payload,
    _persist,
    reap_stale,
)
from app.services.provider.base import (
    PaperFetchMode,
    QuestionProvider,
)
from app.services.provider.cache import (
    AsyncRedisClient,
    PaperCacheError,
    fetch_provider_paper,
    get_or_fill_paper,
    paper_cache_id,
)
from app.services.provider.factory import ProviderFactory

logger = logging.getLogger(__name__)

JAMB = "JAMB"
# The provider's lower-case key for the board.
PROVIDER_JAMB = "jamb"
# Pages walked per paper at most; a JAMB year is at most ~7 pages of 15.
MAX_PAPER_PAGES = 20


def _need(subject: Subject, english: Subject) -> int:
    return (
        JAMB_ENGLISH_QUESTIONS if subject.id == english.id else JAMB_SUBJECT_QUESTIONS
    )


async def _english(session: AsyncSession) -> Subject:
    english = await subjects_repository.by_code(session, JAMB_ENGLISH_CODE)
    if english is None:
        raise ApiError(500, "English is missing from the subject catalogue")
    return english


async def _provider_subjects(
    session: AsyncSession, provider: QuestionProvider
) -> list[tuple[Subject, str]]:
    """Our JAMB subjects the provider also carries, with the key to ask it by.

    Subjects are ours (flagged ``is_jamb``); the provider only narrows them. A
    provider that cannot say what it holds leaves the list whole, and the
    paper fetch then reports what is actually missing.
    """
    try:
        available = await provider.catalogue_subjects(PROVIDER_JAMB)
    except Exception:  # noqa: BLE001 - a provider fault must not hide the subjects
        logger.warning("Provider subject listing failed", exc_info=True)
        available = None
    english = await _english(session)
    found: list[tuple[Subject, str]] = []
    for subject in await subjects_repository.active_ordered(session):
        if not (subject.is_jamb or subject.id == english.id):
            continue
        if available is None or subject.slug in available:
            found.append((subject, subject.slug))
    return found


class GetJambCatalogueService:
    """The subjects a full JAMB sitting can be assembled from.

    Years are not listed here: they are learned per subject, only for the
    subjects a student actually picks, through the neutral catalogue endpoint.
    """

    def __init__(
        self, session: AsyncSession, provider: QuestionProvider | None = None
    ) -> None:
        self.session = session
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        if not self.provider.configured:
            raise ApiError(503, "Questions are unavailable right now.")
        english = await _english(self.session)
        subjects = await _provider_subjects(self.session, self.provider)
        entries = []
        english_entry = None
        for subject, key in subjects:
            entry = {
                "id": subject.id,
                "name": subject.name,
                "slug": subject.slug,
                "code": subject.code,
                "providerKey": key,
                "category": subject.track_category,
                "questions": _need(subject, english),
            }
            if subject.id == english.id:
                english_entry = entry
            else:
                entries.append(entry)
        entries.sort(key=lambda entry: entry["name"])
        return {
            "spec": {
                "englishQuestions": JAMB_ENGLISH_QUESTIONS,
                "subjectQuestions": JAMB_SUBJECT_QUESTIONS,
                "totalQuestions": JAMB_ENGLISH_QUESTIONS
                + JAMB_OTHER_SUBJECTS * JAMB_SUBJECT_QUESTIONS,
                "durationMinutes": JAMB_DURATION_MINUTES,
                "totalMarks": JAMB_TOTAL_MARKS,
            },
            "english": english_entry,
            "subjects": entries,
        }


async def select_subjects(
    session: AsyncSession, provider: QuestionProvider, subject_ids: list[str]
) -> tuple[Subject, list[Subject], dict[str, str]]:
    """English, the three chosen subjects, and each one's provider key."""
    if len(subject_ids) != JAMB_OTHER_SUBJECTS or len(set(subject_ids)) != len(
        subject_ids
    ):
        raise ApiError(400, "Choose exactly 3 subjects besides English")
    english = await _english(session)
    if english.id in subject_ids:
        raise ApiError(400, "English is already included. Choose 3 other subjects")
    offered = {
        subject.id: key for subject, key in await _provider_subjects(session, provider)
    }
    if english.id not in offered:
        raise ApiError(503, "English papers are unavailable right now.")
    chosen = []
    for subject_id in subject_ids:
        if subject_id not in offered:
            raise ApiError(400, "One or more subjects are not available for JAMB")
        subject = await subjects_repository.by_id(session, subject_id)
        if subject is None:
            raise ApiError(400, "One or more subjects are not available for JAMB")
        chosen.append(subject)
    keys = {subject.id: offered[subject.id] for subject in [english, *chosen]}
    return english, chosen, keys


async def _snapshot_paper(
    provider: QuestionProvider, key: str, year: int, redis: AsyncRedisClient
) -> tuple[list[dict], bool]:
    """A sampled provider's paper: its cached snapshot, drawn once and reused.

    A sample is never "the end of the paper", so it only reports complete when
    the provider said the paper does not exist.
    """

    async def load():
        return await fetch_provider_paper(
            provider, subject_slug=key, exam_type=JAMB, exam_year=year
        )

    cache_id = paper_cache_id(provider.name, key, JAMB, year)
    try:
        paper = await get_or_fill_paper(redis, cache_id=cache_id, loader=load)
    except PaperCacheError:
        return [], False
    if not paper.cacheable or (paper.failed and paper.status_code != 404):
        return [], False
    return list(paper.items), paper.complete


async def _walk_paper(
    provider: QuestionProvider,
    key: str,
    year: int,
    redis: AsyncRedisClient | None = None,
) -> tuple[list[dict], bool]:
    """Every item of one paper, and whether the walk reached its end."""
    if redis is not None and provider.paper_fetch_mode == PaperFetchMode.SAMPLED:
        return await _snapshot_paper(provider, key, year, redis)
    items: list[dict] = []
    cursor: str | None = None
    for _ in range(MAX_PAPER_PAGES):
        page = await provider.search(
            subject_slug=key,
            exam_type=JAMB,
            exam_year=year,
            limit=PAST_PAPER_PAGE_SIZE,
            cursor=cursor,
        )
        if page is None or (page.failed and page.status_code != 404):
            return items, False
        items.extend(item for item in page.items if isinstance(item, dict))
        if not page.has_more or not page.next_cursor:
            return items, True
        cursor = page.next_cursor
    return items, False


async def _coverage(
    session: AsyncSession,
    provider: QuestionProvider,
    english: Subject,
    subjects: list[Subject],
    keys: dict[str, str],
    year: int,
) -> list[dict]:
    rows = []
    for subject in subjects:
        fetch = await _ingest(session, provider, subject, keys, year).fetch_row()
        # A paper any provider has already walked to its end is read from the
        # bank, whichever provider is active now.
        banked = await fetches_repository.banked_paper(session, subject.id, JAMB, year)
        if banked is not None:
            fetch = banked
        rows.append(
            {
                "subjectId": subject.id,
                "subjectName": subject.name,
                "code": subject.code,
                "required": _need(subject, english),
                "available": await provider_questions_repository.promoted_count(
                    session, fetch.id
                ),
                "fetchId": fetch.id,
            }
        )
    return rows


def _ingest(
    session: AsyncSession,
    provider: QuestionProvider,
    subject: Subject,
    keys: dict[str, str],
    year: int,
) -> PastPaperIngest:
    return PastPaperIngest(
        session,
        provider,
        provider_subject=keys[subject.id],
        subject=subject,
        exam_type=JAMB,
        exam_year=year,
    )


def _report(year: int, coverage: list[dict]) -> dict:
    short = [row for row in coverage if row["available"] < row["required"]]
    public = [
        {key: value for key, value in row.items() if key != "fetchId"}
        for row in coverage
    ]
    return {
        "outcome": "ok" if not short else "short",
        "examYear": year,
        "ready": not short,
        "message": f"The {year} papers are ready."
        if not short
        else f"The {year} papers are short of a full sitting.",
        "coverage": public,
    }


class SyncJambPaperService:
    """Brings a year's four papers into our bank, then reports readiness."""

    def __init__(
        self,
        session: AsyncSession,
        subject_ids: list[str],
        exam_year: int,
        provider: QuestionProvider | None = None,
        redis: AsyncRedisClient | None = None,
    ) -> None:
        self.session = session
        self.subject_ids = subject_ids
        self.exam_year = exam_year
        self.provider = provider or ProviderFactory.create()
        self.redis = redis

    async def process(self) -> dict:
        if not self.provider.configured:
            raise ApiError(503, "Questions are unavailable right now.")
        english, chosen, keys = await select_subjects(
            self.session, self.provider, self.subject_ids
        )
        subjects = [english, *chosen]
        pending = []
        for subject in subjects:
            fetch = await _ingest(
                self.session, self.provider, subject, keys, self.exam_year
            ).fetch_row()
            banked = await fetches_repository.banked_paper(
                self.session, subject.id, JAMB, self.exam_year
            )
            # A paper already walked to its end (by any provider) has nothing
            # more to give.
            if fetch.status != "SATURATED" and banked is None:
                pending.append(subject)
        walked = await asyncio.gather(
            *(
                _walk_paper(self.provider, keys[subject.id], self.exam_year, self.redis)
                for subject in pending
            )
        )
        # Stored one paper at a time: the session is not safe for concurrent use.
        for subject, (items, complete) in zip(pending, walked, strict=True):
            fetch = await _ingest(
                self.session, self.provider, subject, keys, self.exam_year
            ).store_items(items)
            if complete:
                fetch.status = "SATURATED"
        await self.session.flush()
        coverage = await _coverage(
            self.session, self.provider, english, subjects, keys, self.exam_year
        )
        return _report(self.exam_year, coverage)


async def _cbt_questions(
    session: AsyncSession, paper: list[Question], subjects: list[Subject]
) -> list[dict]:
    by_id = {subject.id: subject for subject in subjects}
    items = await provider_questions_repository.payloads_for(
        session, [question.id for question in paper]
    )
    out = []
    for question in paper:
        subject = by_id.get(question.subject_id)
        if subject is None:
            subject = await subjects_repository.by_id(session, question.subject_id)
            if subject is not None:
                by_id[subject.id] = subject
        payload = paper_question(question, items.get(question.id))
        if subject is not None:
            payload["subjectName"] = subject.name
            payload["subjectCode"] = subject.code
        out.append(payload)
    return out


class GenerateJambPaperService:
    """Starts (or resumes) a sitting from papers already synced into the bank."""

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        subject_ids: list[str],
        exam_year: int,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_ids = subject_ids
        self.exam_year = exam_year
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        english, chosen, keys = await select_subjects(
            self.session, self.provider, self.subject_ids
        )
        subjects = [english, *chosen]
        coverage = await _coverage(
            self.session, self.provider, english, subjects, keys, self.exam_year
        )
        report = _report(self.exam_year, coverage)
        if not report["ready"]:
            raise ApiError(
                422,
                "This year's papers aren't ready yet. "
                "Pick the year again to load them.",
                reason="INSUFFICIENT_QUESTIONS",
                **report,
            )
        await reap_stale(self.session, self.student_id)
        resumed = await self._resume(subjects)
        if resumed is not None:
            return resumed
        drawn: list[Question] = []
        for row in coverage:
            # The paper as it was set: its first N questions, in order.
            drawn.extend(
                await provider_questions_repository.paper_questions(
                    self.session, row["fetchId"], row["required"]
                )
            )
        payload = await _persist(
            self.session,
            student_id=self.student_id,
            subject=english,
            questions=drawn,
            assessment_type="CBT_PRACTICE",
            exam_type=JAMB,
            exam_year=self.exam_year,
            title=f"JAMB UTME {self.exam_year}",
            untimed=False,
            total_marks=JAMB_TOTAL_MARKS,
            time_override=JAMB_DURATION_MINUTES,
        )
        payload["questions"] = await _cbt_questions(self.session, drawn, subjects)
        payload["subjects"] = [
            {"id": subject.id, "name": subject.name, "count": row["required"]}
            for row, subject in zip(coverage, subjects, strict=True)
        ]
        return payload

    async def _resume(self, subjects: list[Subject]) -> dict | None:
        """The student's unfinished sitting of this exact paper, if any.

        Matched on the four subjects, not just the year: a sitting of the same
        year with different subjects is a different paper.
        """
        wanted = {subject.id for subject in subjects}
        attempts: list[AssessmentAttempt] = await attempts_repository.in_progress(
            self.session, self.student_id, limit=100
        )
        for existing in attempts:
            assessment = await assessments_repository.by_id(
                self.session, existing.assessment_id
            )
            if (
                assessment is None
                or assessment.assessment_type != "CBT_PRACTICE"
                or assessment.exam_type != JAMB
                or assessment.exam_year != self.exam_year
            ):
                continue
            paper = await _paper_questions(self.session, assessment.id)
            if {question.subject_id for question in paper} != wanted:
                continue
            payload = _payload(assessment, existing, paper, resumed=True)
            payload["questions"] = await _cbt_questions(self.session, paper, subjects)
            payload["subjects"] = [
                {"id": subject.id, "name": subject.name} for subject in subjects
            ]
            return payload
        return None


class GetJambHistoryService:
    """A student's completed sittings of one exact UTME paper, by year.

    A paper is a year plus its four subjects (English and the three chosen):
    the same year with different subjects is a different paper, so only
    sittings whose questions span exactly these subjects are returned.
    """

    def __init__(
        self, session: AsyncSession, student_id: str, subject_ids: list[str]
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_ids = subject_ids

    async def process(self) -> dict:
        if len(set(self.subject_ids)) != JAMB_OTHER_SUBJECTS:
            raise ApiError(400, "Choose exactly 3 subjects besides English")
        english = await _english(self.session)
        wanted = {english.id, *self.subject_ids}
        sittings = await attempts_repository.completed_cbt_sittings(
            self.session, self.student_id
        )
        sets = await assessment_questions_repository.subject_sets(
            self.session, [assessment.id for _attempt, assessment in sittings]
        )
        by_year: dict[int, list[dict]] = {}
        for attempt, assessment in sittings:
            if sets.get(assessment.id) != wanted or assessment.exam_year is None:
                continue
            if attempt.completed_at is None:
                continue
            by_year.setdefault(assessment.exam_year, []).append(
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
