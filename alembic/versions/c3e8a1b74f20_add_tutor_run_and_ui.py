"""Add tutor run state and study-card columns

Revision ID: c3e8a1b74f20
Revises: a9f3e2b1c804
Create Date: 2026-10-22 19:20:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c3e8a1b74f20"
down_revision = "a9f3e2b1c804"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ChatThread",
        sa.Column("activeRun", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "ChatThread",
        sa.Column("runEvents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "ChatMessage",
        sa.Column("ui", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "ChatMessage",
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ChatMessage", "tools")
    op.drop_column("ChatMessage", "ui")
    op.drop_column("ChatThread", "runEvents")
    op.drop_column("ChatThread", "activeRun")
