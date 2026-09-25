"""SQLAlchemy models for this domain."""

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column
from app.database.models.enums import (
    Difficulty,
    ExamType,
    QuestionType,
)


class Question(Base):
    __tablename__ = "Question"
    __table_args__ = (
        Index("Question_subject_type_idx", "subjectId", "questionType"),
        Index(
            "Question_subject_type_exam_idx", "subjectId", "questionType", "examType"
        ),
        Index("Question_subject_topic_idx", "subjectId", "topicId"),
        Index("Question_exam_year_idx", "examType", "examYear"),
        Index("Question_subject_exam_year_idx", "subjectId", "examType", "examYear"),
        Index("Question_subject_difficulty_idx", "subjectId", "difficulty"),
    )

    id: Mapped[str] = id_column()
    subject_id: Mapped[str] = mapped_column("subjectId", ForeignKey("Subject.id"))
    topic_id: Mapped[str | None] = mapped_column("topicId", ForeignKey("Topic.id"))
    exam_type: Mapped[str] = mapped_column("examType", ExamType)
    exam_year: Mapped[int | None] = mapped_column("examYear", Integer)
    question_number: Mapped[int | None] = mapped_column("questionNumber", Integer)
    question_text: Mapped[str] = mapped_column("questionText", Text)
    question_image_url: Mapped[str | None] = mapped_column("questionImageUrl", String)
    question_type: Mapped[str] = mapped_column(
        "questionType", QuestionType, default="OBJECTIVE"
    )
    options: Mapped[dict | None] = mapped_column(JSONB)
    correct_answer: Mapped[str] = mapped_column("correctAnswer", String)
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_image_url: Mapped[str | None] = mapped_column(
        "explanationImageUrl", String
    )
    difficulty: Mapped[str] = mapped_column(Difficulty, default="INTERMEDIATE")
    marks: Mapped[int] = mapped_column(Integer, default=1)
    time_estimate_seconds: Mapped[int] = mapped_column(
        "timeEstimateSeconds", Integer, default=90
    )
