"""Single writer for User billing mirror + optional Subscription term insert."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.database.models import Subscription, User
from app.database.repositories.billing import subscriptions_repository
from app.services.billing.catalog import plan_code_for_tier, tier_for_plan_code
from app.services.billing.rules import resolve_live_tier

# Sentinel: omit means "leave field unchanged"; None means clear.
_UNSET: Any = object()

BillingStatus = str  # freemium | trialing | active | canceling | past_due


async def apply_billing_state(
    session: AsyncSession,
    user: User,
    *,
    plan_code: str | None = None,
    billing_status: str | None = None,
    tier: str | None = None,
    next_billing_at: Any = _UNSET,
    intro_ends_at: Any = _UNSET,
    subscription: Subscription | None = None,
    recompute_tier_from_subscriptions: bool = False,
) -> None:
    """Mutate User billing fields (and optionally insert a term) in one place.

    Prefer this over scattering User.tier / plan_code / billing_status writes.
    """
    if subscription is not None:
        await subscriptions_repository.add(session, subscription, flush=True)

    if plan_code is not None:
        user.plan_code = plan_code
        if tier is None and not recompute_tier_from_subscriptions:
            tier = tier_for_plan_code(plan_code)

    if recompute_tier_from_subscriptions or (subscription is not None and tier is None):
        rows = await subscriptions_repository.for_user(session, user.id)
        live_tier, _ = resolve_live_tier(
            [
                {
                    "status": row.status,
                    "tier": row.tier,
                    "startsAt": row.starts_at,
                    "endsAt": row.ends_at,
                }
                for row in rows
            ],
            utcnow(),
        )
        tier = live_tier
        if plan_code is None:
            user.plan_code = plan_code_for_tier(live_tier)

    if tier is not None and user.tier != tier:
        user.tier = tier
        user.tier_updated_at = utcnow()
    elif tier is not None:
        user.tier = tier

    if billing_status is not None:
        user.billing_status = billing_status

    if next_billing_at is not _UNSET:
        user.next_billing_at = next_billing_at

    if intro_ends_at is not _UNSET:
        user.intro_ends_at = intro_ends_at


def set_user_billing_cursor(
    user: User,
    when: datetime | None,
    *,
    billing_status: str | None = None,
) -> None:
    """Thin helper kept for call sites that only touch the renewal cursor."""
    user.next_billing_at = when
    if billing_status is not None:
        user.billing_status = billing_status
