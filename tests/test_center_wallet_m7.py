"""M7: 5% referral commission, refund reverse, wallet→tokens, withdraw (D11/D12)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.domain import (
    CENTER_WALLET_MIN_WITHDRAW_KOBO,
    REFERRAL_COMMISSION_BPS,
)
from app.services.centers.wallet import (
    FulfilWithdrawService,
    RequestWithdrawService,
    SpendWalletOnTokensService,
    commission_kobo,
    credit_referral_commission,
    resolve_attribution_membership,
    reverse_referral_commission,
)


def test_commission_kobo_is_five_percent():
    assert REFERRAL_COMMISSION_BPS == 500
    assert commission_kobo(300_000) == 15_000  # Standard ₦3,000 → ₦150
    assert commission_kobo(0) == 0
    assert commission_kobo(-1) == 0


def _membership(**extra):
    base = dict(
        id="mem-1",
        center_id="center-1",
        user_id="student-1",
        kind="STUDENT",
        status="ACTIVE",
        joined_at=utcnow(),
        created_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _center(**extra):
    base = dict(
        id="center-1",
        owner_id="tutor-1",
        name="Lagos Prep",
        archived_at=None,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _wallet(**extra):
    base = dict(
        id="wallet-1",
        owner_id="tutor-1",
        balance_kobo=0,
        updated_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _ledger(**extra):
    base = dict(
        id="led-1",
        owner_id="tutor-1",
        wallet_id="wallet-1",
        kind="COMMISSION",
        delta_kobo=15_000,
        balance_after_kobo=15_000,
        center_id="center-1",
        student_id="student-1",
        subscription_id="sub-1",
        reference="ref-1",
        idempotency_key="commission:sub:sub-1",
        meta={},
        created_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _tutor(**extra):
    base = dict(
        id="tutor-1",
        email="tutor@example.com",
        tutor_verified_at=utcnow(),
        first_name="Tolu",
        last_name="Ade",
    )
    base.update(extra)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_attribution_first_active_membership_wins():
    older = _membership(id="mem-old", center_id="center-old", joined_at=utcnow())
    newer = _membership(id="mem-new", center_id="center-new")
    center = _center(id="center-old", owner_id="tutor-old")
    session = AsyncMock()
    with (
        patch(
            "app.services.centers.wallet.memberships_repository."
            "active_student_memberships_oldest_first",
            AsyncMock(return_value=[older, newer]),
        ),
        patch(
            "app.services.centers.wallet.centers_repository.by_id",
            AsyncMock(return_value=center),
        ),
    ):
        result = await resolve_attribution_membership(session, "student-1")
    assert result is not None
    membership, attributed_center = result
    assert membership.id == "mem-old"
    assert attributed_center.owner_id == "tutor-old"


@pytest.mark.asyncio
async def test_freemium_earns_nothing():
    session = AsyncMock()
    result = await credit_referral_commission(
        session,
        student_id="student-1",
        amount_kobo=0,
        tier="FREEMIUM",
        subscription_id="sub-1",
        reference="ref-1",
        source="checkout",
    )
    assert result is None


@pytest.mark.asyncio
async def test_commission_credit_five_percent():
    session = AsyncMock()
    membership = _membership()
    center = _center()
    wallet = _wallet()

    async def fake_append(session, wallet, **kwargs):
        entry = _ledger(
            id="led-new",
            delta_kobo=kwargs["delta_kobo"],
            balance_after_kobo=wallet.balance_kobo + kwargs["delta_kobo"],
            idempotency_key=kwargs["idempotency_key"],
            kind=kwargs["kind"],
        )
        wallet.balance_kobo = entry.balance_after_kobo
        return entry

    with (
        patch(
            "app.services.centers.wallet.resolve_attribution_membership",
            AsyncMock(return_value=(membership, center)),
        ),
        patch(
            "app.services.centers.wallet.tutor_wallet_ledgers_repository."
            "by_idempotency_key",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.centers.wallet.ensure_wallet",
            AsyncMock(return_value=wallet),
        ),
        patch(
            "app.services.centers.wallet.tutor_wallets_repository.for_owner_for_update",
            AsyncMock(return_value=wallet),
        ),
        patch(
            "app.services.centers.wallet._append_ledger",
            side_effect=fake_append,
        ),
        patch(
            "app.services.centers.wallet.notify_wallet_credit",
            AsyncMock(),
        ),
        patch("app.services.centers.wallet.capture"),
    ):
        entry = await credit_referral_commission(
            session,
            student_id="student-1",
            amount_kobo=300_000,
            tier="STANDARD",
            subscription_id="sub-1",
            reference="ref-1",
            source="checkout",
        )
    assert entry is not None
    assert entry.delta_kobo == 15_000
    assert wallet.balance_kobo == 15_000


@pytest.mark.asyncio
async def test_refund_reverses_commission():
    session = AsyncMock()
    credit = _ledger(delta_kobo=15_000)
    wallet = _wallet(balance_kobo=15_000)

    async def fake_append(session, wallet, **kwargs):
        wallet.balance_kobo = int(wallet.balance_kobo) + int(kwargs["delta_kobo"])
        return _ledger(
            id="led-rev",
            kind=kwargs["kind"],
            delta_kobo=kwargs["delta_kobo"],
            balance_after_kobo=wallet.balance_kobo,
            idempotency_key=kwargs["idempotency_key"],
            meta=kwargs.get("meta"),
        )

    with (
        patch(
            "app.services.centers.wallet._find_commission_entry",
            AsyncMock(return_value=credit),
        ),
        patch(
            "app.services.centers.wallet.tutor_wallet_ledgers_repository."
            "by_idempotency_key",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.centers.wallet.tutor_wallets_repository.for_owner_for_update",
            AsyncMock(return_value=wallet),
        ),
        patch(
            "app.services.centers.wallet._append_ledger",
            side_effect=fake_append,
        ),
        patch("app.services.centers.wallet.capture"),
    ):
        entry = await reverse_referral_commission(
            session, subscription_id="sub-1", reference="ref-1"
        )
    assert entry is not None
    assert entry.kind == "REFUND_REVERSE"
    assert entry.delta_kobo == -15_000
    assert wallet.balance_kobo == 0


@pytest.mark.asyncio
async def test_spend_wallet_on_tokens_uses_hook():
    session = AsyncMock()
    wallet = _wallet(balance_kobo=100_000)
    pool = SimpleNamespace(balance=120, center_id="center-1")

    async def fake_append(session, wallet, **kwargs):
        wallet.balance_kobo = int(wallet.balance_kobo) + int(kwargs["delta_kobo"])
        return _ledger(
            id="led-spend",
            kind="SPEND_TOKENS",
            delta_kobo=kwargs["delta_kobo"],
            balance_after_kobo=wallet.balance_kobo,
            idempotency_key=kwargs["idempotency_key"],
        )

    with (
        patch(
            "app.services.centers.wallet.load_owned_center",
            AsyncMock(return_value=_center()),
        ),
        patch(
            "app.services.centers.wallet.ensure_wallet",
            AsyncMock(return_value=wallet),
        ),
        patch(
            "app.services.centers.wallet.tutor_wallets_repository.for_owner_for_update",
            AsyncMock(return_value=wallet),
        ),
        patch(
            "app.services.centers.wallet._append_ledger",
            side_effect=fake_append,
        ),
        patch(
            "app.services.centers.wallet.credit_tokens_from_wallet",
            AsyncMock(return_value=pool),
        ) as credit_hook,
        patch("app.services.centers.wallet.capture"),
    ):
        result = await SpendWalletOnTokensService(
            session, "tutor-1", "center-1", "astra_pack_100"
        ).process()

    assert result["tokens"]["credited"] == 100
    assert result["wallet"]["balanceKobo"] == 0
    credit_hook.assert_awaited_once()
    kwargs = credit_hook.await_args
    assert kwargs.args[1] == "center-1"
    assert kwargs.args[2] == 100
    assert kwargs.kwargs["wallet_entry_id"] == "led-spend"


@pytest.mark.asyncio
async def test_withdraw_requires_verified_tutor():
    session = AsyncMock()
    tutor = _tutor(tutor_verified_at=None)
    with pytest.raises(ApiError) as exc:
        await RequestWithdrawService(
            session, tutor, CENTER_WALLET_MIN_WITHDRAW_KOBO
        ).process()
    assert exc.value.status_code == 403
    assert exc.value.payload["code"] == "CENTER_WALLET_KYC_REQUIRED"


@pytest.mark.asyncio
async def test_withdraw_request_and_admin_fulfil():
    session = AsyncMock()
    tutor = _tutor()
    wallet = _wallet(balance_kobo=200_000)
    held = []

    async def fake_append(session, wallet, **kwargs):
        wallet.balance_kobo = int(wallet.balance_kobo) + int(kwargs["delta_kobo"])
        entry = _ledger(
            id="led-hold",
            kind=kwargs["kind"],
            delta_kobo=kwargs["delta_kobo"],
            balance_after_kobo=wallet.balance_kobo,
            idempotency_key=kwargs["idempotency_key"],
        )
        held.append(entry)
        return entry

    added = []

    async def fake_add(session, obj, flush=False):
        added.append(obj)
        return obj

    with (
        patch(
            "app.services.centers.wallet.ensure_wallet",
            AsyncMock(return_value=wallet),
        ),
        patch(
            "app.services.centers.wallet.tutor_wallets_repository.for_owner_for_update",
            AsyncMock(return_value=wallet),
        ),
        patch(
            "app.services.centers.wallet._append_ledger",
            side_effect=fake_append,
        ),
        patch(
            "app.services.centers.wallet.tutor_withdraw_requests_repository.add",
            side_effect=fake_add,
        ),
        patch(
            "app.services.centers.wallet.notify_withdraw_status",
            AsyncMock(),
        ),
        patch("app.services.centers.wallet.capture"),
    ):
        created = await RequestWithdrawService(
            session, tutor, CENTER_WALLET_MIN_WITHDRAW_KOBO
        ).process()

    assert created["withdrawal"]["status"] == "pending"
    assert wallet.balance_kobo == 100_000
    assert added
    request = added[0]
    request.status = "PENDING"
    request.amount_kobo = CENTER_WALLET_MIN_WITHDRAW_KOBO
    request.owner_id = "tutor-1"
    request.resolved_at = None
    request.resolved_by_id = None
    request.resolution_note = None
    request.updated_at = utcnow()
    request.created_at = utcnow()
    request.payout_details = None
    request.note = None
    request.hold_ledger_id = "led-hold"
    request.wallet_id = "wallet-1"

    with (
        patch(
            "app.services.centers.wallet.tutor_withdraw_requests_repository.by_id",
            AsyncMock(return_value=request),
        ),
        patch(
            "app.services.centers.wallet.notify_withdraw_status",
            AsyncMock(),
        ),
        patch("app.services.centers.wallet.capture"),
    ):
        fulfilled = await FulfilWithdrawService(
            session, "admin-1", request.id, fulfil=True, note="Paid via transfer"
        ).process()

    assert fulfilled["withdrawal"]["status"] == "fulfilled"
    assert request.resolved_by_id == "admin-1"
