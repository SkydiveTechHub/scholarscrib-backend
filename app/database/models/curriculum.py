"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import (
    ClassLevel,
    EdgeKind,
    MaterialType,
    Term,
    TrackCategory,
)


class Subject(Base):
    __tablename__ = "Subject"

    id: Mapped[str] = id_column()
    name: Mapped[str] = mapped_column(String, unique=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    is_waec: Mapped[bool] = mapped_column("isWaec", Boolean, default=False)
    is_jamb: Mapped[bool] = mapped_column("isJamb", Boolean, default=False)
    is_neco: Mapped[bool] = mapped_column("isNeco", Boolean, default=False)
    track_category: Mapped[str] = mapped_column("trackCategory", TrackCategory)


class SubjectResource(Base):
    __tablename__ = "SubjectResource"

    id: Mapped[str] = id_column()
    subject_id: Mapped[str] = mapped_column(
        "subjectId", ForeignKey("Subject.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column("resourceType", MaterialType)
    url: Mapped[str] = mapped_column(String)
    author: Mapped[str | None] = mapped_column(String)
    is_free: Mapped[bool] = mapped_column("isFree", Boolean, default=True)
    order_index: Mapped[int] = mapped_column("orderIndex", Integer, default=0)


class CurriculumLevel(Base):
    __tablename__ = "CurriculumLevel"
    __table_args__ = (UniqueConstraint("subjectId", "classLevel", "term"),)

    id: Mapped[str] = id_column()
    subject_id: Mapped[str] = mapped_column(
        "subjectId", ForeignKey("Subject.id", ondelete="CASCADE")
    )
    class_level: Mapped[str] = mapped_column("classLevel", ClassLevel)
    term: Mapped[str] = mapped_column(Term)


class Topic(Base):
    __tablename__ = "Topic"
    __table_args__ = (UniqueConstraint("subjectId", "slug"),)

    id: Mapped[str] = id_column()
    subject_id: Mapped[str] = mapped_column(
        "subjectId", ForeignKey("Subject.id", ondelete="CASCADE")
    )
    curriculum_level_id: Mapped[str | None] = mapped_column(
        "curriculumLevelId", ForeignKey("CurriculumLevel.id")
    )
    title: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    order_index: Mapped[int] = mapped_column("orderIndex", Integer, default=0)
    estimated_minutes: Mapped[int] = mapped_column(
        "estimatedMinutes", Integer, default=45
    )
    waec_weight: Mapped[float] = mapped_column("waecWeight", Float, default=0)
    jamb_weight: Mapped[float] = mapped_column("jambWeight", Float, default=0)
    prerequisite_topic_id: Mapped[str | None] = mapped_column(
        "prerequisiteTopicId", String
    )


class TopicEdge(Base):
    __tablename__ = "TopicEdge"
    __table_args__ = (UniqueConstraint("prereqTopicId", "topicId"),)

    id: Mapped[str] = id_column()
    prereq_topic_id: Mapped[str] = mapped_column(
        "prereqTopicId", ForeignKey("Topic.id", ondelete="CASCADE")
    )
    topic_id: Mapped[str] = mapped_column(
        "topicId", ForeignKey("Topic.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(EdgeKind, default="PREREQUISITE")
    strength: Mapped[float] = mapped_column(Float, default=1)
    rationale: Mapped[str | None] = mapped_column(Text)


class Subtopic(Base):
    __tablename__ = "Subtopic"

    id: Mapped[str] = id_column()
    topic_id: Mapped[str] = mapped_column(
        "topicId", ForeignKey("Topic.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String)
    order_index: Mapped[int] = mapped_column("orderIndex", Integer, default=0)


class Lesson(Base):
    __tablename__ = "Lesson"

    id: Mapped[str] = id_column()
    subtopic_id: Mapped[str] = mapped_column(
        "subtopicId", ForeignKey("Subtopic.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str | None] = mapped_column(Text)
    blocks: Mapped[list | dict | None] = mapped_column(JSONB)
    key_points: Mapped[list | None] = mapped_column("keyPoints", JSONB)
    worked_examples: Mapped[list | None] = mapped_column("workedExamples", JSONB)
    exam_tips: Mapped[list | None] = mapped_column("examTips", JSONB)
    mnemonics: Mapped[list | None] = mapped_column(JSONB)
    knowledge_checks: Mapped[list | None] = mapped_column("knowledgeChecks", JSONB)
    prerequisites: Mapped[list | None] = mapped_column(JSONB)
    revision_days: Mapped[list | None] = mapped_column("revisionDays", JSONB)
    created_by: Mapped[str | None] = mapped_column("createdBy", String)
    pass_mark_percent: Mapped[int] = mapped_column(
        "passMarkPercent", Integer, default=60
    )
    practice_count: Mapped[int] = mapped_column("practiceCount", Integer, default=7)
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class LessonResource(Base):
    __tablename__ = "LessonResource"

    id: Mapped[str] = id_column()
    lesson_id: Mapped[str] = mapped_column(
        "lessonId", ForeignKey("Lesson.id", ondelete="CASCADE")
    )
    resource_type: Mapped[str] = mapped_column("resourceType", String)
    url: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
