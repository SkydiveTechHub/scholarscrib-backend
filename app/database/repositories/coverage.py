"""Repository for the coverage question bank."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AssessmentQuestion,
    CoverageQuestion,
    CurriculumLevel,
    Subject,
    Topic,
)
from app.database.repositories.base import BaseRepository


class CoverageQuestionRepository(BaseRepository[CoverageQuestion]):
    model = CoverageQuestion

    async def duplicate_id(
        self,
        session: AsyncSession,
        *,
        subject_id: str,
        exam_type: str,
        exam_year: int | None,
        question_number: int | None,
        question_text: str,
    ) -> str | None:
        """A paper's question number identifies it; unnumbered rows match on text."""
        statement = select(CoverageQuestion.id).where(
            CoverageQuestion.subject_id == subject_id,
            CoverageQuestion.exam_type == exam_type,
            CoverageQuestion.exam_year == exam_year,
        )
        if question_number is not None:
            statement = statement.where(
                CoverageQuestion.question_number == question_number
            )
        else:
            statement = statement.where(CoverageQuestion.question_text == question_text)
        return await session.scalar(statement.limit(1))

    async def list_filtered(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        filters: dict,
    ) -> tuple[list[tuple[CoverageQuestion, Subject, Topic | None]], int]:
        statement = (
            select(CoverageQuestion, Subject, Topic)
            .join(Subject, Subject.id == CoverageQuestion.subject_id)
            .outerjoin(Topic, Topic.id == CoverageQuestion.topic_id)
        )
        if filters.get("subjectId"):
            statement = statement.where(
                CoverageQuestion.subject_id == filters["subjectId"]
            )
        if filters.get("examType"):
            statement = statement.where(
                CoverageQuestion.exam_type == filters["examType"]
            )
        if filters.get("examYear"):
            statement = statement.where(
                CoverageQuestion.exam_year == int(filters["examYear"])
            )
        if filters.get("search"):
            statement = statement.where(
                CoverageQuestion.question_text.ilike(f"%{filters['search']}%")
            )
        total = await session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        result = await session.execute(
            statement.order_by(
                CoverageQuestion.exam_type,
                CoverageQuestion.exam_year.desc().nulls_last(),
                CoverageQuestion.question_number.asc().nulls_last(),
                CoverageQuestion.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [tuple(row) for row in result.all()], int(total or 0)

    async def coverage_counts(self, session: AsyncSession) -> list[tuple]:
        """Question counts per subject, exam type and year, dated papers only."""
        result = await session.execute(
            select(
                CoverageQuestion.subject_id,
                Subject.name,
                Subject.code,
                CoverageQuestion.exam_type,
                CoverageQuestion.exam_year,
                func.count(),
            )
            .join(Subject, Subject.id == CoverageQuestion.subject_id)
            .where(CoverageQuestion.exam_year.is_not(None))
            .group_by(
                CoverageQuestion.subject_id,
                Subject.name,
                Subject.code,
                CoverageQuestion.exam_type,
                CoverageQuestion.exam_year,
            )
        )
        return [tuple(row) for row in result.all()]

    async def count_for_level(
        self,
        session: AsyncSession,
        *,
        subject_id: str,
        exam_type: str | None,
        curriculum_level_id: str,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(CoverageQuestion)
            .join(Topic, Topic.id == CoverageQuestion.topic_id)
            .where(
                CoverageQuestion.subject_id == subject_id,
                CoverageQuestion.question_type == "OBJECTIVE",
                Topic.curriculum_level_id == curriculum_level_id,
            )
        )
        if exam_type:
            statement = statement.where(CoverageQuestion.exam_type == exam_type)
        return int(await session.scalar(statement) or 0)

    async def pick_objective(
        self,
        session: AsyncSession,
        *,
        subject_id: str,
        count: int,
        exam_type: str | None,
        class_levels: list[tuple[str, str]],
    ) -> list[CoverageQuestion]:
        """Random objective questions for a subject within the class/term slots."""
        level_ids: list[str] = []
        for class_level, term in class_levels:
            level_ids.extend(
                (
                    await session.scalars(
                        select(CurriculumLevel.id).where(
                            CurriculumLevel.subject_id == subject_id,
                            CurriculumLevel.class_level == class_level,
                            CurriculumLevel.term == term,
                        )
                    )
                ).all()
            )
        if not level_ids:
            return []
        statement = (
            select(CoverageQuestion)
            .join(Topic, Topic.id == CoverageQuestion.topic_id)
            .where(
                CoverageQuestion.subject_id == subject_id,
                CoverageQuestion.question_type == "OBJECTIVE",
                Topic.curriculum_level_id.in_(level_ids),
            )
        )
        if exam_type:
            statement = statement.where(CoverageQuestion.exam_type == exam_type)
        return await self.many(session, statement.order_by(func.random()).limit(count))

    async def by_ids(
        self, session: AsyncSession, ids: list[str]
    ) -> list[CoverageQuestion]:
        if not ids:
            return []
        return await self.many(
            session, select(CoverageQuestion).where(CoverageQuestion.id.in_(ids))
        )

    async def topic_ids_for_assessment(
        self, session: AsyncSession, assessment_id: str
    ) -> list[str]:
        return list(
            (
                await session.scalars(
                    select(CoverageQuestion.topic_id)
                    .join(
                        AssessmentQuestion,
                        AssessmentQuestion.coverage_question_id == CoverageQuestion.id,
                    )
                    .where(
                        AssessmentQuestion.assessment_id == assessment_id,
                        CoverageQuestion.topic_id.is_not(None),
                    )
                )
            ).all()
        )


coverage_questions_repository = CoverageQuestionRepository()
