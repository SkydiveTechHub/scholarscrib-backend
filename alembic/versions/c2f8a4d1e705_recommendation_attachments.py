"""Add Recommendation.attachments JSONB for optional revise/practice links.

Revision ID: c2f8a4d1e705
Revises: a1c4e8f2b903
Create Date: 2026-11-12 15:10:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c2f8a4d1e705"
down_revision = "a1c4e8f2b903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "Recommendation",
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("Recommendation", "attachments")
