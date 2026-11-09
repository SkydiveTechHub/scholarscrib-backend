"""Billing provider protocol — Paystack Subscriptions or Lago."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Subscription, User
from app.database.models.billing_catalog import BillingPlan


@runtime_checkable
class BillingProvider(Protocol):
    """Remote recurring-billing engine. Local entitlements stay in our DB."""

    name: str

    async def checkout_extras(self, plan: BillingPlan | None, user: User) -> dict:
        """Fields merged into Paystack transaction/initialize (e.g. plan code)."""
        ...

    async def on_checkout_activated(
        self,
        session: AsyncSession,
        user: User,
        row: Subscription,
        transaction: dict,
        *,
        prior_subscription_code: str | None = None,
        prior_email_token: str | None = None,
    ) -> None:
        """After local Subscription activates — mirror remote subscription state.

        ``prior_*`` is the Paystack subscription on the user *before* this
        checkout, used to disable the old plan on mid-cycle upgrades.
        """
        ...

    async def cancel_at_period_end(self, session: AsyncSession, user: User) -> None:
        """Stop remote renewals; local access continues until next_billing_at."""
        ...

    async def resume(self, session: AsyncSession, user: User) -> None:
        """Undo a period-end cancel on the remote side."""
        ...

    async def provision_intro(
        self, session: AsyncSession, user: User, ends_at: datetime
    ) -> None:
        """Optional remote mirror of the free Standard intro (Lago); Paystack no-op."""
        ...

    async def assign_freemium_remote(
        self, session: AsyncSession, user: User, *, freemium_external_id: str
    ) -> None:
        """Terminate paid remote sub / assign freemium remotely if needed."""
        ...

    async def sync_plan(self, session: AsyncSession, plan: BillingPlan) -> str | None:
        """Create/update remote plan; return remote plan code or None."""
        ...

    async def handle_paystack_event(
        self, session: AsyncSession, event_type: str, data: dict
    ) -> dict | None:
        """Handle Paystack subscription lifecycle webhooks. Lago returns None."""
        ...
