"""Add ProviderFetch pagination checkpoint columns

Revision ID: d4a8c2e1f905
Revises: c8e4f1a2b903
Create Date: 2026-10-20 10:30:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "d4a8c2e1f905"
down_revision = "c8e4f1a2b903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ProviderFetch",
        sa.Column("nextCursor", sa.String(), nullable=True),
    )
    op.add_column(
        "ProviderFetch",
        sa.Column("checkpointAt", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ProviderFetch", "checkpointAt")
    op.drop_column("ProviderFetch", "nextCursor")
