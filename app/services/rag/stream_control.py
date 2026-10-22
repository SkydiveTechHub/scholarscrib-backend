"""Cancel tokens for in-flight tutor SSE streams.

In-process registry for same-worker cancel; Upstash Redis so a stop request
on another worker can still interrupt the stream.
"""

from __future__ import annotations

import threading
import time

import httpx

from app.core.config import settings

_TTL_ACTIVE = 15 * 60
_TTL_CANCEL = 2 * 60
_REDIS_TIMEOUT = 0.8
_REDIS_CHECK_EVERY = 0.35

_lock = threading.Lock()
_active: dict[str, str] = {}  # thread_id -> stream_id
_cancelled: set[str] = set()  # "thread_id:stream_id" or "thread_id:*"
_last_remote_check: dict[str, float] = {}


def _active_key(thread_id: str) -> str:
    return f"tutor:stream:active:{thread_id}"


def _cancel_key(thread_id: str) -> str:
    return f"tutor:stream:cancel:{thread_id}"


def _redis_cmd(*args: object) -> object | None:
    url = settings.upstash_redis_rest_url
    token = settings.upstash_redis_rest_token
    if not url or not token:
        return None
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=list(args),
            timeout=_REDIS_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("result")
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


def begin_stream(thread_id: str, stream_id: str) -> None:
    with _lock:
        _active[thread_id] = stream_id
        _cancelled.discard(f"{thread_id}:*")
        _cancelled.discard(f"{thread_id}:{stream_id}")
    _redis_cmd("SET", _active_key(thread_id), stream_id, "EX", _TTL_ACTIVE)
    _redis_cmd("DEL", _cancel_key(thread_id))


def end_stream(thread_id: str, stream_id: str) -> None:
    with _lock:
        if _active.get(thread_id) == stream_id:
            _active.pop(thread_id, None)
        _cancelled.discard(f"{thread_id}:{stream_id}")
        _cancelled.discard(f"{thread_id}:*")
        _last_remote_check.pop(f"{thread_id}:{stream_id}", None)
    current = _redis_cmd("GET", _active_key(thread_id))
    if current is None or str(current) == stream_id:
        _redis_cmd("DEL", _active_key(thread_id))
    _redis_cmd("DEL", _cancel_key(thread_id))


def request_stop(thread_id: str, stream_id: str | None = None) -> dict:
    """Mark the active stream for this thread as cancelled.

    Returns whether a cancel signal was recorded and which stream id was targeted.
    """
    with _lock:
        active = _active.get(thread_id)
    if stream_id is None:
        remote_active = _redis_cmd("GET", _active_key(thread_id))
        target = active or (str(remote_active) if remote_active else None)
        token = f"{thread_id}:*"
    else:
        target = stream_id
        token = f"{thread_id}:{stream_id}"

    with _lock:
        _cancelled.add(token)

    _redis_cmd(
        "SET",
        _cancel_key(thread_id),
        stream_id or "*",
        "EX",
        _TTL_CANCEL,
    )
    return {
        "ok": True,
        "cancelled": True,
        "streamId": target,
    }


def should_stop(thread_id: str, stream_id: str) -> bool:
    token = f"{thread_id}:{stream_id}"
    with _lock:
        if token in _cancelled or f"{thread_id}:*" in _cancelled:
            return True
        last = _last_remote_check.get(token, 0.0)
    now = time.monotonic()
    if now - last < _REDIS_CHECK_EVERY:
        return False
    with _lock:
        _last_remote_check[token] = now
    flag = _redis_cmd("GET", _cancel_key(thread_id))
    if flag is None:
        return False
    value = str(flag)
    if value in ("*", stream_id):
        with _lock:
            _cancelled.add(token)
        return True
    return False


def reset_for_tests() -> None:
    with _lock:
        _active.clear()
        _cancelled.clear()
        _last_remote_check.clear()
