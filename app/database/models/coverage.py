"""SQLAlchemy models for the coverage question bank.

Kept apart from ``Question``: nothing here reads or writes the provider-backed
bank that backfill works on.
"""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import Difficulty, ExamType, QuestionType


class CoverageQuestion(Base):
    __tablename__ = "CoverageQuestion"
    __table_args__ = (
        Index(
            "CoverageQuestion_subject_exam_year_idx",
            "subjectId",
            "examType",
            "examYear",
        ),
        Index("CoverageQuestion_subject_topic_idx", "subjectId", "topicId"),
    )

    id: Mapped[str] = id_column()
    subject_id: Mapped[str] = mapped_column("subjectId", ForeignKey("Subject.id"))
    topic_id: Mapped[str | None] = mapped_column(
        "topicId", ForeignKey("Topic.id", ondelete="SET NULL")
    )
    exam_type: Mapped[str] = mapped_column("examType", ExamType)
    exam_year: Mapped[int | None] = mapped_column("examYear", Integer)
    country: Mapped[str] = mapped_column(String, default="NG")
    question_number: Mapped[int | None] = mapped_column("questionNumber", Integer)
    question_type: Mapped[str] = mapped_column(
        "questionType", QuestionType, default="OBJECTIVE"
    )
    has_passage: Mapped[bool] = mapped_column("hasPassage", Boolean, default=False)
    passage: Mapped[str | None] = mapped_column(Text)
    question_text: Mapped[str] = mapped_column("questionText", Text)
    question_image: Mapped[str | None] = mapped_column("questionImage", String)
    options: Mapped[dict | None] = mapped_column(JSONB)
    correct_answer: Mapped[str] = mapped_column("correctAnswer", String)
    explanation: Mapped[str | None] = mapped_column(Text)
    detailed_explanation: Mapped[str | None] = mapped_column(
        "detailedExplanation", Text
    )
    possible_mistakes: Mapped[list | None] = mapped_column("possibleMistakes", JSONB)
    solution_image: Mapped[str | None] = mapped_column("solutionImage", String)
    difficulty: Mapped[str] = mapped_column(Difficulty, default="INTERMEDIATE")
    marks: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)
