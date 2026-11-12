"""Paystack native Subscriptions — Paystack owns recurring charges."""

from __future__ import annotations

from datetime import datetime

from dateutil import parser as date_parser
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import Subscription, User
from app.database.models.billing_catalog import BillingPlan
from app.database.repositories.identity import users_repository
from app.services.billing.catalog import plan_code_for_tier, tier_for_plan_code
from app.services.billing.paystack import paystack
from app.services.billing.paystack.models import PaystackEvents
from app.services.billing.state import apply_billing_state, set_user_billing_cursor
from app.services.product_events import capture


def _parse_paystack_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = date_parser.isoparse(value)
        if dt.tzinfo is None:
            from datetime import UTC

            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


class PaystackSubscriptionProvider:
    name = "paystack"

    async def checkout_extras(self, plan: BillingPlan | None, user: User) -> dict:
        if plan is None or not plan.paystack_plan_code:
            return {}
        return {"plan": plan.paystack_plan_code}

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
        # Disable the code frozen on the PENDING row at checkout start.
        # _disable_subscription_codes only clears User fields when they still
        # hold that exact code — so a SUB_new stamped by subscription.create
        # is never cancelled.
        await self._disable_subscription_codes(
            user,
            code=prior_subscription_code,
            token=prior_email_token,
            reason="checkout_upgrade",
        )
        # subscription.create stamps next_payment_date when it arrives.
        set_user_billing_cursor(user, row.ends_at, billing_status="active")

    async def _disable_subscription_codes(
        self,
        user: User,
        *,
        code: str | None,
        token: str | None,
        reason: str,
    ) -> None:
        """Stop a specific Paystack subscription so upgrades do not double-bill."""
        if not code or not token:
            return
        try:
            await paystack.disable_subscription(code=code, token=token)
            capture(
                user.id,
                "subscription_canceled",
                {
                    "plan_code": user.plan_code,
                    "source": reason,
                    "immediate": True,
                    "paystack_subscription_code": code,
                    "replaced": True,
                },
            )
        except Exception:
            logger.exception(
                "Paystack disable prior subscription failed user=%s code=%s reason=%s",
                user.id,
                code,
                reason,
            )
        # Clear only if the user still holds this exact prior code.
        if getattr(user, "paystack_subscription_code", None) == code:
            user.paystack_subscription_code = None
            user.paystack_email_token = None

    async def cancel_at_period_end(self, session: AsyncSession, user: User) -> None:
        code = getattr(user, "paystack_subscription_code", None)
        token = getattr(user, "paystack_email_token", None)
        if not code or not token:
            logger.warning(
                "Paystack cancel skipped — missing subscription code/token for %s",
                user.id,
            )
            return
        try:
            await paystack.disable_subscription(code=code, token=token)
        except Exception:
            logger.exception("Paystack disable_subscription failed for %s", user.id)

    async def resume(self, session: AsyncSession, user: User) -> None:
        code = getattr(user, "paystack_subscription_code", None)
        token = getattr(user, "paystack_email_token", None)
        if not code or not token:
            logger.warning(
                "Paystack resume skipped — missing subscription code/token for %s",
                user.id,
            )
            return
        try:
            await paystack.enable_subscription(code=code, token=token)
        except Exception:
            logger.exception("Paystack enable_subscription failed for %s", user.id)

    async def provision_intro(
        self, session: AsyncSession, user: User, ends_at: datetime
    ) -> None:
        # Cardless intro — no Paystack subscription until first paid checkout.
        return

    async def assign_freemium_remote(
        self, session: AsyncSession, user: User, *, freemium_external_id: str
    ) -> None:
        code = getattr(user, "paystack_subscription_code", None)
        token = getattr(user, "paystack_email_token", None)
        if code and token:
            try:
                await paystack.disable_subscription(code=code, token=token)
            except Exception:
                logger.exception("Paystack disable on freemium failed for %s", user.id)
        if hasattr(user, "paystack_subscription_code"):
            user.paystack_subscription_code = None
        if hasattr(user, "paystack_email_token"):
            user.paystack_email_token = None

    async def sync_plan(self, session: AsyncSession, plan: BillingPlan) -> str | None:
        if plan.amount_kobo <= 0 or not plan.is_purchasable:
            return plan.paystack_plan_code
        if plan.paystack_plan_code:
            return plan.paystack_plan_code
        try:
            # Paginate — default page size is 100; do not create duplicates.
            page = 1
            while page <= 20:
                listed = await paystack.list_plans(per_page=100, page=page)
                if not listed.data:
                    break
                for remote in listed.data:
                    if (
                        remote.name
                        and remote.name.lower() == plan.display_name.lower()
                        and remote.amount == plan.amount_kobo
                        and remote.plan_code
                    ):
                        plan.paystack_plan_code = remote.plan_code
                        return remote.plan_code
                if len(listed.data) < 100:
                    break
                page += 1
            created = await paystack.create_plan(
                name=plan.display_name,
                amount_kobo=plan.amount_kobo,
                interval=(plan.interval or "monthly").lower(),
                currency=plan.currency or "NGN",
            )
            code = created.data.plan_code if created.data else None
            if code:
                plan.paystack_plan_code = code
            return code
        except Exception:
            logger.exception("Paystack sync_plan failed for %s", plan.code)
            return None

    async def handle_paystack_event(
        self, session: AsyncSession, event_type: str, data: dict
    ) -> dict | None:
        if event_type == PaystackEvents.SUBSCRIPTION_CREATE:
            return await self._on_subscription_create(session, data)
        if event_type == PaystackEvents.SUBSCRIPTION_NOT_RENEW:
            return await self._on_subscription_not_renew(session, data)
        if event_type == PaystackEvents.SUBSCRIPTION_DISABLE:
            return await self._on_subscription_disable(session, data)
        if event_type == PaystackEvents.INVOICE_PAYMENT_FAILED:
            return await self._on_invoice_payment_failed(session, data)
        if event_type == PaystackEvents.SUBSCRIPTION_EXPIRING_CARDS:
            logger.info(
                "Paystack subscription.expiring_cards (%s items)",
                len(data) if isinstance(data, list) else 1,
            )
            return {"handled": True, "event": event_type}
        if event_type == PaystackEvents.CHARGE_SUCCESS:
            return await self._on_renewal_charge(session, data)
        if event_type in {
            PaystackEvents.REFUND_PROCESSED,
            PaystackEvents.CHARGE_REFUNDED,
        }:
            return await self._on_refund(session, data, event_type)
        logger.info("Paystack event unhandled by provider event=%s", event_type)
        return None

    async def _user_from_customer(
        self, session: AsyncSession, data: dict
    ) -> User | None:
        customer = data.get("customer") or {}
        if isinstance(customer, dict):
            code = customer.get("customer_code")
            email = customer.get("email")
        else:
            code = getattr(customer, "customer_code", None)
            email = getattr(customer, "email", None)
        if code:
            user = await users_repository.first(
                session, User.paystack_customer_code == code
            )
            if user:
                return user
        if email:
            return await users_repository.by_email(session, email)
        # Metadata from initialize
        meta = data.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("userId"):
            return await users_repository.by_id(session, meta["userId"])
        return None

    async def _on_subscription_create(self, session: AsyncSession, data: dict) -> dict:
        user = await self._user_from_customer(session, data)
        if user is None:
            raw_customer = data.get("customer")
            customer = raw_customer if isinstance(raw_customer, dict) else {}
            logger.warning(
                "subscription.create user-not-found customer=%s email=%s meta=%s",
                customer.get("customer_code"),
                customer.get("email"),
                data.get("metadata"),
            )
            return {"handled": False, "reason": "user-not-found"}
        new_code = data.get("subscription_code")
        old_code = getattr(user, "paystack_subscription_code", None)
        old_token = getattr(user, "paystack_email_token", None)
        logger.info(
            "subscription.create user=%s tier=%s plan=%s billing_status=%s "
            "old_sub=%s new_sub=%s",
            user.id,
            user.tier,
            user.plan_code,
            user.billing_status,
            old_code,
            new_code,
        )
        if new_code and old_code and old_code != new_code:
            await self._disable_subscription_codes(
                user,
                code=old_code,
                token=old_token,
                reason="subscription_create_replace",
            )
        user.paystack_subscription_code = new_code
        user.paystack_email_token = data.get("email_token")
        next_at = _parse_paystack_dt(data.get("next_payment_date"))
        if next_at:
            set_user_billing_cursor(user, next_at, billing_status="active")
        return {"handled": True, "event": "subscription.create", "userId": user.id}

    async def _on_subscription_not_renew(
        self, session: AsyncSession, data: dict
    ) -> dict:
        user = await self._user_from_customer(session, data)
        if user is None:
            sub = data.get("subscription") or data
            code = sub.get("subscription_code") if isinstance(sub, dict) else None
            if code:
                user = await users_repository.first(
                    session, User.paystack_subscription_code == code
                )
        if user is None:
            return {"handled": False, "reason": "user-not-found"}
        if user.billing_status != "canceling":
            set_user_billing_cursor(
                user, user.next_billing_at, billing_status="canceling"
            )
        return {"handled": True, "event": "subscription.not_renew", "userId": user.id}

    async def _on_subscription_disable(self, session: AsyncSession, data: dict) -> dict:
        from app.services.billing.provisioning import assign_freemium

        user = await self._user_from_customer(session, data)
        if user is None:
            code = data.get("subscription_code")
            if code:
                user = await users_repository.first(
                    session, User.paystack_subscription_code == code
                )
        if user is None:
            raw_customer = data.get("customer")
            customer = raw_customer if isinstance(raw_customer, dict) else {}
            logger.warning(
                "subscription.disable user-not-found code=%s customer=%s",
                data.get("subscription_code"),
                customer.get("customer_code"),
            )
            return {"handled": False, "reason": "user-not-found"}
        # Avoid re-entrant disable when assign_freemium clears codes.
        if user.billing_status == "freemium" and user.tier == "FREEMIUM":
            logger.info(
                "subscription.disable noop user=%s already freemium code=%s",
                user.id,
                data.get("subscription_code"),
            )
            return {"handled": True, "event": "subscription.disable", "noop": True}
        logger.warning(
            "subscription.disable assigning freemium user=%s tier=%s plan=%s "
            "billing_status=%s live_sub=%s disabled_sub=%s",
            user.id,
            user.tier,
            user.plan_code,
            user.billing_status,
            getattr(user, "paystack_subscription_code", None),
            data.get("subscription_code"),
        )
        await assign_freemium(session, user, source="paystack_subscription_disable")
        return {"handled": True, "event": "subscription.disable", "userId": user.id}

    async def _on_invoice_payment_failed(
        self, session: AsyncSession, data: dict
    ) -> dict:
        user = await self._user_from_customer(session, data)
        sub = data.get("subscription") or {}
        if user is None and isinstance(sub, dict):
            code = sub.get("subscription_code")
            if code:
                user = await users_repository.first(
                    session, User.paystack_subscription_code == code
                )
        if user is None:
            return {"handled": False, "reason": "user-not-found"}
        set_user_billing_cursor(user, user.next_billing_at, billing_status="past_due")
        capture(
            user.id,
            "subscription_renewal_failed",
            {
                "plan_code": user.plan_code,
                "source": "paystack_invoice",
            },
            set_props={"billing_status": "past_due"},
        )
        return {"handled": True, "event": "invoice.payment_failed", "userId": user.id}

    async def _on_renewal_charge(
        self, session: AsyncSession, data: dict
    ) -> dict | None:
        """Extend access for recurring charges (not first-checkout PENDING)."""
        from app.core.ids import cuid
        from app.database.models import Subscription
        from app.database.repositories.billing import subscriptions_repository
        from app.services.billing.rules import new_reference, term_end
        from app.services.billing.service import RefreshUserTierService

        reference = data.get("reference")
        if reference:
            row = await subscriptions_repository.by_reference(session, reference)
            if row is not None and row.status == "PENDING":
                # First checkout — leave to ApplyChargeSuccessService.
                logger.info(
                    "charge.success deferred to checkout settlement "
                    "reference=%s user=%s pending_tier=%s pending_amount=%s",
                    reference,
                    row.user_id,
                    row.tier,
                    row.amount_kobo,
                )
                return None
            if row is not None:
                logger.info(
                    "charge.success existing subscription row reference=%s "
                    "status=%s tier=%s kind=%s — evaluating as renewal path",
                    reference,
                    row.status,
                    row.tier,
                    getattr(row, "kind", None),
                )
            else:
                logger.info(
                    "charge.success no local subscription for reference=%s "
                    "— evaluating as renewal path",
                    reference,
                )

        user = await self._user_from_customer(session, data)
        if user is None:
            raw_customer = data.get("customer")
            customer = raw_customer if isinstance(raw_customer, dict) else {}
            logger.warning(
                "charge.success user-not-found reference=%s customer=%s meta=%s",
                reference,
                customer.get("customer_code"),
                data.get("metadata"),
            )
            return {"handled": False, "reason": "user-not-found"}

        sub_code = getattr(user, "paystack_subscription_code", None)
        # Already active paid user: treat as renewal.
        if user.billing_status in {"freemium", "trialing"} and not sub_code:
            logger.info(
                "charge.success deferred to checkout settlement user=%s "
                "billing_status=%s (no paystack sub code)",
                user.id,
                user.billing_status,
            )
            return None

        meta = data.get("metadata") or {}
        plan_code = None
        if isinstance(meta, dict):
            plan_code = meta.get("plan_code")
        if not plan_code:
            plan_code = user.plan_code or plan_code_for_tier(user.tier)

        logger.info(
            "charge.success treating as renewal user=%s tier=%s plan=%s "
            "billing_status=%s sub=%s resolved_plan=%s amount=%s reference=%s "
            "meta=%s",
            user.id,
            user.tier,
            user.plan_code,
            user.billing_status,
            sub_code,
            plan_code,
            data.get("amount"),
            reference,
            meta if isinstance(meta, dict) else None,
        )

        now = utcnow()
        next_at = term_end(now, "MONTHLY")
        sub = data.get("subscription") or {}
        if isinstance(sub, dict):
            parsed = _parse_paystack_dt(sub.get("next_payment_date"))
            if parsed:
                next_at = parsed

        amount = int(data.get("amount") or 0)
        channel = None
        auth = data.get("authorization")
        if isinstance(auth, dict):
            channel = auth.get("channel")
        channel = channel or data.get("channel")

        renewal_ref = str(reference or new_reference())
        renewal_sub_id = cuid()
        await apply_billing_state(
            session,
            user,
            plan_code=plan_code,
            billing_status="active",
            tier=tier_for_plan_code(plan_code),
            next_billing_at=next_at,
            intro_ends_at=None,
            subscription=Subscription(
                id=renewal_sub_id,
                user_id=user.id,
                tier=tier_for_plan_code(plan_code),
                period="MONTHLY",
                reference=renewal_ref,
                amount_kobo=amount,
                currency=str(data.get("currency") or "NGN"),
                status="ACTIVE",
                source="PAYSTACK",
                starts_at=now,
                ends_at=next_at,
                paid_at=now,
                channel=channel,
                kind="renewal",
                note="Paystack subscription renewal",
            ),
        )
        await RefreshUserTierService(session, user.id).process()
        capture(
            user.id,
            "subscription_renewed",
            {
                "plan_code": plan_code,
                "source": "paystack_subscription",
                "reference": reference,
                "amount_kobo": amount,
            },
            set_props={
                "tier": user.tier,
                "plan_code": plan_code,
                "billing_status": "active",
            },
        )
        if user.email:
            from app.core.config import settings
            from app.services.email import EmailService

            await EmailService.enqueue(
                session,
                to_email=user.email,
                subject="Payment received",
                template="billing_payment_succeeded.html",
                context={
                    "first_name": user.first_name or "there",
                    "plan_name": user.tier.title(),
                    "amount_naira": amount // 100,
                    "billing_url": (
                        f"{settings.frontend_url.rstrip('/')}/settings/billing"
                    ),
                },
                idempotency_key=(
                    f"renewal_ok:{reference or user.id}:{now.date().isoformat()}"
                ),
            )
        try:
            from app.services.centers.wallet import credit_referral_commission

            await credit_referral_commission(
                session,
                student_id=user.id,
                amount_kobo=amount,
                tier=tier_for_plan_code(plan_code),
                subscription_id=renewal_sub_id,
                reference=renewal_ref,
                source="paystack_renewal",
            )
        except Exception:
            logger.exception(
                "referral commission credit failed renewal user=%s ref=%s",
                user.id,
                reference,
            )
        return {
            "handled": True,
            "event": "charge.success",
            "renewal": True,
            "userId": user.id,
        }

    async def _on_refund(
        self, session: AsyncSession, data: dict, event_type: str
    ) -> dict:
        """D12: reverse referral commission when Paystack reports a refund."""
        reference = data.get("transaction_reference") or data.get("reference")
        if not reference and isinstance(data.get("transaction"), dict):
            reference = data["transaction"].get("reference")
        try:
            from app.services.centers.wallet import reverse_referral_commission

            entry = await reverse_referral_commission(
                session, reference=str(reference) if reference else None
            )
        except Exception:
            logger.exception(
                "referral commission reverse failed event=%s ref=%s",
                event_type,
                reference,
            )
            return {"handled": False, "event": event_type, "reason": "reverse-error"}
        return {
            "handled": True,
            "event": event_type,
            "reversed": entry is not None,
            "reference": reference,
        }
