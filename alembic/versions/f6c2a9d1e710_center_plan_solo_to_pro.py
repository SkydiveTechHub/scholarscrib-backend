"""Rename center plan SOLO → PRO; catalog center_solo → center_pro.

Revision ID: f6c2a9d1e710
Revises: d5e1f9a4b608
Create Date: 2026-11-12 18:00:00.000000

"""

from alembic import op

revision = "f6c2a9d1e710"
down_revision = "d5e1f9a4b608"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres 10+: rename enum label in place (Centers.plan, CenterPlanCheckout.plan).
    op.execute("ALTER TYPE \"CenterPlan\" RENAME VALUE 'SOLO' TO 'PRO'")
    op.execute(
        """
        UPDATE "BillingPlan"
        SET
            code = 'center_pro',
            "displayName" = 'Center Pro',
            "lagoPlanCode" = 'center_pro',
            blurb = 'Unlimited students — up to 3 facilitators'
        WHERE code = 'center_solo'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE "BillingPlan"
        SET
            code = 'center_solo',
            "displayName" = 'Center Solo',
            "lagoPlanCode" = 'center_solo'
        WHERE code = 'center_pro'
        """
    )
    op.execute("ALTER TYPE \"CenterPlan\" RENAME VALUE 'PRO' TO 'SOLO'")
