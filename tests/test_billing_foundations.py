"""Billing foundations: state writer, catalog Redis version, outbox, cron auth."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.auth.rules import cron_decision
from app.services.billing.catalog import (
    _cache,
    invalidate_catalog_cache,
    plan_code_for_tier,
    tier_for_plan_code,
)
from app.services.billing.state import apply_billing_state, set_user_billing_cursor
from app.services.email import EmailService


def test_catalog_plan_tier_mapping_is_single_sot():
    assert plan_code_for_tier("STANDARD") == "standard"
    assert plan_code_for_tier("PREMIUM") == "premium"
    assert plan_code_for_tier("FREEMIUM") == "freemium"
    assert tier_for_plan_code("standard") == "STANDARD"
    assert tier_for_plan_code("unknown") == "FREEMIUM"


@pytest.mark.asyncio
async def test_apply_billing_state_sets_mirror_fields():
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        tier="FREEMIUM",
        plan_code="freemium",
        billing_status="freemium",
        next_billing_at=None,
        intro_ends_at=None,
        tier_updated_at=None,
    )
    next_at = datetime(2026, 12, 1, tzinfo=UTC)
    await apply_billing_state(
        session,
        user,
        plan_code="standard",
        billing_status="active",
        next_billing_at=next_at,
        intro_ends_at=None,
    )
    assert user.plan_code == "standard"
    assert user.tier == "STANDARD"
    assert user.billing_status == "active"
    assert user.next_billing_at == next_at
    assert user.intro_ends_at is None
    assert user.tier_updated_at is not None


@pytest.mark.asyncio
async def test_apply_billing_state_inserts_subscription(monkeypatch):
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        tier="STANDARD",
        plan_code="standard",
        billing_status="active",
        next_billing_at=None,
        intro_ends_at=None,
        tier_updated_at=None,
    )
    add = AsyncMock()
    monkeypatch.setattr("app.services.billing.state.subscriptions_repository.add", add)
    sub = SimpleNamespace(
        id="s1",
        user_id="u1",
        status="ACTIVE",
        tier="STANDARD",
        starts_at=datetime.now(UTC),
        ends_at=datetime.now(UTC) + timedelta(days=30),
        kind="renewal",
    )
    await apply_billing_state(
        session,
        user,
        plan_code="standard",
        billing_status="active",
        tier="STANDARD",
        next_billing_at=sub.ends_at,
        subscription=sub,
    )
    add.assert_awaited_once()
    assert add.await_args.args[1] is sub
    assert user.next_billing_at == sub.ends_at


def test_set_user_billing_cursor_only_touches_cursor():
    user = SimpleNamespace(
        next_billing_at=None, billing_status="active", tier="STANDARD"
    )
    when = datetime(2026, 12, 8, tzinfo=UTC)
    set_user_billing_cursor(user, when, billing_status="canceling")
    assert user.next_billing_at == when
    assert user.billing_status == "canceling"
    assert user.tier == "STANDARD"


def test_catalog_version_bump_clears_local_cache(monkeypatch):
    _cache["at"] = 999.0
    _cache["version"] = 1
    _cache["plans"] = ["stale"]
    _cache["by_code"] = {"standard": "stale"}
    _cache["gates"] = {}

    monkeypatch.setattr(
        "app.services.billing.catalog._redis_bump_catalog_version",
        lambda: 2,
    )
    invalidate_catalog_cache()
    assert _cache["plans"] is None
    assert _cache["version"] == 2


@pytest.mark.asyncio
async def test_load_catalog_skips_redis_within_ttl(monkeypatch):
    import time

    from app.services.billing.catalog import load_catalog

    _cache["at"] = time.monotonic()
    _cache["version"] = 1
    _cache["plans"] = ["cached"]
    monkeypatch.setattr(
        "app.services.billing.catalog._redis_catalog_version",
        lambda: (_ for _ in ()).throw(AssertionError("redis within TTL")),
    )
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=AssertionError("db within TTL"))
    assert await load_catalog(session) == ["cached"]


@pytest.mark.asyncio
async def test_load_catalog_extends_ttl_when_redis_version_matches(monkeypatch):
    import time

    from app.services.billing.catalog import load_catalog

    _cache["at"] = time.monotonic() - 120
    _cache["version"] = 5
    _cache["plans"] = ["cached"]
    monkeypatch.setattr(
        "app.services.billing.catalog._redis_catalog_version",
        lambda: 5,
    )
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=AssertionError("db when version matches"))
    before = _cache["at"]
    assert await load_catalog(session) == ["cached"]
    assert _cache["at"] > before


def test_cron_fail_closed_without_secret(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.cron_fail_open", False, raising=False)
    # Patch the settings import inside cron_decision.
    with patch("app.core.config.settings") as settings:
        settings.cron_fail_open = False
        assert cron_decision("POST", None, None) == 503
        assert cron_decision("POST", "short", None) == 503
        assert cron_decision("POST", "a" * 16, "a" * 16) is None


def test_cron_fail_open_dev_skip(monkeypatch):
    with patch("app.core.config.settings") as settings:
        settings.cron_fail_open = True
        assert cron_decision("POST", None, None) == 204
        assert cron_decision("POST", "short", None) == 204


@pytest.mark.asyncio
async def test_outbox_enqueue_writes_pending_row():
    session = AsyncMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=None)
    session.begin_nested = MagicMock(return_value=nested)
    session.add = MagicMock()
    session.flush = AsyncMock()

    ok = await EmailService.enqueue(
        session,
        to_email="a@b.com",
        subject="Hi",
        template="billing_downgraded.html",
        context={"first_name": "Ada"},
        idempotency_key="test:1",
    )
    assert ok is True
    session.add.assert_called_once()
    row = session.add.call_args.args[0]
    assert row.status == "pending"
    assert row.idempotency_key == "test:1"
    assert row.to_email == "a@b.com"


@pytest.mark.asyncio
async def test_outbox_drain_claims_then_commits_per_send(monkeypatch):
    now = datetime.now(UTC)
    pending = SimpleNamespace(
        to_email="a@b.com",
        subject="Hi",
        template="billing_downgraded.html",
        context={"first_name": "Ada"},
        status="pending",
        attempts=0,
        available_at=now,
        last_error=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [pending]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    send = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(EmailService, "send_email", send)

    # First drain: claim commit + outcome commit; failure stays pending with backoff.
    out = await EmailService.drain_outbox(session, limit=10)
    assert out["failed"] == 1
    assert pending.status == "pending"
    assert pending.attempts == 1
    assert pending.available_at > now
    assert session.commit.await_count == 2

    # Second drain succeeds.
    pending.available_at = now
    session.commit.reset_mock()
    out = await EmailService.drain_outbox(session, limit=10)
    assert out["sent"] == 1
    assert pending.status == "sent"
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_apply_charge_locks_row_before_activate(monkeypatch):
    from app.services.billing.service import ApplyChargeSuccessService

    row = SimpleNamespace(
        id="s1",
        user_id="u1",
        status="ACTIVE",
        tier="STANDARD",
        period="MONTHLY",
        amount_kobo=300_000,
        currency="NGN",
        ends_at=datetime.now(UTC) + timedelta(days=30),
        kind="checkout",
    )
    lock = AsyncMock(return_value=row)
    monkeypatch.setattr(
        "app.services.billing.service.subscriptions_repository.by_reference_for_update",
        lock,
    )
    activate = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.service.subscriptions_repository.activate", activate
    )
    outcome = await ApplyChargeSuccessService(
        AsyncMock(),
        "pw_ref",
        {"status": "success", "amount": 300_000, "currency": "NGN"},
    ).process()
    assert outcome == "already-applied"
    lock.assert_awaited_once()
    activate.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_advances_stale_cursor(monkeypatch):
    from app.services.billing.service import PaystackReconcileService

    now = datetime.now(UTC)
    user = SimpleNamespace(
        id="u1",
        plan_code="standard",
        billing_status="active",
        paystack_subscription_code="SUB_x",
        next_billing_at=now - timedelta(days=1),
        tier="STANDARD",
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [user]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    next_at = now + timedelta(days=28)
    monkeypatch.setattr(
        "app.services.billing.service.get_billing_provider",
        lambda: SimpleNamespace(name="paystack"),
    )
    monkeypatch.setattr(
        "app.services.billing.service.settings",
        SimpleNamespace(billing_enabled=True),
    )
    monkeypatch.setattr(
        "app.services.billing.service.paystack.fetch_subscription",
        AsyncMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(
                    status="active",
                    next_payment_date=next_at.isoformat(),
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.billing.service.subscriptions_repository.by_reference",
        AsyncMock(return_value=None),
    )
    applied = {}

    async def fake_apply(session, user, **kwargs):
        applied.update(kwargs)
        user.billing_status = kwargs.get("billing_status") or user.billing_status
        user.next_billing_at = kwargs.get("next_billing_at", user.next_billing_at)

    monkeypatch.setattr("app.services.billing.service.apply_billing_state", fake_apply)
    monkeypatch.setattr("app.services.billing.service.capture", MagicMock())

    out = await PaystackReconcileService(session).process()
    assert out["checked"] == 1
    assert out["advanced"] == 1
    assert applied["next_billing_at"] == next_at
    assert applied["tier"] == "STANDARD"
    assert applied["subscription"] is not None
    assert applied["subscription"].kind == "renewal"
    assert applied["subscription"].ends_at == next_at
    assert user.billing_status == "active"


@pytest.mark.asyncio
async def test_checkout_pending_freezes_prior_paystack_codes(monkeypatch):
    from app.services.billing.service import CheckoutService

    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        paystack_subscription_code="SUB_old",
        paystack_email_token="tok_old",
    )
    session = AsyncMock()
    added = {}

    async def fake_add(session, row, flush=False):
        added["row"] = row

    monkeypatch.setattr(
        "app.services.billing.service.subscriptions_repository.add", fake_add
    )
    svc = CheckoutService(session, user, "PREMIUM")
    await svc._store_pending("pw_ref", 1_000_000)
    assert added["row"].prior_paystack_subscription_code == "SUB_old"
    assert added["row"].prior_paystack_email_token == "tok_old"


@pytest.mark.asyncio
async def test_activate_syncs_row_ends_at_for_provider(monkeypatch):
    from app.services.billing.service import ApplyChargeSuccessService

    now = datetime.now(UTC)
    end = now + timedelta(days=30)
    row = SimpleNamespace(
        id="s1",
        user_id="u1",
        status="PENDING",
        period="MONTHLY",
        starts_at=None,
        ends_at=None,
        paid_at=None,
        channel=None,
    )
    session = AsyncMock()
    svc = ApplyChargeSuccessService(session, "ref", {"channel": "card"})
    monkeypatch.setattr(
        "app.services.billing.service.subscriptions_repository.latest_live_end",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.billing.service.term_start", lambda *_a, **_k: now
    )
    monkeypatch.setattr("app.services.billing.service.term_end", lambda *_a, **_k: end)
    monkeypatch.setattr(
        "app.services.billing.service.subscriptions_repository.activate",
        AsyncMock(return_value=SimpleNamespace(rowcount=1)),
    )
    assert await svc._activate(row) is True
    assert row.ends_at == end
    assert row.status == "ACTIVE"
    assert row.starts_at == now


@pytest.mark.asyncio
async def test_charge_renewal_appends_subscription_kind(monkeypatch):
    from app.services.billing.service import ChargeRenewalService

    session = AsyncMock()
    next_bill = datetime.now(UTC) + timedelta(days=30)
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        first_name="Ada",
        tier="STANDARD",
        plan_code="standard",
        billing_status="active",
        paystack_authorization_code="AUTH_x",
        next_billing_at=None,
        intro_ends_at=None,
        tier_updated_at=None,
    )
    charge = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(status="success"))
    )
    monkeypatch.setattr(
        "app.services.billing.service.paystack.charge_authorization", charge
    )
    monkeypatch.setattr(
        "app.services.billing.service.settings",
        SimpleNamespace(
            lago_enabled=False,
            billing_grace_days=3,
            frontend_url="https://example.com",
        ),
    )
    monkeypatch.setattr("app.services.billing.service.capture", MagicMock())
    enqueue = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.billing.service.EmailService.enqueue", enqueue)
    applied = {}

    async def fake_apply(session, user, **kwargs):
        applied.update(kwargs)
        if kwargs.get("subscription") is not None:
            user.next_billing_at = kwargs["next_billing_at"]
            user.billing_status = kwargs["billing_status"]

    monkeypatch.setattr("app.services.billing.service.apply_billing_state", fake_apply)
    monkeypatch.setattr(
        "app.services.billing.service.term_end", lambda *_a, **_k: next_bill
    )

    result = await ChargeRenewalService(session, user, 300_000, None).process()
    assert result == "success"
    assert applied["subscription"].kind == "renewal"
    assert applied["billing_status"] == "active"
    assert user.next_billing_at == next_bill
    enqueue.assert_awaited()
