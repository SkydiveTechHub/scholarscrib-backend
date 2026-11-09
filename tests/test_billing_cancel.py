"""Student cancel / resume — period-end vs immediate intro cancel."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import ApiError
from app.services.billing.service import (
    CancelSubscriptionService,
    ResumeSubscriptionService,
)


@pytest.mark.asyncio
async def test_cancel_trialing_goes_freemium_immediately(monkeypatch):
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        first_name="Ada",
        tier="STANDARD",
        plan_code="standard",
        billing_status="trialing",
        next_billing_at=None,
        lago_subscription_external_id="sub_intro",
    )
    assign = AsyncMock()
    monkeypatch.setattr("app.services.billing.service.assign_freemium", assign)
    monkeypatch.setattr("app.services.billing.service.capture", MagicMock())

    out = await CancelSubscriptionService(session, user).process()
    assert out["status"] == "freemium"
    assign.assert_awaited()
    assert assign.await_args.kwargs["source"] == "cancel"


@pytest.mark.asyncio
async def test_cancel_active_schedules_period_end(monkeypatch):
    session = AsyncMock()
    until = datetime.now(UTC) + timedelta(days=12)
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        first_name="Ada",
        tier="PREMIUM",
        plan_code="premium",
        billing_status="active",
        next_billing_at=until,
        lago_subscription_external_id="sub_prem",
    )
    monkeypatch.setattr("app.services.billing.service.settings.lago_api_key", None)
    monkeypatch.setattr("app.services.billing.service.capture", MagicMock())
    enqueue = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.billing.service.EmailService.enqueue", enqueue)

    out = await CancelSubscriptionService(session, user).process()
    assert out["status"] == "canceling"
    assert user.billing_status == "canceling"
    assert user.next_billing_at == until
    enqueue.assert_awaited()


@pytest.mark.asyncio
async def test_resume_restores_active(monkeypatch):
    session = AsyncMock()
    until = datetime.now(UTC) + timedelta(days=5)
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        tier="STANDARD",
        plan_code="standard",
        billing_status="canceling",
        next_billing_at=until,
        lago_subscription_external_id=None,
    )
    monkeypatch.setattr("app.services.billing.service.settings.lago_api_key", None)
    monkeypatch.setattr("app.services.billing.service.capture", MagicMock())

    out = await ResumeSubscriptionService(session, user).process()
    assert out["status"] == "active"
    assert user.billing_status == "active"


@pytest.mark.asyncio
async def test_resume_rejects_when_not_canceling():
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        billing_status="active",
        next_billing_at=datetime.now(UTC) + timedelta(days=5),
    )
    with pytest.raises(ApiError) as exc:
        await ResumeSubscriptionService(session, user).process()
    assert exc.value.status_code == 400
