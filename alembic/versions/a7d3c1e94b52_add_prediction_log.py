"""Add PredictionLog so the mastery engine's predictions can be scored

Revision ID: a7d3c1e94b52
Revises: e2c5b8d1a7f3
Create Date: 2026-10-27 10:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "a7d3c1e94b52"
down_revision = "e2c5b8d1a7f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "PredictionLog",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("studentId", sa.String(), nullable=False),
        sa.Column("subjectId", sa.String(), nullable=True),
        sa.Column("topicId", sa.String(), nullable=False),
        sa.Column("questionId", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("predictedP", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("accObservations", sa.Integer(), nullable=False),
        sa.Column(
            "difficulty",
            postgresql.ENUM(
                "BASIC", "INTERMEDIATE", "ADVANCED", name="Difficulty", create_type=False
            ),
            nullable=True,
        ),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("scoringVersion", sa.Integer(), nullable=False),
        sa.Column("occurredAt", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subjectId"], ["Subject.id"]),
        sa.ForeignKeyConstraint(["topicId"], ["Topic.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "PredictionLog_version_occurred_idx",
        "PredictionLog",
        ["scoringVersion", "occurredAt"],
    )
    op.create_index("PredictionLog_topic_idx", "PredictionLog", ["topicId"])


def downgrade() -> None:
    op.drop_index("PredictionLog_topic_idx", table_name="PredictionLog")
    op.drop_index("PredictionLog_version_occurred_idx", table_name="PredictionLog")
    op.drop_table("PredictionLog")
