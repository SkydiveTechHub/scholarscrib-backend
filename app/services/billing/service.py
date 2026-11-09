"""Checkout, Paystack settlement, Lago webhooks, comps."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import LagoEvent, PaystackEvent, Subscription, User
from app.database.models.billing_catalog import BillingPlan, BillingReminderLog
from app.database.repositories.billing import (
    paystack_events_repository,
    subscriptions_repository,
)
from app.database.repositories.identity import users_repository
from app.domain import is_purchasable, price_kobo
from app.services.billing import lago_client
from app.services.billing.catalog import (
    load_catalog,
    plan_by_code,
    plan_code_for_tier,
    tier_for_plan_code,
)
from app.services.billing.paystack import paystack
from app.services.billing.providers import get_billing_provider
from app.services.billing.provisioning import assign_freemium
from app.services.billing.rules import (
    new_reference,
    resolve_live_tier,
    settlement_outcome,
    signatures_match,
    term_end,
    term_start,
)
from app.services.billing.state import apply_billing_state, set_user_billing_cursor
from app.services.email import EmailService
from app.services.product_events import capture


def _billing_settings_url() -> str:
    return f"{settings.frontend_url.rstrip('/')}/settings/billing"


class CheckoutService:
    def __init__(
        self, session: AsyncSession, user: User, tier: str, period: str = "MONTHLY"
    ) -> None:
        self.session = session
        self.user = user
        self.tier = tier
        self.period = "MONTHLY"  # yearly dropped for this pass
        self.plan_code = plan_code_for_tier(tier)

    async def process(self) -> dict:
        await self._validate()
        await subscriptions_repository.abandon_pending(self.session, self.user.id)
        reference = new_reference()
        amount = await self._amount()
        await self._store_pending(reference, amount)
        url = await self._initialize(reference, amount)
        capture(
            self.user.id,
            "checkout_started",
            {
                "tier": self.tier,
                "plan_code": self.plan_code,
                "period": self.period,
                "amount_kobo": amount,
                "currency": "NGN",
                "source": "checkout",
            },
        )
        return {"authorizationUrl": url}

    async def _validate(self) -> None:
        if not settings.billing_enabled:
            raise ApiError(503, "Billing is not configured")
        if not is_purchasable(self.tier):
            raise ApiError(400, "That plan cannot be purchased")
        if not self.user.email:
            raise ApiError(400, "Add an email address before paying")
        plan = await plan_by_code(self.session, self.plan_code)
        if plan is not None and not plan.is_purchasable:
            raise ApiError(400, "That plan cannot be purchased")

    async def _amount(self) -> int:
        plan = await plan_by_code(self.session, self.plan_code)
        if plan is not None:
            return plan.amount_kobo
        return price_kobo(self.tier, self.period)

    async def _store_pending(self, reference: str, amount: int) -> None:
        # Freeze the active Paystack subscription at checkout start. Activation
        # disables *this* code only — not whatever subscription.create stamped.
        row = Subscription(
            id=cuid(),
            user_id=self.user.id,
            tier=self.tier,
            period=self.period,
            reference=reference,
            amount_kobo=amount,
            currency="NGN",
            status="PENDING",
            source="PAYSTACK",
            kind="checkout",
            prior_paystack_subscription_code=getattr(
                self.user, "paystack_subscription_code", None
            ),
            prior_paystack_email_token=getattr(self.user, "paystack_email_token", None),
        )
        await subscriptions_repository.add(self.session, row, flush=True)

    async def _initialize(self, reference: str, amount: int) -> str:
        # ORM row (not PlanSnapshot) — sync_plan persists paystack_plan_code.
        # Row lock avoids concurrent checkouts creating duplicate Paystack plans.
        plan = await self.session.scalar(
            select(BillingPlan)
            .where(BillingPlan.code == self.plan_code)
            .with_for_update()
        )
        provider = get_billing_provider()
        # Ensure Paystack plan codes exist before attaching them to initialize.
        if (
            provider.name == "paystack"
            and plan is not None
            and plan.is_purchasable
            and not plan.paystack_plan_code
        ):
            await provider.sync_plan(self.session, plan)
        extras = await provider.checkout_extras(plan, self.user)
        try:
            result = await paystack.initialize(
                email=self.user.email or "",
                amount_kobo=amount,
                reference=reference,
                # Paystack returns the browser here (frontend), appending ?reference=.
                callback_url=(f"{settings.frontend_url.rstrip('/')}/settings/billing"),
                metadata={
                    "userId": self.user.id,
                    "tier": self.tier,
                    "plan_code": self.plan_code,
                    "period": self.period,
                },
                plan=extras.get("plan"),
            )
        except Exception as exc:
            raise ApiError(502, "Paystack could not start checkout") from exc
        url = result.data.authorization_url if result.data else None
        if not url:
            raise ApiError(502, "Paystack could not start checkout")
        return url


class VerifyPaymentService:
    def __init__(self, session: AsyncSession, reference: str) -> None:
        self.session = session
        self.reference = reference

    async def process(self) -> dict | None:
        if not settings.billing_enabled:
            return None
        try:
            result = await paystack.verify(self.reference)
        except Exception:
            logger.exception("Paystack verify failed for %s", self.reference)
            return None
        data = result.data
        if data is None:
            return None
        auth = data.authorization
        customer = data.customer
        return {
            "status": data.status,
            "amount": data.amount,
            "currency": data.currency,
            "channel": data.channel,
            "reference": data.reference or self.reference,
            "authorization_code": auth.authorization_code if auth else None,
            "card_last4": auth.last4 if auth else None,
            "customer_code": customer.customer_code if customer else None,
            "metadata": data.metadata or {},
        }


class RefreshUserTierService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> str:
        tier = await self._live_tier()
        await self._store(tier)
        return tier

    async def _live_tier(self) -> str:
        rows = await subscriptions_repository.for_user(self.session, self.user_id)
        tier, _expires = resolve_live_tier(
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
        return tier

    async def _store(self, tier: str) -> None:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            return
        status = None
        if tier == "FREEMIUM" and user.billing_status not in {
            "past_due",
            "trialing",
            "canceling",
        }:
            status = "freemium"
        await apply_billing_state(
            self.session,
            user,
            plan_code=plan_code_for_tier(tier),
            tier=tier,
            billing_status=status,
        )


class ApplyChargeSuccessService:
    def __init__(
        self, session: AsyncSession, reference: str, transaction: dict
    ) -> None:
        self.session = session
        self.reference = reference
        self.transaction = transaction

    async def process(self) -> str:
        # Lock first so /verify and the webhook cannot both side-effect.
        row = await subscriptions_repository.by_reference_for_update(
            self.session, self.reference
        )
        outcome = self._outcome(row)
        logger.info(
            "ApplyChargeSuccess reference=%s outcome=%s row=%s txn_status=%s "
            "txn_amount=%s/%s",
            self.reference,
            outcome,
            None
            if row is None
            else {
                "id": row.id,
                "user_id": row.user_id,
                "status": row.status,
                "tier": row.tier,
                "amount_kobo": row.amount_kobo,
                "currency": row.currency,
                "kind": getattr(row, "kind", None),
            },
            self.transaction.get("status"),
            self.transaction.get("amount"),
            self.transaction.get("currency"),
        )
        if outcome == "not-successful" and row is not None and row.status == "PENDING":
            row.status = "FAILED"
            capture(
                row.user_id,
                "checkout_failed",
                {
                    "plan_code": plan_code_for_tier(row.tier),
                    "tier": row.tier,
                    "amount_kobo": row.amount_kobo,
                    "currency": row.currency,
                    "source": "checkout",
                },
            )
        if outcome != "activate" or row is None:
            if row is None:
                logger.warning(
                    "ApplyChargeSuccess no subscription row for reference=%s "
                    "outcome=%s — checkout PENDING may be missing",
                    self.reference,
                    outcome,
                )
            elif outcome != "activate":
                logger.warning(
                    "ApplyChargeSuccess not activating reference=%s outcome=%s "
                    "row_status=%s row_tier=%s row_amount=%s txn_amount=%s",
                    self.reference,
                    outcome,
                    row.status,
                    row.tier,
                    row.amount_kobo,
                    self.transaction.get("amount"),
                )
            return outcome
        # Re-check under the lock: peer may have already activated.
        if row.status == "ACTIVE":
            logger.info(
                "ApplyChargeSuccess already-applied reference=%s user=%s tier=%s",
                self.reference,
                row.user_id,
                row.tier,
            )
            return "already-applied"
        applied = await self._activate(row)
        if not applied:
            logger.info(
                "ApplyChargeSuccess activate raced reference=%s user=%s",
                self.reference,
                row.user_id,
            )
            return "already-applied"
        user = await users_repository.by_id(self.session, row.user_id)
        if user:
            # Prefer codes frozen on the PENDING row at checkout — never the live
            # user fields (subscription.create may already have stamped SUB_new).
            prior_code = getattr(row, "prior_paystack_subscription_code", None)
            prior_token = getattr(row, "prior_paystack_email_token", None)
            logger.info(
                "ApplyChargeSuccess activating user=%s tier=%s→%s plan=%s "
                "prior_sub=%s live_sub=%s",
                user.id,
                user.tier,
                row.tier,
                user.plan_code,
                prior_code,
                getattr(user, "paystack_subscription_code", None),
            )
            await self._store_paystack_tokens(user)
            await get_billing_provider().on_checkout_activated(
                self.session,
                user,
                row,
                self.transaction,
                prior_subscription_code=prior_code,
                prior_email_token=prior_token,
            )
        await RefreshUserTierService(self.session, row.user_id).process()
        if user:
            await apply_billing_state(
                self.session,
                user,
                plan_code=plan_code_for_tier(row.tier),
                billing_status="active",
                tier=row.tier,
                next_billing_at=user.next_billing_at or row.ends_at,
                intro_ends_at=None,
            )
            if not getattr(row, "kind", None):
                row.kind = "checkout"
            logger.info(
                "ApplyChargeSuccess applied user=%s tier=%s plan=%s "
                "billing_status=%s next_billing_at=%s sub=%s",
                user.id,
                user.tier,
                user.plan_code,
                user.billing_status,
                user.next_billing_at,
                getattr(user, "paystack_subscription_code", None),
            )
        capture(
            row.user_id,
            "subscription_activated",
            {
                "tier": row.tier,
                "plan_code": plan_code_for_tier(row.tier),
                "period": row.period,
                "amount_kobo": row.amount_kobo,
                "currency": row.currency,
                "source": "checkout",
                "billing_provider": get_billing_provider().name,
                "lago_subscription_external_id": user.lago_subscription_external_id
                if user
                else None,
                "paystack_subscription_code": user.paystack_subscription_code
                if user
                else None,
            },
            set_props={
                "tier": row.tier,
                "plan_code": plan_code_for_tier(row.tier),
                "billing_status": "active",
            },
        )
        if user and user.email:
            await EmailService.enqueue(
                self.session,
                to_email=user.email,
                subject=f"You're on {row.tier.title()}",
                template="billing_payment_succeeded.html",
                context={
                    "first_name": user.first_name or "there",
                    "plan_name": row.tier.title(),
                    "amount_naira": row.amount_kobo // 100,
                    "billing_url": _billing_settings_url(),
                },
                idempotency_key=f"checkout_ok:{self.reference}",
            )
        return "activate"

    def _outcome(self, row: Subscription | None) -> str:
        return settlement_outcome(
            row_status=None if row is None else row.status,
            row_amount=None if row is None else row.amount_kobo,
            row_currency=None if row is None else row.currency,
            transaction_status=self.transaction.get("status"),
            transaction_amount=self.transaction.get("amount"),
            transaction_currency=self.transaction.get("currency"),
        )

    async def _activate(self, row: Subscription) -> bool:
        now = utcnow()
        live_end = await subscriptions_repository.latest_live_end(
            self.session, row.user_id, now
        )
        start = term_start(now, live_end)
        end = term_end(start, row.period)
        channel = self.transaction.get("channel")
        result = await subscriptions_repository.activate(
            self.session,
            row.id,
            {
                "status": "ACTIVE",
                "paid_at": now,
                "channel": channel,
                "starts_at": start,
                "ends_at": end,
            },
        )
        if result.rowcount == 0:
            return False
        # Core UPDATE does not refresh this instance; providers read row.ends_at.
        row.status = "ACTIVE"
        row.paid_at = now
        row.channel = channel
        row.starts_at = start
        row.ends_at = end
        return True

    async def _store_paystack_tokens(self, user: User) -> None:
        code = self.transaction.get("authorization_code")
        if code:
            user.paystack_authorization_code = code
        customer = self.transaction.get("customer_code")
        if customer:
            user.paystack_customer_code = customer
        last4 = self.transaction.get("card_last4")
        if last4:
            user.paystack_card_last4 = last4


class RecordPaystackEventService:
    def __init__(
        self, session: AsyncSession, reference: str, event_type: str, payload: dict
    ) -> None:
        self.session = session
        self.reference = reference
        self.event_type = event_type
        self.payload = payload

    async def process(self) -> bool:
        key = f"{self.reference}:{self.event_type}"
        try:
            async with self.session.begin_nested():
                await paystack_events_repository.add(
                    self.session,
                    PaystackEvent(event_key=key, payload=self.payload),
                    flush=True,
                )
        except IntegrityError:
            return False
        return True


class ForgetPaystackEventService:
    def __init__(self, session: AsyncSession, reference: str, event_type: str) -> None:
        self.session = session
        self.reference = reference
        self.event_type = event_type

    async def process(self) -> None:
        row = await paystack_events_repository.by_id(
            self.session, f"{self.reference}:{self.event_type}"
        )
        if row:
            await paystack_events_repository.remove(self.session, row)


class VerifyPaystackSignatureService:
    def __init__(self, body: bytes, header: str | None) -> None:
        self.body = body
        self.header = header

    async def process(self) -> bool:
        if not settings.billing_enabled or not settings.paystack_secret_key:
            return False
        return signatures_match(settings.paystack_secret_key, self.body, self.header)


class GrantCompSubscriptionService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        tier: str,
        period: str,
        admin_id: str,
        note: str | None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.tier = tier
        self.period = period if period == "MONTHLY" else "MONTHLY"
        self.admin_id = admin_id
        self.note = note

    async def process(self) -> None:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            return
        now = utcnow()
        live_end = await subscriptions_repository.latest_live_end(
            self.session, self.user_id, now
        )
        start = term_start(now, live_end)
        end = term_end(start, self.period)
        await apply_billing_state(
            self.session,
            user,
            plan_code=plan_code_for_tier(self.tier),
            billing_status="freemium" if self.tier == "FREEMIUM" else "active",
            tier=self.tier,
            next_billing_at=None if self.tier == "FREEMIUM" else end,
            subscription=Subscription(
                id=cuid(),
                user_id=self.user_id,
                tier=self.tier,
                period=self.period,
                reference=new_reference(),
                amount_kobo=0,
                currency="NGN",
                status="ACTIVE",
                source="COMP",
                starts_at=start,
                ends_at=end,
                granted_by_id=self.admin_id,
                note=self.note,
                paid_at=now,
                kind="comp",
            ),
        )
        await RefreshUserTierService(self.session, self.user_id).process()


class RevokeSubscriptionService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> None:
        user = await users_repository.by_id(self.session, self.user_id)
        if user:
            await assign_freemium(self.session, user, source="admin_revoke")
        else:
            await subscriptions_repository.revoke_active(self.session, self.user_id)
            await RefreshUserTierService(self.session, self.user_id).process()


class CancelSubscriptionService:
    """Student cancel: paid plans keep access until User.next_billing_at, then Freemium.

    Intro (trialing) cancels immediately. Admin revoke remains immediate via
    RevokeSubscriptionService.
    """

    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def process(self) -> dict:
        user = self.user
        if user.billing_status == "canceling":
            return {
                "status": "canceling",
                "accessUntil": (
                    user.next_billing_at.isoformat() if user.next_billing_at else None
                ),
            }
        if user.billing_status == "trialing":
            await assign_freemium(self.session, user, source="cancel")
            capture(
                user.id,
                "subscription_canceled",
                {
                    "plan_code": "freemium",
                    "previous_plan_code": "standard",
                    "source": "cancel",
                    "immediate": True,
                },
                set_props={
                    "tier": "FREEMIUM",
                    "plan_code": "freemium",
                    "billing_status": "freemium",
                },
            )
            return {"status": "freemium", "accessUntil": None}

        if user.billing_status not in {"active", "past_due"} or user.tier == "FREEMIUM":
            raise ApiError(400, "No paid plan to cancel")

        access_until = user.next_billing_at
        if access_until is None:
            access_until = await subscriptions_repository.latest_live_end(
                self.session, user.id, utcnow()
            )
        if access_until is None:
            # No remaining term — drop to Freemium now.
            await assign_freemium(self.session, user, source="cancel")
            capture(
                user.id,
                "subscription_canceled",
                {
                    "plan_code": "freemium",
                    "previous_plan_code": user.plan_code,
                    "source": "cancel",
                    "immediate": True,
                },
                set_props={
                    "tier": "FREEMIUM",
                    "plan_code": "freemium",
                    "billing_status": "freemium",
                },
            )
            return {"status": "freemium", "accessUntil": None}

        previous = user.plan_code
        set_user_billing_cursor(user, access_until, billing_status="canceling")

        # Stop remote renewals; local ACTIVE rows keep access until access_until.
        await get_billing_provider().cancel_at_period_end(self.session, user)

        capture(
            user.id,
            "subscription_canceled",
            {
                "plan_code": previous,
                "previous_plan_code": previous,
                "source": "cancel",
                "immediate": False,
                "access_until": access_until.date().isoformat(),
            },
            set_props={"billing_status": "canceling", "plan_code": previous},
        )
        if user.email:
            await EmailService.enqueue(
                self.session,
                to_email=user.email,
                subject="Your plan will end soon",
                template="billing_cancel_scheduled.html",
                context={
                    "first_name": user.first_name or "there",
                    "plan_name": user.tier.title(),
                    "access_until_label": access_until.strftime("%d %b %Y"),
                    "billing_url": _billing_settings_url(),
                },
                idempotency_key=f"cancel_sched:{user.id}:{access_until.date().isoformat()}",
            )
        return {
            "status": "canceling",
            "accessUntil": access_until.isoformat(),
        }


class ResumeSubscriptionService:
    """Undo a period-end cancel while access is still live."""

    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def process(self) -> dict:
        user = self.user
        if user.billing_status != "canceling":
            raise ApiError(400, "Plan is not scheduled to cancel")
        if user.next_billing_at is None or user.next_billing_at <= utcnow():
            raise ApiError(400, "Plan has already ended — choose a plan to resubscribe")

        set_user_billing_cursor(user, user.next_billing_at, billing_status="active")
        plan_code = user.plan_code or plan_code_for_tier(user.tier)
        await get_billing_provider().resume(self.session, user)

        capture(
            user.id,
            "subscription_activated",
            {
                "plan_code": plan_code,
                "source": "checkout",
                "resumed": True,
                "billing_provider": get_billing_provider().name,
            },
            set_props={"billing_status": "active", "plan_code": plan_code},
        )
        return {
            "status": "active",
            "accessUntil": user.next_billing_at.isoformat(),
        }


class RecordLagoEventService:
    def __init__(
        self, session: AsyncSession, event_key: str, webhook_type: str, payload: dict
    ):
        self.session = session
        self.event_key = event_key
        self.webhook_type = webhook_type
        self.payload = payload

    async def process(self) -> bool:
        try:
            async with self.session.begin_nested():
                self.session.add(
                    LagoEvent(
                        event_key=self.event_key,
                        webhook_type=self.webhook_type,
                        payload=self.payload,
                    )
                )
                await self.session.flush()
        except IntegrityError:
            return False
        return True


class HandleLagoWebhookService:
    def __init__(self, session: AsyncSession, payload: dict) -> None:
        self.session = session
        self.payload = payload
        self.webhook_type = payload.get("webhook_type") or ""

    async def process(self) -> dict:
        object_type = self.payload.get("object_type") or ""
        obj = self.payload.get(object_type) or {}

        if self.webhook_type.startswith("subscription."):
            await self._handle_subscription(obj)
        elif self.webhook_type == "invoice.created":
            await self._handle_invoice_created(obj)
        return {"handled": self.webhook_type}

    async def _handle_subscription(self, obj: dict) -> None:
        external_customer = obj.get("external_customer_id")
        if not external_customer:
            return
        user = await users_repository.by_id(self.session, external_customer)
        if user is None:
            return
        plan_code = (obj.get("plan_code") or user.plan_code or "freemium").lower()
        status = (obj.get("status") or "").lower()

        if self.webhook_type in {"subscription.terminated", "subscription.canceled"}:
            # Intro ended without conversion → freemium.
            intro_over = user.billing_status == "trialing" or (
                user.intro_ends_at is not None and user.intro_ends_at <= utcnow()
            )
            if intro_over and user.plan_code != "freemium":
                await assign_freemium(
                    self.session,
                    user,
                    source="intro_ended",
                    previous_plan=plan_code,
                )
            return

        if status == "active":
            if plan_code == "freemium":
                billing_status = "freemium"
            elif user.billing_status == "trialing" and plan_code == "standard":
                billing_status = None
            else:
                billing_status = "active"
            await apply_billing_state(
                self.session,
                user,
                plan_code=plan_code,
                billing_status=billing_status,
                tier=tier_for_plan_code(plan_code),
            )
            external_id = obj.get("external_id")
            if external_id:
                user.lago_subscription_external_id = external_id

    async def _handle_invoice_created(self, invoice: dict) -> None:
        amount = int(invoice.get("total_amount_cents") or 0)
        if amount <= 0:
            return
        customer = invoice.get("customer") or {}
        external_customer = customer.get("external_id") or invoice.get(
            "external_customer_id"
        )
        if not external_customer:
            return
        user = await users_repository.by_id(self.session, external_customer)
        if user is None:
            return
        # Lago invoice renewals only when Lago is the active billing provider.
        if get_billing_provider().name != "lago":
            return
        lago_id = invoice.get("lago_id") or invoice.get("id")
        await ChargeRenewalService(self.session, user, amount, lago_id).process()


class ChargeRenewalService:
    def __init__(
        self,
        session: AsyncSession,
        user: User,
        amount_kobo: int,
        lago_invoice_id: str | None,
    ) -> None:
        self.session = session
        self.user = user
        self.amount_kobo = amount_kobo
        self.lago_invoice_id = lago_invoice_id

    async def process(self) -> str:
        # Period-end cancel: do not renew; drop to Freemium when the term is due.
        if self.user.billing_status == "canceling":
            await assign_freemium(self.session, self.user, source="cancel")
            return "canceled"
        if not self.user.paystack_authorization_code or not self.user.email:
            await self._fail("missing_authorization")
            return "failed"
        reference = new_reference()
        try:
            result = await paystack.charge_authorization(
                authorization_code=self.user.paystack_authorization_code,
                email=self.user.email,
                amount_kobo=self.amount_kobo,
                reference=reference,
                metadata={
                    "userId": self.user.id,
                    "plan_code": self.user.plan_code,
                    "source": "renewal",
                    "lago_invoice_id": self.lago_invoice_id,
                },
            )
        except Exception:
            logger.exception("Renewal charge failed for %s", self.user.id)
            await self._fail("charge_error")
            return "failed"

        data = result.data
        status = (data.status if data else None) or ""
        if status != "success":
            await self._fail("not_success")
            return "failed"

        if self.lago_invoice_id and settings.lago_enabled:
            try:
                await lago_client.update_invoice_payment_status(
                    self.lago_invoice_id, payment_status="succeeded"
                )
            except Exception:
                logger.exception("Mark Lago invoice paid failed")

        now = utcnow()
        next_bill = term_end(now, "MONTHLY")
        await apply_billing_state(
            self.session,
            self.user,
            plan_code=self.user.plan_code or plan_code_for_tier(self.user.tier),
            billing_status="active",
            tier=self.user.tier,
            next_billing_at=next_bill,
            intro_ends_at=None,
            subscription=Subscription(
                id=cuid(),
                user_id=self.user.id,
                tier=self.user.tier,
                period="MONTHLY",
                reference=reference,
                amount_kobo=self.amount_kobo,
                currency="NGN",
                status="ACTIVE",
                source="LAGO",
                starts_at=now,
                ends_at=next_bill,
                paid_at=now,
                kind="renewal",
                lago_invoice_id=self.lago_invoice_id,
                note="Lago invoice renewal",
            ),
        )
        capture(
            self.user.id,
            "subscription_renewal_charged",
            {
                "plan_code": self.user.plan_code,
                "amount_kobo": self.amount_kobo,
                "currency": "NGN",
                "source": "renewal",
            },
            set_props={"billing_status": "active", "plan_code": self.user.plan_code},
        )
        if self.user.email:
            await EmailService.enqueue(
                self.session,
                to_email=self.user.email,
                subject="Payment received",
                template="billing_payment_succeeded.html",
                context={
                    "first_name": self.user.first_name or "there",
                    "plan_name": self.user.tier.title(),
                    "amount_naira": self.amount_kobo // 100,
                    "billing_url": _billing_settings_url(),
                },
                idempotency_key=f"renewal_ok:{reference}",
            )
        return "success"

    async def _fail(self, reason: str) -> None:
        await apply_billing_state(self.session, self.user, billing_status="past_due")
        capture(
            self.user.id,
            "subscription_renewal_failed",
            {
                "plan_code": self.user.plan_code,
                "amount_kobo": self.amount_kobo,
                "currency": "NGN",
                "source": "renewal",
                "reason": reason,
            },
            set_props={"billing_status": "past_due"},
        )
        capture(
            self.user.id,
            "subscription_past_due",
            {"plan_code": self.user.plan_code, "source": "renewal"},
            set_props={"billing_status": "past_due"},
        )
        if self.user.email:
            await EmailService.enqueue(
                self.session,
                to_email=self.user.email,
                subject="We couldn't renew your plan",
                template="billing_payment_failed.html",
                context={
                    "first_name": self.user.first_name or "there",
                    "plan_name": self.user.tier.title(),
                    "billing_url": _billing_settings_url(),
                    "grace_days": settings.billing_grace_days,
                },
                idempotency_key=f"renewal_fail:{self.user.id}:{utcnow().date().isoformat()}",
            )
        if self.lago_invoice_id and settings.lago_enabled:
            try:
                await lago_client.update_invoice_payment_status(
                    self.lago_invoice_id, payment_status="failed"
                )
            except Exception:
                logger.exception("Mark Lago invoice failed status error")


class BillingRemindersService:
    """Daily job: intro ending + renewal reminders (7d / 1d)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> dict[str, int]:
        sent = {"intro": 0, "renewal": 0}
        leads = settings.billing_reminder_leads or [7, 1]
        now = utcnow()

        for lead in leads:
            day = (now + timedelta(days=lead)).date()
            day_start = datetime(day.year, day.month, day.day, tzinfo=now.tzinfo)
            day_end = day_start + timedelta(days=1)

            intro_users = (
                (
                    await self.session.execute(
                        select(User).where(
                            User.is_active.is_(True),
                            User.billing_status == "trialing",
                            User.intro_ends_at.is_not(None),
                            User.intro_ends_at >= day_start,
                            User.intro_ends_at < day_end,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for user in intro_users:
                if user.intro_ends_at and await self._claim(
                    user.id, "intro_ending", lead, user.intro_ends_at
                ):
                    await self._send_intro(user, lead)
                    sent["intro"] += 1

            renewal_users = (
                (
                    await self.session.execute(
                        select(User).where(
                            User.is_active.is_(True),
                            User.billing_status == "active",
                            User.next_billing_at.is_not(None),
                            User.next_billing_at >= day_start,
                            User.next_billing_at < day_end,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for user in renewal_users:
                if user.next_billing_at and await self._claim(
                    user.id, "renewal", lead, user.next_billing_at
                ):
                    await self._send_renewal(user, lead)
                    sent["renewal"] += 1

        # Period-end cancel → freemium
        canceling = (
            (
                await self.session.execute(
                    select(User).where(
                        User.is_active.is_(True),
                        User.billing_status == "canceling",
                        User.next_billing_at.is_not(None),
                        User.next_billing_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for user in canceling:
            await assign_freemium(self.session, user, source="cancel")
            if user.email:
                await EmailService.enqueue(
                    self.session,
                    to_email=user.email,
                    subject="You're now on Free",
                    template="billing_downgraded.html",
                    context={
                        "first_name": user.first_name or "there",
                        "billing_url": _billing_settings_url(),
                    },
                    idempotency_key=f"downgrade_cancel:{user.id}:{now.date().isoformat()}",
                )

        # Grace exhaustion → freemium (small past_due set only)
        grace = timedelta(days=settings.billing_grace_days)
        past_due = (
            (
                await self.session.execute(
                    select(User).where(
                        User.is_active.is_(True),
                        User.billing_status == "past_due",
                    )
                )
            )
            .scalars()
            .all()
        )
        for user in past_due:
            anchor = user.next_billing_at or user.tier_updated_at
            if anchor and now >= anchor + grace:
                await assign_freemium(self.session, user, source="grace_downgrade")
                if user.email:
                    await EmailService.enqueue(
                        self.session,
                        to_email=user.email,
                        subject="You're now on Free",
                        template="billing_downgraded.html",
                        context={
                            "first_name": user.first_name or "there",
                            "billing_url": _billing_settings_url(),
                        },
                        idempotency_key=f"downgrade_grace:{user.id}:{now.date().isoformat()}",
                    )

        # Intro ended without webhook
        intro_ended = (
            (
                await self.session.execute(
                    select(User).where(
                        User.is_active.is_(True),
                        User.billing_status == "trialing",
                        User.intro_ends_at.is_not(None),
                        User.intro_ends_at <= now,
                        User.plan_code != "freemium",
                    )
                )
            )
            .scalars()
            .all()
        )
        for user in intro_ended:
            await assign_freemium(self.session, user, source="intro_ended")

        return sent

    async def _claim(self, user_id: str, kind: str, lead: int, target) -> bool:
        period_key = target.date().isoformat()
        try:
            async with self.session.begin_nested():
                self.session.add(
                    BillingReminderLog(
                        id=cuid(),
                        user_id=user_id,
                        kind=kind,
                        lead_days=lead,
                        period_key=period_key,
                    )
                )
                await self.session.flush()
        except IntegrityError:
            return False
        return True

    async def _send_intro(self, user: User, lead: int) -> None:
        capture(
            user.id,
            "billing_intro_ending_soon",
            {
                "plan_code": user.plan_code,
                "lead_days": lead,
                "source": "signup_intro",
            },
        )
        if user.email and user.intro_ends_at:
            await EmailService.enqueue(
                self.session,
                to_email=user.email,
                subject="Your free Standard month ends soon",
                template="billing_intro_ending.html",
                context={
                    "first_name": user.first_name or "there",
                    "lead_days": lead,
                    "billing_url": _billing_settings_url(),
                },
                idempotency_key=f"intro_ending:{user.id}:{lead}:{user.intro_ends_at.date().isoformat()}",
            )

    async def _send_renewal(self, user: User, lead: int) -> None:
        capture(
            user.id,
            "subscription_renewal_upcoming",
            {
                "plan_code": user.plan_code,
                "lead_days": lead,
                "amount_kobo": price_kobo(user.tier),
                "currency": "NGN",
                "source": "renewal",
            },
        )
        if user.email and user.next_billing_at:
            await EmailService.enqueue(
                self.session,
                to_email=user.email,
                subject="Your plan renews soon",
                template="billing_renewal_reminder.html",
                context={
                    "first_name": user.first_name or "there",
                    "plan_name": user.tier.title(),
                    "lead_days": lead,
                    "amount_naira": price_kobo(user.tier) // 100,
                    "billing_url": _billing_settings_url(),
                },
                idempotency_key=f"renewal_rem:{user.id}:{lead}:{user.next_billing_at.date().isoformat()}",
            )


class PaystackReconcileService:
    """Repair ``next_billing_at`` when Paystack renewal webhooks were dropped.

    Looks at paid users whose access cursor is in the past, fetches the remote
    subscription, and advances the local cursor when Paystack still shows an
    active renewal. Run *before* the daily downgrade cron.
    """

    def __init__(self, session: AsyncSession, *, limit: int = 100) -> None:
        self.session = session
        self.limit = limit

    async def process(self) -> dict[str, int]:
        if get_billing_provider().name != "paystack":
            return {"checked": 0, "advanced": 0, "skipped": 0, "errors": 0}
        if not settings.billing_enabled:
            return {"checked": 0, "advanced": 0, "skipped": 0, "errors": 0}

        now = utcnow()
        users = (
            (
                await self.session.execute(
                    select(User)
                    .where(
                        User.is_active.is_(True),
                        User.paystack_subscription_code.is_not(None),
                        User.next_billing_at.is_not(None),
                        User.next_billing_at <= now,
                        User.billing_status.in_(("active", "past_due")),
                    )
                    .order_by(User.next_billing_at)
                    .limit(self.limit)
                )
            )
            .scalars()
            .all()
        )

        checked = advanced = skipped = errors = 0
        for user in users:
            checked += 1
            code = user.paystack_subscription_code
            if not code:
                skipped += 1
                continue
            try:
                fetched = await paystack.fetch_subscription(code)
            except Exception:
                logger.exception("Reconcile fetch_subscription failed user=%s", user.id)
                errors += 1
                continue
            data = fetched.data
            if data is None:
                skipped += 1
                continue
            status = (data.status or "").lower()
            next_at = None
            if data.next_payment_date:
                try:
                    from dateutil import parser as date_parser

                    next_at = date_parser.isoparse(data.next_payment_date)
                    if next_at.tzinfo is None:
                        from datetime import UTC

                        next_at = next_at.replace(tzinfo=UTC)
                except (ValueError, TypeError):
                    next_at = None

            if status in {"active", "non-renewing"} and next_at and next_at > now:
                plan_code = user.plan_code or plan_code_for_tier(user.tier)
                tier = tier_for_plan_code(plan_code)
                # Idempotent reference so a second reconcile does not insert twice.
                reconcile_ref = f"reconcile_{code}_{next_at.date().isoformat()}"
                existing = await subscriptions_repository.by_reference(
                    self.session, reconcile_ref
                )
                sub = None
                if existing is None:
                    sub = Subscription(
                        id=cuid(),
                        user_id=user.id,
                        tier=tier,
                        period="MONTHLY",
                        reference=reconcile_ref,
                        amount_kobo=price_kobo(tier),
                        currency="NGN",
                        status="ACTIVE",
                        source="PAYSTACK",
                        starts_at=now,
                        ends_at=next_at,
                        paid_at=now,
                        kind="renewal",
                        note="Paystack reconciliation sync",
                    )
                await apply_billing_state(
                    self.session,
                    user,
                    plan_code=plan_code,
                    billing_status=("active" if status == "active" else "canceling"),
                    tier=tier,
                    next_billing_at=next_at,
                    subscription=sub,
                )
                capture(
                    user.id,
                    "subscription_renewed",
                    {
                        "plan_code": plan_code,
                        "source": "reconcile",
                        "paystack_subscription_code": code,
                        "next_billing_at": next_at.date().isoformat(),
                    },
                    set_props={
                        "billing_status": user.billing_status,
                        "plan_code": plan_code,
                        "tier": tier,
                    },
                )
                advanced += 1
            else:
                skipped += 1
        return {
            "checked": checked,
            "advanced": advanced,
            "skipped": skipped,
            "errors": errors,
        }


class ListPublicPlansService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> list[dict]:
        from app.services.billing.catalog import public_plan_payload, seed_catalog

        plans = await load_catalog(self.session)
        if not plans:
            await seed_catalog(self.session)
            plans = await load_catalog(self.session)
        return [public_plan_payload(p) for p in plans]
