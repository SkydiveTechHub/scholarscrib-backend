"""Add StudyPlan.timetable

Revision ID: d7a3f5c1e208
Revises: c6d8e0f2a413
Create Date: 2026-10-25 10:00:00.000000

The student's timetable intent (per-day minutes and subject-to-day allocations).
Nullable with no backfill: NULL is a Smart plan, which is how every existing plan
already behaves. The downgrade drops the column; no StudyPlanItem is touched.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "d7a3f5c1e208"
down_revision = "c6d8e0f2a413"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "StudyPlan",
        sa.Column("timetable", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("StudyPlan", "timetable")
