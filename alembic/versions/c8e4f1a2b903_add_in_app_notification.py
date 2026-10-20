"""Add InAppNotification and merge alembic heads

Revision ID: c8e4f1a2b903
Revises: 1d1a929beb8c, b7d41c9e5a02
Create Date: 2026-10-20 08:30:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c8e4f1a2b903"
down_revision = ("1d1a929beb8c", "b7d41c9e5a02")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "InAppNotification",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("sourceKey", sa.String(), nullable=False),
        sa.Column("readAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "userId", "sourceKey", name="InAppNotification_userId_sourceKey_key"
        ),
    )
    op.create_index(
        "InAppNotification_userId_createdAt_idx",
        "InAppNotification",
        ["userId", "createdAt"],
    )


def downgrade() -> None:
    op.drop_index(
        "InAppNotification_userId_createdAt_idx", table_name="InAppNotification"
    )
    op.drop_table("InAppNotification")
