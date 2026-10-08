"""Add Admin.sessionsValidFrom so admin logout revokes issued tokens

Revision ID: a41c9e7d2b58
Revises: 7b1e4c2a9f10
Create Date: 2026-10-08 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a41c9e7d2b58"
down_revision = "7b1e4c2a9f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "Admin",
        sa.Column("sessionsValidFrom", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("Admin", "sessionsValidFrom")
