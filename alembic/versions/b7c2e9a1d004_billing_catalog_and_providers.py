"""Billing catalog, provider fields, enums, EmailOutbox (squashed).

Replaces e4f1a8c2b907 + f2a8c1d4e605 + a3b9c2d4e701. Pre-launch only —
stamp or recreate DB if those revisions were applied locally.

Revision ID: b7c2e9a1d004
Revises: c9a1d4e7b265
Create Date: 2026-11-08 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b7c2e9a1d004"
down_revision = "c9a1d4e7b265"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE \"SubscriptionSource\" ADD VALUE IF NOT EXISTS 'LAGO'")

    billing_status = postgresql.ENUM(
        "freemium",
        "trialing",
        "active",
        "canceling",
        "past_due",
        name="BillingStatus",
        create_type=False,
    )
    subscription_kind = postgresql.ENUM(
        "intro",
        "checkout",
        "renewal",
        "comp",
        "freemium",
        name="SubscriptionKind",
        create_type=False,
    )
    email_outbox_status = postgresql.ENUM(
        "pending",
        "sent",
        "failed",
        name="EmailOutboxStatus",
        create_type=False,
    )
    billing_status.create(op.get_bind(), checkfirst=True)
    subscription_kind.create(op.get_bind(), checkfirst=True)
    email_outbox_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "BillingPlan",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("displayName", sa.String(), nullable=False),
        sa.Column("amountKobo", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("interval", sa.String(), nullable=False),
        sa.Column("lagoPlanCode", sa.String(), nullable=False),
        sa.Column("paystackPlanCode", sa.String(), nullable=True),
        sa.Column("isPurchasable", sa.Boolean(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("blurb", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_BillingPlan_code", "BillingPlan", ["code"])

    op.create_table(
        "BillingFeature",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("valueType", sa.String(), nullable=False),
        sa.Column(
            "selectOptions", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("gateKey", sa.String(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("gateKey"),
    )
    op.create_index("ix_BillingFeature_code", "BillingFeature", ["code"])

    op.create_table(
        "BillingPlanEntitlement",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("planId", sa.String(), nullable=False),
        sa.Column("featureId", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "privilegeValue", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
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
            ["featureId"], ["BillingFeature.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["planId"], ["BillingPlan.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "planId", "featureId", name="BillingPlanEntitlement_plan_feature"
        ),
    )
    op.create_index(
        "ix_BillingPlanEntitlement_planId", "BillingPlanEntitlement", ["planId"]
    )
    op.create_index(
        "ix_BillingPlanEntitlement_featureId", "BillingPlanEntitlement", ["featureId"]
    )

    op.create_table(
        "LagoEvent",
        sa.Column("eventKey", sa.String(), nullable=False),
        sa.Column("webhookType", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("eventKey"),
    )

    op.create_table(
        "BillingReminderLog",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("leadDays", sa.Integer(), nullable=False),
        sa.Column("periodKey", sa.String(), nullable=False),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "userId",
            "kind",
            "leadDays",
            "periodKey",
            name="BillingReminderLog_unique",
        ),
    )
    op.create_index("ix_BillingReminderLog_userId", "BillingReminderLog", ["userId"])

    op.create_table(
        "EmailOutbox",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("idempotencyKey", sa.String(), nullable=False),
        sa.Column("toEmail", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("template", sa.String(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            email_outbox_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("availableAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lastError", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotencyKey"),
    )
    op.create_index("ix_EmailOutbox_idempotencyKey", "EmailOutbox", ["idempotencyKey"])
    op.create_index("EmailOutbox_pending_idx", "EmailOutbox", ["status", "availableAt"])

    op.add_column(
        "User",
        sa.Column("planCode", sa.String(), server_default="freemium", nullable=False),
    )
    op.add_column(
        "User",
        sa.Column(
            "billingStatus",
            billing_status,
            server_default="freemium",
            nullable=False,
        ),
    )
    op.add_column(
        "User", sa.Column("paystackAuthorizationCode", sa.String(), nullable=True)
    )
    op.add_column("User", sa.Column("paystackCustomerCode", sa.String(), nullable=True))
    op.add_column("User", sa.Column("paystackCardLast4", sa.String(), nullable=True))
    op.add_column(
        "User", sa.Column("paystackSubscriptionCode", sa.String(), nullable=True)
    )
    op.add_column("User", sa.Column("paystackEmailToken", sa.String(), nullable=True))
    op.add_column(
        "User", sa.Column("nextBillingAt", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "User", sa.Column("introEndsAt", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "User", sa.Column("lagoSubscriptionExternalId", sa.String(), nullable=True)
    )

    op.add_column(
        "Subscription",
        sa.Column("lagoSubscriptionExternalId", sa.String(), nullable=True),
    )
    op.add_column(
        "Subscription", sa.Column("lagoInvoiceId", sa.String(), nullable=True)
    )
    op.add_column(
        "Subscription",
        sa.Column("kind", subscription_kind, nullable=True),
    )
    op.add_column(
        "Subscription",
        sa.Column("priorPaystackSubscriptionCode", sa.String(), nullable=True),
    )
    op.add_column(
        "Subscription",
        sa.Column("priorPaystackEmailToken", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("Subscription", "priorPaystackEmailToken")
    op.drop_column("Subscription", "priorPaystackSubscriptionCode")
    op.drop_column("Subscription", "kind")
    op.drop_column("Subscription", "lagoInvoiceId")
    op.drop_column("Subscription", "lagoSubscriptionExternalId")

    op.drop_column("User", "lagoSubscriptionExternalId")
    op.drop_column("User", "introEndsAt")
    op.drop_column("User", "nextBillingAt")
    op.drop_column("User", "paystackEmailToken")
    op.drop_column("User", "paystackSubscriptionCode")
    op.drop_column("User", "paystackCardLast4")
    op.drop_column("User", "paystackCustomerCode")
    op.drop_column("User", "paystackAuthorizationCode")
    op.drop_column("User", "billingStatus")
    op.drop_column("User", "planCode")

    op.drop_index("EmailOutbox_pending_idx", table_name="EmailOutbox")
    op.drop_index("ix_EmailOutbox_idempotencyKey", table_name="EmailOutbox")
    op.drop_table("EmailOutbox")
    op.drop_index("ix_BillingReminderLog_userId", table_name="BillingReminderLog")
    op.drop_table("BillingReminderLog")
    op.drop_table("LagoEvent")
    op.drop_index(
        "ix_BillingPlanEntitlement_featureId", table_name="BillingPlanEntitlement"
    )
    op.drop_index(
        "ix_BillingPlanEntitlement_planId", table_name="BillingPlanEntitlement"
    )
    op.drop_table("BillingPlanEntitlement")
    op.drop_index("ix_BillingFeature_code", table_name="BillingFeature")
    op.drop_table("BillingFeature")
    op.drop_index("ix_BillingPlan_code", table_name="BillingPlan")
    op.drop_table("BillingPlan")

    op.execute('DROP TYPE IF EXISTS "EmailOutboxStatus"')
    op.execute('DROP TYPE IF EXISTS "SubscriptionKind"')
    op.execute('DROP TYPE IF EXISTS "BillingStatus"')
