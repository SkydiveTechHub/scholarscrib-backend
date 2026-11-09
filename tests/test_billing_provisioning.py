"""Signup intro provisioning (Lago optional)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.billing.provisioning import assign_freemium, provision_signup_billing


@pytest.fixture
def session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_provision_noop_when_billing_disabled(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.provisioning.settings",
        SimpleNamespace(
            billing_enabled=False,
            billing_intro_days=30,
            lago_api_key=None,
        ),
    )
    add = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.provisioning.subscriptions_repository.add", add
    )
    user = SimpleNamespace(
        id="u1",
        tier="FREEMIUM",
        plan_code="freemium",
        billing_status="freemium",
        intro_ends_at=None,
    )
    await provision_signup_billing(session, user)
    assert user.tier == "FREEMIUM"
    assert user.billing_status == "freemium"
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_provision_sets_standard_intro(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.provisioning.settings",
        SimpleNamespace(
            billing_enabled=True,
            billing_intro_days=30,
            lago_api_key=None,
        ),
    )
    add = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.provisioning.subscriptions_repository.add", add
    )
    capture = MagicMock()
    monkeypatch.setattr("app.services.billing.provisioning.capture", capture)
    monkeypatch.setattr(
        "app.services.billing.provisioning.get_billing_provider",
        lambda: SimpleNamespace(name="paystack", provision_intro=AsyncMock()),
    )

    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        first_name="Ada",
        last_name="Lovelace",
        tier="FREEMIUM",
        plan_code="freemium",
        billing_status="freemium",
        tier_updated_at=None,
        intro_ends_at=None,
        lago_subscription_external_id=None,
        next_billing_at=None,
    )
    await provision_signup_billing(session, user)
    assert user.tier == "STANDARD"
    assert user.plan_code == "standard"
    assert user.billing_status == "trialing"
    assert user.intro_ends_at is not None
    add.assert_awaited()
    assert capture.call_args[0][1] == "billing_intro_started"


@pytest.mark.asyncio
async def test_assign_freemium(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.provisioning.get_billing_provider",
        lambda: SimpleNamespace(name="paystack", assign_freemium_remote=AsyncMock()),
    )
    monkeypatch.setattr(
        "app.services.billing.provisioning.subscriptions_repository.revoke_active",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.billing.provisioning.subscriptions_repository.add",
        AsyncMock(),
    )
    capture = MagicMock()
    monkeypatch.setattr("app.services.billing.provisioning.capture", capture)

    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        first_name="Ada",
        tier="STANDARD",
        plan_code="standard",
        billing_status="trialing",
        tier_updated_at=None,
        intro_ends_at=None,
        lago_subscription_external_id="sub_intro_u1",
        next_billing_at=None,
    )
    await assign_freemium(session, user, source="intro_ended")
    assert user.tier == "FREEMIUM"
    assert user.plan_code == "freemium"
    assert user.billing_status == "freemium"
    assert capture.call_args[0][1] == "billing_intro_ended"
