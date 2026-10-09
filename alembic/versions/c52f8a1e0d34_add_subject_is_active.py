"""Add Subject.isActive so admins can hide a subject from the classroom

Revision ID: c52f8a1e0d34
Revises: a41c9e7d2b58
Create Date: 2026-10-09 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c52f8a1e0d34"
down_revision = "a41c9e7d2b58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing subjects stay visible: the default is true.
    op.add_column(
        "Subject",
        sa.Column(
            "isActive", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )


def downgrade() -> None:
    op.drop_column("Subject", "isActive")
