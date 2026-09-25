"""Repositories for study plans and academic terms."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AcademicTerm,
    StudyPlan,
    StudyPlanItem,
    StudyPlanPosition,
)
from app.database.repositories.base import BaseRepository


class StudyPlanRepository(BaseRepository[StudyPlan]):
    model = StudyPlan

    async def active(self, session: AsyncSession, student_id: str) -> StudyPlan | None:
        return await self.first(
            session,
            StudyPlan.student_id == student_id,
            StudyPlan.is_active.is_(True),
        )

    async def list_active(
        self, session: AsyncSession, student_id: str
    ) -> list[StudyPlan]:
        return await self.list_where(
            session,
            StudyPlan.student_id == student_id,
            StudyPlan.is_active.is_(True),
        )


class StudyPlanItemRepository(BaseRepository[StudyPlanItem]):
    model = StudyPlanItem

    async def for_plan(
        self, session: AsyncSession, study_plan_id: str
    ) -> list[StudyPlanItem]:
        return await self.list_where(
            session,
            StudyPlanItem.study_plan_id == study_plan_id,
            order_by=(StudyPlanItem.date,),
        )

    async def pending_for_plan(
        self, session: AsyncSession, study_plan_id: str
    ) -> list[StudyPlanItem]:
        return await self.list_where(
            session,
            StudyPlanItem.study_plan_id == study_plan_id,
            StudyPlanItem.status == "PENDING",
        )

    async def pending_for_activity(
        self,
        session: AsyncSession,
        study_plan_id: str,
        activity: str,
    ) -> list[StudyPlanItem]:
        return await self.list_where(
            session,
            StudyPlanItem.study_plan_id == study_plan_id,
            StudyPlanItem.status == "PENDING",
            StudyPlanItem.activity_type == activity,
            order_by=(StudyPlanItem.date,),
        )


class StudyPlanPositionRepository(BaseRepository[StudyPlanPosition]):
    model = StudyPlanPosition

    async def for_plan(
        self, session: AsyncSession, study_plan_id: str
    ) -> list[StudyPlanPosition]:
        return await self.list_where(
            session, StudyPlanPosition.study_plan_id == study_plan_id
        )

    async def for_plan_subject(
        self, session: AsyncSession, study_plan_id: str, subject_id: str
    ) -> StudyPlanPosition | None:
        return await self.first(
            session,
            StudyPlanPosition.study_plan_id == study_plan_id,
            StudyPlanPosition.subject_id == subject_id,
        )


class AcademicTermRepository(BaseRepository[AcademicTerm]):
    model = AcademicTerm

    async def ordered(self, session: AsyncSession) -> list[AcademicTerm]:
        return await self.all_ordered(session, AcademicTerm.starts_on)


plans_repository = StudyPlanRepository()
plan_items_repository = StudyPlanItemRepository()
plan_positions_repository = StudyPlanPositionRepository()
academic_terms_repository = AcademicTermRepository()
