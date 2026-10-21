"""Add RAG tutor chat and index job tables

Revision ID: a9f3e2b1c804
Revises: d4a8c2e1f905
Create Date: 2026-10-20 19:30:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a9f3e2b1c804"
down_revision = "d4a8c2e1f905"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ChatThread",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("subjectId", sa.String(), nullable=True),
        sa.Column("topicId", sa.String(), nullable=True),
        sa.Column("questionId", sa.String(), nullable=True),
        sa.Column("lessonId", sa.String(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
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
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subjectId"], ["Subject.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["topicId"], ["Topic.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["questionId"], ["Question.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lessonId"], ["Lesson.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ChatThread_userId_updatedAt_idx",
        "ChatThread",
        ["userId", "updatedAt"],
    )

    op.create_table(
        "ChatMessage",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("threadId", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("groundedOn", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["threadId"], ["ChatThread.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ChatMessage_threadId_createdAt_idx",
        "ChatMessage",
        ["threadId", "createdAt"],
    )

    op.create_table(
        "RagIndexJob",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("entityId", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finishedAt", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "RagIndexJob_status_createdAt_idx",
        "RagIndexJob",
        ["status", "createdAt"],
    )
    op.create_index(
        "RagIndexJob_source_entityId_idx",
        "RagIndexJob",
        ["source", "entityId"],
    )

    op.create_table(
        "ChatFeedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("messageId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("rating", sa.String(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["messageId"], ["ChatMessage.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ChatFeedback_messageId_idx", "ChatFeedback", ["messageId"])


def downgrade() -> None:
    op.drop_index("ChatFeedback_messageId_idx", table_name="ChatFeedback")
    op.drop_table("ChatFeedback")
    op.drop_index("RagIndexJob_source_entityId_idx", table_name="RagIndexJob")
    op.drop_index("RagIndexJob_status_createdAt_idx", table_name="RagIndexJob")
    op.drop_table("RagIndexJob")
    op.drop_index("ChatMessage_threadId_createdAt_idx", table_name="ChatMessage")
    op.drop_table("ChatMessage")
    op.drop_index("ChatThread_userId_updatedAt_idx", table_name="ChatThread")
    op.drop_table("ChatThread")
