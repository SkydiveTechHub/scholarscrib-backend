"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column
from app.database.models.enums import (
    ExamType,
    ProviderCircuitState,
    ProviderFetchStatus,
    ProviderQuestionStatus,
    QuestionProvider,
)


class ProviderFetch(Base):
    __tablename__ = "ProviderFetch"
    __table_args__ = (UniqueConstraint("provider", "cacheKey"),)

    id: Mapped[str] = id_column()
    provider: Mapped[str] = mapped_column(QuestionProvider, default="SDASH")
    cache_key: Mapped[str] = mapped_column("cacheKey", String)
    status: Mapped[str] = mapped_column(ProviderFetchStatus, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(
        "startedAt", DateTime(timezone=True)
    )
    draw_count: Mapped[int] = mapped_column("drawCount", Integer, default=0)
    raw_count: Mapped[int] = mapped_column("rawCount", Integer, default=0)
    new_in_last_draw: Mapped[int] = mapped_column("newInLastDraw", Integer, default=0)
    promoted_count: Mapped[int] = mapped_column("promotedCount", Integer, default=0)
    rejected_count: Mapped[int] = mapped_column("rejectedCount", Integer, default=0)
    subject_id: Mapped[str | None] = mapped_column(
        "subjectId", ForeignKey("Subject.id")
    )
    exam_type: Mapped[str | None] = mapped_column("examType", ExamType)
    exam_year: Mapped[int | None] = mapped_column("examYear", Integer)


class ProviderQuestion(Base):
    __tablename__ = "ProviderQuestion"
    __table_args__ = (
        UniqueConstraint("fetchId", "providerQuestionId"),
        UniqueConstraint("fetchId", "fingerprint"),
    )

    id: Mapped[str] = id_column()
    fetch_id: Mapped[str] = mapped_column(
        "fetchId", ForeignKey("ProviderFetch.id", ondelete="CASCADE")
    )
    provider_question_id: Mapped[str] = mapped_column("providerQuestionId", String)
    fingerprint: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(ProviderQuestionStatus, default="PENDING")
    rejection_reasons: Mapped[list | None] = mapped_column("rejectionReasons", JSONB)
    question_id: Mapped[str | None] = mapped_column(
        "questionId", ForeignKey("Question.id"), unique=True
    )
    mapper_version: Mapped[int | None] = mapped_column("mapperVersion", Integer)
    promoted_at: Mapped[datetime | None] = mapped_column(
        "promotedAt", DateTime(timezone=True)
    )


class ProviderCatalogue(Base):
    __tablename__ = "ProviderCatalogue"
    __table_args__ = (
        UniqueConstraint("provider", "subjectId", "examType", "examYear"),
    )

    id: Mapped[str] = id_column()
    provider: Mapped[str] = mapped_column(QuestionProvider, default="SDASH")
    subject_id: Mapped[str] = mapped_column("subjectId", ForeignKey("Subject.id"))
    exam_type: Mapped[str] = mapped_column("examType", ExamType)
    exam_year: Mapped[int] = mapped_column("examYear", Integer)


class ProviderState(Base):
    __tablename__ = "ProviderState"

    provider: Mapped[str] = mapped_column(QuestionProvider, primary_key=True)
    circuit: Mapped[str] = mapped_column(ProviderCircuitState, default="OK")
    cooldown_until: Mapped[datetime | None] = mapped_column(
        "cooldownUntil", DateTime(timezone=True)
    )
    credits_remaining: Mapped[int | None] = mapped_column("creditsRemaining", Integer)
    probe_in_flight: Mapped[bool] = mapped_column(
        "probeInFlight", Boolean, default=False
    )
