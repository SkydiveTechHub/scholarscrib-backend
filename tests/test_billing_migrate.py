"""Migration path for existing live paid / COMP term holders."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.billing.migrate import (
    migrate_live_paid_users,
    migration_summary,
)


def _sub(**kwargs):
    now = datetime.now(UTC)
    defaults = dict(
        id="sub1",
        user_id="u1",
        tier="STANDARD",
        period="MONTHLY",
        status="ACTIVE",
        source="PAYSTACK",
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=25),
        amount_kobo=300_000,
        currency="NGN",
        lago_subscription_external_id=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_migrate_live_paid_user_stamps_fields(monkeypatch):
    now = datetime.now(UTC)
    row = _sub()
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        tier="FREEMIUM",
        plan_code="freemium",
        billing_status="freemium",
        tier_updated_at=None,
        next_billing_at=None,
        intro_ends_at=now + timedelta(days=10),
        lago_subscription_external_id=None,
    )

    session = AsyncMock()
    result_proxy = MagicMock()
    result_proxy.scalars.return_value.all.return_value = [row]
    session.execute = AsyncMock(return_value=result_proxy)
    session.get = AsyncMock(return_value=user)

    monkeypatch.setattr("app.services.billing.migrate.settings.lago_api_key", None)
    capture = MagicMock()
    monkeypatch.setattr("app.services.billing.migrate.capture", capture)

    out = await migrate_live_paid_users(session, now=now, push_lago=False)
    assert out.users == 1
    assert out.subscriptions == 1
    assert user.tier == "STANDARD"
    assert user.plan_code == "standard"
    assert user.billing_status == "active"
    assert user.intro_ends_at is None
    assert user.next_billing_at == row.ends_at
    assert row.source == "LAGO"
    assert row.lago_subscription_external_id
    assert capture.call_args[0][1] == "subscription_activated"
    summary = migration_summary(out)
    assert summary["users"] == 1


@pytest.mark.asyncio
async def test_migrate_skips_expired(monkeypatch):
    now = datetime.now(UTC)
    row = _sub(
        starts_at=now - timedelta(days=40),
        ends_at=now - timedelta(days=5),
    )
    session = AsyncMock()
    result_proxy = MagicMock()
    result_proxy.scalars.return_value.all.return_value = [row]
    session.execute = AsyncMock(return_value=result_proxy)

    out = await migrate_live_paid_users(session, now=now, push_lago=False)
    assert out.skipped == 1
    assert out.users == 0


@pytest.mark.asyncio
async def test_migrate_dry_run_does_not_mutate(monkeypatch):
    now = datetime.now(UTC)
    row = _sub(source="COMP")
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        tier="PREMIUM",
        plan_code="premium",
        billing_status="active",
        tier_updated_at=None,
        next_billing_at=None,
        intro_ends_at=None,
        lago_subscription_external_id=None,
    )
    session = AsyncMock()
    result_proxy = MagicMock()
    result_proxy.scalars.return_value.all.return_value = [row]
    session.execute = AsyncMock(return_value=result_proxy)
    session.get = AsyncMock(return_value=user)

    out = await migrate_live_paid_users(session, now=now, push_lago=False, dry_run=True)
    assert out.users == 1
    assert row.source == "COMP"
    assert user.lago_subscription_external_id is None
