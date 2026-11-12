"""Per-center Tutor Astra token pool + Paystack pack top-up (M6 / AI-5 / D10)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import (
    CenterTokenCheckout,
    CenterTokenLedger,
    CenterTokenPool,
    User,
)
from app.database.repositories.centers import (
    center_token_checkouts_repository,
    center_token_ledgers_repository,
    center_token_pools_repository,
)
from app.domain import (
    CENTER_ASTRA_STARTER_TOKENS,
    CENTER_ASTRA_TOKENS_PER_TURN,
    CENTER_TOKEN_PACKS,
    CenterTokenPack,
)
from app.services.billing.paystack import client as paystack
from app.services.billing.rules import new_reference, settlement_outcome
from app.services.billing.service import VerifyPaymentService
from app.services.centers.access import load_owned_center, load_staff_center
from app.services.product_events import capture


def token_pack_by_code(code: str) -> CenterTokenPack | None:
    needle = (code or "").strip().lower()
    for pack in CENTER_TOKEN_PACKS:
        if pack["code"].lower() == needle:
            return pack
    return None


def list_token_packs() -> list[dict[str, Any]]:
    return [
        {
            "code": pack["code"],
            "label": pack["label"],
            "tokens": pack["tokens"],
            "amountKobo": pack["amount_kobo"],
            "currency": "NGN",
        }
        for pack in CENTER_TOKEN_PACKS
    ]


def _pool_out(pool: CenterTokenPool) -> dict[str, Any]:
    return {
        "centerId": pool.center_id,
        "balance": pool.balance,
        "tokensPerTurn": CENTER_ASTRA_TOKENS_PER_TURN,
        "updatedAt": pool.updated_at.isoformat() if pool.updated_at else None,
    }


def _ledger_out(row: CenterTokenLedger) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind.lower(),
        "delta": row.delta,
        "balanceAfter": row.balance_after,
        "packCode": row.pack_code,
        "reference": row.reference,
        "actorId": row.actor_id,
        "meta": row.meta,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


async def ensure_token_pool(
    session: AsyncSession, center_id: str, *, actor_id: str | None = None
) -> CenterTokenPool:
    """Return the pool row, creating it (with optional starter credit) if missing."""
    pool = await center_token_pools_repository.for_center(session, center_id)
    if pool is not None:
        return pool
    pool = CenterTokenPool(id=cuid(), center_id=center_id, balance=0)
    await center_token_pools_repository.add(session, pool, flush=True)
    if CENTER_ASTRA_STARTER_TOKENS > 0:
        await credit_tokens(
            session,
            center_id,
            CENTER_ASTRA_STARTER_TOKENS,
            kind="STARTER",
            actor_id=actor_id,
            idempotency_key=f"starter:{center_id}",
            meta={"reason": "new_center"},
        )
        pool = await center_token_pools_repository.for_center(session, center_id)
        assert pool is not None
    return pool


async def credit_tokens(
    session: AsyncSession,
    center_id: str,
    amount: int,
    *,
    kind: str,
    actor_id: str | None = None,
    pack_code: str | None = None,
    reference: str | None = None,
    idempotency_key: str,
    meta: dict[str, Any] | None = None,
) -> CenterTokenPool:
    if amount <= 0:
        raise ApiError(400, "Token credit must be positive")
    existing = await center_token_ledgers_repository.by_idempotency_key(
        session, idempotency_key
    )
    if existing is not None:
        pool = await center_token_pools_repository.for_center(session, center_id)
        if pool is None:
            raise ApiError(500, "Token pool missing after credit")
        return pool
    pool = await center_token_pools_repository.for_center_for_update(session, center_id)
    if pool is None:
        pool = CenterTokenPool(id=cuid(), center_id=center_id, balance=0)
        await center_token_pools_repository.add(session, pool, flush=True)
        pool = await center_token_pools_repository.for_center_for_update(
            session, center_id
        )
        assert pool is not None
    pool.balance = int(pool.balance) + amount
    pool.updated_at = utcnow()
    await center_token_ledgers_repository.add(
        session,
        CenterTokenLedger(
            id=cuid(),
            center_id=center_id,
            actor_id=actor_id,
            kind=kind,
            delta=amount,
            balance_after=pool.balance,
            pack_code=pack_code,
            reference=reference,
            idempotency_key=idempotency_key,
            meta=meta,
        ),
        flush=True,
    )
    return pool


async def credit_tokens_from_wallet(
    session: AsyncSession,
    center_id: str,
    amount: int,
    *,
    actor_id: str,
    wallet_entry_id: str,
) -> CenterTokenPool:
    """M7 hook: spend referral wallet balance into this center's token pool."""
    return await credit_tokens(
        session,
        center_id,
        amount,
        kind="WALLET",
        actor_id=actor_id,
        idempotency_key=f"wallet:{wallet_entry_id}",
        meta={"walletEntryId": wallet_entry_id, "source": "referral_wallet"},
    )


async def deduct_turn_tokens(
    session: AsyncSession,
    center_id: str,
    *,
    actor_id: str,
    thread_id: str | None = None,
    tokens: int | None = None,
) -> CenterTokenPool:
    """AI-5: deduct per turn; refuse closed at zero with a top-up path."""
    cost = tokens if tokens is not None else CENTER_ASTRA_TOKENS_PER_TURN
    if cost <= 0:
        pool = await ensure_token_pool(session, center_id, actor_id=actor_id)
        return pool
    await ensure_token_pool(session, center_id, actor_id=actor_id)
    pool = await center_token_pools_repository.for_center_for_update(session, center_id)
    assert pool is not None
    if pool.balance < cost:
        raise ApiError(
            402,
            "This center has no AI tokens left. Buy a token pack to keep using "
            "Tutor Astra.",
            code="CENTER_ASTRA_TOKENS_EMPTY",
            topUpPath=f"/center/api/centers/{center_id}/tokens/packs",
            balance=pool.balance,
            tokensRequired=cost,
        )
    pool.balance = int(pool.balance) - cost
    pool.updated_at = utcnow()
    key = f"spend:{thread_id}:{cuid()}" if thread_id else f"spend:{cuid()}"
    await center_token_ledgers_repository.add(
        session,
        CenterTokenLedger(
            id=cuid(),
            center_id=center_id,
            actor_id=actor_id,
            kind="SPEND",
            delta=-cost,
            balance_after=pool.balance,
            idempotency_key=key,
            meta={"threadId": thread_id, "tokensPerTurn": cost},
        ),
        flush=True,
    )
    return pool


class GetCenterTokenPoolService:
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        await load_staff_center(self.session, self.center_id, self.tutor_id)
        pool = await ensure_token_pool(
            self.session, self.center_id, actor_id=self.tutor_id
        )
        ledger = await center_token_ledgers_repository.for_center(
            self.session, self.center_id, limit=30
        )
        return {
            "pool": _pool_out(pool),
            "packs": list_token_packs(),
            "ledger": [_ledger_out(row) for row in ledger],
        }


class CenterTokenCheckoutService:
    """Owner buys a one-off token pack via Paystack (no subscription plan)."""

    def __init__(
        self,
        session: AsyncSession,
        user: User,
        center_id: str,
        pack_code: str,
    ) -> None:
        self.session = session
        self.user = user
        self.center_id = center_id
        self.pack_code = pack_code

    async def process(self) -> dict[str, Any]:
        if not settings.billing_enabled:
            raise ApiError(503, "Billing is not configured")
        if not self.user.email:
            raise ApiError(400, "Add an email address before paying")
        await load_owned_center(self.session, self.center_id, self.user.id)
        pack = token_pack_by_code(self.pack_code)
        if pack is None:
            raise ApiError(400, "Unknown token pack")
        await ensure_token_pool(self.session, self.center_id, actor_id=self.user.id)
        await center_token_checkouts_repository.abandon_pending(
            self.session, self.user.id, self.center_id
        )
        reference = new_reference()
        amount = int(pack["amount_kobo"])
        tokens = int(pack["tokens"])
        row = CenterTokenCheckout(
            id=cuid(),
            center_id=self.center_id,
            user_id=self.user.id,
            pack_code=str(pack["code"]),
            tokens=tokens,
            reference=reference,
            amount_kobo=amount,
            currency="NGN",
            status="PENDING",
        )
        await center_token_checkouts_repository.add(self.session, row, flush=True)
        callback = (
            f"{settings.frontend_url.rstrip('/')}/center/billing/tokens"
            f"?centerId={self.center_id}"
        )
        try:
            result = await paystack.initialize(
                email=self.user.email or "",
                amount_kobo=amount,
                reference=reference,
                callback_url=callback,
                metadata={
                    "centerId": self.center_id,
                    "userId": self.user.id,
                    "packCode": str(pack["code"]),
                    "tokens": tokens,
                    "source": "center_token_checkout",
                },
            )
        except Exception as exc:
            raise ApiError(502, "Paystack could not start checkout") from exc
        url = result.data.authorization_url if result.data else None
        if not url:
            raise ApiError(502, "Paystack could not start checkout")
        capture(
            self.user.id,
            "center_token_checkout_started",
            {
                "center_id": self.center_id,
                "pack_code": str(pack["code"]),
                "tokens": tokens,
                "amount_kobo": amount,
                "currency": "NGN",
                "source": "center_token_checkout",
            },
        )
        return {
            "authorizationUrl": url,
            "reference": reference,
            "pack": {
                "code": pack["code"],
                "label": pack["label"],
                "tokens": tokens,
                "amountKobo": amount,
            },
        }


class ApplyCenterTokenChargeSuccessService:
    """Credit tokens when a CenterTokenCheckout Paystack charge succeeds."""

    def __init__(
        self, session: AsyncSession, reference: str, transaction: dict
    ) -> None:
        self.session = session
        self.reference = reference
        self.transaction = transaction

    async def process(self) -> str | None:
        row = await center_token_checkouts_repository.by_reference_for_update(
            self.session, self.reference
        )
        if row is None:
            return None
        outcome = settlement_outcome(
            row_status=row.status,
            row_amount=row.amount_kobo,
            row_currency=row.currency,
            transaction_status=self.transaction.get("status"),
            transaction_amount=self.transaction.get("amount"),
            transaction_currency=self.transaction.get("currency"),
        )
        logger.info(
            "ApplyCenterTokenChargeSuccess reference=%s outcome=%s center=%s pack=%s",
            self.reference,
            outcome,
            row.center_id,
            row.pack_code,
        )
        if outcome == "not-successful" and row.status == "PENDING":
            row.status = "FAILED"
            capture(
                row.user_id,
                "center_token_checkout_failed",
                {
                    "center_id": row.center_id,
                    "pack_code": row.pack_code,
                    "amount_kobo": row.amount_kobo,
                    "source": "center_token_checkout",
                },
            )
            return outcome
        if outcome != "activate":
            return outcome
        if row.status == "ACTIVE":
            return "already-applied"
        now = utcnow()
        row.status = "ACTIVE"
        row.paid_at = now
        row.channel = self.transaction.get("channel")
        await credit_tokens(
            self.session,
            row.center_id,
            row.tokens,
            kind="PURCHASE",
            actor_id=row.user_id,
            pack_code=row.pack_code,
            reference=row.reference,
            idempotency_key=f"purchase:{row.reference}",
            meta={"amountKobo": row.amount_kobo},
        )
        capture(
            row.user_id,
            "center_token_pack_purchased",
            {
                "center_id": row.center_id,
                "pack_code": row.pack_code,
                "tokens": row.tokens,
                "amount_kobo": row.amount_kobo,
                "source": "center_token_checkout",
            },
        )
        await self.session.flush()
        return "activate"


class VerifyCenterTokenPaymentService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        center_id: str,
        reference: str,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.center_id = center_id
        self.reference = reference

    async def process(self) -> dict[str, Any]:
        await load_owned_center(self.session, self.center_id, self.user_id)
        row = await center_token_checkouts_repository.by_reference(
            self.session, self.reference
        )
        if (
            row is None
            or row.center_id != self.center_id
            or row.user_id != self.user_id
        ):
            raise ApiError(404, "Checkout not found")
        if row.status == "ACTIVE":
            pool = await ensure_token_pool(
                self.session, self.center_id, actor_id=self.user_id
            )
            return {"outcome": "already-applied", "pool": _pool_out(pool)}
        transaction = await VerifyPaymentService(self.session, self.reference).process()
        if not transaction:
            raise ApiError(502, "Could not verify payment")
        outcome = await ApplyCenterTokenChargeSuccessService(
            self.session, self.reference, transaction
        ).process()
        pool = await ensure_token_pool(
            self.session, self.center_id, actor_id=self.user_id
        )
        return {"outcome": outcome or "unknown", "pool": _pool_out(pool)}
