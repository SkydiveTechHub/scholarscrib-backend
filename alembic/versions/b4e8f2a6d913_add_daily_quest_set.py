"""Add DailyQuestSet so a student's daily quests are stable for the day

Revision ID: b4e8f2a6d913
Revises: a7d2e5c91b38
Create Date: 2026-10-28 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "b4e8f2a6d913"
down_revision = "a7d2e5c91b38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "DailyQuestSet",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("studentId", sa.String(), nullable=False),
        sa.Column("day", sa.String(), nullable=False),
        sa.Column("quests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "generatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("studentId", "day", name="DailyQuestSet_studentId_day_key"),
    )


def downgrade() -> None:
    op.drop_table("DailyQuestSet")
