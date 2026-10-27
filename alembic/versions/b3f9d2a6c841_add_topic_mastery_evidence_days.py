"""Add evidence-day tracking to TopicMastery

Revision ID: b3f9d2a6c841
Revises: a7d3c1e94b52
Create Date: 2026-10-27 14:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "b3f9d2a6c841"
down_revision = "a7d3c1e94b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "TopicMastery",
        sa.Column("evidenceDays", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "TopicMastery", sa.Column("lastEvidenceDay", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("TopicMastery", "lastEvidenceDay")
    op.drop_column("TopicMastery", "evidenceDays")
