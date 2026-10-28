"""Add the XpTransaction ledger

Revision ID: a7d2e5c91b38
Revises: f1b3d7e9a024
Create Date: 2026-10-28 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a7d2e5c91b38"
down_revision = "f1b3d7e9a024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "XpTransaction",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("studentId", sa.String(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("sourceId", sa.String(), nullable=False),
        sa.Column("idempotencyKey", sa.String(), nullable=False),
        sa.Column(
            "occurredAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotencyKey"),
    )
    op.create_index(
        "XpTransaction_student_occurred_idx",
        "XpTransaction",
        ["studentId", "occurredAt"],
    )


def downgrade() -> None:
    op.drop_index("XpTransaction_student_occurred_idx", table_name="XpTransaction")
    op.drop_table("XpTransaction")
