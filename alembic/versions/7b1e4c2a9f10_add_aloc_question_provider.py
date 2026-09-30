"""Add ALOC question provider

Revision ID: 7b1e4c2a9f10
Revises: d30a7d28fe0a
Create Date: 2026-09-28 15:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "7b1e4c2a9f10"
down_revision = "d30a7d28fe0a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("""ALTER TYPE "QuestionProvider" ADD VALUE IF NOT EXISTS 'ALOC'""")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type.
    pass
