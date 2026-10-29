"""PostHog traces for student Astra. Privacy mode is always on.

The handler is attached only to graph runs. Checkpoint reads stay callback-free.
A missing client, a disabled flag, or a construction error leaves the reply
unchanged.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.core.config import settings
from app.core.logger import logger
from app.services import product_events

_KINDS = frozenset({"message", "stream", "resume", "replay", "fork"})
_CONTEXT_FIELDS = (
    "subject_id",
    "topic_id",
    "lesson_id",
    "question_id",
    "exam_type",
)


def tutor_callback(thread: Any, *, kind: str) -> Any | None:
    """One privacy-mode handler per student graph run. None when tracing is off."""
    if kind not in _KINDS:
        logger.error("Unknown Astra trace kind %s", kind)
        return None
    if not settings.posthog_ai_tracing:
        return None
    client = product_events.client()
    if client is None:
        return None
    try:
        from posthog.ai.langchain import CallbackHandler

        return CallbackHandler(
            client,
            distinct_id=str(thread.user_id),
            properties=_properties(thread, kind),
            privacy_mode=True,
        )
    except Exception:
        logger.exception("Astra trace handler failed to start")
        return None


def with_tutor_trace(
    config: RunnableConfig, thread: Any, *, kind: str
) -> RunnableConfig:
    """Copy ``config`` and attach a handler. Return ``config`` when tracing is off."""
    handler = tutor_callback(thread, kind=kind)
    if handler is None:
        return config
    traced: RunnableConfig = {**config, "callbacks": [handler]}
    return traced


def _properties(thread: Any, kind: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "$ai_session_id": thread.id,
        "agent": "astra",
        "kind": kind,
    }
    for key in _CONTEXT_FIELDS:
        value = getattr(thread, key, None)
        if isinstance(value, str) and value:
            properties[key] = value
    return properties
