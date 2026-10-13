"""Add DailySession so a student's study session is stable for the day

Revision ID: f6a3d9b1c470
Revises: c52f8a1e0d34
Create Date: 2026-10-12 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f6a3d9b1c470"
down_revision = "c52f8a1e0d34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "DailySession",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("studentId", sa.String(), nullable=False),
        sa.Column("day", sa.String(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "generatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("studentId", "day", name="DailySession_studentId_day_key"),
    )


def downgrade() -> None:
    op.drop_table("DailySession")
