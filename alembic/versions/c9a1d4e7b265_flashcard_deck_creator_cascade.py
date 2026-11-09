"""Cascade FlashcardDeck.createdBy on user delete so accounts can be removed

Revision ID: c9a1d4e7b265
Revises: b4e8f2a6d913
Create Date: 2026-11-04 14:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c9a1d4e7b265"
down_revision = "b4e8f2a6d913"
branch_labels = None
depends_on = None

CONSTRAINT = "FlashcardDeck_createdBy_fkey"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "FlashcardDeck", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT,
        "FlashcardDeck",
        "User",
        ["createdBy"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "FlashcardDeck", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT,
        "FlashcardDeck",
        "User",
        ["createdBy"],
        ["id"],
    )
