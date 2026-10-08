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
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.models import AssessmentAttempt, Question, Subject
from app.database.repositories.assessment import (
    assessment_questions_repository,
    assessments_repository,
    attempts_repository,
)
from app.database.repositories.curriculum import subjects_repository
from app.database.repositories.provider import provider_questions_repository
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
from app.services.provider.base import DiscoveryResource, QuestionProvider
from app.services.provider.factory import ProviderFactory
from app.services.provider.mapping import subject_slug_candidates
from app.services.provider.service import ProviderDiscoveryService

JAMB = "JAMB"
# The provider's lower-case key for the board.
PROVIDER_JAMB = "jamb"
# Pages walked per paper at most; a JAMB year is at most ~7 pages of 15.
MAX_PAPER_PAGES = 20
YEAR_COUNTS_TTL_SECONDS = 6 * 60 * 60
# Provider subject key -> (fetched at, JAMB questions per year).
_YEAR_COUNTS: dict[str, tuple[float, dict[int, int]]] = {}


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
) -> list[tuple[Subject, dict]]:
    """Our subjects the provider carries for JAMB, with the provider's record."""
    rows = await ProviderDiscoveryService(
        DiscoveryResource.SUBJECTS, provider=provider
    )._fetch()
    if not isinstance(rows, list):
        raise ApiError(503, "Question coverage is unavailable right now.")
    found: list[tuple[Subject, dict]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        if PROVIDER_JAMB not in (row.get("examTypes") or []):
            continue
        for slug in subject_slug_candidates(str(row["name"]), row):
            subject = await subjects_repository.by_slug(session, slug)
            if subject is not None and subject.id not in seen:
                seen.add(subject.id)
                found.append((subject, row))
                break
    return found


async def _jamb_year_counts(provider: QuestionProvider, key: str) -> dict[int, int]:
    """JAMB questions the provider holds per year for one subject.

    Cached for hours, not the discovery default of minutes: past papers
    barely change, and a cold catalogue costs one slow provider call per
    subject (~12s for the full list) on the page a student is waiting for.
    """
    cached = _YEAR_COUNTS.get(key)
    if cached and time.monotonic() - cached[0] < YEAR_COUNTS_TTL_SECONDS:
        return cached[1]
    counts = await _fetch_jamb_year_counts(provider, key)
    if counts:
        _YEAR_COUNTS[key] = (time.monotonic(), counts)
    return counts


async def _fetch_jamb_year_counts(
    provider: QuestionProvider, key: str
) -> dict[int, int]:
    rows = await ProviderDiscoveryService(
        DiscoveryResource.SUBJECT_YEARS, key, provider=provider
    )._fetch()
    counts: dict[int, int] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("year"), int):
            continue
        breakdown = (
            row.get("breakdown") if isinstance(row.get("breakdown"), dict) else {}
        )
        count = breakdown.get(PROVIDER_JAMB)
        if isinstance(count, int) and count > 0:
            counts[row["year"]] = count
    return counts


class GetJambCatalogueService:
    """The subjects and years a full JAMB sitting can be assembled from."""

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
        # Discovery is HTTP only (and cached), so the per-subject year lists
        # are fetched concurrently rather than one slow call after another.
        counts = await asyncio.gather(
            *(
                _jamb_year_counts(self.provider, str(row["name"]))
                for _, row in subjects
            ),
            return_exceptions=True,
        )
        entries = []
        english_entry = None
        for (subject, row), years in zip(subjects, counts, strict=True):
            need = _need(subject, english)
            sittable = (
                []
                if isinstance(years, BaseException)
                else sorted(
                    (year for year, count in years.items() if count >= need),
                    reverse=True,
                )
            )
            entry = {
                "id": subject.id,
                "name": subject.name,
                "slug": subject.slug,
                "code": subject.code,
                "providerKey": row["name"],
                "category": row.get("category"),
                "questions": need,
                "years": sittable,
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
        subject.id: row for subject, row in await _provider_subjects(session, provider)
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
    keys = {
        subject.id: str(offered[subject.id]["name"]) for subject in [english, *chosen]
    }
    return english, chosen, keys


async def _walk_paper(
    provider: QuestionProvider, key: str, year: int
) -> tuple[list[dict], bool]:
    """Every item of one paper, and whether the walk reached its end."""
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
    ) -> None:
        self.session = session
        self.subject_ids = subject_ids
        self.exam_year = exam_year
        self.provider = provider or ProviderFactory.create()

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
            # A paper already walked to its end has nothing more to give.
            if fetch.status != "SATURATED":
                pending.append(subject)
        walked = await asyncio.gather(
            *(
                _walk_paper(self.provider, keys[subject.id], self.exam_year)
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
