"""One-shot migration: preserve live Paystack/COMP term access under Lago fields.

Existing ACTIVE term holders keep their tier until `ends_at`. We stamp
`plan_code` / `billing_status` / `next_billing_at` on the user and (when Lago
is configured) create a matching Lago subscription so renewals can take over
after the current term. COMP rows stay COMP; paid Paystack rows become LAGO
once mirrored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import cuid
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import Subscription, User
from app.services.billing import lago_client
from app.services.billing.catalog import plan_code_for_tier
from app.services.billing.rules import is_live
from app.services.billing.state import apply_billing_state
from app.services.product_events import capture


@dataclass
class MigrateResult:
    users: int = 0
    subscriptions: int = 0
    lago_created: int = 0
    skipped: int = 0


async def migrate_live_paid_users(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    push_lago: bool = True,
    dry_run: bool = False,
) -> MigrateResult:
    """Bring live STANDARD/PREMIUM holders onto the Lago billing shape."""
    moment = now or utcnow()
    result = MigrateResult()
    rows = (
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.status == "ACTIVE",
                    Subscription.tier.in_(("STANDARD", "PREMIUM")),
                )
            )
        )
        .scalars()
        .all()
    )

    touched_users: set[str] = set()

    for row in rows:
        if not is_live(row.status, row.starts_at, row.ends_at, moment):
            result.skipped += 1
            continue

        user = await session.get(User, row.user_id)
        if user is None:
            result.skipped += 1
            continue

        plan_code = plan_code_for_tier(row.tier)
        external_id = (
            row.lago_subscription_external_id
            or f"migrate_{plan_code}_{user.id}_{row.id[:8]}"
        )

        if dry_run:
            result.subscriptions += 1
            if user.id not in touched_users:
                result.users += 1
                touched_users.add(user.id)
            continue

        await apply_billing_state(
            session,
            user,
            plan_code=plan_code,
            billing_status="active",
            tier=row.tier,
            next_billing_at=row.ends_at,
            intro_ends_at=None,
        )
        user.lago_subscription_external_id = external_id

        row.lago_subscription_external_id = external_id
        if row.source == "PAYSTACK":
            row.source = "LAGO"
        # COMP stays COMP (admin grant); still mirrored to Lago when enabled.

        if push_lago and settings.lago_enabled:
            try:
                await lago_client.upsert_customer(external_id=user.id, email=user.email)
                ending = row.ends_at.isoformat() if row.ends_at else None
                await lago_client.create_subscription(
                    external_customer_id=user.id,
                    plan_code=plan_code,
                    external_id=external_id,
                    ending_at=ending,
                )
                result.lago_created += 1
            except Exception:
                logger.exception(
                    "Lago migrate failed for user=%s sub=%s", user.id, row.id
                )

        capture(
            user.id,
            "subscription_activated",
            {
                "plan_code": plan_code,
                "amount_kobo": row.amount_kobo,
                "currency": row.currency or "NGN",
                "source": "admin_comp" if row.source == "COMP" else "checkout",
                "lago_subscription_external_id": external_id,
                "migration": True,
            },
            set_props={
                "tier": row.tier,
                "plan_code": plan_code,
                "billing_status": "active",
            },
        )

        result.subscriptions += 1
        if user.id not in touched_users:
            result.users += 1
            touched_users.add(user.id)

    return result


async def ensure_freemium_plan_fields(
    session: AsyncSession, *, dry_run: bool = False
) -> int:
    """Align plan_code/billing_status for FREEMIUM users left inconsistent."""
    users = (
        (
            await session.execute(
                select(User).where(
                    User.tier == "FREEMIUM",
                    User.plan_code != "freemium",
                )
            )
        )
        .scalars()
        .all()
    )

    count = 0
    for user in users:
        count += 1
        if dry_run:
            continue
        status = None
        if user.billing_status not in {
            "freemium",
            "trialing",
            "past_due",
            "active",
            "canceling",
        }:
            status = "freemium"
        await apply_billing_state(
            session,
            user,
            plan_code="freemium",
            billing_status=status,
            tier="FREEMIUM",
        )
    return count


def migration_summary(result: MigrateResult) -> dict[str, Any]:
    return {
        "users": result.users,
        "subscriptions": result.subscriptions,
        "lago_created": result.lago_created,
        "skipped": result.skipped,
        "id": cuid(),
    }
