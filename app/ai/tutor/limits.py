"""Daily message caps for the AI tutor (per-plan from catalog)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.domain import AI_TUTOR_DAILY_FALLBACK


def enforce_daily_message_limit(user_id: str, limit: int | None = None) -> None:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    cap = limit if limit is not None else settings.ai_tutor_daily_message_limit
    if cap <= 0:
        raise ApiError(403, "AI tutor is not included in your plan.", feature="aiTutor")
    hit(f"ai-tutor:{user_id}:{day}", cap, 24 * 60 * 60)


def fallback_limit_for_tier(tier: str) -> int:
    return AI_TUTOR_DAILY_FALLBACK.get(tier, settings.ai_tutor_daily_message_limit)


async def resolve_tutor_daily_limit(session: AsyncSession, tier: str) -> int:
    from app.services.billing.catalog import ai_tutor_daily_limit

    try:
        limit = await ai_tutor_daily_limit(session, tier)
    except Exception:
        limit = 0
    if limit <= 0:
        return fallback_limit_for_tier(tier)
    return limit


async def enforce_tutor_quota(session: AsyncSession, user_id: str, tier: str) -> None:
    limit = await resolve_tutor_daily_limit(session, tier)
    enforce_daily_message_limit(user_id, limit)
