"""Let assessments and responses point at coverage questions

Revision ID: c6d8e0f2a413
Revises: b5c7d9e1f302, e7c1a9d04b32
Create Date: 2026-10-24 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "c6d8e0f2a413"
down_revision = ("b5c7d9e1f302", "e7c1a9d04b32")
branch_labels = None
depends_on = None

TABLES = ("AssessmentQuestion", "QuestionResponse")
PARENT_COLUMN = {"AssessmentQuestion": "assessmentId", "QuestionResponse": "attemptId"}


def upgrade() -> None:
    for table in TABLES:
        op.alter_column(table, "questionId", existing_type=sa.String(), nullable=True)
        op.add_column(
            table, sa.Column("coverageQuestionId", sa.String(), nullable=True)
        )
        op.create_foreign_key(
            f"{table}_coverageQuestionId_fkey",
            table,
            "CoverageQuestion",
            ["coverageQuestionId"],
            ["id"],
        )
        op.create_unique_constraint(
            f"{table}_{PARENT_COLUMN[table]}_coverageQuestionId_key",
            table,
            [PARENT_COLUMN[table], "coverageQuestionId"],
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_constraint(
            f"{table}_{PARENT_COLUMN[table]}_coverageQuestionId_key",
            table,
            type_="unique",
        )
        op.drop_constraint(
            f"{table}_coverageQuestionId_fkey", table, type_="foreignkey"
        )
        # Rows that point at a coverage question have no bank question to go back to.
        op.execute(f'DELETE FROM "{table}" WHERE "questionId" IS NULL')
        op.drop_column(table, "coverageQuestionId")
        op.alter_column(table, "questionId", existing_type=sa.String(), nullable=False)
