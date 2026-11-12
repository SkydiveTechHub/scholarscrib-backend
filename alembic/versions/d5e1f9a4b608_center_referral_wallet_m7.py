"""M7: owner referral wallet, ledger, withdraw requests.

Revision ID: d5e1f9a4b608
Revises: c4d0e8f3a507
Create Date: 2026-11-12 19:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d5e1f9a4b608"
down_revision = "c4d0e8f3a507"
branch_labels = None
depends_on = None


def upgrade() -> None:
    wallet_kind = postgresql.ENUM(
        "COMMISSION",
        "REFUND_REVERSE",
        "SPEND_TOKENS",
        "WITHDRAW_HOLD",
        "WITHDRAW_REJECT",
        "ADJUSTMENT",
        name="TutorWalletLedgerKind",
        create_type=False,
    )
    wallet_kind.create(op.get_bind(), checkfirst=True)

    withdraw_status = postgresql.ENUM(
        "PENDING",
        "FULFILLED",
        "REJECTED",
        "CANCELLED",
        name="TutorWithdrawStatus",
        create_type=False,
    )
    withdraw_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "TutorWallet",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ownerId", sa.String(), nullable=False),
        sa.Column("balanceKobo", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["ownerId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ownerId"),
    )

    op.create_table(
        "TutorWalletLedger",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ownerId", sa.String(), nullable=False),
        sa.Column("walletId", sa.String(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "COMMISSION",
                "REFUND_REVERSE",
                "SPEND_TOKENS",
                "WITHDRAW_HOLD",
                "WITHDRAW_REJECT",
                "ADJUSTMENT",
                name="TutorWalletLedgerKind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("deltaKobo", sa.Integer(), nullable=False),
        sa.Column("balanceAfterKobo", sa.Integer(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=True),
        sa.Column("studentId", sa.String(), nullable=True),
        sa.Column("subscriptionId", sa.String(), nullable=True),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("idempotencyKey", sa.String(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ownerId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["subscriptionId"], ["Subscription.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["walletId"], ["TutorWallet.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "TutorWalletLedger_ownerId_createdAt_idx",
        "TutorWalletLedger",
        ["ownerId", "createdAt"],
    )
    op.create_index(
        "TutorWalletLedger_idempotencyKey_idx",
        "TutorWalletLedger",
        ["idempotencyKey"],
        unique=True,
    )
    op.create_index(
        "TutorWalletLedger_subscriptionId_idx",
        "TutorWalletLedger",
        ["subscriptionId"],
    )

    op.create_table(
        "TutorWithdrawRequest",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ownerId", sa.String(), nullable=False),
        sa.Column("walletId", sa.String(), nullable=False),
        sa.Column("amountKobo", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "FULFILLED",
                "REJECTED",
                "CANCELLED",
                name="TutorWithdrawStatus",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("holdLedgerId", sa.String(), nullable=True),
        sa.Column(
            "payoutDetails", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolvedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolvedById", sa.String(), nullable=True),
        sa.Column("resolutionNote", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["holdLedgerId"], ["TutorWalletLedger.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["ownerId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolvedById"], ["Admin.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["walletId"], ["TutorWallet.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "TutorWithdrawRequest_ownerId_createdAt_idx",
        "TutorWithdrawRequest",
        ["ownerId", "createdAt"],
    )
    op.create_index(
        "TutorWithdrawRequest_status_createdAt_idx",
        "TutorWithdrawRequest",
        ["status", "createdAt"],
    )


def downgrade() -> None:
    op.drop_index(
        "TutorWithdrawRequest_status_createdAt_idx",
        table_name="TutorWithdrawRequest",
    )
    op.drop_index(
        "TutorWithdrawRequest_ownerId_createdAt_idx",
        table_name="TutorWithdrawRequest",
    )
    op.drop_table("TutorWithdrawRequest")
    op.drop_index(
        "TutorWalletLedger_subscriptionId_idx", table_name="TutorWalletLedger"
    )
    op.drop_index(
        "TutorWalletLedger_idempotencyKey_idx", table_name="TutorWalletLedger"
    )
    op.drop_index(
        "TutorWalletLedger_ownerId_createdAt_idx", table_name="TutorWalletLedger"
    )
    op.drop_table("TutorWalletLedger")
    op.drop_table("TutorWallet")
    op.execute('DROP TYPE IF EXISTS "TutorWithdrawStatus"')
    op.execute('DROP TYPE IF EXISTS "TutorWalletLedgerKind"')
