import hashlib
import hmac


def cron_decision(method: str, secret: str | None, bearer: str | None) -> int | None:
    """None means the caller may proceed. 204 is the fail-open skip."""
    if method.upper() != "POST":
        return 405
    if not secret or len(secret) < 16:
        return 204
    if not bearer:
        return 401
    left = hashlib.sha256(secret.encode()).digest()
    right = hashlib.sha256(bearer.encode()).digest()
    if not hmac.compare_digest(left, right):
        return 401
    return None


def is_session_revoked(
    is_active: bool, sessions_valid_from, issued_at_seconds: int | None
) -> bool:
    if not is_active:
        return True
    if sessions_valid_from is None:
        return False
    if issued_at_seconds is None:
        return True
    valid_from = sessions_valid_from.timestamp()
    return issued_at_seconds <= int(valid_from)


def current_streak(days: set[str], end_day: str) -> int:
    from app.services.planner.days import add_days

    if end_day not in days:
        return 0
    count = 0
    cursor = end_day
    while cursor in days:
        count += 1
        cursor = add_days(cursor, -1)
    return count
