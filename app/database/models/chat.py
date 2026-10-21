"""Chat threads, messages, RAG index jobs, and tutor feedback."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column


class ChatThread(Base):
    __tablename__ = "ChatThread"
    __table_args__ = (Index("ChatThread_userId_updatedAt_idx", "userId", "updatedAt"),)

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    title: Mapped[str | None] = mapped_column(String)
    subject_id: Mapped[str | None] = mapped_column(
        "subjectId", ForeignKey("Subject.id", ondelete="SET NULL")
    )
    topic_id: Mapped[str | None] = mapped_column(
        "topicId", ForeignKey("Topic.id", ondelete="SET NULL")
    )
    question_id: Mapped[str | None] = mapped_column(
        "questionId", ForeignKey("Question.id", ondelete="SET NULL")
    )
    lesson_id: Mapped[str | None] = mapped_column(
        "lessonId", ForeignKey("Lesson.id", ondelete="SET NULL")
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class ChatMessage(Base):
    __tablename__ = "ChatMessage"
    __table_args__ = (
        Index("ChatMessage_threadId_createdAt_idx", "threadId", "createdAt"),
    )

    id: Mapped[str] = id_column()
    thread_id: Mapped[str] = mapped_column(
        "threadId", ForeignKey("ChatThread.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    grounded_on: Mapped[list | None] = mapped_column("groundedOn", JSONB)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class RagIndexJob(Base):
    __tablename__ = "RagIndexJob"
    __table_args__ = (
        Index("RagIndexJob_status_createdAt_idx", "status", "createdAt"),
        Index("RagIndexJob_source_entityId_idx", "source", "entityId"),
    )

    id: Mapped[str] = id_column()
    source: Mapped[str] = mapped_column(String)  # lesson | question | material | full
    entity_id: Mapped[str | None] = mapped_column("entityId", String)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    finished_at: Mapped[datetime | None] = mapped_column(
        "finishedAt", DateTime(timezone=True), nullable=True
    )


class ChatFeedback(Base):
    __tablename__ = "ChatFeedback"
    __table_args__ = (Index("ChatFeedback_messageId_idx", "messageId"),)

    id: Mapped[str] = id_column()
    message_id: Mapped[str] = mapped_column(
        "messageId", ForeignKey("ChatMessage.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    rating: Mapped[str] = mapped_column(String)  # up | down
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
