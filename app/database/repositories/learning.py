"""Repositories for progress, mastery, and achievements."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Achievement,
    LearningEvent,
    PerformanceMetric,
    StudentAchievement,
    StudentProgress,
    TopicMastery,
)
from app.database.repositories.base import BaseRepository


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
achievements_repository = AchievementRepository()
student_achievements_repository = StudentAchievementRepository()
