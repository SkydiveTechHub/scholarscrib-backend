"""Keep tool payloads small enough for the model. Drop secrets."""

from __future__ import annotations

import json
from typing import Any

_SECRET_KEYS = frozenset(
    {
        "password",
        "passwordHash",
        "password_hash",
        "accessToken",
        "token",
        "signature",
        "apiKey",
        "api_key",
    }
)


def scrub(
    value: Any,
    *,
    list_limit: int = 30,
    string_limit: int = 500,
) -> Any:
    if isinstance(value, dict):
        return {
            key: scrub(item, list_limit=list_limit, string_limit=string_limit)
            for key, item in value.items()
            if key not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [
            scrub(item, list_limit=list_limit, string_limit=string_limit)
            for item in value[:list_limit]
        ]
    if isinstance(value, str) and len(value) > string_limit:
        return value[:string_limit] + "…"
    return value


def dumps(data: Any, *, limit: int = 8000) -> str:
    text = json.dumps(scrub(data), default=str)
    if len(text) <= limit:
        return text
    return json.dumps({"truncated": True, "preview": text[:limit]})


def error_payload(exc: Exception) -> str:
    return dumps({"ok": False, "error": str(exc)})
