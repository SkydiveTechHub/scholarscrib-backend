"""SQLAlchemy models for this domain."""

from sqlalchemy import (
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column


class JambCombination(Base):
    __tablename__ = "JambCombination"

    id: Mapped[str] = id_column()
    course_of_study: Mapped[str] = mapped_column("courseOfStudy", String)
    faculty: Mapped[str] = mapped_column(String)
    required_subjects: Mapped[list] = mapped_column("requiredSubjects", JSONB)
    alternate_subjects: Mapped[list | None] = mapped_column("alternateSubjects", JSONB)
