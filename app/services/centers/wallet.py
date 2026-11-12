"""M7 referral wallet: 5% commission, token spend, withdraw requests (D11/D12)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import (
    Center,
    CenterMembership,
    Subscription,
    TutorWallet,
    TutorWalletLedger,
    TutorWithdrawRequest,
    User,
)
from app.database.repositories.centers import (
    centers_repository,
    memberships_repository,
    tutor_wallet_ledgers_repository,
    tutor_wallets_repository,
    tutor_withdraw_requests_repository,
)
from app.database.repositories.identity import users_repository
from app.domain import (
    CENTER_WALLET_MIN_WITHDRAW_KOBO,
    REFERRAL_COMMISSION_BPS,
)
from app.services.centers.access import load_owned_center
from app.services.centers.notify import (
    notify_wallet_credit,
    notify_withdraw_status,
)
from app.services.centers.tokens import credit_tokens_from_wallet, token_pack_by_code
from app.services.product_events import capture


def commission_kobo(amount_kobo: int) -> int:
    """5% of paid term, floored to whole kobo."""
    if amount_kobo <= 0:
        return 0
    return (int(amount_kobo) * REFERRAL_COMMISSION_BPS) // 10_000


def _wallet_out(wallet: TutorWallet) -> dict[str, Any]:
    return {
        "ownerId": wallet.owner_id,
        "balanceKobo": wallet.balance_kobo,
        "minWithdrawKobo": CENTER_WALLET_MIN_WITHDRAW_KOBO,
        "commissionBps": REFERRAL_COMMISSION_BPS,
        "updatedAt": wallet.updated_at.isoformat() if wallet.updated_at else None,
    }


def _ledger_out(row: TutorWalletLedger) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind.lower(),
        "deltaKobo": row.delta_kobo,
        "balanceAfterKobo": row.balance_after_kobo,
        "centerId": row.center_id,
        "studentId": row.student_id,
        "subscriptionId": row.subscription_id,
        "reference": row.reference,
        "meta": row.meta,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _withdraw_out(row: TutorWithdrawRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "ownerId": row.owner_id,
        "amountKobo": row.amount_kobo,
        "status": row.status.lower(),
        "payoutDetails": row.payout_details,
        "note": row.note,
        "resolutionNote": row.resolution_note,
        "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolvedById": row.resolved_by_id,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


async def ensure_wallet(session: AsyncSession, owner_id: str) -> TutorWallet:
    wallet = await tutor_wallets_repository.for_owner(session, owner_id)
    if wallet is not None:
        return wallet
    wallet = TutorWallet(id=cuid(), owner_id=owner_id, balance_kobo=0)
    await tutor_wallets_repository.add(session, wallet, flush=True)
    return wallet


async def resolve_attribution_membership(
    session: AsyncSession, student_id: str
) -> tuple[CenterMembership, Center] | None:
    """D12: credit inviting center owner while student remains active.

    Multi-center: first accepted invite that is still ACTIVE wins; else none.
    """
    memberships = await memberships_repository.active_student_memberships_oldest_first(
        session, student_id
    )
    if not memberships:
        return None
    membership = memberships[0]
    center = await centers_repository.by_id(session, membership.center_id)
    if center is None or center.archived_at is not None:
        return None
    return membership, center


async def _append_ledger(
    session: AsyncSession,
    wallet: TutorWallet,
    *,
    kind: str,
    delta_kobo: int,
    idempotency_key: str,
    center_id: str | None = None,
    student_id: str | None = None,
    subscription_id: str | None = None,
    reference: str | None = None,
    meta: dict[str, Any] | None = None,
) -> TutorWalletLedger:
    existing = await tutor_wallet_ledgers_repository.by_idempotency_key(
        session, idempotency_key
    )
    if existing is not None:
        return existing
    wallet.balance_kobo = int(wallet.balance_kobo) + int(delta_kobo)
    if wallet.balance_kobo < 0:
        raise ApiError(400, "Insufficient wallet balance")
    wallet.updated_at = utcnow()
    entry = TutorWalletLedger(
        id=cuid(),
        owner_id=wallet.owner_id,
        wallet_id=wallet.id,
        kind=kind,
        delta_kobo=int(delta_kobo),
        balance_after_kobo=wallet.balance_kobo,
        center_id=center_id,
        student_id=student_id,
        subscription_id=subscription_id,
        reference=reference,
        idempotency_key=idempotency_key,
        meta=meta,
    )
    await tutor_wallet_ledgers_repository.add(session, entry, flush=True)
    return entry


async def credit_referral_commission(
    session: AsyncSession,
    *,
    student_id: str,
    amount_kobo: int,
    tier: str | None,
    subscription_id: str | None,
    reference: str | None,
    source: str,
) -> TutorWalletLedger | None:
    """Credit 5% on successful paid term (checkout + renewals). Freemium = 0."""
    if (tier or "").upper() == "FREEMIUM":
        return None
    credit = commission_kobo(amount_kobo)
    if credit <= 0:
        return None
    if not subscription_id and not reference:
        logger.warning(
            "referral commission skipped: missing subscription/reference student=%s",
            student_id,
        )
        return None

    attributed = await resolve_attribution_membership(session, student_id)
    if attributed is None:
        return None
    membership, center = attributed
    idem = (
        f"commission:sub:{subscription_id}"
        if subscription_id
        else f"commission:ref:{reference}"
    )
    existing = await tutor_wallet_ledgers_repository.by_idempotency_key(session, idem)
    if existing is not None:
        return existing

    await ensure_wallet(session, center.owner_id)
    wallet = await tutor_wallets_repository.for_owner_for_update(
        session, center.owner_id
    )
    assert wallet is not None
    entry = await _append_ledger(
        session,
        wallet,
        kind="COMMISSION",
        delta_kobo=credit,
        idempotency_key=idem,
        center_id=center.id,
        student_id=student_id,
        subscription_id=subscription_id,
        reference=reference,
        meta={
            "source": source,
            "tier": tier,
            "paidAmountKobo": amount_kobo,
            "commissionBps": REFERRAL_COMMISSION_BPS,
            "membershipId": membership.id,
        },
    )
    capture(
        center.owner_id,
        "center_wallet_commission_credited",
        {
            "amount_kobo": credit,
            "student_id": student_id,
            "center_id": center.id,
            "subscription_id": subscription_id,
            "reference": reference,
            "source": source,
        },
    )
    try:
        await notify_wallet_credit(
            session,
            owner_id=center.owner_id,
            amount_kobo=credit,
            center_id=center.id,
            ledger_id=entry.id,
        )
    except Exception:
        logger.exception("wallet credit notification failed owner=%s", center.owner_id)
    return entry


async def maybe_credit_from_subscription(
    session: AsyncSession,
    subscription: Subscription,
    *,
    source: str,
) -> TutorWalletLedger | None:
    return await credit_referral_commission(
        session,
        student_id=subscription.user_id,
        amount_kobo=int(subscription.amount_kobo or 0),
        tier=subscription.tier,
        subscription_id=subscription.id,
        reference=subscription.reference,
        source=source,
    )


async def _find_commission_entry(
    session: AsyncSession,
    *,
    subscription_id: str | None = None,
    reference: str | None = None,
) -> TutorWalletLedger | None:
    if subscription_id:
        credit = await tutor_wallet_ledgers_repository.commission_for_subscription(
            session, subscription_id
        )
        if credit is not None:
            return credit
    if reference:
        credit = await tutor_wallet_ledgers_repository.by_idempotency_key(
            session, f"commission:ref:{reference}"
        )
        if credit is not None:
            return credit
        statement = (
            select(TutorWalletLedger)
            .where(
                TutorWalletLedger.kind == "COMMISSION",
                TutorWalletLedger.reference == reference,
            )
            .order_by(TutorWalletLedger.created_at.desc())
            .limit(1)
        )
        return await session.scalar(statement)
    return None


async def reverse_referral_commission(
    session: AsyncSession,
    *,
    subscription_id: str | None = None,
    reference: str | None = None,
) -> TutorWalletLedger | None:
    """D12: refunds reverse the commission credit."""
    credit = await _find_commission_entry(
        session, subscription_id=subscription_id, reference=reference
    )
    if credit is None:
        return None

    reverse_key = f"refund_reverse:{credit.id}"
    existing = await tutor_wallet_ledgers_repository.by_idempotency_key(
        session, reverse_key
    )
    if existing is not None:
        return existing

    wallet = await tutor_wallets_repository.for_owner_for_update(
        session, credit.owner_id
    )
    if wallet is None:
        return None
    # Cap reverse at available balance (spend/withdraw may have drawn down).
    debit = min(int(credit.delta_kobo), int(wallet.balance_kobo))
    entry = await _append_ledger(
        session,
        wallet,
        kind="REFUND_REVERSE",
        delta_kobo=-debit,
        idempotency_key=reverse_key,
        center_id=credit.center_id,
        student_id=credit.student_id,
        subscription_id=credit.subscription_id,
        reference=credit.reference,
        meta={
            "reversedLedgerId": credit.id,
            "requestedKobo": credit.delta_kobo,
            "appliedKobo": debit,
            **(
                {"reason": "balance_already_spent"}
                if debit < int(credit.delta_kobo)
                else {}
            ),
        },
    )
    capture(
        credit.owner_id,
        "center_wallet_commission_reversed",
        {
            "amount_kobo": debit,
            "subscription_id": credit.subscription_id,
            "reference": credit.reference,
            "ledger_id": credit.id,
        },
    )
    return entry


class GetOwnerWalletService:
    def __init__(self, session: AsyncSession, owner_id: str) -> None:
        self.session = session
        self.owner_id = owner_id

    async def process(self) -> dict[str, Any]:
        wallet = await ensure_wallet(self.session, self.owner_id)
        ledger = await tutor_wallet_ledgers_repository.for_owner(
            self.session, self.owner_id, limit=50
        )
        withdraws = await tutor_withdraw_requests_repository.for_owner(
            self.session, self.owner_id, limit=20
        )
        return {
            "wallet": _wallet_out(wallet),
            "ledger": [_ledger_out(row) for row in ledger],
            "withdrawals": [_withdraw_out(row) for row in withdraws],
        }


class SpendWalletOnTokensService:
    """Apply wallet balance to a center token pack (Paystack catalog)."""

    def __init__(
        self,
        session: AsyncSession,
        owner_id: str,
        center_id: str,
        pack_code: str,
    ) -> None:
        self.session = session
        self.owner_id = owner_id
        self.center_id = center_id
        self.pack_code = pack_code

    async def process(self) -> dict[str, Any]:
        await load_owned_center(self.session, self.center_id, self.owner_id)
        pack = token_pack_by_code(self.pack_code)
        if pack is None:
            raise ApiError(400, "Unknown token pack", code="CENTER_TOKEN_PACK_UNKNOWN")
        amount_kobo = int(pack["amount_kobo"])
        tokens = int(pack["tokens"])
        await ensure_wallet(self.session, self.owner_id)
        wallet = await tutor_wallets_repository.for_owner_for_update(
            self.session, self.owner_id
        )
        assert wallet is not None
        if wallet.balance_kobo < amount_kobo:
            raise ApiError(
                402,
                "Insufficient wallet balance for this token pack.",
                code="CENTER_WALLET_INSUFFICIENT",
                balanceKobo=wallet.balance_kobo,
                requiredKobo=amount_kobo,
            )
        spend_id = cuid()
        entry = await _append_ledger(
            self.session,
            wallet,
            kind="SPEND_TOKENS",
            delta_kobo=-amount_kobo,
            idempotency_key=f"spend_tokens:{spend_id}",
            center_id=self.center_id,
            meta={
                "packCode": pack["code"],
                "tokens": tokens,
                "amountKobo": amount_kobo,
            },
        )
        pool = await credit_tokens_from_wallet(
            self.session,
            self.center_id,
            tokens,
            actor_id=self.owner_id,
            wallet_entry_id=entry.id,
        )
        capture(
            self.owner_id,
            "center_wallet_spent_on_tokens",
            {
                "center_id": self.center_id,
                "pack_code": pack["code"],
                "tokens": tokens,
                "amount_kobo": amount_kobo,
                "wallet_entry_id": entry.id,
            },
        )
        return {
            "wallet": _wallet_out(wallet),
            "ledgerEntry": _ledger_out(entry),
            "tokens": {
                "centerId": self.center_id,
                "balance": pool.balance,
                "credited": tokens,
                "packCode": pack["code"],
            },
        }


class RequestWithdrawService:
    """D11: request + ops fulfilment; gated by tutor verification + min balance."""

    def __init__(
        self,
        session: AsyncSession,
        owner: User,
        amount_kobo: int,
        *,
        payout_details: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> None:
        self.session = session
        self.owner = owner
        self.amount_kobo = amount_kobo
        self.payout_details = payout_details
        self.note = note

    async def process(self) -> dict[str, Any]:
        if self.owner.tutor_verified_at is None:
            raise ApiError(
                403,
                "Withdrawals require a verified tutor account.",
                code="CENTER_WALLET_KYC_REQUIRED",
            )
        if self.amount_kobo < CENTER_WALLET_MIN_WITHDRAW_KOBO:
            raise ApiError(
                400,
                f"Minimum withdrawal is ₦{CENTER_WALLET_MIN_WITHDRAW_KOBO // 100}.",
                code="CENTER_WALLET_MIN_WITHDRAW",
                minWithdrawKobo=CENTER_WALLET_MIN_WITHDRAW_KOBO,
            )
        await ensure_wallet(self.session, self.owner.id)
        wallet = await tutor_wallets_repository.for_owner_for_update(
            self.session, self.owner.id
        )
        assert wallet is not None
        if wallet.balance_kobo < self.amount_kobo:
            raise ApiError(
                402,
                "Insufficient wallet balance.",
                code="CENTER_WALLET_INSUFFICIENT",
                balanceKobo=wallet.balance_kobo,
                requiredKobo=self.amount_kobo,
            )
        request_id = cuid()
        hold = await _append_ledger(
            self.session,
            wallet,
            kind="WITHDRAW_HOLD",
            delta_kobo=-self.amount_kobo,
            idempotency_key=f"withdraw_hold:{request_id}",
            meta={"withdrawRequestId": request_id},
        )
        row = TutorWithdrawRequest(
            id=request_id,
            owner_id=self.owner.id,
            wallet_id=wallet.id,
            amount_kobo=self.amount_kobo,
            status="PENDING",
            hold_ledger_id=hold.id,
            payout_details=self.payout_details,
            note=self.note,
        )
        await tutor_withdraw_requests_repository.add(self.session, row, flush=True)
        try:
            await notify_withdraw_status(
                self.session,
                owner_id=self.owner.id,
                request_id=row.id,
                status="PENDING",
                amount_kobo=self.amount_kobo,
            )
        except Exception:
            logger.exception("withdraw request notification failed id=%s", row.id)
        capture(
            self.owner.id,
            "center_wallet_withdraw_requested",
            {"amount_kobo": self.amount_kobo, "request_id": row.id},
        )
        return {"withdrawal": _withdraw_out(row), "wallet": _wallet_out(wallet)}


class ListWithdrawQueueService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
    ) -> None:
        self.session = session
        self.page = page
        self.page_size = page_size
        self.status = status.upper() if status else None

    async def process(self) -> dict[str, Any]:
        rows, total = await tutor_withdraw_requests_repository.queue(
            self.session,
            status=self.status,
            page=self.page,
            page_size=self.page_size,
        )
        owner_ids = list({row.owner_id for row in rows})
        owners: dict[str, User] = {}
        if owner_ids:
            loaded = await users_repository.list_where(
                self.session, User.id.in_(owner_ids)
            )
            owners = {user.id: user for user in loaded}
        items = []
        for row in rows:
            owner = owners.get(row.owner_id)
            payload = _withdraw_out(row)
            payload["owner"] = (
                {
                    "id": owner.id,
                    "email": owner.email,
                    "firstName": owner.first_name,
                    "lastName": owner.last_name,
                    "tutorVerifiedAt": (
                        owner.tutor_verified_at.isoformat()
                        if owner.tutor_verified_at
                        else None
                    ),
                }
                if owner
                else None
            )
            items.append(payload)
        return {
            "items": items,
            "page": self.page,
            "pageSize": self.page_size,
            "total": total,
        }


class FulfilWithdrawService:
    def __init__(
        self,
        session: AsyncSession,
        admin_id: str,
        request_id: str,
        *,
        fulfil: bool = True,
        note: str | None = None,
    ) -> None:
        self.session = session
        self.admin_id = admin_id
        self.request_id = request_id
        self.fulfil = fulfil
        self.note = note

    async def process(self) -> dict[str, Any]:
        row = await tutor_withdraw_requests_repository.by_id(
            self.session, self.request_id
        )
        if row is None:
            raise ApiError(404, "Withdraw request not found")
        if row.status != "PENDING":
            raise ApiError(
                409,
                f"Withdraw request is already {row.status.lower()}.",
                code="CENTER_WITHDRAW_NOT_PENDING",
            )
        now = utcnow()
        if self.fulfil:
            row.status = "FULFILLED"
            row.resolved_at = now
            row.resolved_by_id = self.admin_id
            row.resolution_note = self.note
            # Hold already debited the wallet; fulfilment is ops confirmation.
        else:
            wallet = await tutor_wallets_repository.for_owner_for_update(
                self.session, row.owner_id
            )
            if wallet is None:
                raise ApiError(500, "Wallet missing for withdraw reject")
            await _append_ledger(
                self.session,
                wallet,
                kind="WITHDRAW_REJECT",
                delta_kobo=row.amount_kobo,
                idempotency_key=f"withdraw_reject:{row.id}",
                meta={"withdrawRequestId": row.id},
            )
            row.status = "REJECTED"
            row.resolved_at = now
            row.resolved_by_id = self.admin_id
            row.resolution_note = self.note
        row.updated_at = now
        try:
            await notify_withdraw_status(
                self.session,
                owner_id=row.owner_id,
                request_id=row.id,
                status=row.status,
                amount_kobo=row.amount_kobo,
            )
        except Exception:
            logger.exception("withdraw status notification failed id=%s", row.id)
        capture(
            row.owner_id,
            "center_wallet_withdraw_resolved",
            {
                "request_id": row.id,
                "status": row.status,
                "amount_kobo": row.amount_kobo,
                "admin_id": self.admin_id,
            },
        )
        return {"withdrawal": _withdraw_out(row)}
