"""Add CoverageQuestion table

Revision ID: b5c7d9e1f302
Revises: a9f3e2b1c804
Create Date: 2026-10-23 10:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b5c7d9e1f302"
down_revision = "a9f3e2b1c804"
branch_labels = None
depends_on = None

exam_type = postgresql.ENUM(name="ExamType", create_type=False)
question_type = postgresql.ENUM(name="QuestionType", create_type=False)
difficulty = postgresql.ENUM(name="Difficulty", create_type=False)


def upgrade() -> None:
    op.create_table(
        "CoverageQuestion",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("subjectId", sa.String(), nullable=False),
        sa.Column("topicId", sa.String(), nullable=True),
        sa.Column("examType", exam_type, nullable=False),
        sa.Column("examYear", sa.Integer(), nullable=True),
        sa.Column("country", sa.String(), nullable=False, server_default="NG"),
        sa.Column("questionNumber", sa.Integer(), nullable=True),
        sa.Column(
            "questionType", question_type, nullable=False, server_default="OBJECTIVE"
        ),
        sa.Column("hasPassage", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("passage", sa.Text(), nullable=True),
        sa.Column("questionText", sa.Text(), nullable=False),
        sa.Column("questionImage", sa.String(), nullable=True),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("correctAnswer", sa.String(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("detailedExplanation", sa.Text(), nullable=True),
        sa.Column(
            "possibleMistakes", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("solutionImage", sa.String(), nullable=True),
        sa.Column(
            "difficulty", difficulty, nullable=False, server_default="INTERMEDIATE"
        ),
        sa.Column("marks", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["subjectId"], ["Subject.id"]),
        sa.ForeignKeyConstraint(["topicId"], ["Topic.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "CoverageQuestion_subject_exam_year_idx",
        "CoverageQuestion",
        ["subjectId", "examType", "examYear"],
    )
    op.create_index(
        "CoverageQuestion_subject_topic_idx",
        "CoverageQuestion",
        ["subjectId", "topicId"],
    )


def downgrade() -> None:
    op.drop_index("CoverageQuestion_subject_topic_idx", table_name="CoverageQuestion")
    op.drop_index(
        "CoverageQuestion_subject_exam_year_idx", table_name="CoverageQuestion"
    )
    op.drop_table("CoverageQuestion")
