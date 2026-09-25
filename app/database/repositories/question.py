"""Repository for the question bank."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AssessmentQuestion,
    CurriculumLevel,
    Question,
    QuestionResponse,
    Topic,
)
from app.database.repositories.base import BaseRepository


class QuestionRepository(BaseRepository[Question]):
    model = Question

    async def list_filtered(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        filters: dict,
    ) -> tuple[list[Question], int]:
        statement = select(Question)
        if filters.get("subjectId"):
            statement = statement.where(Question.subject_id == filters["subjectId"])
        if filters.get("examType"):
            statement = statement.where(Question.exam_type == filters["examType"])
        if filters.get("examYear"):
            statement = statement.where(Question.exam_year == int(filters["examYear"]))
        if filters.get("difficulty"):
            statement = statement.where(Question.difficulty == filters["difficulty"])
        if filters.get("search"):
            statement = statement.where(
                Question.question_text.ilike(f"%{filters['search']}%")
            )
        total = await session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        rows = await self.many(
            session,
            statement.order_by(Question.id)
            .offset((page - 1) * page_size)
            .limit(page_size),
        )
        return rows, int(total or 0)

    async def usage_counts(
        self, session: AsyncSession, question_id: str
    ) -> tuple[int, int]:
        responses = await session.scalar(
            select(func.count())
            .select_from(QuestionResponse)
            .where(QuestionResponse.question_id == question_id)
        )
        assessments = await session.scalar(
            select(func.count())
            .select_from(AssessmentQuestion)
            .where(AssessmentQuestion.question_id == question_id)
        )
        return int(responses or 0), int(assessments or 0)

    async def duplicate_id(
        self,
        session: AsyncSession,
        *,
        subject_id: str,
        exam_type: str,
        exam_year: int | None,
        question_text: str,
    ) -> str | None:
        return await session.scalar(
            select(Question.id).where(
                Question.subject_id == subject_id,
                Question.exam_type == exam_type,
                Question.exam_year == exam_year,
                Question.question_text == question_text,
            )
        )

    async def count_for_subject(self, session: AsyncSession, subject_id: str) -> int:
        return await self.count(session, Question.subject_id == subject_id)

    async def count_for_topic(self, session: AsyncSession, topic_id: str) -> int:
        return await self.count(session, Question.topic_id == topic_id)

    async def past_paper_groups(
        self,
        session: AsyncSession,
        exam_type: str | None = None,
        subject_id: str | None = None,
    ):
        statement = (
            select(
                Question.exam_type,
                Question.exam_year,
                Question.subject_id,
                func.count(Question.id),
            )
            .where(Question.exam_year.is_not(None))
            .group_by(Question.exam_type, Question.exam_year, Question.subject_id)
        )
        if exam_type:
            statement = statement.where(Question.exam_type == exam_type)
        if subject_id:
            statement = statement.where(Question.subject_id == subject_id)
        return (await session.execute(statement)).all()

    async def pick_objective(
        self,
        session: AsyncSession,
        *,
        subject_id: str,
        count: int,
        topic_ids: list[str] | None = None,
        exam_type: str | None = None,
        exam_year: int | None = None,
        difficulty: str | None = None,
        exclude: set[str] | None = None,
        class_levels: list[tuple[str, str]] | None = None,
    ) -> list[Question]:
        statement = select(Question).where(
            Question.subject_id == subject_id,
            Question.question_type == "OBJECTIVE",
        )
        if topic_ids:
            statement = statement.where(Question.topic_id.in_(topic_ids))
        if exam_type:
            statement = statement.where(Question.exam_type == exam_type)
        if exam_year:
            statement = statement.where(Question.exam_year == exam_year)
        if difficulty:
            statement = statement.where(Question.difficulty == difficulty)
        if class_levels:
            level_ids: list[str] = []
            for class_level, term in class_levels:
                found = (
                    await session.scalars(
                        select(CurriculumLevel.id).where(
                            CurriculumLevel.subject_id == subject_id,
                            CurriculumLevel.class_level == class_level,
                            CurriculumLevel.term == term,
                        )
                    )
                ).all()
                level_ids.extend(found)
            if not level_ids:
                return []
            statement = statement.join(Topic, Topic.id == Question.topic_id).where(
                Topic.curriculum_level_id.in_(level_ids)
            )
        if exclude:
            statement = statement.where(Question.id.notin_(exclude))
        statement = statement.order_by(func.random()).limit(count)
        return await self.many(session, statement)

    async def for_assessment(
        self, session: AsyncSession, assessment_id: str
    ) -> list[Question]:
        return list(
            (
                await session.execute(
                    select(Question)
                    .join(
                        AssessmentQuestion,
                        AssessmentQuestion.question_id == Question.id,
                    )
                    .where(AssessmentQuestion.assessment_id == assessment_id)
                    .order_by(AssessmentQuestion.order_index)
                )
            )
            .scalars()
            .all()
        )

    async def topic_ids_for_assessment(
        self, session: AsyncSession, assessment_id: str
    ) -> list[str]:
        rows = list(
            (
                await session.scalars(
                    select(Question.topic_id)
                    .join(
                        AssessmentQuestion,
                        AssessmentQuestion.question_id == Question.id,
                    )
                    .where(
                        AssessmentQuestion.assessment_id == assessment_id,
                        Question.topic_id.is_not(None),
                    )
                )
            ).all()
        )
        return [topic_id for topic_id in rows if topic_id is not None]

    async def board_subject_counts(self, session: AsyncSession, exam_type: str) -> list:
        return list(
            (
                await self.rows(
                    session,
                    select(Question.subject_id, func.count())
                    .where(
                        Question.exam_type == exam_type,
                        Question.topic_id.is_not(None),
                        Question.question_type == "OBJECTIVE",
                    )
                    .group_by(Question.subject_id),
                )
            ).all()
        )

    async def count_for_level(
        self,
        session: AsyncSession,
        *,
        subject_id: str,
        exam_type: str,
        curriculum_level_id: str,
    ) -> int:
        value = await session.scalar(
            select(func.count())
            .select_from(Question)
            .join(Topic, Topic.id == Question.topic_id)
            .where(
                Question.subject_id == subject_id,
                Question.exam_type == exam_type,
                Question.question_type == "OBJECTIVE",
                Topic.curriculum_level_id == curriculum_level_id,
            )
        )
        return int(value or 0)

    async def exam_years(
        self, session: AsyncSession, subject_id: str, exam_type: str
    ) -> list[int]:
        rows = list(
            (
                await session.scalars(
                    select(Question.exam_year)
                    .where(
                        Question.subject_id == subject_id,
                        Question.exam_type == exam_type,
                        Question.exam_year.is_not(None),
                    )
                    .distinct()
                )
            ).all()
        )
        return [year for year in rows if year is not None]

    async def count_objective_for_topic(
        self, session: AsyncSession, topic_id: str
    ) -> int:
        return await self.count(
            session,
            Question.topic_id == topic_id,
            Question.question_type == "OBJECTIVE",
        )

    async def count_objective(
        self,
        session: AsyncSession,
        *,
        subject_id: str,
        exam_type: str,
        exam_year: int,
    ) -> int:
        return await self.count(
            session,
            Question.subject_id == subject_id,
            Question.exam_type == exam_type,
            Question.exam_year == exam_year,
            Question.question_type == "OBJECTIVE",
        )

    async def list_page(
        self,
        session: AsyncSession,
        *,
        subject_id: str | None = None,
        topic_id: str | None = None,
        exam_type: str | None = None,
        exam_year: int | None = None,
        difficulty: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[Question], int]:
        criteria = []
        if subject_id:
            criteria.append(Question.subject_id == subject_id)
        if topic_id:
            criteria.append(Question.topic_id == topic_id)
        if exam_type:
            criteria.append(Question.exam_type == exam_type)
        if exam_year:
            criteria.append(Question.exam_year == exam_year)
        if difficulty:
            criteria.append(Question.difficulty == difficulty)
        total = await self.count(session, *criteria)
        rows = await self.list_where(
            session,
            *criteria,
            limit=limit,
            offset=(page - 1) * limit,
        )
        return rows, total


questions_repository = QuestionRepository()
