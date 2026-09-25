"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import (
    Difficulty,
    FlashcardSource,
    FlashcardState,
    FlashcardType,
    ReviewRating,
)


class FlashcardDeck(Base):
    __tablename__ = "FlashcardDeck"
    __table_args__ = (UniqueConstraint("lessonId", "source"),)

    id: Mapped[str] = id_column()
    title: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str | None] = mapped_column(
        "subjectId", ForeignKey("Subject.id")
    )
    topic_id: Mapped[str | None] = mapped_column("topicId", ForeignKey("Topic.id"))
    lesson_id: Mapped[str | None] = mapped_column("lessonId", ForeignKey("Lesson.id"))
    source: Mapped[str] = mapped_column(FlashcardSource, default="AUTHORED")
    created_by: Mapped[str | None] = mapped_column("createdBy", ForeignKey("User.id"))
    slug: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class Flashcard(Base):
    __tablename__ = "Flashcard"
    __table_args__ = (UniqueConstraint("deckId", "sourceKey"),)

    id: Mapped[str] = id_column()
    deck_id: Mapped[str] = mapped_column(
        "deckId", ForeignKey("FlashcardDeck.id", ondelete="CASCADE")
    )
    card_type: Mapped[str] = mapped_column("cardType", FlashcardType)
    payload: Mapped[dict] = mapped_column(JSONB)
    source_key: Mapped[str | None] = mapped_column("sourceKey", String)
    difficulty: Mapped[str | None] = mapped_column(Difficulty)


class FlashcardReview(Base):
    __tablename__ = "FlashcardReview"
    __table_args__ = (UniqueConstraint("studentId", "flashcardId"),)

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    flashcard_id: Mapped[str] = mapped_column(
        "flashcardId", ForeignKey("Flashcard.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(FlashcardState, default="NEW")
    ease_factor: Mapped[float] = mapped_column("easeFactor", Float, default=2.5)
    stability: Mapped[float] = mapped_column(Float, default=0)
    difficulty: Mapped[float] = mapped_column(Float, default=5)
    interval_days: Mapped[float] = mapped_column("intervalDays", Float, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    retention: Mapped[float] = mapped_column(Float, default=0)
    due_at: Mapped[datetime | None] = mapped_column("dueAt", DateTime(timezone=True))
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        "lastReviewedAt", DateTime(timezone=True)
    )


class FlashcardReviewLog(Base):
    __tablename__ = "FlashcardReviewLog"

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    flashcard_id: Mapped[str] = mapped_column(
        "flashcardId", ForeignKey("Flashcard.id", ondelete="CASCADE")
    )
    rating: Mapped[str] = mapped_column(ReviewRating)
    response_time_ms: Mapped[int | None] = mapped_column("responseTimeMs", Integer)
    scheduled_days: Mapped[float | None] = mapped_column("scheduledDays", Float)
    objective_correct: Mapped[bool | None] = mapped_column("objectiveCorrect", Boolean)
    reviewed_at: Mapped[datetime] = mapped_column(
        "reviewedAt", DateTime(timezone=True), server_default=func.now()
    )


class FlashcardEnrollment(Base):
    __tablename__ = "FlashcardEnrollment"
    __table_args__ = (UniqueConstraint("studentId", "deckId"),)

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    deck_id: Mapped[str] = mapped_column(
        "deckId", ForeignKey("FlashcardDeck.id", ondelete="CASCADE")
    )
