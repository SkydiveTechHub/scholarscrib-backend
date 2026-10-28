"""Repositories for progress, mastery, and achievements."""

from datetime import datetime

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import cuid
from app.core.timeutil import lagos_day_start
from app.database.models import (
    Achievement,
    Assessment,
    AssessmentAttempt,
    DailyQuestSet,
    DailySession,
    LearningEvent,
    PerformanceMetric,
    QuestionResponse,
    StudentAchievement,
    StudentProgress,
    Subject,
    TopicMastery,
    XpTransaction,
)
from app.database.repositories.base import BaseRepository
from app.services.gamification.improvement import Attempt
from app.services.gamification.quests import DayCounts


class StudentProgressRepository(BaseRepository[StudentProgress]):
    model = StudentProgress

    async def for_lesson(
        self, session: AsyncSession, student_id: str, lesson_id: str
    ) -> StudentProgress | None:
        return await self.first(
            session,
            StudentProgress.student_id == student_id,
            StudentProgress.lesson_id == lesson_id,
        )

    async def completed_count(self, session: AsyncSession, student_id: str) -> int:
        return await self.count(
            session,
            StudentProgress.student_id == student_id,
            StudentProgress.status == "COMPLETED",
        )

    async def completed_for_student(
        self, session: AsyncSession, student_id: str
    ) -> list[StudentProgress]:
        return await self.list_where(
            session,
            StudentProgress.student_id == student_id,
            StudentProgress.status == "COMPLETED",
            order_by=(StudentProgress.last_accessed_at.desc(),),
        )

    async def revision_due_by_topic(
        self, session: AsyncSession, student_id: str
    ) -> dict[str, datetime]:
        """Earliest fixed-cadence revision date per topic."""
        result = await self.rows(
            session,
            select(StudentProgress.topic_id, func.min(StudentProgress.revision_due_at))
            .where(
                StudentProgress.student_id == student_id,
                StudentProgress.revision_due_at.is_not(None),
            )
            .group_by(StudentProgress.topic_id),
        )
        return {row[0]: row[1] for row in result.all() if row[0] and row[1]}

    async def latest_in_progress(
        self, session: AsyncSession, student_id: str
    ) -> StudentProgress | None:
        rows = await self.list_where(
            session,
            StudentProgress.student_id == student_id,
            StudentProgress.status == "IN_PROGRESS",
            order_by=(StudentProgress.last_accessed_at.desc(),),
            limit=1,
        )
        return rows[0] if rows else None


class PerformanceMetricRepository(BaseRepository[PerformanceMetric]):
    model = PerformanceMetric

    async def for_topic(
        self,
        session: AsyncSession,
        student_id: str,
        subject_id: str,
        topic_id: str,
    ) -> PerformanceMetric | None:
        return await self.first(
            session,
            PerformanceMetric.student_id == student_id,
            PerformanceMetric.subject_id == subject_id,
            PerformanceMetric.topic_id == topic_id,
        )

    async def pretest_passed(
        self, session: AsyncSession, student_id: str, topic_id: str
    ) -> PerformanceMetric | None:
        return await self.first(
            session,
            PerformanceMetric.student_id == student_id,
            PerformanceMetric.topic_id == topic_id,
            PerformanceMetric.pretest_passed_at.is_not(None),
        )

    async def passed_topic_ids(
        self,
        session: AsyncSession,
        student_id: str,
        topic_ids: list[str],
    ) -> list[str]:
        if not topic_ids:
            return []
        rows = await self.rows(
            session,
            select(PerformanceMetric.topic_id).where(
                PerformanceMetric.student_id == student_id,
                PerformanceMetric.topic_id.in_(topic_ids),
                PerformanceMetric.pretest_passed_at.is_not(None),
            ),
        )
        return [row[0] for row in rows.all()]


class LearningEventRepository(BaseRepository[LearningEvent]):
    model = LearningEvent

    async def recent_outcomes_by_topic(
        self,
        session: AsyncSession,
        student_id: str,
        topic_ids: list[str],
        limit: int = 8,
    ) -> dict[str, list[bool]]:
        """Each topic's last `limit` answers, newest first (True = correct)."""
        if not topic_ids:
            return {}
        ranked = (
            select(
                LearningEvent.topic_id.label("topic_id"),
                LearningEvent.correct.label("correct"),
                func.row_number()
                .over(
                    partition_by=LearningEvent.topic_id,
                    order_by=LearningEvent.seq.desc(),
                )
                .label("rank"),
            )
            .where(
                LearningEvent.student_id == student_id,
                LearningEvent.kind == "QUESTION_ANSWERED",
                LearningEvent.correct.is_not(None),
                LearningEvent.topic_id.in_(topic_ids),
            )
            .subquery()
        )
        rows = await self.rows(
            session,
            select(ranked.c.topic_id, ranked.c.correct)
            .where(ranked.c.rank <= limit)
            .order_by(ranked.c.topic_id, ranked.c.rank),
        )
        found: dict[str, list[bool]] = {}
        for topic_id, correct in rows.all():
            found.setdefault(topic_id, []).append(bool(correct))
        return found

    async def for_student(
        self,
        session: AsyncSession,
        student_id: str,
        topic_ids: list[str] | None = None,
    ) -> list[LearningEvent]:
        statement = (
            select(LearningEvent)
            .where(LearningEvent.student_id == student_id)
            .order_by(LearningEvent.seq)
        )
        if topic_ids is not None:
            statement = statement.where(LearningEvent.topic_id.in_(topic_ids))
        return await self.many(session, statement)

    async def count_kind(
        self, session: AsyncSession, student_id: str, kind: str
    ) -> int:
        return await self.count(
            session,
            LearningEvent.student_id == student_id,
            LearningEvent.kind == kind,
        )

    async def recorded_sources(
        self, session: AsyncSession, student_id: str, topic_id: str, kind: str
    ) -> set[str]:
        rows = await self.rows(
            session,
            select(LearningEvent.source_id).where(
                LearningEvent.student_id == student_id,
                LearningEvent.topic_id == topic_id,
                LearningEvent.kind == kind,
                LearningEvent.source_id.is_not(None),
            ),
        )
        return {source for (source,) in rows.all()}

    async def since(
        self, session: AsyncSession, student_id: str, moment: datetime
    ) -> list[LearningEvent]:
        return await self.many(
            session,
            select(LearningEvent)
            .where(
                LearningEvent.student_id == student_id,
                LearningEvent.occurred_at >= moment,
            )
            .order_by(LearningEvent.seq),
        )

    async def last_active(
        self, session: AsyncSession, student_ids: list[str]
    ) -> dict[str, object]:
        if not student_ids:
            return {}
        rows = (
            await self.rows(
                session,
                select(
                    LearningEvent.student_id,
                    func.max(LearningEvent.occurred_at),
                )
                .where(LearningEvent.student_id.in_(student_ids))
                .group_by(LearningEvent.student_id),
            )
        ).all()
        return {student_id: moment for student_id, moment in rows}


class TopicMasteryRepository(BaseRepository[TopicMastery]):
    model = TopicMastery

    async def for_student(
        self,
        session: AsyncSession,
        student_id: str,
        topic_ids: list[str] | None = None,
    ) -> list[TopicMastery]:
        statement = select(TopicMastery).where(TopicMastery.student_id == student_id)
        if topic_ids is not None:
            statement = statement.where(TopicMastery.topic_id.in_(topic_ids))
        return await self.many(session, statement)


class DailySessionRepository(BaseRepository[DailySession]):
    model = DailySession

    async def for_day(
        self, session: AsyncSession, student_id: str, day: str
    ) -> DailySession | None:
        return await self.first(
            session,
            DailySession.student_id == student_id,
            DailySession.day == day,
        )


class AchievementRepository(BaseRepository[Achievement]):
    model = Achievement


class StudentAchievementRepository(BaseRepository[StudentAchievement]):
    model = StudentAchievement

    async def for_student(
        self, session: AsyncSession, student_id: str
    ) -> list[StudentAchievement]:
        return await self.list_where(
            session, StudentAchievement.student_id == student_id
        )

    async def earned_ids(self, session: AsyncSession, student_id: str) -> list[str]:
        return list(
            (
                await session.scalars(
                    select(StudentAchievement.achievement_id).where(
                        StudentAchievement.student_id == student_id
                    )
                )
            ).all()
        )

    async def count_for_student(self, session: AsyncSession, student_id: str) -> int:
        return await self.count(session, StudentAchievement.student_id == student_id)

    async def recent_with_titles(
        self, session: AsyncSession, student_id: str, *, limit: int = 3
    ) -> list[tuple[StudentAchievement, Achievement]]:
        result = await self.rows(
            session,
            select(StudentAchievement, Achievement)
            .join(Achievement, Achievement.id == StudentAchievement.achievement_id)
            .where(StudentAchievement.student_id == student_id)
            .order_by(StudentAchievement.earned_at.desc())
            .limit(limit),
        )
        return [(row[0], row[1]) for row in result.all()]


progress_repository = StudentProgressRepository()
metrics_repository = PerformanceMetricRepository()
learning_events_repository = LearningEventRepository()
topic_mastery_repository = TopicMasteryRepository()
daily_sessions_repository = DailySessionRepository()
achievements_repository = AchievementRepository()
student_achievements_repository = StudentAchievementRepository()


class XpRepository(BaseRepository[XpTransaction]):
    model = XpTransaction

    async def award(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        event: str,
        source_id: str,
        amount: int,
        reason: str,
        key: str,
    ) -> bool:
        """Insert one ledger row; False when the key was already paid."""
        row_id = await session.scalar(
            pg_insert(XpTransaction)
            .values(
                id=cuid(),
                student_id=student_id,
                amount=amount,
                event=event,
                reason=reason,
                source_id=source_id,
                idempotency_key=key,
            )
            .on_conflict_do_nothing(index_elements=[XpTransaction.idempotency_key])
            .returning(XpTransaction.id)
        )
        return row_id is not None

    async def total(self, session: AsyncSession, student_id: str) -> int:
        return int(
            await session.scalar(
                select(func.coalesce(func.sum(XpTransaction.amount), 0)).where(
                    XpTransaction.student_id == student_id
                )
            )
            or 0
        )

    async def count_since(
        self, session: AsyncSession, student_id: str, event: str, since: datetime
    ) -> int:
        return await self.count(
            session,
            XpTransaction.student_id == student_id,
            XpTransaction.event == event,
            XpTransaction.occurred_at >= since,
        )

    async def recent(
        self, session: AsyncSession, student_id: str, limit: int = 20
    ) -> list[XpTransaction]:
        return list(
            await session.scalars(
                select(XpTransaction)
                .where(XpTransaction.student_id == student_id)
                .order_by(XpTransaction.occurred_at.desc(), XpTransaction.id.desc())
                .limit(limit)
            )
        )

    async def attempt_facts(
        self, session: AsyncSession, student_id: str, attempt_id: str
    ) -> tuple[bool, int, str | None]:
        """(completed, questions answered, topic of a lesson this attempt passed)."""
        completed = (
            await session.scalar(
                select(AssessmentAttempt.id).where(
                    AssessmentAttempt.id == attempt_id,
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.status == "COMPLETED",
                )
            )
            is not None
        )
        answered = int(
            await session.scalar(
                select(func.count())
                .select_from(QuestionResponse)
                .where(QuestionResponse.attempt_id == attempt_id)
            )
            or 0
        )
        lesson_topic = await session.scalar(
            select(LearningEvent.topic_id)
            .where(
                LearningEvent.student_id == student_id,
                LearningEvent.kind == "LESSON_COMPLETED",
                LearningEvent.source_id == attempt_id,
            )
            .limit(1)
        )
        return completed, answered, lesson_topic

    async def touched_topic_ids(
        self, session: AsyncSession, student_id: str, attempt_id: str
    ) -> list[str]:
        """Topics with learning events recorded since this attempt began."""
        started = await session.scalar(
            select(AssessmentAttempt.started_at).where(
                AssessmentAttempt.id == attempt_id,
                AssessmentAttempt.student_id == student_id,
            )
        )
        if started is None:
            return []
        return list(
            await session.scalars(
                select(LearningEvent.topic_id)
                .where(
                    LearningEvent.student_id == student_id,
                    LearningEvent.occurred_at >= started,
                    LearningEvent.topic_id.is_not(None),
                )
                .distinct()
            )
        )

    async def count_event(
        self, session: AsyncSession, student_id: str, event: str
    ) -> int:
        return await self.count(
            session,
            XpTransaction.student_id == student_id,
            XpTransaction.event == event,
        )

    async def corrected_questions(
        self,
        session: AsyncSession,
        student_id: str,
        attempt_id: str,
        before: datetime,
    ) -> list[str]:
        """Questions answered correctly in this attempt that were missed earlier.

        The earlier miss must predate `before` (the start of today), so a
        wrong answer followed at once by a right one earns nothing.
        """
        question_id = func.coalesce(
            QuestionResponse.question_id, QuestionResponse.coverage_question_id
        )
        missed_earlier = (
            select(LearningEvent.seq)
            .where(
                LearningEvent.student_id == student_id,
                LearningEvent.kind == "QUESTION_ANSWERED",
                LearningEvent.source_id == question_id,
                LearningEvent.correct.is_(False),
                LearningEvent.occurred_at < before,
            )
            .exists()
        )
        return list(
            await session.scalars(
                select(question_id)
                .join(
                    AssessmentAttempt,
                    AssessmentAttempt.id == QuestionResponse.attempt_id,
                )
                .where(
                    QuestionResponse.attempt_id == attempt_id,
                    AssessmentAttempt.student_id == student_id,
                    QuestionResponse.is_correct.is_(True),
                    missed_earlier,
                )
            )
        )

    async def _history(self, session: AsyncSession, *criteria) -> list[Attempt]:
        answered = (
            select(func.count())
            .where(QuestionResponse.attempt_id == AssessmentAttempt.id)
            .scalar_subquery()
        )
        rows = await session.execute(
            select(AssessmentAttempt.percentage, answered)
            .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
            .where(
                AssessmentAttempt.status == "COMPLETED",
                AssessmentAttempt.percentage.is_not(None),
                AssessmentAttempt.completed_at.is_not(None),
                *criteria,
            )
            .order_by(AssessmentAttempt.completed_at)
        )
        return [Attempt(float(pct), int(n or 0)) for pct, n in rows.all()]

    async def mock_history(
        self, session: AsyncSession, student_id: str
    ) -> list[Attempt]:
        """A student's mock and CBT attempts, oldest first."""
        return await self._history(
            session,
            AssessmentAttempt.student_id == student_id,
            Assessment.assessment_type.in_(("MOCK_EXAM", "CBT_PRACTICE")),
        )

    async def comparable_history(
        self, session: AsyncSession, student_id: str, attempt_id: str
    ) -> list[Attempt]:
        """Completed attempts of the same subject and kind up to this one."""
        current = (
            await session.execute(
                select(
                    Assessment.subject_id,
                    Assessment.assessment_type,
                    AssessmentAttempt.completed_at,
                )
                .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
                .where(
                    AssessmentAttempt.id == attempt_id,
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.status == "COMPLETED",
                    AssessmentAttempt.completed_at.is_not(None),
                )
            )
        ).first()
        if current is None:
            return []
        subject_id, kind, completed_at = current
        return await self._history(
            session,
            AssessmentAttempt.student_id == student_id,
            Assessment.assessment_type == kind,
            Assessment.subject_id == subject_id
            if subject_id is not None
            else Assessment.subject_id.is_(None),
            AssessmentAttempt.completed_at <= completed_at,
        )


xp_repository = XpRepository()


class QuestRepository(XpRepository):
    """Reads and writes behind daily quests. Awards reuse the XP ledger."""

    async def quests_for_day(
        self, session: AsyncSession, student_id: str, day: str
    ) -> list[dict] | None:
        row = await session.scalar(
            select(DailyQuestSet).where(
                DailyQuestSet.student_id == student_id, DailyQuestSet.day == day
            )
        )
        return list(row.quests) if row is not None else None

    async def save_quests(
        self, session: AsyncSession, student_id: str, day: str, quests: list[dict]
    ) -> None:
        """First writer wins; a concurrent second writer is ignored."""
        await session.execute(
            pg_insert(DailyQuestSet)
            .values(id=cuid(), student_id=student_id, day=day, quests=quests)
            .on_conflict_do_nothing(
                index_elements=[DailyQuestSet.student_id, DailyQuestSet.day]
            )
        )

    async def day_counts(
        self, session: AsyncSession, student_id: str, since: datetime
    ) -> DayCounts:
        rows = (
            await session.execute(
                select(
                    LearningEvent.kind,
                    LearningEvent.subject_id,
                    func.count(),
                )
                .where(
                    LearningEvent.student_id == student_id,
                    LearningEvent.occurred_at >= since,
                    LearningEvent.kind.in_(("QUESTION_ANSWERED", "CARD_REVIEWED")),
                )
                .group_by(LearningEvent.kind, LearningEvent.subject_id)
            )
        ).all()
        by_subject: dict[str, int] = {}
        questions = cards = 0
        for kind, subject_id, count in rows:
            if kind == "QUESTION_ANSWERED":
                questions += count
                if subject_id:
                    by_subject[subject_id] = by_subject.get(subject_id, 0) + count
            else:
                cards += count
        sets = int(
            await session.scalar(
                select(func.count())
                .select_from(AssessmentAttempt)
                .where(
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.status == "COMPLETED",
                    AssessmentAttempt.completed_at >= since,
                )
            )
            or 0
        )
        return DayCounts(questions, by_subject, cards, sets)

    async def recent_focus_subject(
        self, session: AsyncSession, student_id: str, since: datetime
    ) -> tuple[str, str] | None:
        """The active subject the student most recently answered questions in."""
        row = (
            await session.execute(
                select(Subject.id, Subject.name)
                .join(LearningEvent, LearningEvent.subject_id == Subject.id)
                .where(
                    LearningEvent.student_id == student_id,
                    LearningEvent.kind == "QUESTION_ANSWERED",
                    LearningEvent.occurred_at >= since,
                    Subject.is_active.is_(True),
                )
                .order_by(LearningEvent.seq.desc())
                .limit(1)
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def due_card_total(
        self, session: AsyncSession, student_id: str, now: datetime
    ) -> int:
        from app.database.repositories.flashcard import reviews_repository

        due = await reviews_repository.due_counts_by_topic(session, student_id, now)
        return sum(due.values())


quest_repository = QuestRepository()


def _lagos_day(column):
    """The Lagos civil day of a timestamp, as YYYY-MM-DD."""
    # Inline literals, not bind parameters: asyncpg numbers every bind, so the
    # same expression in SELECT and GROUP BY would no longer compare equal.
    return func.to_char(
        func.timezone(literal_column("'Africa/Lagos'"), column),
        literal_column("'YYYY-MM-DD'"),
    )


class StreakRepository:
    async def study_day_inputs(
        self, session: AsyncSession, student_id: str, since_day: str
    ) -> tuple[set[str], dict[str, int]]:
        """Lagos days with a completed attempt, and card reviews per Lagos day.

        Grouped in SQL, so a busy student's history is never truncated.
        """
        since = lagos_day_start(since_day)
        attempt_day = _lagos_day(AssessmentAttempt.completed_at)
        attempt_days = set(
            await session.scalars(
                select(attempt_day)
                .where(
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.status == "COMPLETED",
                    AssessmentAttempt.completed_at >= since,
                )
                .group_by(attempt_day)
            )
        )
        card_day = _lagos_day(LearningEvent.occurred_at)
        rows = await session.execute(
            select(card_day, func.count())
            .where(
                LearningEvent.student_id == student_id,
                LearningEvent.kind == "CARD_REVIEWED",
                LearningEvent.occurred_at >= since,
            )
            .group_by(card_day)
        )
        return attempt_days, {day: int(count) for day, count in rows.all()}


streak_repository = StreakRepository()
