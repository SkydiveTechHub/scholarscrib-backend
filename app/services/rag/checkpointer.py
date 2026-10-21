"""LangGraph Postgres checkpointer wiring."""

from __future__ import annotations

from typing import Any, cast

from app.core.config import settings
from app.core.logger import logger

_checkpointer: Any = None
_setup_done = False


def _psycopg_url() -> str:
    url = settings.database_url
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if url.startswith("postgres+asyncpg://"):
        return url.replace("postgres+asyncpg://", "postgresql://", 1)
    return url


async def get_checkpointer() -> Any:
    """Return a shared AsyncPostgresSaver, creating tables once."""
    global _checkpointer, _setup_done
    if _checkpointer is not None:
        return _checkpointer
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError:
        logger.warning("langgraph-checkpoint-postgres not available")
        return None

    # from_conn_string is typed as AsyncIterator but returns an async CM at runtime.
    cm = cast(Any, AsyncPostgresSaver.from_conn_string(_psycopg_url()))
    if hasattr(cm, "__aenter__"):
        _checkpointer = await cm.__aenter__()
    else:
        _checkpointer = cm
    if not _setup_done and hasattr(_checkpointer, "setup"):
        await _checkpointer.setup()
        _setup_done = True
    return _checkpointer
