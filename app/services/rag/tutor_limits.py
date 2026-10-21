"""Daily message caps for the AI tutor."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.core.rate_limit import hit


def enforce_daily_message_limit(user_id: str) -> None:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    hit(
        f"ai-tutor:{user_id}:{day}",
        settings.ai_tutor_daily_message_limit,
        24 * 60 * 60,
    )
