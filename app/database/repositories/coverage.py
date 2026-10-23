"""Repository for the coverage question bank."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CoverageQuestion, Subject, Topic
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


coverage_questions_repository = CoverageQuestionRepository()
