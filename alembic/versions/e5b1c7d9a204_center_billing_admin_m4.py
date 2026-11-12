"""M4: center Solo checkout, tutor verify, abuse reports.

Revision ID: e5b1c7d9a204
Revises: c2f8a4d1e705
Create Date: 2026-11-12 15:20:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e5b1c7d9a204"
down_revision = "c2f8a4d1e705"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "Center",
        sa.Column("planPaidUntil", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "User",
        sa.Column("tutorVerifiedAt", sa.DateTime(timezone=True), nullable=True),
    )

    center_checkout_status = postgresql.ENUM(
        "PENDING",
        "ACTIVE",
        "FAILED",
        "ABANDONED",
        name="CenterCheckoutStatus",
        create_type=False,
    )
    abuse_report_kind = postgresql.ENUM(
        "TUTOR",
        "INVITE",
        "RECOMMENDATION",
        name="CenterAbuseReportKind",
        create_type=False,
    )
    abuse_report_status = postgresql.ENUM(
        "OPEN",
        "REVIEWING",
        "RESOLVED",
        "DISMISSED",
        name="CenterAbuseReportStatus",
        create_type=False,
    )
    for enum_type in (
        center_checkout_status,
        abuse_report_kind,
        abuse_report_status,
    ):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "CenterPlanCheckout",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column(
            "plan",
            postgresql.ENUM(
                "FREE", "SOLO", "TEAM", name="CenterPlan", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "period",
            postgresql.ENUM(
                "MONTHLY", "YEARLY", name="BillingPeriod", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("reference", sa.String(), nullable=False),
        sa.Column("amountKobo", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "ACTIVE",
                "FAILED",
                "ABANDONED",
                name="CenterCheckoutStatus",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("paidAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("startsAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("endsAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="CenterPlanCheckout_reference"),
    )
    op.create_index(
        "CenterPlanCheckout_centerId_idx", "CenterPlanCheckout", ["centerId"]
    )
    op.create_index(
        "CenterPlanCheckout_userId_status_idx",
        "CenterPlanCheckout",
        ["userId", "status"],
    )

    op.create_table(
        "CenterAbuseReport",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("reporterId", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=True),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "TUTOR",
                "INVITE",
                "RECOMMENDATION",
                name="CenterAbuseReportKind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("targetId", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "OPEN",
                "REVIEWING",
                "RESOLVED",
                "DISMISSED",
                name="CenterAbuseReportStatus",
                create_type=False,
            ),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["reporterId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolvedById"], ["Admin.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "CenterAbuseReport_status_createdAt_idx",
        "CenterAbuseReport",
        ["status", "createdAt"],
    )
    op.create_index("CenterAbuseReport_kind_idx", "CenterAbuseReport", ["kind"])


def downgrade() -> None:
    op.drop_index("CenterAbuseReport_kind_idx", table_name="CenterAbuseReport")
    op.drop_index(
        "CenterAbuseReport_status_createdAt_idx", table_name="CenterAbuseReport"
    )
    op.drop_table("CenterAbuseReport")
    op.drop_index(
        "CenterPlanCheckout_userId_status_idx", table_name="CenterPlanCheckout"
    )
    op.drop_index("CenterPlanCheckout_centerId_idx", table_name="CenterPlanCheckout")
    op.drop_table("CenterPlanCheckout")
    op.drop_column("User", "tutorVerifiedAt")
    op.drop_column("Center", "planPaidUntil")
    op.execute('DROP TYPE IF EXISTS "CenterAbuseReportStatus"')
    op.execute('DROP TYPE IF EXISTS "CenterAbuseReportKind"')
    op.execute('DROP TYPE IF EXISTS "CenterCheckoutStatus"')
