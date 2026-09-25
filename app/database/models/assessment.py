"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import (
    AssessmentType,
    AttemptStatus,
    ExamType,
)


class Assessment(Base):
    __tablename__ = "Assessment"

    id: Mapped[str] = id_column()
    title: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str | None] = mapped_column(
        "subjectId", ForeignKey("Subject.id")
    )
    assessment_type: Mapped[str] = mapped_column("assessmentType", AssessmentType)
    exam_type: Mapped[str | None] = mapped_column("examType", ExamType)
    exam_year: Mapped[int | None] = mapped_column("examYear", Integer)
    total_marks: Mapped[int] = mapped_column("totalMarks", Integer)
    time_limit_minutes: Mapped[int | None] = mapped_column("timeLimitMinutes", Integer)
    pass_mark_percent: Mapped[int] = mapped_column(
        "passMarkPercent", Integer, default=50
    )
    created_by: Mapped[str | None] = mapped_column("createdBy", String)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class AssessmentQuestion(Base):
    __tablename__ = "AssessmentQuestion"
    __table_args__ = (UniqueConstraint("assessmentId", "questionId"),)

    id: Mapped[str] = id_column()
    assessment_id: Mapped[str] = mapped_column(
        "assessmentId", ForeignKey("Assessment.id", ondelete="CASCADE")
    )
    question_id: Mapped[str] = mapped_column("questionId", ForeignKey("Question.id"))
    order_index: Mapped[int] = mapped_column("orderIndex", Integer)


class AssessmentAttempt(Base):
    __tablename__ = "AssessmentAttempt"
    __table_args__ = (
        Index("AssessmentAttempt_student_assessment_idx", "studentId", "assessmentId"),
        Index("AssessmentAttempt_student_status_idx", "studentId", "status"),
        Index(
            "AssessmentAttempt_student_status_completed_idx",
            "studentId",
            "status",
            "completedAt",
        ),
    )

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    assessment_id: Mapped[str] = mapped_column(
        "assessmentId", ForeignKey("Assessment.id")
    )
    status: Mapped[str] = mapped_column(AttemptStatus, default="IN_PROGRESS")
    score: Mapped[float | None] = mapped_column(Float)
    total_marks: Mapped[float | None] = mapped_column("totalMarks", Float)
    percentage: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String)
    time_spent_seconds: Mapped[int | None] = mapped_column("timeSpentSeconds", Integer)
    away_events: Mapped[int | None] = mapped_column("awayEvents", Integer)
    started_at: Mapped[datetime] = mapped_column(
        "startedAt", DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True)
    )


class QuestionResponse(Base):
    __tablename__ = "QuestionResponse"
    __table_args__ = (UniqueConstraint("attemptId", "questionId"),)

    id: Mapped[str] = id_column()
    attempt_id: Mapped[str] = mapped_column(
        "attemptId", ForeignKey("AssessmentAttempt.id", ondelete="CASCADE")
    )
    question_id: Mapped[str] = mapped_column("questionId", ForeignKey("Question.id"))
    selected_answer: Mapped[str | None] = mapped_column("selectedAnswer", String)
    is_correct: Mapped[bool] = mapped_column("isCorrect", Boolean, default=False)
    time_spent_seconds: Mapped[int | None] = mapped_column("timeSpentSeconds", Integer)
    flagged_for_review: Mapped[bool] = mapped_column(
        "flaggedForReview", Boolean, default=False
    )
