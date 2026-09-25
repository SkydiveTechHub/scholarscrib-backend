"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column
from app.database.models.enums import (
    Difficulty,
    LearningEventKind,
    MasteryLevel,
    ProgressStatus,
)


class StudentProgress(Base):
    __tablename__ = "StudentProgress"
    __table_args__ = (
        UniqueConstraint("studentId", "subjectId", "topicId", "lessonId"),
    )

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    subject_id: Mapped[str] = mapped_column("subjectId", ForeignKey("Subject.id"))
    topic_id: Mapped[str] = mapped_column("topicId", ForeignKey("Topic.id"))
    lesson_id: Mapped[str] = mapped_column("lessonId", ForeignKey("Lesson.id"))
    status: Mapped[str] = mapped_column(ProgressStatus, default="NOT_STARTED")
    completion_percent: Mapped[int] = mapped_column(
        "completionPercent", Integer, default=0
    )
    time_spent_minutes: Mapped[int] = mapped_column(
        "timeSpentMinutes", Integer, default=0
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        "lastAccessedAt", DateTime(timezone=True)
    )
    checkpoint_data: Mapped[dict | None] = mapped_column("checkpointData", JSONB)
    mastery_score: Mapped[int | None] = mapped_column("masteryScore", Integer)
    revision_due_at: Mapped[datetime | None] = mapped_column(
        "revisionDueAt", DateTime(timezone=True)
    )


class PerformanceMetric(Base):
    __tablename__ = "PerformanceMetric"
    __table_args__ = (UniqueConstraint("studentId", "subjectId", "topicId"),)

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    subject_id: Mapped[str] = mapped_column("subjectId", ForeignKey("Subject.id"))
    topic_id: Mapped[str] = mapped_column("topicId", ForeignKey("Topic.id"))
    mastery_level: Mapped[str] = mapped_column(
        "masteryLevel", MasteryLevel, default="WEAK"
    )
    pretest_passed_at: Mapped[datetime | None] = mapped_column(
        "pretestPassedAt", DateTime(timezone=True)
    )


class LearningEvent(Base):
    __tablename__ = "LearningEvent"
    __table_args__ = (
        Index("LearningEvent_student_topic_seq_idx", "studentId", "topicId", "seq"),
        Index("LearningEvent_student_seq_idx", "studentId", "seq"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    subject_id: Mapped[str | None] = mapped_column(
        "subjectId", ForeignKey("Subject.id")
    )
    topic_id: Mapped[str | None] = mapped_column("topicId", ForeignKey("Topic.id"))
    kind: Mapped[str] = mapped_column(LearningEventKind)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[str | None] = mapped_column(Difficulty)
    seconds: Mapped[float | None] = mapped_column(Float)
    source_id: Mapped[str | None] = mapped_column("sourceId", String)
    occurred_at: Mapped[datetime] = mapped_column(
        "occurredAt", DateTime(timezone=True), server_default=func.now()
    )


class TopicMastery(Base):
    __tablename__ = "TopicMastery"

    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[str] = mapped_column(
        "topicId", ForeignKey("Topic.id", ondelete="CASCADE"), primary_key=True
    )
    acc_outcome: Mapped[float] = mapped_column("accOutcome", Float, default=0)
    acc_mass: Mapped[float] = mapped_column("accMass", Float, default=0)
    acc_observations: Mapped[int] = mapped_column("accObservations", Integer, default=0)
    lesson_outcome: Mapped[float] = mapped_column("lessonOutcome", Float, default=0)
    lesson_mass: Mapped[float] = mapped_column("lessonMass", Float, default=0)
    lesson_observations: Mapped[int] = mapped_column(
        "lessonObservations", Integer, default=0
    )
    srs_outcome: Mapped[float] = mapped_column("srsOutcome", Float, default=0)
    srs_mass: Mapped[float] = mapped_column("srsMass", Float, default=0)
    srs_observations: Mapped[int] = mapped_column("srsObservations", Integer, default=0)
    abandon_count: Mapped[int] = mapped_column("abandonCount", Integer, default=0)
    decay_anchor: Mapped[datetime | None] = mapped_column(
        "decayAnchor", DateTime(timezone=True)
    )
    last_effort_at: Mapped[datetime | None] = mapped_column(
        "lastEffortAt", DateTime(timezone=True)
    )
    cursor_seq: Mapped[int] = mapped_column("cursorSeq", BigInteger, default=0)
    scoring_version: Mapped[int] = mapped_column("scoringVersion", Integer, default=1)


class Achievement(Base):
    __tablename__ = "Achievement"

    id: Mapped[str] = id_column()
    title: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    criteria_type: Mapped[str] = mapped_column("criteriaType", String)
    criteria_value: Mapped[int] = mapped_column("criteriaValue", Integer)


class StudentAchievement(Base):
    __tablename__ = "StudentAchievement"
    __table_args__ = (UniqueConstraint("studentId", "achievementId"),)

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    achievement_id: Mapped[str] = mapped_column(
        "achievementId", ForeignKey("Achievement.id", ondelete="CASCADE")
    )
    earned_at: Mapped[datetime] = mapped_column(
        "earnedAt", DateTime(timezone=True), server_default=func.now()
    )
