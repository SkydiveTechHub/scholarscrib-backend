"""Repositories for assessments and attempts."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    Question,
    QuestionResponse,
)
from app.database.repositories.base import BaseRepository


class AssessmentRepository(BaseRepository[Assessment]):
    model = Assessment


class AssessmentQuestionRepository(BaseRepository[AssessmentQuestion]):
    model = AssessmentQuestion


class AssessmentAttemptRepository(BaseRepository[AssessmentAttempt]):
    model = AssessmentAttempt

    async def in_progress(
        self,
        session: AsyncSession,
        student_id: str,
        *,
        limit: int = 100,
    ) -> list[AssessmentAttempt]:
        return await self.list_where(
            session,
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "IN_PROGRESS",
            limit=limit,
        )

    async def resumable(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        subject_id: str,
        assessment_type: str,
        exam_type: str | None,
        exam_year: int | None,
        total_marks: int,
        limit: int = 5,
    ) -> list[AssessmentAttempt]:
        return await self.many(
            session,
            select(AssessmentAttempt)
            .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
            .where(
                AssessmentAttempt.student_id == student_id,
                AssessmentAttempt.status == "IN_PROGRESS",
                Assessment.subject_id == subject_id,
                Assessment.assessment_type == assessment_type,
                Assessment.exam_type == exam_type,
                Assessment.exam_year == exam_year,
                Assessment.total_marks == total_marks,
            )
            .order_by(AssessmentAttempt.started_at.desc())
            .limit(limit),
        )

    async def in_progress_cbt(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        exam_type: str,
        exam_year: int,
        total_marks: int,
        assessment_type: str = "CBT_PRACTICE",
    ) -> AssessmentAttempt | None:
        return await self.one(
            session,
            select(AssessmentAttempt)
            .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
            .where(
                AssessmentAttempt.student_id == student_id,
                AssessmentAttempt.status == "IN_PROGRESS",
                Assessment.assessment_type == assessment_type,
                Assessment.exam_type == exam_type,
                Assessment.exam_year == exam_year,
                Assessment.total_marks == total_marks,
            ),
        )

    async def perfect_scores(self, session: AsyncSession, student_id: str) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AssessmentAttempt)
                .where(
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.status == "COMPLETED",
                    AssessmentAttempt.percentage >= 100,
                )
            )
            or 0
        )

    async def mock_scores_at_least(
        self, session: AsyncSession, student_id: str, percentage: float
    ) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AssessmentAttempt)
                .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
                .where(
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.status == "COMPLETED",
                    AssessmentAttempt.percentage >= percentage,
                    Assessment.assessment_type.in_(("MOCK_EXAM", "CBT_PRACTICE")),
                )
            )
            or 0
        )

    async def completed_at(
        self, session: AsyncSession, student_id: str, limit: int = 400
    ) -> list:
        return list(
            (
                await session.scalars(
                    select(AssessmentAttempt.completed_at)
                    .where(
                        AssessmentAttempt.student_id == student_id,
                        AssessmentAttempt.status == "COMPLETED",
                        AssessmentAttempt.completed_at.is_not(None),
                    )
                    .order_by(AssessmentAttempt.completed_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def completed_with_assessment(
        self,
        session: AsyncSession,
        student_id: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[tuple[AssessmentAttempt, Assessment]]:
        result = await self.rows(
            session,
            select(AssessmentAttempt, Assessment)
            .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
            .where(
                AssessmentAttempt.student_id == student_id,
                AssessmentAttempt.status == "COMPLETED",
            )
            .order_by(AssessmentAttempt.completed_at.desc())
            .offset(offset)
            .limit(limit),
        )
        return [(row[0], row[1]) for row in result.all()]

    async def latest_completed_topic_quiz(
        self, session: AsyncSession, student_id: str, subject_id: str
    ) -> AssessmentAttempt | None:
        return await self.one(
            session,
            select(AssessmentAttempt)
            .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
            .where(
                AssessmentAttempt.student_id == student_id,
                AssessmentAttempt.status == "COMPLETED",
                Assessment.subject_id == subject_id,
                Assessment.assessment_type == "TOPIC_QUIZ",
            )
            .order_by(AssessmentAttempt.completed_at.desc()),
        )


class QuestionResponseRepository(BaseRepository[QuestionResponse]):
    model = QuestionResponse

    async def seen_question_ids(
        self, session: AsyncSession, student_id: str, since
    ) -> set[str]:
        rows = await self.rows(
            session,
            select(QuestionResponse.question_id)
            .join(
                AssessmentAttempt,
                AssessmentAttempt.id == QuestionResponse.attempt_id,
            )
            .where(
                AssessmentAttempt.student_id == student_id,
                AssessmentAttempt.status == "COMPLETED",
                AssessmentAttempt.completed_at >= since,
            ),
        )
        return {row[0] for row in rows.all()}

    async def for_attempt(
        self, session: AsyncSession, attempt_id: str
    ) -> list[QuestionResponse]:
        return await self.list_where(session, QuestionResponse.attempt_id == attempt_id)

    async def answered_count(self, session: AsyncSession, student_id: str) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(QuestionResponse)
                .join(
                    AssessmentAttempt,
                    AssessmentAttempt.id == QuestionResponse.attempt_id,
                )
                .where(AssessmentAttempt.student_id == student_id)
            )
            or 0
        )

    async def timing_for_subject(
        self, session: AsyncSession, student_id: str, subject_id: str
    ) -> list[tuple[int | None, int | None]]:
        result = await self.rows(
            session,
            select(
                QuestionResponse.time_spent_seconds,
                Question.time_estimate_seconds,
            )
            .join(Question, Question.id == QuestionResponse.question_id)
            .join(
                AssessmentAttempt,
                AssessmentAttempt.id == QuestionResponse.attempt_id,
            )
            .where(
                AssessmentAttempt.student_id == student_id,
                Question.subject_id == subject_id,
            ),
        )
        return [(row[0], row[1]) for row in result.all()]


assessments_repository = AssessmentRepository()
assessment_questions_repository = AssessmentQuestionRepository()
attempts_repository = AssessmentAttemptRepository()
responses_repository = QuestionResponseRepository()
