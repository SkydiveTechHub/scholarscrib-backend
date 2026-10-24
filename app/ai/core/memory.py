"""Long-term memory store (cross-thread), via AsyncPostgresStore.

Opened once for the duration of the server (FastAPI lifespan), matching the
LangGraph custom-store pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.ai.core.checkpointer import _pool_config, _psycopg_url
from app.core.logger import logger

_store: Any = None


@asynccontextmanager
async def open_memory_store() -> AsyncIterator[Any]:
    """Yield an ``AsyncPostgresStore`` open for the duration of the server."""
    global _store
    try:
        from langgraph.store.postgres.aio import AsyncPostgresStore
        from psycopg_pool import AsyncConnectionPool
    except ImportError:
        logger.warning("AsyncPostgresStore not available; long-term memory off")
        yield None
        return

    pool: Any = None
    try:
        cfg = _pool_config(name="langgraph-store")
        kwargs = cfg.pop("kwargs")
        pool = AsyncConnectionPool(
            conninfo=_psycopg_url(),
            open=False,
            kwargs=kwargs,
            **cfg,
        )
        await pool.open()
        store = AsyncPostgresStore(conn=pool)
        await store.setup()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to open LangGraph memory store")
        if pool is not None:
            await pool.close()
        yield None
        return

    _store = store
    try:
        yield store
    finally:
        _store = None
        await pool.close()


def get_memory_store() -> Any:
    """Return the server-scoped store opened in app lifespan."""
    return _store


def memory_namespace(user_id: str) -> tuple[str, ...]:
    return ("tutor", user_id, "memories")
