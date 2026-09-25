"""SQLAlchemy models for this domain."""

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import (
    ExamType,
    PlanCompletionSource,
    PlanItemActivity,
    PlanItemStatus,
    Term,
)


class StudyPlan(Base):
    __tablename__ = "StudyPlan"
    __table_args__ = (Index("StudyPlan_student_active_idx", "studentId", "isActive"),)

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    subject_ids: Mapped[list] = mapped_column("subjectIds", JSONB)
    target_exam: Mapped[str | None] = mapped_column("targetExam", ExamType)
    target_date: Mapped[date_type | None] = mapped_column("targetDate", Date)
    force_exam_mode: Mapped[bool] = mapped_column(
        "forceExamMode", Boolean, default=False
    )
    study_days: Mapped[list] = mapped_column("studyDays", ARRAY(Integer))
    weekday_minutes: Mapped[int] = mapped_column("weekdayMinutes", Integer)
    weekend_minutes: Mapped[int] = mapped_column("weekendMinutes", Integer)
    planned_through: Mapped[date_type | None] = mapped_column("plannedThrough", Date)
    last_replanned_at: Mapped[datetime | None] = mapped_column(
        "lastReplannedAt", DateTime(timezone=True)
    )
    outline: Mapped[list | None] = mapped_column(JSONB)
    overload: Mapped[dict | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class StudyPlanItem(Base):
    __tablename__ = "StudyPlanItem"

    id: Mapped[str] = id_column()
    study_plan_id: Mapped[str] = mapped_column(
        "studyPlanId", ForeignKey("StudyPlan.id", ondelete="CASCADE")
    )
    date: Mapped[date_type] = mapped_column(Date)
    subject_id: Mapped[str] = mapped_column("subjectId", ForeignKey("Subject.id"))
    topic_id: Mapped[str | None] = mapped_column("topicId", ForeignKey("Topic.id"))
    activity_type: Mapped[str] = mapped_column("activityType", PlanItemActivity)
    duration_minutes: Mapped[int] = mapped_column("durationMinutes", Integer)
    status: Mapped[str] = mapped_column(PlanItemStatus, default="PENDING")
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True)
    )
    completion_source: Mapped[str | None] = mapped_column(
        "completionSource", PlanCompletionSource
    )
    carried_from_date: Mapped[date_type | None] = mapped_column("carriedFromDate", Date)


class StudyPlanPosition(Base):
    __tablename__ = "StudyPlanPosition"
    __table_args__ = (UniqueConstraint("studyPlanId", "subjectId"),)

    id: Mapped[str] = id_column()
    study_plan_id: Mapped[str] = mapped_column(
        "studyPlanId", ForeignKey("StudyPlan.id", ondelete="CASCADE")
    )
    subject_id: Mapped[str] = mapped_column("subjectId", ForeignKey("Subject.id"))
    topic_id: Mapped[str] = mapped_column("topicId", ForeignKey("Topic.id"))


class AcademicTerm(Base):
    __tablename__ = "AcademicTerm"
    __table_args__ = (UniqueConstraint("session", "term"),)

    id: Mapped[str] = id_column()
    session: Mapped[str] = mapped_column(String)
    term: Mapped[str] = mapped_column(Term)
    starts_on: Mapped[date_type] = mapped_column("startsOn", Date)
    ends_on: Mapped[date_type] = mapped_column("endsOn", Date)
