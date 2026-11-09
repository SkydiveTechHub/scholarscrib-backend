"""Billing provider factory + Paystack subscription event handling."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.billing.paystack.models import PaystackEvents
from app.services.billing.providers import (
    LagoBillingProvider,
    PaystackSubscriptionProvider,
    clear_billing_provider_cache,
    get_billing_provider,
)
from app.services.billing.providers.paystack_provider import (
    PaystackSubscriptionProvider as PSP,
)


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    clear_billing_provider_cache()
    yield
    clear_billing_provider_cache()


def test_factory_defaults_to_paystack(monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.providers.factory.settings.billing_provider", "paystack"
    )
    clear_billing_provider_cache()
    provider = get_billing_provider()
    assert provider.name == "paystack"
    assert isinstance(provider, PaystackSubscriptionProvider)


def test_factory_selects_lago(monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.providers.factory.settings.billing_provider", "lago"
    )
    clear_billing_provider_cache()
    provider = get_billing_provider()
    assert provider.name == "lago"
    assert isinstance(provider, LagoBillingProvider)


@pytest.mark.asyncio
async def test_paystack_checkout_extras_include_plan():
    plan = SimpleNamespace(paystack_plan_code="PLN_test123")
    user = SimpleNamespace(id="u1")
    extras = await PaystackSubscriptionProvider().checkout_extras(plan, user)
    assert extras == {"plan": "PLN_test123"}


@pytest.mark.asyncio
async def test_lago_checkout_extras_empty():
    plan = SimpleNamespace(paystack_plan_code="PLN_x")
    user = SimpleNamespace(id="u1")
    extras = await LagoBillingProvider().checkout_extras(plan, user)
    assert extras == {}


@pytest.mark.asyncio
async def test_paystack_cancel_calls_disable(monkeypatch):
    disable = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.paystack.disable_subscription",
        disable,
    )
    user = SimpleNamespace(
        id="u1",
        paystack_subscription_code="SUB_abc",
        paystack_email_token="tok_xyz",
    )
    await PaystackSubscriptionProvider().cancel_at_period_end(AsyncMock(), user)
    disable.assert_awaited_once_with(code="SUB_abc", token="tok_xyz")


@pytest.mark.asyncio
async def test_paystack_resume_calls_enable(monkeypatch):
    enable = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.paystack.enable_subscription",
        enable,
    )
    user = SimpleNamespace(
        id="u1",
        paystack_subscription_code="SUB_abc",
        paystack_email_token="tok_xyz",
    )
    await PaystackSubscriptionProvider().resume(AsyncMock(), user)
    enable.assert_awaited_once_with(code="SUB_abc", token="tok_xyz")


@pytest.mark.asyncio
async def test_subscription_create_stores_codes(monkeypatch):
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        paystack_subscription_code=None,
        paystack_email_token=None,
        next_billing_at=None,
        billing_status="active",
        plan_code="standard",
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.by_id",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.first",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.by_email",
        AsyncMock(return_value=None),
    )

    next_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    data = {
        "subscription_code": "SUB_new",
        "email_token": "tok_new",
        "next_payment_date": next_at,
        "metadata": {"userId": "u1"},
    }
    out = await PSP().handle_paystack_event(
        session, PaystackEvents.SUBSCRIPTION_CREATE, data
    )
    assert out["handled"] is True
    assert user.paystack_subscription_code == "SUB_new"
    assert user.paystack_email_token == "tok_new"
    assert user.next_billing_at is not None


@pytest.mark.asyncio
async def test_checkout_activated_disables_prior_subscription(monkeypatch):
    disable = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.paystack.disable_subscription",
        disable,
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.capture", MagicMock()
    )
    user = SimpleNamespace(
        id="u1",
        plan_code="standard",
        paystack_subscription_code="SUB_old",
        paystack_email_token="tok_old",
        next_billing_at=None,
        billing_status="active",
    )
    row = SimpleNamespace(ends_at=datetime.now(UTC) + timedelta(days=30))
    await PaystackSubscriptionProvider().on_checkout_activated(
        AsyncMock(),
        user,
        row,
        {},
        prior_subscription_code="SUB_old",
        prior_email_token="tok_old",
    )
    disable.assert_awaited_once_with(code="SUB_old", token="tok_old")
    assert user.paystack_subscription_code is None
    assert user.next_billing_at == row.ends_at


@pytest.mark.asyncio
async def test_checkout_activated_does_not_clear_newer_subscription(monkeypatch):
    """subscription.create stamped SUB_new before verify — only disable SUB_old."""
    disable = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.paystack.disable_subscription",
        disable,
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.capture", MagicMock()
    )
    user = SimpleNamespace(
        id="u1",
        plan_code="premium",
        paystack_subscription_code="SUB_new",
        paystack_email_token="tok_new",
        next_billing_at=None,
        billing_status="active",
    )
    row = SimpleNamespace(ends_at=datetime.now(UTC) + timedelta(days=30))
    await PaystackSubscriptionProvider().on_checkout_activated(
        AsyncMock(),
        user,
        row,
        {},
        prior_subscription_code="SUB_old",
        prior_email_token="tok_old",
    )
    disable.assert_awaited_once_with(code="SUB_old", token="tok_old")
    assert user.paystack_subscription_code == "SUB_new"
    assert user.paystack_email_token == "tok_new"


@pytest.mark.asyncio
async def test_subscription_create_disables_differing_prior(monkeypatch):
    disable = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.paystack.disable_subscription",
        disable,
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.capture", MagicMock()
    )
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        plan_code="standard",
        paystack_subscription_code="SUB_old",
        paystack_email_token="tok_old",
        next_billing_at=None,
        billing_status="active",
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.first",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.by_email",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.by_id",
        AsyncMock(return_value=user),
    )
    out = await PSP().handle_paystack_event(
        session,
        PaystackEvents.SUBSCRIPTION_CREATE,
        {
            "subscription_code": "SUB_new",
            "email_token": "tok_new",
            "next_payment_date": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "metadata": {"userId": "u1"},
        },
    )
    assert out["handled"] is True
    disable.assert_awaited_once_with(code="SUB_old", token="tok_old")
    assert user.paystack_subscription_code == "SUB_new"


@pytest.mark.asyncio
async def test_subscription_disable_assigns_freemium(monkeypatch):
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        billing_status="canceling",
        tier="STANDARD",
        plan_code="standard",
        paystack_subscription_code="SUB_x",
    )
    assign = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.first",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        "app.services.billing.providers.paystack_provider.users_repository.by_email",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.billing.provisioning.assign_freemium",
        assign,
    )

    out = await PSP().handle_paystack_event(
        session,
        PaystackEvents.SUBSCRIPTION_DISABLE,
        {"subscription_code": "SUB_x"},
    )
    assert out["handled"] is True
    assign.assert_awaited_once()


@pytest.mark.asyncio
async def test_lago_provider_ignores_paystack_events():
    out = await LagoBillingProvider().handle_paystack_event(
        AsyncMock(), "subscription.create", {"subscription_code": "SUB_x"}
    )
    assert out is None


@pytest.mark.asyncio
async def test_charge_success_pending_returns_none_for_checkout_path(monkeypatch):
    """First checkout PENDING row must fall through to ApplyChargeSuccessService."""
    session = AsyncMock()
    pending = SimpleNamespace(status="PENDING", user_id="u1")
    monkeypatch.setattr(
        "app.database.repositories.billing.subscriptions_repository.by_reference",
        AsyncMock(return_value=pending),
    )

    out = await PSP().handle_paystack_event(
        session,
        PaystackEvents.CHARGE_SUCCESS,
        {"reference": "pw_abc", "metadata": {"userId": "u1"}},
    )
    assert out is None
