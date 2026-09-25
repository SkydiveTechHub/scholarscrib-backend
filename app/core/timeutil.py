from datetime import UTC, datetime
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def lagos_day_key(moment: datetime | None = None) -> str:
    """Civil date in Africa/Lagos. Streaks, plan days, and reminders use this."""
    current = as_utc(moment or utcnow())
    return current.astimezone(LAGOS).strftime("%Y-%m-%d")


def previous_day_key(day: str) -> str:
    from app.services.planner.days import add_days

    return add_days(day, -1)
