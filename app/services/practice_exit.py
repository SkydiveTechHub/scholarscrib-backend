"""The practice exit: the graded, timed test that completes a lesson.

A lesson is COMPLETED only by passing this test, and only the server decides
that. The proof that an attempt is a genuine exit attempt is a marker written
into the lesson's `StudentProgress.checkpointData` when the attempt is started,
so no client-supplied flag can stand in for it and no schema change is needed.

checkpointData layout (the client may only ever write `visited` and `checks`):
    exit      {"attemptId", "startedAt"}   the one open exit attempt, if any
    practice  [{"attemptId", "percentage", "passed", "at"}, ...]  oldest first
"""

from datetime import datetime, timedelta
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import GenerateQuizIn, PracticeExitIn
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import as_utc, utcnow
from app.database.models import LearningEvent, Lesson, StudentProgress, Topic
from app.database.repositories.assessment import (
    assessments_repository,
    attempts_repository,
)
from app.database.repositories.curriculum import (
    lessons_repository,
    subjects_repository,
    topics_repository,
)
from app.database.repositories.learning import (
    learning_events_repository,
    progress_repository,
)
from app.services.assessments import GetAttemptResultService
from app.services.assessments.service import (
    _is_stale,
    _paper_questions,
    _payload,
    _persist,
    _pick_prefer_unseen,
    _subject,
)
from app.services.pages import GetTopicPageService

EXIT_COOLDOWN = timedelta(minutes=30)
SECONDS_PER_QUESTION = 90
# Fewer than this and a pass would say nothing about the topic.
MIN_QUESTIONS = 3
RECORDS_KEPT = 20
BEST_OF = 3
FIRST_REVISION = timedelta(days=1)


# ── Pure rules ──────────────────────────────────────────────────────────────


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def retry_available_at(records: list[dict], now: datetime) -> datetime | None:
    """When a failed student may try again, or None when they may start now.

    Only the latest attempt counts: a pass clears the wait, and a fail starts
    one that runs from when that attempt was submitted.
    """
    if not records:
        return None
    last = records[-1]
    if last.get("passed"):
        return None
    submitted = _parse_at(last.get("at"))
    if submitted is None:
        return None
    until = submitted + EXIT_COOLDOWN
    return until if until > now else None


def best_recent(records: list[dict]) -> int:
    """Best percentage of the last few attempts: a bad retake never erases a pass."""
    recent = [
        float(r["percentage"])
        for r in records[-BEST_OF:]
        if isinstance(r.get("percentage"), int | float)
    ]
    return round(max(recent)) if recent else 0


def record_attempt(
    checkpoint: dict,
    *,
    attempt_id: str,
    percentage: float,
    passed: bool,
    now: datetime,
) -> dict:
    """Returns the checkpoint with this attempt recorded and the open marker cleared."""
    updated = {key: value for key, value in checkpoint.items() if key != "exit"}
    records = list(updated.get("practice") or [])
    records.append(
        {
            "attemptId": attempt_id,
            "percentage": round(percentage, 1),
            "passed": passed,
            "at": now.isoformat(),
        }
    )
    updated["practice"] = records[-RECORDS_KEPT:]
    return updated


# ── Start ───────────────────────────────────────────────────────────────────


async def _progress_row(
    session: AsyncSession, student_id: str, topic: Topic, lesson: Lesson
) -> StudentProgress:
    row = await progress_repository.for_lesson(session, student_id, lesson.id)
    if row is None:
        row = StudentProgress(
            id=cuid(),
            student_id=student_id,
            subject_id=topic.subject_id,
            topic_id=topic.id,
            lesson_id=lesson.id,
        )
        await progress_repository.add(session, row)
    return row


class StartPracticeExitService:
    def __init__(
        self, session: AsyncSession, student_id: str, body: GenerateQuizIn
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.body = body

    async def process(self) -> dict:
        if not self.body.topicSlug:
            raise ApiError(400, "A practice exit needs a topic")
        subject = await _subject(
            self.session, self.body.subjectId, self.body.subjectSlug
        )
        if subject is None:
            raise ApiError(404, "Subject not found")
        # The study view decides unlock for this student, so the exit can never
        # be sat for a lesson the student is not yet allowed to open.
        page = await GetTopicPageService(
            self.session, self.student_id, subject.slug, self.body.topicSlug, "study"
        ).process()
        lesson_view = page.get("lesson")
        if lesson_view is None:
            raise ApiError(404, "This topic has no lesson")
        if not lesson_view["unlocked"]:
            raise ApiError(403, "Finish the prerequisite topics first")
        topic = await topics_repository.by_slug(
            self.session, subject.id, self.body.topicSlug
        )
        lesson = await lessons_repository.by_id(self.session, lesson_view["id"])
        if topic is None or lesson is None:
            raise ApiError(404, "This topic has no lesson")

        row = await _progress_row(self.session, self.student_id, topic, lesson)
        checkpoint = dict(row.checkpoint_data or {})
        now = utcnow()

        wait = retry_available_at(checkpoint.get("practice") or [], now)
        if wait is not None:
            raise ApiError(
                409,
                "You can retry the practice test in a little while. "
                "Use the time to revisit the cards you missed.",
                retryAvailableAt=wait.isoformat(),
            )

        resumed = await self._resume(row, checkpoint, now)
        if resumed is not None:
            return resumed

        count = lesson.practice_count or 7
        picked = await _pick_prefer_unseen(
            self.session,
            self.student_id,
            count,
            subject_id=subject.id,
            topic_ids=[topic.id],
        )
        if len(picked) < MIN_QUESTIONS:
            raise ApiError(
                404, "There aren't enough practice questions for this topic yet"
            )
        payload = await _persist(
            self.session,
            student_id=self.student_id,
            subject=subject,
            questions=picked,
            assessment_type="TOPIC_QUIZ",
            exam_type=None,
            exam_year=None,
            title=f"{lesson.title} — Practice Exit",
            untimed=False,
            pass_mark=lesson.pass_mark_percent,
            time_override=ceil(len(picked) * SECONDS_PER_QUESTION / 60),
        )
        checkpoint["exit"] = {
            "attemptId": payload["attemptId"],
            "startedAt": now.isoformat(),
        }
        row.checkpoint_data = checkpoint
        await self.session.flush()
        return payload

    async def _resume(
        self, row: StudentProgress, checkpoint: dict, now: datetime
    ) -> dict | None:
        marker = checkpoint.get("exit")
        if not isinstance(marker, dict):
            return None
        attempt = await attempts_repository.by_id(
            self.session, str(marker.get("attemptId"))
        )
        if (
            attempt is not None
            and attempt.student_id == self.student_id
            and attempt.status == "IN_PROGRESS"
        ):
            assessment = await assessments_repository.by_id(
                self.session, attempt.assessment_id
            )
            if assessment is not None and not _is_stale(attempt, assessment, now):
                paper = await _paper_questions(self.session, assessment.id)
                return _payload(assessment, attempt, paper, resumed=True)
        # Abandoned or expired: it earns nothing, and a new one may start.
        checkpoint.pop("exit", None)
        row.checkpoint_data = checkpoint
        return None


# ── Complete ────────────────────────────────────────────────────────────────


class CompletePracticeExitService:
    """Grades a just-submitted attempt against the lesson's pass mark.

    Runs in the submit request, after the attempt is scored. An attempt that is
    not the student's open exit attempt is ignored, so the practiceExit hint in
    the submit body only chooses which lesson to look at, never what it proves.
    """

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        attempt_id: str,
        percentage: float,
        exit_in: PracticeExitIn,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id
        self.percentage = percentage
        self.exit_in = exit_in

    async def process(self) -> bool | None:
        """True when passed, False when failed, None when not an exit attempt."""
        if not self.exit_in.subjectSlug or not self.exit_in.topicSlug:
            return None
        subject = await subjects_repository.by_slug(
            self.session, self.exit_in.subjectSlug
        )
        topic = (
            await topics_repository.by_slug(
                self.session, subject.id, self.exit_in.topicSlug
            )
            if subject
            else None
        )
        lesson = (
            await lessons_repository.latest_for_topic(self.session, topic.id)
            if topic
            else None
        )
        row = (
            await progress_repository.for_lesson(
                self.session, self.student_id, lesson.id
            )
            if lesson
            else None
        )
        if topic is None or lesson is None or row is None:
            return None
        checkpoint = dict(row.checkpoint_data or {})
        marker = checkpoint.get("exit")
        if not isinstance(marker, dict) or marker.get("attemptId") != self.attempt_id:
            return None

        now = utcnow()
        passed = self.percentage >= lesson.pass_mark_percent
        checkpoint = record_attempt(
            checkpoint,
            attempt_id=self.attempt_id,
            percentage=self.percentage,
            passed=passed,
            now=now,
        )
        row.checkpoint_data = checkpoint
        row.mastery_score = best_recent(checkpoint["practice"])
        row.last_accessed_at = now
        if passed:
            row.status = "COMPLETED"
            row.completion_percent = 100
            row.revision_due_at = now + FIRST_REVISION
            await learning_events_repository.add(
                self.session,
                LearningEvent(
                    student_id=self.student_id,
                    subject_id=topic.subject_id,
                    topic_id=topic.id,
                    kind="LESSON_COMPLETED",
                    score=self.percentage / 100,
                    source_id=self.attempt_id,
                    occurred_at=now,
                ),
            )
        await self.session.flush()
        return passed


# ── Result ──────────────────────────────────────────────────────────────────


class GetPracticeResultService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        subject_slug: str,
        topic_slug: str,
        attempt_id: str | None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_slug = subject_slug
        self.topic_slug = topic_slug
        self.attempt_id = attempt_id

    async def process(self) -> dict:
        subject = await subjects_repository.active_by_slug(
            self.session, self.subject_slug
        )
        topic = (
            await topics_repository.by_slug(self.session, subject.id, self.topic_slug)
            if subject
            else None
        )
        if subject is None or topic is None:
            raise ApiError(404, "Topic not found")
        lesson = await lessons_repository.latest_for_topic(self.session, topic.id)
        row = (
            await progress_repository.for_lesson(
                self.session, self.student_id, lesson.id
            )
            if lesson
            else None
        )
        records = list((row.checkpoint_data or {}).get("practice") or []) if row else []
        record = self._pick(records)
        if lesson is None or row is None or record is None:
            return {"result": None}

        attempt = await GetAttemptResultService(
            self.session, self.student_id, record["attemptId"]
        ).process()
        missed = [
            {
                "id": item["questionId"],
                "questionText": item["questionText"],
                "options": item["options"],
                "correctAnswer": item["correctAnswer"],
                "explanation": item["explanation"],
                "selectedAnswer": item.get("selectedAnswer"),
                "topicId": item.get("topicId") or topic.id,
            }
            for item in attempt["results"]
            if not item["isCorrect"]
        ]
        retry = retry_available_at(records, utcnow()) if record is records[-1] else None
        return {
            "result": {
                "attemptId": record["attemptId"],
                "subjectId": subject.id,
                "topicId": topic.id,
                "topicTitle": topic.title,
                "percentage": record["percentage"],
                "grade": attempt["grade"],
                "score": attempt["score"],
                "totalMarks": attempt["totalMarks"],
                "completedAt": record["at"],
                "passed": bool(record["passed"]),
                "passMarkPercent": lesson.pass_mark_percent,
                "bestMastery": best_recent(records),
                "nextRevisionAt": (
                    row.revision_due_at.isoformat()
                    if record["passed"] and row.revision_due_at
                    else None
                ),
                "retryAvailableAt": retry.isoformat() if retry else None,
                "missed": missed,
            }
        }

    def _pick(self, records: list[dict]) -> dict | None:
        if not records:
            return None
        if self.attempt_id is None:
            return records[-1]
        return next((r for r in records if r.get("attemptId") == self.attempt_id), None)
