import calendar
import hashlib
import hmac
import uuid
from datetime import UTC, datetime

from app.domain import TIER_RANK


def new_reference() -> str:
    return "pw_" + uuid.uuid4().hex


def add_months_utc(moment: datetime, months: int) -> datetime:
    current = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    index = current.month - 1 + months
    year = current.year + index // 12
    month = index % 12 + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day)


def term_start(now: datetime, current_ends_at: datetime | None) -> datetime:
    if current_ends_at is not None and current_ends_at > now:
        return current_ends_at
    return now


def term_end(start: datetime, period: str) -> datetime:
    return add_months_utc(start, 12 if period == "YEARLY" else 1)


def is_live(
    status: str, starts_at: datetime | None, ends_at: datetime | None, now: datetime
) -> bool:
    return (
        status == "ACTIVE"
        and ends_at is not None
        and (starts_at is None or starts_at <= now)
        and ends_at > now
    )


def resolve_live_tier(rows: list[dict], now: datetime) -> tuple[str, datetime | None]:
    live = [
        row
        for row in rows
        if is_live(row["status"], row.get("startsAt"), row.get("endsAt"), now)
    ]
    if not live:
        return "FREEMIUM", None
    best = max(live, key=lambda row: TIER_RANK[row["tier"]])
    expires = max(row["endsAt"] for row in live if row["tier"] == best["tier"])
    return best["tier"], expires


def paystack_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def signatures_match(secret: str, body: bytes, header: str | None) -> bool:
    if not header:
        return False
    expected = paystack_signature(secret, body)
    return hmac.compare_digest(expected, header.strip())


def settlement_outcome(
    *,
    row_status: str | None,
    row_amount: int | None,
    row_currency: str | None,
    transaction_status: str | None,
    transaction_amount: int | None,
    transaction_currency: str | None,
) -> str:
    if row_status is None:
        return "unknown-reference"
    if row_status == "ACTIVE":
        return "already-applied"
    if transaction_status != "success":
        return "not-successful"
    if transaction_amount != row_amount:
        return "amount-mismatch"
    if (transaction_currency or "").upper() != (row_currency or "NGN").upper():
        return "currency-mismatch"
    if row_status not in {"PENDING", "ABANDONED"}:
        return "not-pending"
    return "activate"
