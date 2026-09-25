"""Repositories for subjects, topics, and lessons."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    CurriculumLevel,
    Lesson,
    LessonResource,
    Subject,
    SubjectResource,
    Subtopic,
    Topic,
    TopicEdge,
)
from app.database.repositories.base import BaseRepository


class SubjectRepository(BaseRepository[Subject]):
    model = Subject

    async def by_slug(self, session: AsyncSession, slug: str) -> Subject | None:
        return await self.first(session, Subject.slug == slug)

    async def by_code(self, session: AsyncSession, code: str) -> Subject | None:
        return await self.first(session, Subject.code == code)

    async def ordered(self, session: AsyncSession) -> list[Subject]:
        return await self.all_ordered(session, Subject.name)


class TopicRepository(BaseRepository[Topic]):
    model = Topic

    async def by_slug(
        self, session: AsyncSession, subject_id: str, slug: str
    ) -> Topic | None:
        return await self.first(
            session, Topic.subject_id == subject_id, Topic.slug == slug
        )

    async def for_subject(self, session: AsyncSession, subject_id: str) -> list[Topic]:
        return await self.list_where(
            session, Topic.subject_id == subject_id, order_by=(Topic.order_index,)
        )

    async def count_for_subject(self, session: AsyncSession, subject_id: str) -> int:
        return await self.count(session, Topic.subject_id == subject_id)

    async def for_ids(self, session: AsyncSession, topic_ids: list[str]) -> list[Topic]:
        if not topic_ids:
            return []
        return await self.list_where(session, Topic.id.in_(topic_ids))


class CurriculumLevelRepository(BaseRepository[CurriculumLevel]):
    model = CurriculumLevel


class TopicEdgeRepository(BaseRepository[TopicEdge]):
    model = TopicEdge

    async def for_topics(
        self, session: AsyncSession, topic_ids: list[str]
    ) -> list[TopicEdge]:
        if not topic_ids:
            return []
        return await self.list_where(session, TopicEdge.topic_id.in_(topic_ids))


class SubtopicRepository(BaseRepository[Subtopic]):
    model = Subtopic

    async def first_for_topic(
        self, session: AsyncSession, topic_id: str
    ) -> Subtopic | None:
        return await session.scalar(
            select(Subtopic)
            .where(Subtopic.topic_id == topic_id)
            .order_by(Subtopic.order_index)
        )

    async def by_topic_and_title(
        self, session: AsyncSession, topic_id: str, title: str
    ) -> Subtopic | None:
        return await self.first(
            session,
            Subtopic.topic_id == topic_id,
            Subtopic.title == title,
        )


class LessonRepository(BaseRepository[Lesson]):
    model = Lesson

    async def for_subtopic(
        self, session: AsyncSession, subtopic_id: str
    ) -> list[Lesson]:
        return await self.list_where(session, Lesson.subtopic_id == subtopic_id)

    async def counts_by_topic(self, session: AsyncSession) -> dict[str, int]:
        rows = (
            await self.rows(
                session,
                select(Subtopic.topic_id, func.count(Lesson.id))
                .join(Lesson, Lesson.subtopic_id == Subtopic.id)
                .group_by(Subtopic.topic_id),
            )
        ).all()
        return {topic_id: count for topic_id, count in rows}

    async def latest_for_topic(
        self, session: AsyncSession, topic_id: str
    ) -> Lesson | None:
        return await self.one(
            session,
            select(Lesson)
            .join(Subtopic, Subtopic.id == Lesson.subtopic_id)
            .where(Subtopic.topic_id == topic_id)
            .order_by(Lesson.updated_at.desc()),
        )

    async def canonical_ids_for_topics(
        self, session: AsyncSession, topic_ids: list[str]
    ) -> dict[str, str]:
        if not topic_ids:
            return {}
        rows = await self.rows(
            session,
            select(Subtopic.topic_id, Lesson.id)
            .join(Lesson, Lesson.subtopic_id == Subtopic.id)
            .where(Subtopic.topic_id.in_(topic_ids))
            .order_by(Lesson.updated_at.desc()),
        )
        found: dict[str, str] = {}
        for topic_id, lesson_id in rows.all():
            found.setdefault(topic_id, lesson_id)
        return found


class LessonResourceRepository(BaseRepository[LessonResource]):
    model = LessonResource


class SubjectResourceRepository(BaseRepository[SubjectResource]):
    model = SubjectResource

    async def for_subject(
        self, session: AsyncSession, subject_id: str
    ) -> list[SubjectResource]:
        return await self.list_where(
            session,
            SubjectResource.subject_id == subject_id,
            order_by=(SubjectResource.order_index,),
        )

    async def max_order_index(self, session: AsyncSession, subject_id: str) -> int:
        value = await session.scalar(
            select(func.max(SubjectResource.order_index)).where(
                SubjectResource.subject_id == subject_id
            )
        )
        return int(value or 0)

    async def visible(
        self, session: AsyncSession, track: str | None
    ) -> list[SubjectResource]:
        statement = select(SubjectResource).join(
            Subject, Subject.id == SubjectResource.subject_id
        )
        if track:
            statement = statement.where(
                (Subject.track_category == "CORE") | (Subject.track_category == track)
            )
        return list(
            (
                await session.scalars(statement.order_by(SubjectResource.order_index))
            ).all()
        )


subjects_repository = SubjectRepository()
topics_repository = TopicRepository()
curriculum_levels_repository = CurriculumLevelRepository()
topic_edges_repository = TopicEdgeRepository()
subtopics_repository = SubtopicRepository()
lessons_repository = LessonRepository()
lesson_resources_repository = LessonResourceRepository()
subject_resources_repository = SubjectResourceRepository()
