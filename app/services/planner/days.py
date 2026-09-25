from datetime import UTC, datetime

DAY_MS = 86_400_000
DayKey = str


def _to_utc_ms(key: DayKey) -> int:
    year, month, day = (int(part) for part in key.split("-"))
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _from_utc_ms(ms: int) -> DayKey:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def add_days(key: DayKey, days: int) -> DayKey:
    return _from_utc_ms(_to_utc_ms(key) + days * DAY_MS)


def days_between(start: DayKey, end: DayKey) -> int:
    return round((_to_utc_ms(end) - _to_utc_ms(start)) / DAY_MS)


def iso_weekday(key: DayKey) -> int:
    year, month, day = (int(part) for part in key.split("-"))
    weekday = datetime(year, month, day, tzinfo=UTC).weekday()
    return weekday + 1


def is_weekend(key: DayKey) -> bool:
    return iso_weekday(key) >= 6


def monday_of(key: DayKey) -> DayKey:
    return add_days(key, 1 - iso_weekday(key))
