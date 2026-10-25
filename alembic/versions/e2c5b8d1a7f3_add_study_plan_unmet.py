"""Add StudyPlan.unmet

Revision ID: e2c5b8d1a7f3
Revises: d7a3f5c1e208
Create Date: 2026-10-25 14:00:00.000000

Timetable sessions the last replan could not give to their subject, so the plan
page can explain them. Nullable with no backfill: NULL means nothing is unmet,
which is true of every existing plan. The downgrade drops the column.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "e2c5b8d1a7f3"
down_revision = "d7a3f5c1e208"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "StudyPlan",
        sa.Column("unmet", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("StudyPlan", "unmet")
