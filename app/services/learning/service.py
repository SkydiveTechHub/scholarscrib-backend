from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ProgressIn, TopicAnswersIn
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import lagos_day_key, utcnow
from app.database.models import (
    LearningEvent,
    PerformanceMetric,
    Question,
    StudentAchievement,
    StudentProgress,
)
from app.database.repositories.assessment import (
    attempts_repository,
    responses_repository,
)
from app.database.repositories.curriculum import (
    lessons_repository,
    subject_resources_repository,
    subjects_repository,
    subtopics_repository,
    topics_repository,
)
from app.database.repositories.learning import (
    achievements_repository,
    learning_events_repository,
    metrics_repository,
    progress_repository,
    student_achievements_repository,
)
from app.database.repositories.question import questions_repository
from app.domain import can
from app.services.assessments.grading import coarse_grade
from app.services.auth.rules import current_streak
from app.services.learning.evidence import PRETEST_PASS

STATUS_RANK = {"NOT_STARTED": 0, "IN_PROGRESS": 1, "COMPLETED": 2}


def _check_score(result: dict) -> float:
    """Right first time 1.0, right after a retry 0.5, never right 0.0."""
    if result.get("correct") is not True:
        return 0.0
    return 1.0 if result.get("attempts", 1) == 1 else 0.5


class SaveLessonProgressService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        lesson_id: str,
        body: ProgressIn,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.lesson_id = lesson_id
        self.body = body

    async def process(self) -> dict:
        lesson = await lessons_repository.by_id(self.session, self.lesson_id)
        if lesson is None:
            raise ApiError(404, "Lesson not found")
        subtopic = await subtopics_repository.by_id(self.session, lesson.subtopic_id)
        topic = (
            await topics_repository.by_id(self.session, subtopic.topic_id)
            if subtopic
            else None
        )
        if topic is None:
            raise ApiError(404, "Lesson not found")
        row = await self._row(topic, lesson)
        settled_before = set((row.checkpoint_data or {}).get("checks") or {})
        self._apply(row)
        row.last_accessed_at = utcnow()
        await self._record_check_events(row, topic, settled_before)
        await self.session.flush()
        return {
            "progress": {
                "status": row.status,
                "completionPercent": row.completion_percent,
                "masteryScore": row.mastery_score,
                "checkpointData": row.checkpoint_data,
            }
        }

    async def _record_check_events(
        self, row: StudentProgress, topic, settled_before: set[str]
    ) -> None:
        """One mastery event per knowledge-check card, the first time it settles.

        Reading a card proves nothing, so only checks count. The player re-sends
        the whole checkpoint on every change; ids already settled are skipped so
        a card is never counted twice.
        """
        checks = (row.checkpoint_data or {}).get("checks") or {}
        now = utcnow()
        for block_id, result in checks.items():
            if block_id in settled_before or not isinstance(result, dict):
                continue
            await learning_events_repository.add(
                self.session,
                LearningEvent(
                    student_id=self.student_id,
                    subject_id=topic.subject_id,
                    topic_id=topic.id,
                    kind="LESSON_BLOCK_COMPLETED",
                    score=_check_score(result),
                    source_id=block_id,
                    occurred_at=now,
                ),
            )

    async def _row(self, topic, lesson) -> StudentProgress:
        row = await progress_repository.for_lesson(
            self.session, self.student_id, self.lesson_id
        )
        if row is None:
            row = StudentProgress(
                id=cuid(),
                student_id=self.student_id,
                subject_id=topic.subject_id,
                topic_id=topic.id,
                lesson_id=lesson.id,
            )
            await progress_repository.add(self.session, row)
        return row

    def _apply(self, row: StudentProgress) -> None:
        fields = self.body.model_fields_set
        incoming_status = self.body.status or row.status
        if STATUS_RANK.get(incoming_status, 0) >= STATUS_RANK.get(row.status, 0):
            row.status = incoming_status
        if "completionPercent" in fields and self.body.completionPercent is not None:
            row.completion_percent = max(
                row.completion_percent or 0,
                self.body.completionPercent,
            )
        if self.body.checkpointData:
            merged = dict(row.checkpoint_data or {})
            merged.update(self.body.checkpointData)
            row.checkpoint_data = merged
        if "masteryScore" in fields and self.body.masteryScore is not None:
            row.mastery_score = self.body.masteryScore
        if "timeSpentMinutes" in fields and self.body.timeSpentMinutes is not None:
            row.time_spent_minutes = (row.time_spent_minutes or 0) + (
                self.body.timeSpentMinutes
            )


class GetLibraryService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        tier: str,
        track: str | None,
        subject_id: str | None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.tier = tier
        self.track = track
        self.subject_id = subject_id

    async def process(self) -> dict:
        premium = can(self.tier, "premiumLibrary")
        if self.subject_id:
            rows = await subject_resources_repository.for_subject(
                self.session, self.subject_id
            )
        else:
            rows = await subject_resources_repository.visible(self.session, self.track)
        resources = []
        for row in rows:
            locked = not row.is_free and not premium
            resources.append(
                {
                    "id": row.id,
                    "subjectId": row.subject_id,
                    "title": row.title,
                    "description": row.description,
                    "resourceType": row.resource_type,
                    "url": "" if locked else row.url,
                    "locked": locked,
                    "isFree": row.is_free,
                    "author": row.author,
                }
            )
        return {"resources": resources}


class GetAchievementsService:
    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id

    async def process(self) -> dict:
        catalogue = await achievements_repository.all(self.session) or []
        earned_rows = await student_achievements_repository.for_student(
            self.session, self.student_id
        )
        earned = {row.achievement_id: row.earned_at for row in earned_rows}
        return {
            "achievements": [
                {
                    "id": item.id,
                    "title": item.title,
                    "description": item.description,
                    "criteriaType": item.criteria_type,
                    "criteriaValue": item.criteria_value,
                    "earned": item.id in earned,
                    "earnedAt": (
                        earned[item.id].isoformat() if item.id in earned else None
                    ),
                }
                for item in catalogue
            ],
            "earned": len(earned),
        }


class AwardAchievementsService:
    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id

    async def process(self) -> dict:
        catalogue = await achievements_repository.all(self.session) or []
        earned_ids = set(
            await student_achievements_repository.earned_ids(
                self.session, self.student_id
            )
        )
        newly = []
        for item in catalogue:
            if item.id in earned_ids:
                continue
            if await self._passes(item.criteria_type, item.criteria_value):
                await student_achievements_repository.add(
                    self.session,
                    StudentAchievement(
                        id=cuid(),
                        student_id=self.student_id,
                        achievement_id=item.id,
                    ),
                )
                newly.append(item.title)
        await self.session.flush()
        return {
            "checked": True,
            "newlyEarned": newly,
            "count": len(newly),
        }

    async def _passes(self, criteria: str, value: int) -> bool:
        if criteria == "questions_answered":
            count = await responses_repository.answered_count(
                self.session, self.student_id
            )
            return count >= value
        if criteria == "perfect_score":
            found = await attempts_repository.perfect_scores(
                self.session, self.student_id
            )
            return found > 0
        if criteria == "lessons_completed":
            count = await progress_repository.completed_count(
                self.session, self.student_id
            )
            return count >= value
        if criteria == "mock_score_70":
            found = await attempts_repository.mock_scores_at_least(
                self.session, self.student_id, 70
            )
            return found > 0
        if criteria == "streak_days":
            rows = await attempts_repository.completed_at(self.session, self.student_id)
            days = {lagos_day_key(moment) for moment in rows if moment}
            return current_streak(days, lagos_day_key()) >= value
        if criteria == "subject_mastery":
            return await self._subject_mastery(value)
        return False

    async def _subject_mastery(self, value: int) -> bool:
        from app.services.learning.mastery_store import GetTopicMasteryService

        states = await GetTopicMasteryService(self.session, self.student_id).process()
        if not states:
            return False
        topic_rows = await topics_repository.for_ids(self.session, list(states))
        strong_subjects = {
            topic.subject_id
            for topic in topic_rows
            if states[topic.id].level == "STRONG"
        }
        return len(strong_subjects) >= value


class RecordPretestPassService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        subject_id: str,
        topic_id: str,
        percentage: float,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_id = subject_id
        self.topic_id = topic_id
        self.percentage = percentage

    async def process(self) -> bool:
        if self.percentage < PRETEST_PASS:
            return False
        metric = await metrics_repository.for_topic(
            self.session,
            self.student_id,
            self.subject_id,
            self.topic_id,
        )
        already = metric is not None and metric.pretest_passed_at is not None
        if metric is None:
            metric = PerformanceMetric(
                id=cuid(),
                student_id=self.student_id,
                subject_id=self.subject_id,
                topic_id=self.topic_id,
            )
            await metrics_repository.add(self.session, metric)
        if not already:
            metric.pretest_passed_at = utcnow()
            await learning_events_repository.add(
                self.session,
                LearningEvent(
                    student_id=self.student_id,
                    subject_id=self.subject_id,
                    topic_id=self.topic_id,
                    kind="PRETEST_PASSED",
                    score=self.percentage / 100,
                    occurred_at=utcnow(),
                ),
            )
        return already


def performance_letter(percentage: float | None) -> str | None:
    if percentage is None:
        return None
    return coarse_grade(percentage)


class RecordTopicAnswersService:
    """Turns answers to a topic's practice questions into mastery evidence.

    Two kinds of question arrive here. Bank questions (practice) are rows in the
    question table: each becomes a QUESTION_ANSWERED event. The lesson note's own
    checks (the quick quiz) are blocks of the lesson: each becomes a single
    LESSON_BLOCK_COMPLETED event, once, so retaking the quiz cannot inflate
    mastery. Marking happens here, against the stored answer, never from a
    client-supplied verdict, except for practice's `firstTry`, which the client
    alone can know because it lets a student retry until right.
    """

    def __init__(
        self, session: AsyncSession, student_id: str, body: TopicAnswersIn
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.body = body

    async def process(self) -> dict:
        subject = await subjects_repository.by_id_or_slug(
            self.session, self.body.subjectId
        )
        if subject is None:
            raise ApiError(404, "Subject not found")
        topic = await topics_repository.by_slug(
            self.session, subject.id, self.body.topic
        ) or await topics_repository.by_id(self.session, self.body.topic)
        if topic is None or topic.subject_id != subject.id:
            raise ApiError(404, "Topic not found")

        ids = list(dict.fromkeys(item.questionId for item in self.body.answers))
        bank = {
            question.id: question
            for question in await questions_repository.many(
                self.session,
                select(Question).where(
                    Question.id.in_(ids), Question.topic_id == topic.id
                ),
            )
        }
        checks = await self._lesson_checks(topic)
        recorded_checks = await learning_events_repository.recorded_sources(
            self.session, self.student_id, topic.id, "LESSON_BLOCK_COMPLETED"
        )

        recorded = 0
        seen: set[str] = set()
        now = utcnow()
        for item in self.body.answers:
            if item.questionId in seen:
                continue
            seen.add(item.questionId)
            question = bank.get(item.questionId)
            if question is not None:
                correct = (
                    str(item.selectedAnswer) == str(question.correct_answer)
                    if item.selectedAnswer is not None
                    else bool(item.firstTry)
                )
                await learning_events_repository.add(
                    self.session,
                    LearningEvent(
                        student_id=self.student_id,
                        subject_id=subject.id,
                        topic_id=topic.id,
                        kind="QUESTION_ANSWERED",
                        correct=correct,
                        difficulty=question.difficulty,
                        seconds=item.seconds,
                        source_id=question.id,
                        occurred_at=now,
                    ),
                )
                recorded += 1
                continue
            block = checks.get(item.questionId)
            if block is not None and item.questionId not in recorded_checks:
                if block.get("type") == "short":
                    # Self-marked: the student's own verdict is the only signal
                    # there is for a typed answer, so trust it as given.
                    if item.firstTry is None:
                        continue
                    score = 1.0 if item.firstTry else 0.0
                elif item.selectedAnswer is None:
                    continue
                else:
                    score = (
                        1.0
                        if str(item.selectedAnswer) == str(block.get("answer"))
                        else 0.0
                    )
                await learning_events_repository.add(
                    self.session,
                    LearningEvent(
                        student_id=self.student_id,
                        subject_id=subject.id,
                        topic_id=topic.id,
                        kind="LESSON_BLOCK_COMPLETED",
                        score=score,
                        source_id=item.questionId,
                        occurred_at=now,
                    ),
                )
                recorded += 1
        await self.session.flush()
        return {"recorded": recorded}

    async def _lesson_checks(self, topic) -> dict[str, dict]:
        lesson = await lessons_repository.latest_for_topic(self.session, topic.id)
        if lesson is None:
            return {}
        return {
            block["id"]: block
            for block in (lesson.blocks or [])
            if isinstance(block, dict)
            and block.get("type") in {"check", "short"}
            and isinstance(block.get("id"), str)
        }
