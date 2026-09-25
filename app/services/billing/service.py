import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import PaystackEvent, Subscription, User
from app.database.repositories.billing import (
    paystack_events_repository,
    subscriptions_repository,
)
from app.database.repositories.identity import users_repository
from app.domain import is_purchasable, price_kobo
from app.services.billing.rules import (
    new_reference,
    resolve_live_tier,
    settlement_outcome,
    signatures_match,
    term_end,
    term_start,
)


class CheckoutService:
    def __init__(
        self, session: AsyncSession, user: User, tier: str, period: str
    ) -> None:
        self.session = session
        self.user = user
        self.tier = tier
        self.period = period

    async def process(self) -> dict:
        self._validate()
        await self._abandon_pending()
        reference, amount = self._price()
        await self._store_pending(reference, amount)
        url = await self._initialize(reference, amount)
        return {"authorizationUrl": url}

    def _validate(self) -> None:
        if not settings.billing_enabled:
            raise ApiError(503, "Billing is not configured")
        if not is_purchasable(self.tier):
            raise ApiError(400, "That plan cannot be purchased")
        if not self.user.email:
            raise ApiError(400, "Add an email address before paying")

    async def _abandon_pending(self) -> None:
        await subscriptions_repository.abandon_pending(self.session, self.user.id)

    def _price(self) -> tuple[str, int]:
        return new_reference(), price_kobo(self.tier, self.period)

    async def _store_pending(self, reference: str, amount: int) -> None:
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
        )
        await subscriptions_repository.add(self.session, row, flush=True)

    async def _initialize(self, reference: str, amount: int) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.paystack.co/transaction/initialize",
                headers={"Authorization": f"Bearer {settings.paystack_secret_key}"},
                json={
                    "email": self.user.email,
                    "amount": amount,
                    "reference": reference,
                    "currency": "NGN",
                    "callback_url": (
                        f"{settings.app_url.rstrip('/')}/api/billing/callback"
                    ),
                    "metadata": {
                        "userId": self.user.id,
                        "tier": self.tier,
                        "period": self.period,
                    },
                },
            )
        if response.status_code >= 400:
            raise ApiError(502, "Paystack could not start checkout")
        url = response.json().get("data", {}).get("authorization_url")
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
        return await self._fetch()

    async def _fetch(self) -> dict | None:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"https://api.paystack.co/transaction/verify/{self.reference}",
                headers={"Authorization": f"Bearer {settings.paystack_secret_key}"},
            )
        if response.status_code >= 400:
            return None
        data = response.json().get("data") or {}
        return {
            "status": data.get("status"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "channel": data.get("channel"),
            "reference": data.get("reference") or self.reference,
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
        if user and user.tier != tier:
            user.tier = tier
            user.tier_updated_at = utcnow()


class ApplyChargeSuccessService:
    def __init__(
        self, session: AsyncSession, reference: str, transaction: dict
    ) -> None:
        self.session = session
        self.reference = reference
        self.transaction = transaction

    async def process(self) -> str:
        row = await subscriptions_repository.by_reference(self.session, self.reference)
        outcome = self._outcome(row)
        if outcome == "not-successful" and row is not None and row.status == "PENDING":
            row.status = "FAILED"
        if outcome != "activate" or row is None:
            return outcome
        applied = await self._activate(row)
        if not applied:
            return "already-applied"
        await RefreshUserTierService(self.session, row.user_id).process()
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
        result = await subscriptions_repository.activate(
            self.session,
            row.id,
            {
                "status": "ACTIVE",
                "paid_at": now,
                "channel": self.transaction.get("channel"),
                "starts_at": start,
                "ends_at": end,
            },
        )
        return result.rowcount != 0


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
        self.period = period
        self.admin_id = admin_id
        self.note = note

    async def process(self) -> None:
        await self._grant()
        await RefreshUserTierService(self.session, self.user_id).process()

    async def _grant(self) -> None:
        now = utcnow()
        live_end = await subscriptions_repository.latest_live_end(
            self.session, self.user_id, now
        )
        start = term_start(now, live_end)
        await subscriptions_repository.add(
            self.session,
            Subscription(
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
                ends_at=term_end(start, self.period),
                granted_by_id=self.admin_id,
                note=self.note,
                paid_at=now,
            ),
            flush=True,
        )


class RevokeSubscriptionService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> None:
        await subscriptions_repository.revoke_active(self.session, self.user_id)
        await RefreshUserTierService(self.session, self.user_id).process()
