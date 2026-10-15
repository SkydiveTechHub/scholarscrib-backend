"""Drop FlashcardReview.retention and FlashcardReviewLog.scheduledDays

Revision ID: e8b2c4f7a613
Revises: f6a3d9b1c470
Create Date: 2026-10-14 12:00:00.000000

`retention` was the recall right after scheduling, always 0.87..1.0, so nothing
could ever read it as "fading"; recall is computed from stability and the last
review time instead. `scheduledDays` was written on every review and read by
nothing.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "e8b2c4f7a613"
down_revision = "f6a3d9b1c470"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("FlashcardReview", "retention")
    op.drop_column("FlashcardReviewLog", "scheduledDays")


def downgrade() -> None:
    op.add_column(
        "FlashcardReviewLog", sa.Column("scheduledDays", sa.Float(), nullable=True)
    )
    op.add_column(
        "FlashcardReview",
        sa.Column("retention", sa.Float(), nullable=False, server_default="0"),
    )
    op.alter_column("FlashcardReview", "retention", server_default=None)
