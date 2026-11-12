"""Tutor Astra (center-scoped) chat threads. Separate from student and admin Astra."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column


class CenterChatThread(Base):
    __tablename__ = "CenterChatThread"
    __table_args__ = (
        Index(
            "CenterChatThread_centerId_tutorId_updatedAt_idx",
            "centerId",
            "tutorId",
            "updatedAt",
        ),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    tutor_id: Mapped[str] = mapped_column(
        "tutorId", ForeignKey("User.id", ondelete="CASCADE")
    )
    title: Mapped[str | None] = mapped_column(String)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    active_run: Mapped[dict | None] = mapped_column(
        "activeRun", JSONB, nullable=True, default=None
    )
    run_events: Mapped[list | None] = mapped_column(
        "runEvents", JSONB, nullable=True, default=None
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class CenterChatMessage(Base):
    __tablename__ = "CenterChatMessage"
    __table_args__ = (
        Index("CenterChatMessage_threadId_createdAt_idx", "threadId", "createdAt"),
    )

    id: Mapped[str] = id_column()
    thread_id: Mapped[str] = mapped_column(
        "threadId", ForeignKey("CenterChatThread.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    grounded_on: Mapped[list | None] = mapped_column("groundedOn", JSONB)
    ui: Mapped[list | None] = mapped_column("ui", JSONB, nullable=True, default=None)
    tools: Mapped[list | None] = mapped_column(
        "tools", JSONB, nullable=True, default=None
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")
