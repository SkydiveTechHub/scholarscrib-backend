"""Add examType, examYear to ChatThread and index on (userId, questionId)

Revision ID: f1b3d7e9a024
Revises: c3e8a1b74f20
Create Date: 2026-10-28 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "f1b3d7e9a024"
down_revision = "b3f9d2a6c841"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ChatThread",
        sa.Column("examType", sa.String(), nullable=True),
    )
    op.add_column(
        "ChatThread",
        sa.Column("examYear", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ChatThread_userId_questionId_idx",
        "ChatThread",
        ["userId", "questionId"],
    )


def downgrade() -> None:
    op.drop_index("ChatThread_userId_questionId_idx", table_name="ChatThread")
    op.drop_column("ChatThread", "examYear")
    op.drop_column("ChatThread", "examType")
