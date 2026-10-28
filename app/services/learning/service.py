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
    xp_repository,
)
from app.database.repositories.question import questions_repository
from app.domain import can
from app.services.assessments.grading import coarse_grade
from app.services.auth.rules import current_streak
from app.services.cloudinary_delivery import file_link, is_authenticated_url
from app.services.learning.achievement_progress import (
    category_for,
    progress_for,
    target_for,
)
from app.services.learning.checkpoint import sanitise_checkpoint
from app.services.learning.evidence import PRETEST_PASS
from app.services.learning.library_shelf import library_shelf
from app.services.learning.prediction_log import AnsweredQuestion, record_predictions

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
        # A client reports progress, never completion: COMPLETED is set only by
        # passing the practice exit (see services/practice_exit.py), and the
        # mastery score is derived from those attempts, so neither is taken here.
        incoming_status = self.body.status or row.status
        if incoming_status == "COMPLETED":
            incoming_status = "IN_PROGRESS"
        if STATUS_RANK.get(incoming_status, 0) >= STATUS_RANK.get(row.status, 0):
            row.status = incoming_status
        if "completionPercent" in fields and self.body.completionPercent is not None:
            ceiling = 100 if row.status == "COMPLETED" else 99
            row.completion_percent = max(
                row.completion_percent or 0,
                min(self.body.completionPercent, ceiling),
            )
        if self.body.checkpointData:
            merged = dict(row.checkpoint_data or {})
            merged.update(sanitise_checkpoint(self.body.checkpointData))
            row.checkpoint_data = merged
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
        if not self.subject_id:
            subjects = await subjects_repository.active_ordered(self.session)
            counts = await subject_resources_repository.counts_by_subject(self.session)
            return {"subjects": library_shelf(subjects, counts, self.track)}
        premium = can(self.tier, "premiumLibrary")
        rows = await subject_resources_repository.for_subject(
            self.session, self.subject_id
        )
        resources = []
        for row in rows:
            locked = not row.is_free and not premium
            url, expires_at = row.url, None
            if is_authenticated_url(row.url):
                url, expires_at = file_link(row.id)
            resources.append(
                {
                    "id": row.id,
                    "subjectId": row.subject_id,
                    "title": row.title,
                    "description": row.description,
                    "resourceType": row.resource_type,
                    "url": "" if locked else url,
                    "expiresAt": None if locked else expires_at,
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
        measure = AchievementMeasure(self.session, self.student_id)
        rows = []
        for item in catalogue:
            target = target_for(item.criteria_type, item.criteria_value)
            if item.id in earned:
                progress = target
            else:
                progress = progress_for(
                    item.criteria_type,
                    await measure.current(item.criteria_type),
                    item.criteria_value,
                )
            rows.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "description": item.description,
                    "criteriaType": item.criteria_type,
                    "criteriaValue": item.criteria_value,
                    "iconUrl": item.icon_url,
                    "category": category_for(item.criteria_type),
                    "earned": item.id in earned,
                    "earnedAt": (
                        earned[item.id].isoformat() if item.id in earned else None
                    ),
                    "progress": progress,
                    "target": target,
                }
            )
        return {"achievements": rows, "earned": len(earned)}


class AwardAchievementsService:
    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id
        self.measure = AchievementMeasure(session, student_id)

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
        raw = await self.measure.current(criteria)
        return progress_for(criteria, raw, value) >= target_for(criteria, value)


class AchievementMeasure:
    """The student's current figure for each achievement criterion."""

    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id
        self._cache: dict[str, int] = {}

    async def current(self, criteria: str) -> int:
        if criteria not in self._cache:
            self._cache[criteria] = await self._measure(criteria)
        return self._cache[criteria]

    async def _measure(self, criteria: str) -> int:
        if criteria == "questions_answered":
            return await responses_repository.answered_count(
                self.session, self.student_id
            )
        if criteria == "perfect_score":
            return await attempts_repository.perfect_scores(
                self.session, self.student_id
            )
        if criteria == "lessons_completed":
            return await progress_repository.completed_count(
                self.session, self.student_id
            )
        if criteria == "mock_score_70":
            return await attempts_repository.mock_scores_at_least(
                self.session, self.student_id, 70
            )
        if criteria == "streak_days":
            from app.services.gamification.service import study_days_for

            days = await study_days_for(self.session, self.student_id)
            return current_streak(days, lagos_day_key())
        if criteria == "questions_corrected":
            return await xp_repository.count_event(
                self.session, self.student_id, "answer_corrected"
            )
        if criteria == "mock_improvement":
            from app.services.gamification.improvement import ever_improved

            history = await xp_repository.mock_history(self.session, self.student_id)
            return 1 if ever_improved(history) else 0
        if criteria == "cards_reviewed":
            return await learning_events_repository.count_kind(
                self.session, self.student_id, "CARD_REVIEWED"
            )
        if criteria == "subject_mastery":
            return await self._strong_subjects()
        return 0

    async def _strong_subjects(self) -> int:
        from app.services.learning.mastery_store import GetTopicMasteryService

        states = await GetTopicMasteryService(self.session, self.student_id).process()
        if not states:
            return 0
        topic_rows = await topics_repository.for_ids(self.session, list(states))
        return len(
            {
                topic.subject_id
                for topic in topic_rows
                if states[topic.id].level == "STRONG"
            }
        )


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


def _bank_answer_correct(item, question) -> bool:
    return (
        str(item.selectedAnswer) == str(question.correct_answer)
        if item.selectedAnswer is not None
        else bool(item.firstTry)
    )


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

        now = utcnow()
        first_answers = {}
        for item in self.body.answers:
            first_answers.setdefault(item.questionId, item)
        answered = []
        for item in first_answers.values():
            question = bank.get(item.questionId)
            if question is None:
                continue
            answered.append(
                AnsweredQuestion(
                    topic_id=topic.id,
                    subject_id=subject.id,
                    question_id=question.id,
                    difficulty=question.difficulty,
                    correct=(
                        None
                        if item.selectedAnswer is None and item.firstTry is None
                        else _bank_answer_correct(item, question)
                    ),
                    occurred_at=now,
                )
            )
        # Before any event below exists, so an answer cannot predict itself.
        await record_predictions(
            self.session, self.student_id, "LESSON_CHECK", answered
        )

        recorded = 0
        seen: set[str] = set()
        for item in self.body.answers:
            if item.questionId in seen:
                continue
            seen.add(item.questionId)
            question = bank.get(item.questionId)
            if question is not None:
                correct = _bank_answer_correct(item, question)
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
