"""Add admin Astra chat tables

Revision ID: e7c1a9d04b32
Revises: c3e8a1b74f20
Create Date: 2026-10-22 21:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e7c1a9d04b32"
down_revision = "c3e8a1b74f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "AdminChatThread",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("adminId", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("activeRun", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("runEvents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["adminId"], ["Admin.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "AdminChatThread_adminId_updatedAt_idx",
        "AdminChatThread",
        ["adminId", "updatedAt"],
    )

    op.create_table(
        "AdminChatMessage",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("threadId", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("groundedOn", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ui", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["threadId"], ["AdminChatThread.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "AdminChatMessage_threadId_createdAt_idx",
        "AdminChatMessage",
        ["threadId", "createdAt"],
    )


def downgrade() -> None:
    op.drop_index(
        "AdminChatMessage_threadId_createdAt_idx", table_name="AdminChatMessage"
    )
    op.drop_table("AdminChatMessage")
    op.drop_index("AdminChatThread_adminId_updatedAt_idx", table_name="AdminChatThread")
    op.drop_table("AdminChatThread")
