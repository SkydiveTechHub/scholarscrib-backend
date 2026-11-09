"""Reminder due logic + email template smoke renders."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.billing.service import BillingRemindersService
from app.services.email import EmailService


@pytest.mark.asyncio
async def test_reminder_claim_dedupes(monkeypatch):
    session = AsyncMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=None)
    session.begin_nested = MagicMock(return_value=nested)
    session.add = MagicMock()
    session.flush = AsyncMock()

    svc = BillingRemindersService(session)
    target = datetime(2026, 11, 14, tzinfo=UTC)
    assert await svc._claim("u1", "intro_ending", 7, target) is True

    from sqlalchemy.exc import IntegrityError

    session.flush = AsyncMock(side_effect=IntegrityError("dup", {}, None))
    assert await svc._claim("u1", "intro_ending", 7, target) is False


@pytest.mark.asyncio
async def test_billing_email_templates_render():
    contexts = {
        "billing_intro_ending.html": {
            "first_name": "Ada",
            "lead_days": 7,
            "billing_url": "https://example.com/settings/billing",
        },
        "billing_renewal_reminder.html": {
            "first_name": "Ada",
            "plan_name": "Standard",
            "lead_days": 1,
            "amount_naira": 3000,
            "billing_url": "https://example.com/settings/billing",
        },
        "billing_payment_succeeded.html": {
            "first_name": "Ada",
            "plan_name": "Premium",
            "amount_naira": 10000,
            "billing_url": "https://example.com/settings/billing",
        },
        "billing_payment_failed.html": {
            "first_name": "Ada",
            "plan_name": "Standard",
            "billing_url": "https://example.com/settings/billing",
            "grace_days": 3,
        },
        "billing_downgraded.html": {
            "first_name": "Ada",
            "billing_url": "https://example.com/settings/billing",
        },
    }
    for name, ctx in contexts.items():
        html = await EmailService.render_template(name, ctx)
        assert len(html) > 200
        assert "settings/billing" in html
        assert "ScholarsCrib" in html


@pytest.mark.asyncio
async def test_send_intro_emits_posthog_once(monkeypatch):
    capture = MagicMock()
    enqueue = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.billing.service.capture", capture)
    monkeypatch.setattr("app.services.billing.service.EmailService.enqueue", enqueue)
    svc = BillingRemindersService(AsyncMock())
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        first_name="Ada",
        plan_code="standard",
        intro_ends_at=datetime(2026, 11, 14, tzinfo=UTC),
    )
    await svc._send_intro(user, 7)
    assert capture.call_args[0][1] == "billing_intro_ending_soon"
    assert capture.call_args[0][2]["lead_days"] == 7
    enqueue.assert_awaited()
