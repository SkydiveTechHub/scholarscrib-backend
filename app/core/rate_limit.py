import time
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.errors import RateLimited

WINDOW_CAP = 10_000
REDIS_TIMEOUT = 0.8


@dataclass
class _Window:
    count: int
    reset_at: float


_memory: dict[str, _Window] = {}


def _sweep(now: float) -> None:
    if len(_memory) < WINDOW_CAP:
        return
    expired = [key for key, window in _memory.items() if window.reset_at <= now]
    for key in expired:
        _memory.pop(key, None)
    if len(_memory) >= WINDOW_CAP:
        for key in list(_memory)[: len(_memory) - WINDOW_CAP + 1]:
            _memory.pop(key, None)


def _memory_hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    now = time.monotonic()
    _sweep(now)
    current = _memory.get(key)
    if current is None or current.reset_at <= now:
        _memory[key] = _Window(count=1, reset_at=now + window_seconds)
        return True, window_seconds
    current.count += 1
    retry = max(1, int(current.reset_at - now))
    # The request that opens a window is always allowed, even if limit is 0.
    if current.count <= 1 or current.count <= limit:
        return True, retry
    return False, retry


def _redis_hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int] | None:
    url = settings.upstash_redis_rest_url
    token = settings.upstash_redis_rest_token
    if not url or not token:
        return None
    prefixed = f"rl:{key}"
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=["INCR", prefixed],
            timeout=REDIS_TIMEOUT,
        )
        response.raise_for_status()
        count = int(response.json()["result"])
        if count == 1:
            httpx.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=["EXPIRE", prefixed, window_seconds],
                timeout=REDIS_TIMEOUT,
            )
        allowed = count <= 1 or count <= limit
        return allowed, window_seconds
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


def client_ip(forwarded_for: str | None, real_ip: str | None) -> str:
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"
    return (real_ip or "unknown").strip() or "unknown"


def hit(key: str, limit: int, window_seconds: int) -> None:
    redis_result = _redis_hit(key, limit, window_seconds)
    allowed, retry = (
        redis_result
        if redis_result is not None
        else _memory_hit(key, limit, window_seconds)
    )
    if not allowed:
        raise RateLimited(retry)


def reset_memory() -> None:
    _memory.clear()
