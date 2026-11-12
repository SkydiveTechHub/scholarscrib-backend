"""Center plan catalog hook (Free solo / Pro yearly / Team yearly).

Student billing plans stay in ``app.services.billing.catalog``. Center plans are
seeded into the same ``BillingPlan`` table with ``center_`` codes so M4 can wire
Paystack yearly checkout without mixing them into the student public catalog.

Free is the solo (owner-only) plan. Paid mid-tier is Pro.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import cuid
from app.database.models.billing_catalog import BillingPlan
from app.domain import (
    CENTER_FREE_ACTIVE_STUDENT_LIMIT,
    CENTER_PRO_MAX_FACILITATORS,
    CENTER_PRO_YEARLY_KOBO,
    CENTER_TEAM_MAX_FACILITATORS,
    CENTER_TEAM_YEARLY_KOBO,
)

CENTER_PLAN_CODES = frozenset({"center_free", "center_pro", "center_team"})

SEED_CENTER_PLANS = [
    {
        "code": "center_free",
        "display_name": "Center Free",
        "amount_kobo": 0,
        "interval": "yearly",
        "is_purchasable": False,
        "rank": 100,
        "blurb": (
            f"Solo owner — fewer than {CENTER_FREE_ACTIVE_STUDENT_LIMIT} "
            "active students"
        ),
    },
    {
        "code": "center_pro",
        "display_name": "Center Pro",
        "amount_kobo": CENTER_PRO_YEARLY_KOBO,
        "interval": "yearly",
        "is_purchasable": True,
        "rank": 101,
        "blurb": (
            f"Unlimited students — up to {CENTER_PRO_MAX_FACILITATORS} facilitators"
        ),
    },
    {
        "code": "center_team",
        "display_name": "Center Team",
        "amount_kobo": CENTER_TEAM_YEARLY_KOBO,
        "interval": "yearly",
        "is_purchasable": True,
        "rank": 102,
        "blurb": (
            f"Unlimited students — up to {CENTER_TEAM_MAX_FACILITATORS} facilitators"
        ),
    },
]


def is_center_plan_code(code: str) -> bool:
    return code in CENTER_PLAN_CODES or code.startswith("center_")


async def seed_center_catalog(session: AsyncSession) -> int:
    """Idempotent upsert of center Free/Pro/Team yearly plan rows."""
    # Migrate legacy catalog code if present (pre-Pro rename).
    legacy = await session.scalar(
        select(BillingPlan).where(BillingPlan.code == "center_solo")
    )
    if legacy is not None:
        existing_pro = await session.scalar(
            select(BillingPlan).where(BillingPlan.code == "center_pro")
        )
        if existing_pro is None:
            legacy.code = "center_pro"
            legacy.lago_plan_code = "center_pro"
            legacy.display_name = "Center Pro"
        else:
            legacy.active = False

    count = 0
    for row in SEED_CENTER_PLANS:
        existing = await session.scalar(
            select(BillingPlan).where(BillingPlan.code == row["code"])
        )
        if existing is None:
            session.add(
                BillingPlan(
                    id=cuid(),
                    code=row["code"],
                    display_name=row["display_name"],
                    amount_kobo=row["amount_kobo"],
                    currency="NGN",
                    interval=row["interval"],
                    lago_plan_code=row["code"],
                    is_purchasable=row["is_purchasable"],
                    rank=row["rank"],
                    blurb=row["blurb"],
                    active=True,
                )
            )
        else:
            existing.display_name = row["display_name"]
            existing.amount_kobo = row["amount_kobo"]
            existing.interval = row["interval"]
            existing.is_purchasable = row["is_purchasable"]
            existing.rank = row["rank"]
            existing.blurb = row["blurb"]
            existing.lago_plan_code = row["code"]
            existing.active = True
        count += 1
    await session.flush()
    return count
