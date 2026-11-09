"""Paystack HMAC + Lago webhook idempotency / intro terminate."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.student.billing import _paystack_event_key
from app.services.billing.lago_client import verify_hmac_signature
from app.services.billing.rules import paystack_signature, signatures_match
from app.services.billing.service import (
    HandleLagoWebhookService,
    RecordLagoEventService,
    VerifyPaystackSignatureService,
)


def test_paystack_lifecycle_key_includes_stamp_not_bare_id():
    # Re-cancel must not collide on subscription entity id alone.
    data = {
        "id": 12345,
        "subscription_code": "SUB_abc",
        "updated_at": "2026-11-08T10:00:00.000Z",
    }
    key1 = _paystack_event_key("subscription.disable", {}, data)
    assert key1 == "subscription.disable:SUB_abc:2026-11-08T10:00:00.000Z"
    data2 = {**data, "updated_at": "2026-11-09T10:00:00.000Z"}
    key2 = _paystack_event_key("subscription.disable", {}, data2)
    assert key1 != key2


def test_paystack_bare_id_without_stamp_does_not_lock_out():
    # No stamp and no subscription_code → None rather than event:id forever.
    assert _paystack_event_key("subscription.disable", {}, {"id": 99}) is None


def test_paystack_hmac_roundtrip():
    body = b'{"event":"charge.success"}'
    secret = "sk_test_secret"
    sig = paystack_signature(secret, body)
    assert signatures_match(secret, body, sig)
    assert not signatures_match(secret, body, "deadbeef")
    assert not signatures_match(secret, body, None)


@pytest.mark.asyncio
async def test_verify_paystack_signature_service(monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.service.settings.paystack_secret_key", "sk_test"
    )
    body = b'{"event":"charge.success"}'
    good = paystack_signature("sk_test", body)
    assert await VerifyPaystackSignatureService(body, good).process()
    assert not await VerifyPaystackSignatureService(body, "nope").process()


def test_lago_hmac_roundtrip():
    body = b'{"webhook_type":"subscription.terminated"}'
    secret = "lago_hmac"
    import hashlib
    import hmac

    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    import base64

    sig = base64.b64encode(digest).decode()
    assert verify_hmac_signature(body, sig, secret)
    assert not verify_hmac_signature(body, "nope", secret)
    assert not verify_hmac_signature(body, sig, None)


@pytest.mark.asyncio
async def test_record_lago_event_idempotent(monkeypatch):
    session = AsyncMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=None)
    session.begin_nested = MagicMock(return_value=nested)
    session.add = MagicMock()
    session.flush = AsyncMock()

    first = await RecordLagoEventService(
        session, "key-1", "subscription.terminated", {"a": 1}
    ).process()
    assert first is True

    from sqlalchemy.exc import IntegrityError

    session.flush = AsyncMock(side_effect=IntegrityError("dup", {}, None))
    second = await RecordLagoEventService(
        session, "key-1", "subscription.terminated", {"a": 1}
    ).process()
    assert second is False


@pytest.mark.asyncio
async def test_lago_subscription_terminated_assigns_freemium(monkeypatch):
    session = AsyncMock()
    user = SimpleNamespace(
        id="u1",
        email="a@b.com",
        first_name="Ada",
        tier="STANDARD",
        plan_code="standard",
        billing_status="trialing",
        intro_ends_at=datetime.now(UTC) - timedelta(hours=1),
        lago_subscription_external_id="sub_intro",
        tier_updated_at=None,
        next_billing_at=None,
    )
    assign = AsyncMock()
    monkeypatch.setattr(
        "app.services.billing.service.users_repository.by_id",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr("app.services.billing.service.assign_freemium", assign)

    payload = {
        "webhook_type": "subscription.terminated",
        "object_type": "subscription",
        "subscription": {
            "external_customer_id": "u1",
            "plan_code": "standard",
            "status": "terminated",
            "external_id": "sub_intro",
        },
    }
    await HandleLagoWebhookService(session, payload).process()
    assign.assert_awaited()
    assert assign.await_args.kwargs["source"] == "intro_ended"
