"""Billing domain fallbacks and live-tier rules (no DB required)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import (
    AI_TUTOR_DAILY_FALLBACK,
    BETA_OPEN_FEATURES,
    ENTITLEMENTS,
    PAST_QUESTIONS_DETAIL_FALLBACK,
    PRICES_KOBO,
    can,
    price_kobo,
)
from app.services.billing.catalog import (
    SEED_ENTITLEMENTS,
    SEED_FEATURES,
    SEED_PLANS,
    past_questions_detail,
    performance_scope,
)
from app.services.billing.rules import is_live, resolve_live_tier


def test_beta_opens_are_empty():
    assert frozenset() == BETA_OPEN_FEATURES
    assert ENTITLEMENTS["studyPlanner"] == "STANDARD"
    assert ENTITLEMENTS["flashcards"] == "STANDARD"
    assert ENTITLEMENTS["premiumLibrary"] == "PREMIUM"
    assert ENTITLEMENTS["advancedAnalytics"] == "PREMIUM"
    assert can("FREEMIUM", "studyPlanner") is False
    assert can("STANDARD", "studyPlanner") is True
    assert can("FREEMIUM", "aiTutor") is True


def test_monthly_prices_match_product():
    assert price_kobo("STANDARD", "MONTHLY") == 300_000
    assert price_kobo("PREMIUM", "MONTHLY") == 1_000_000
    assert PRICES_KOBO[("FREEMIUM", "MONTHLY")] == 0


def test_seed_matrix_matches_product_brief():
    assert len(SEED_PLANS) == 3
    assert len(SEED_FEATURES) == 7
    assert SEED_ENTITLEMENTS["freemium"]["ai_tutor"] == 2
    assert SEED_ENTITLEMENTS["standard"]["ai_tutor"] == 20
    assert SEED_ENTITLEMENTS["premium"]["ai_tutor"] == 60
    assert SEED_ENTITLEMENTS["freemium"]["past_questions"] == "answers"
    assert SEED_ENTITLEMENTS["standard"]["past_questions"] == "explanation"
    assert SEED_ENTITLEMENTS["premium"]["past_questions"] == "ai_explanation"
    assert SEED_ENTITLEMENTS["premium"]["performance"] == "by_subject"
    assert SEED_ENTITLEMENTS["standard"]["study_plan"] is True
    assert SEED_ENTITLEMENTS["freemium"]["study_plan"] is False
    assert AI_TUTOR_DAILY_FALLBACK["PREMIUM"] == 60
    assert PAST_QUESTIONS_DETAIL_FALLBACK["STANDARD"] == "explanation"


@pytest.mark.asyncio
async def test_detail_resolvers_honor_enabled_flag(monkeypatch):
    from unittest.mock import AsyncMock

    async def disabled_pq(*_a, **_k):
        return {"enabled": False, "value": "explanation"}

    async def disabled_perf(*_a, **_k):
        return {"enabled": False, "value": "by_subject"}

    monkeypatch.setattr("app.services.billing.catalog.privilege_for_tier", disabled_pq)
    assert await past_questions_detail(AsyncMock(), "STANDARD") == "answers"

    monkeypatch.setattr(
        "app.services.billing.catalog.privilege_for_tier", disabled_perf
    )
    assert await performance_scope(AsyncMock(), "PREMIUM") == "overview"


def test_open_ended_active_subscription_is_live():
    now = datetime(2026, 11, 7, tzinfo=UTC)
    assert is_live("ACTIVE", now - timedelta(days=1), None, now) is True
    assert (
        is_live("ACTIVE", now - timedelta(days=1), now - timedelta(hours=1), now)
        is False
    )


def test_resolve_live_tier_prefers_richest_open_row():
    now = datetime(2026, 11, 7, tzinfo=UTC)
    tier, expires = resolve_live_tier(
        [
            {
                "status": "ACTIVE",
                "tier": "FREEMIUM",
                "startsAt": now - timedelta(days=10),
                "endsAt": None,
            },
            {
                "status": "ACTIVE",
                "tier": "STANDARD",
                "startsAt": now - timedelta(days=5),
                "endsAt": now + timedelta(days=25),
            },
        ],
        now,
    )
    assert tier == "STANDARD"
    assert expires is not None
