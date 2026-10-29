"""Server-side product events for PostHog.

Capture never raises into a request. With no project token, or when tests
disable the client, every call is a no-op. Person properties are an allowlist:
names, emails, and phone numbers are dropped even if a caller passes them.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.core.logger import logger
from app.core.timeutil import lagos_day_key

_PERSON_FIELDS = frozenset({"class_level", "track", "state", "tier", "signup_method"})
_DISALLOWED = frozenset(
    {
        "email",
        "name",
        "phone",
        "first_name",
        "last_name",
        "firstName",
        "lastName",
        "message",
        "content",
        "password",
        "password_hash",
        "passwordHash",
    }
)
_ACTIVE_TTL_SECONDS = 36 * 60 * 60
_REDIS_TIMEOUT = 0.8
_SEEN_CAP = 10_000

_client: Any = None
_disabled = False
_lock = threading.Lock()
_seen: dict[str, str] = {}


def open_client() -> None:
    """Start the process-wide client. Safe to call when analytics is unconfigured."""
    global _client
    if _disabled:
        return
    token = (settings.posthog_project_token or "").strip()
    if not token:
        return
    try:
        from posthog import Posthog

        _client = Posthog(project_api_key=token, host=settings.posthog_host)
    except Exception:
        logger.exception("PostHog client failed to start")
        _client = None


def shutdown_client() -> None:
    global _client
    client = _client
    _client = None
    if client is None:
        return
    try:
        client.shutdown()
    except Exception:
        logger.exception("PostHog shutdown failed")


def disable() -> None:
    """Stop capture and forget the in-process daily-active set. Tests use this."""
    global _disabled
    _disabled = True
    with _lock:
        _seen.clear()


def use_client(client: Any) -> None:
    """Install a client and allow capture. Tests pass a fake."""
    global _client, _disabled
    _client = client
    _disabled = False
    with _lock:
        _seen.clear()


def capture(
    distinct_id: str,
    event: str,
    properties: dict[str, Any] | None = None,
    set_props: dict[str, Any] | None = None,
    set_once: dict[str, Any] | None = None,
) -> None:
    """Enqueue one identified event. Returns immediately and never raises."""
    if not _sending():
        return
    try:
        props = _event_properties(properties)
        person = _person_properties(set_props)
        once = _person_properties(set_once)
        if person:
            props["$set"] = person
        if once:
            props["$set_once"] = once
        _client.capture(
            event,
            distinct_id=str(distinct_id),
            properties=props or None,
        )
    except Exception:
        logger.exception("Product event %s failed", event)


def note_user_active(user_id: str) -> None:
    """Capture ``user_active`` at most once per user per Lagos day."""
    if not _sending():
        return
    try:
        if _claim_today(user_id):
            capture(user_id, "user_active")
    except Exception:
        logger.exception("Product event user_active failed")


def _sending() -> bool:
    return not _disabled and _client is not None


def _claim_today(user_id: str) -> bool:
    day = lagos_day_key()
    token = f"{user_id}:{day}"
    if _already_seen(token, day):
        return False
    claimed = _redis_claim(token)
    if claimed is None:
        return _memory_claim(token, day)
    _memory_claim(token, day)
    return claimed


def _already_seen(token: str, day: str) -> bool:
    with _lock:
        _drop_stale(day)
        return _seen.get(token) == day


def _memory_claim(token: str, day: str) -> bool:
    with _lock:
        _drop_stale(day)
        if _seen.get(token) == day:
            return False
        if len(_seen) >= _SEEN_CAP:
            _seen.pop(next(iter(_seen)))
        _seen[token] = day
        return True


def _drop_stale(day: str) -> None:
    stale = [key for key, seen_day in _seen.items() if seen_day != day]
    for key in stale:
        _seen.pop(key, None)


def _redis_claim(token: str) -> bool | None:
    """True when this call won the key, False when taken, None when Redis is down."""
    url = settings.upstash_redis_rest_url
    secret = settings.upstash_redis_rest_token
    if not url or not secret:
        return None
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {secret}"},
            json=["SET", f"ph:active:{token}", "1", "NX", "EX", _ACTIVE_TTL_SECONDS],
            timeout=_REDIS_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json().get("result")
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None
    if result == "OK":
        return True
    if result is None:
        return False
    return None


def _event_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        if not isinstance(key, str) or key in _DISALLOWED or key.startswith("$"):
            continue
        scalar = _scalar(value)
        if scalar is None:
            continue
        cleaned[key] = scalar
    return cleaned


def _person_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        if key not in _PERSON_FIELDS:
            continue
        scalar = _scalar(value)
        if scalar is None or scalar == "":
            continue
        cleaned[key] = scalar
    return cleaned


def _scalar(value: object) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float | Decimal):
        return float(value)
    return None
