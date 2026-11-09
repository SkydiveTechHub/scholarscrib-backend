"""Signup intro + freemium assignment (local SoT + provider remote mirror)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import Subscription, User
from app.database.repositories.billing import subscriptions_repository
from app.services.billing.catalog import plan_code_for_tier
from app.services.billing.providers import get_billing_provider
from app.services.billing.rules import new_reference
from app.services.billing.state import apply_billing_state
from app.services.product_events import capture


def _intro_external_id(user_id: str) -> str:
    return f"sub_intro_{user_id}"


async def provision_signup_billing(session: AsyncSession, user: User) -> None:
    """Best-effort provider provisioning. Local tier is set even if remote is down.

    No-op while billing is off (missing / placeholder ``PAYSTACK_SECRET_KEY``).
    Users stay on the User defaults (FREEMIUM / freemium). When Paystack is ready,
    set a real secret — new signups get the Standard intro; backfill existing
    users with a one-shot script.
    """
    if not settings.billing_enabled:
        return

    now = utcnow()
    intro_days = settings.billing_intro_days
    ends = now + timedelta(days=intro_days)
    external_id = _intro_external_id(user.id)
    provider = get_billing_provider()

    await apply_billing_state(
        session,
        user,
        plan_code="standard",
        billing_status="trialing",
        tier="STANDARD",
        next_billing_at=None,
        intro_ends_at=ends,
        subscription=Subscription(
            id=cuid(),
            user_id=user.id,
            tier="STANDARD",
            period="MONTHLY",
            reference=new_reference(),
            amount_kobo=0,
            currency="NGN",
            status="ACTIVE",
            source="LAGO" if provider.name == "lago" else "COMP",
            starts_at=now,
            ends_at=ends,
            paid_at=now,
            kind="intro",
            lago_subscription_external_id=(
                external_id if provider.name == "lago" else None
            ),
            note="Free Standard intro month",
        ),
    )
    if provider.name == "lago":
        user.lago_subscription_external_id = external_id

    await provider.provision_intro(session, user, ends)

    capture(
        user.id,
        "billing_intro_started",
        {
            "plan_code": "standard",
            "source": "signup_intro",
            "amount_kobo": 0,
            "currency": "NGN",
            "billing_provider": provider.name,
            "lago_subscription_external_id": user.lago_subscription_external_id,
        },
        set_props={
            "tier": "STANDARD",
            "plan_code": "standard",
            "billing_status": "trialing",
            "intro_ends_at": ends.date().isoformat(),
        },
    )


async def assign_freemium(
    session: AsyncSession,
    user: User,
    *,
    source: str,
    previous_plan: str | None = None,
) -> None:
    now = utcnow()
    previous = previous_plan or user.plan_code or plan_code_for_tier(user.tier)
    freemium_external = f"sub_freemium_{user.id}_{int(now.timestamp())}"
    provider = get_billing_provider()

    await provider.assign_freemium_remote(
        session, user, freemium_external_id=freemium_external
    )

    await subscriptions_repository.revoke_active(session, user.id)
    await apply_billing_state(
        session,
        user,
        plan_code="freemium",
        billing_status="freemium",
        tier="FREEMIUM",
        next_billing_at=None,
        intro_ends_at=None,
        subscription=Subscription(
            id=cuid(),
            user_id=user.id,
            tier="FREEMIUM",
            period="MONTHLY",
            reference=new_reference(),
            amount_kobo=0,
            currency="NGN",
            status="ACTIVE",
            source="LAGO" if provider.name == "lago" else "COMP",
            starts_at=now,
            ends_at=None,
            paid_at=now,
            kind="freemium",
            lago_subscription_external_id=(
                freemium_external if provider.name == "lago" else None
            ),
            note=f"Assigned freemium ({source})",
        ),
    )
    user.lago_subscription_external_id = (
        freemium_external if provider.name == "lago" else None
    )

    event = (
        "billing_intro_ended"
        if source == "intro_ended"
        else "billing_downgraded_to_freemium"
    )
    capture(
        user.id,
        event,
        {
            "plan_code": "freemium",
            "previous_plan_code": previous,
            "source": source,
            "currency": "NGN",
            "amount_kobo": 0,
            "billing_provider": provider.name,
        },
        set_props={
            "tier": "FREEMIUM",
            "plan_code": "freemium",
            "billing_status": "freemium",
        },
    )
