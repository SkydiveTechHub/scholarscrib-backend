"""Long-term memory store (cross-thread), via AsyncPostgresStore."""

from __future__ import annotations

from typing import Any, cast

from app.core.logger import logger
from app.services.rag.checkpointer import _psycopg_url

_store: Any = None
_setup_done = False


async def get_memory_store() -> Any:
    """Shared AsyncPostgresStore for user memories across chat threads."""
    global _store, _setup_done
    if _store is not None:
        return _store
    try:
        from langgraph.store.postgres.aio import AsyncPostgresStore
    except ImportError:
        logger.warning("AsyncPostgresStore not available; long-term memory off")
        return None

    # from_conn_string is typed as AsyncIterator but returns an async CM at runtime.
    cm = cast(Any, AsyncPostgresStore.from_conn_string(_psycopg_url()))
    if hasattr(cm, "__aenter__"):
        _store = await cm.__aenter__()
    else:
        _store = cm
    if not _setup_done and hasattr(_store, "setup"):
        await _store.setup()
        _setup_done = True
    return _store


def memory_namespace(user_id: str) -> tuple[str, ...]:
    return ("tutor", user_id, "memories")
