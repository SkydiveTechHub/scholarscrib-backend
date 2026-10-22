"""Admin Astra threads. Separate from student ChatThread, which belongs to User."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column


class AdminChatThread(Base):
    __tablename__ = "AdminChatThread"
    __table_args__ = (
        Index("AdminChatThread_adminId_updatedAt_idx", "adminId", "updatedAt"),
    )

    id: Mapped[str] = id_column()
    admin_id: Mapped[str] = mapped_column(
        "adminId", ForeignKey("Admin.id", ondelete="CASCADE")
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


class AdminChatMessage(Base):
    __tablename__ = "AdminChatMessage"
    __table_args__ = (
        Index("AdminChatMessage_threadId_createdAt_idx", "threadId", "createdAt"),
    )

    id: Mapped[str] = id_column()
    thread_id: Mapped[str] = mapped_column(
        "threadId", ForeignKey("AdminChatThread.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    grounded_on: Mapped[list | None] = mapped_column("groundedOn", JSONB)
    ui: Mapped[list | None] = mapped_column("ui", JSONB, nullable=True, default=None)
    tools: Mapped[list | None] = mapped_column(
        "tools", JSONB, nullable=True, default=None
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")
