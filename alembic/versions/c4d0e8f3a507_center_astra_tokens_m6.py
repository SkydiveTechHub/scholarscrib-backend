"""M6: Tutor Astra chat + per-center AI token pool.

Revision ID: c4d0e8f3a507
Revises: f2a8c6e1b305
Create Date: 2026-11-12 18:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c4d0e8f3a507"
down_revision = "f2a8c6e1b305"
branch_labels = None
depends_on = None


def upgrade() -> None:
    token_kind = postgresql.ENUM(
        "PURCHASE",
        "SPEND",
        "STARTER",
        "ADJUSTMENT",
        "WALLET",
        name="CenterTokenLedgerKind",
        create_type=False,
    )
    token_kind.create(op.get_bind(), checkfirst=True)

    checkout_status = postgresql.ENUM(
        "PENDING",
        "ACTIVE",
        "FAILED",
        "ABANDONED",
        name="CenterCheckoutStatus",
        create_type=False,
    )

    op.create_table(
        "CenterTokenPool",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("centerId"),
    )

    op.create_table(
        "CenterTokenLedger",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("actorId", sa.String(), nullable=True),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "PURCHASE",
                "SPEND",
                "STARTER",
                "ADJUSTMENT",
                "WALLET",
                name="CenterTokenLedgerKind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balanceAfter", sa.Integer(), nullable=False),
        sa.Column("packCode", sa.String(), nullable=True),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("idempotencyKey", sa.String(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actorId"], ["User.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "CenterTokenLedger_centerId_createdAt_idx",
        "CenterTokenLedger",
        ["centerId", "createdAt"],
    )
    op.create_index(
        "CenterTokenLedger_idempotencyKey_idx",
        "CenterTokenLedger",
        ["idempotencyKey"],
        unique=True,
    )

    op.create_table(
        "CenterTokenCheckout",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("packCode", sa.String(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(), nullable=False),
        sa.Column("amountKobo", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="NGN"),
        sa.Column("status", checkout_status, nullable=False, server_default="PENDING"),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("paidAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
    )
    op.create_index(
        "CenterTokenCheckout_centerId_idx", "CenterTokenCheckout", ["centerId"]
    )
    op.create_index(
        "CenterTokenCheckout_userId_status_idx",
        "CenterTokenCheckout",
        ["userId", "status"],
    )

    op.create_table(
        "CenterChatThread",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("tutorId", sa.String(), nullable=False),
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
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tutorId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "CenterChatThread_centerId_tutorId_updatedAt_idx",
        "CenterChatThread",
        ["centerId", "tutorId", "updatedAt"],
    )

    op.create_table(
        "CenterChatMessage",
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
            ["threadId"], ["CenterChatThread.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "CenterChatMessage_threadId_createdAt_idx",
        "CenterChatMessage",
        ["threadId", "createdAt"],
    )


def downgrade() -> None:
    op.drop_index(
        "CenterChatMessage_threadId_createdAt_idx", table_name="CenterChatMessage"
    )
    op.drop_table("CenterChatMessage")
    op.drop_index(
        "CenterChatThread_centerId_tutorId_updatedAt_idx",
        table_name="CenterChatThread",
    )
    op.drop_table("CenterChatThread")
    op.drop_index(
        "CenterTokenCheckout_userId_status_idx", table_name="CenterTokenCheckout"
    )
    op.drop_index("CenterTokenCheckout_centerId_idx", table_name="CenterTokenCheckout")
    op.drop_table("CenterTokenCheckout")
    op.drop_index(
        "CenterTokenLedger_idempotencyKey_idx", table_name="CenterTokenLedger"
    )
    op.drop_index(
        "CenterTokenLedger_centerId_createdAt_idx", table_name="CenterTokenLedger"
    )
    op.drop_table("CenterTokenLedger")
    op.drop_table("CenterTokenPool")
    op.execute('DROP TYPE IF EXISTS "CenterTokenLedgerKind"')
