"""Lago billing provider — invoices drive renewals via charge_authorization."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import Subscription, User
from app.database.models.billing_catalog import BillingPlan
from app.services.billing import lago_client
from app.services.billing.catalog import plan_code_for_tier
from app.services.billing.state import set_user_billing_cursor


class LagoBillingProvider:
    name = "lago"

    async def checkout_extras(self, plan: BillingPlan | None, user: User) -> dict:
        return {}

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
        plan_code = plan_code_for_tier(row.tier)
        external_id = f"sub_{plan_code}_{user.id}_{int(utcnow().timestamp())}"
        if user.lago_subscription_external_id and settings.lago_enabled:
            try:
                await lago_client.terminate_subscription(
                    user.lago_subscription_external_id
                )
            except Exception:
                logger.exception("terminate prior Lago sub failed")
        if settings.lago_enabled:
            try:
                await lago_client.upsert_customer(external_id=user.id, email=user.email)
                await lago_client.create_subscription(
                    external_customer_id=user.id,
                    plan_code=plan_code,
                    external_id=external_id,
                )
            except Exception:
                logger.exception("Lago paid subscription create failed")
        user.lago_subscription_external_id = external_id
        set_user_billing_cursor(user, row.ends_at, billing_status="active")
        row.lago_subscription_external_id = external_id

    async def cancel_at_period_end(self, session: AsyncSession, user: User) -> None:
        if settings.lago_enabled and user.lago_subscription_external_id:
            try:
                await lago_client.terminate_subscription(
                    user.lago_subscription_external_id
                )
            except Exception:
                logger.exception("Lago terminate on cancel failed for %s", user.id)

    async def resume(self, session: AsyncSession, user: User) -> None:
        plan_code = user.plan_code or plan_code_for_tier(user.tier)
        external_id = f"sub_{plan_code}_{user.id}_{int(utcnow().timestamp())}"
        if settings.lago_enabled:
            try:
                await lago_client.upsert_customer(external_id=user.id, email=user.email)
                ending = (
                    user.next_billing_at.isoformat() if user.next_billing_at else None
                )
                await lago_client.create_subscription(
                    external_customer_id=user.id,
                    plan_code=plan_code,
                    external_id=external_id,
                    ending_at=ending,
                )
                user.lago_subscription_external_id = external_id
            except Exception:
                logger.exception("Lago resume failed for %s", user.id)

    async def provision_intro(
        self, session: AsyncSession, user: User, ends_at: datetime
    ) -> None:
        if not settings.lago_enabled:
            return
        external_id = user.lago_subscription_external_id or f"sub_intro_{user.id}"
        try:
            name = (
                " ".join(
                    part for part in [user.first_name, user.last_name] if part
                ).strip()
                or None
            )
            await lago_client.upsert_customer(
                external_id=user.id,
                email=user.email,
                name=name,
            )
            await lago_client.create_subscription(
                external_customer_id=user.id,
                plan_code="standard",
                external_id=external_id,
                ending_at=ends_at.isoformat(),
                amount_cents=0,
            )
        except Exception:
            logger.exception("Lago signup provisioning failed for %s", user.id)

    async def assign_freemium_remote(
        self, session: AsyncSession, user: User, *, freemium_external_id: str
    ) -> None:
        old_external = user.lago_subscription_external_id
        if settings.lago_enabled and old_external:
            try:
                await lago_client.terminate_subscription(old_external)
            except Exception:
                logger.exception("Failed to terminate Lago sub %s", old_external)
        if settings.lago_enabled:
            try:
                await lago_client.create_subscription(
                    external_customer_id=user.id,
                    plan_code="freemium",
                    external_id=freemium_external_id,
                    amount_cents=0,
                )
            except Exception:
                logger.exception("Failed to assign Lago freemium for %s", user.id)

    async def sync_plan(self, session: AsyncSession, plan: BillingPlan) -> str | None:
        if not settings.lago_enabled:
            return None
        try:
            await lago_client.create_plan(
                code=plan.lago_plan_code or plan.code,
                name=plan.display_name,
                amount_cents=plan.amount_kobo,
                interval=plan.interval or "monthly",
                description=plan.blurb,
            )
            return plan.lago_plan_code or plan.code
        except Exception:
            logger.exception("Lago sync_plan failed for %s", plan.code)
            return None

    async def handle_paystack_event(
        self, session: AsyncSession, event_type: str, data: dict
    ) -> dict | None:
        return None
