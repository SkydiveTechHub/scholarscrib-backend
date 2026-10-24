"""LangGraph Postgres checkpointer wiring.

Opened once for the duration of the server (FastAPI lifespan), matching the
LangGraph custom-checkpointer pattern: yield a saver for the process lifetime,
not per request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.config import settings
from app.core.logger import logger

_checkpointer: Any = None


def _psycopg_url() -> str:
    """Convert SQLAlchemy/asyncpg ``DATABASE_URL`` into a libpq/psycopg URI.

    Render and similar hosts often use ``?ssl=require`` (asyncpg). Psycopg
    rejects ``ssl`` and expects ``sslmode`` instead.
    """
    url = settings.database_url.strip()
    for prefix in (
        "postgresql+asyncpg://",
        "postgres+asyncpg://",
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
    ):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
            break
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    ssl_val = query.pop("ssl", None)
    if ssl_val is not None and "sslmode" not in query:
        normalized = str(ssl_val).strip().lower()
        if normalized in ("1", "true", "yes", "require"):
            query["sslmode"] = "require"
        elif normalized in ("0", "false", "no", "disable"):
            query["sslmode"] = "disable"
        else:
            # prefer / verify-ca / verify-full, etc.
            query["sslmode"] = normalized

    # asyncpg-only knobs are not valid for psycopg
    query.pop("prepared_statement_cache_size", None)

    return urlunparse(parsed._replace(query=urlencode(query)))


def _pool_config(*, name: str) -> dict[str, Any]:
    """Shared psycopg pool settings for server-lifetime LangGraph resources."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    return {
        "min_size": 1,
        "max_size": 5,
        "timeout": 30,
        "max_idle": 300,
        "max_lifetime": 1800,
        "check": AsyncConnectionPool.check_connection,
        "name": name,
        "kwargs": {
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    }


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[Any]:
    """Yield an ``AsyncPostgresSaver`` open for the duration of the server."""
    global _checkpointer
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool
    except ImportError:
        logger.warning("langgraph-checkpoint-postgres not available")
        yield None
        return

    # ``from_conn_string`` opens a single connection; use a pool so concurrent
    # requests and idle timeouts (Render) do not leave a dead socket in place.
    pool: Any = None
    try:
        cfg = _pool_config(name="langgraph-checkpointer")
        kwargs = cfg.pop("kwargs")
        pool = AsyncConnectionPool(
            conninfo=_psycopg_url(),
            open=False,
            kwargs=kwargs,
            **cfg,
        )
        await pool.open()
        saver = AsyncPostgresSaver(conn=pool)
        await saver.setup()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to open LangGraph checkpointer")
        if pool is not None:
            await pool.close()
        yield None
        return

    _checkpointer = saver
    try:
        yield saver
    finally:
        _checkpointer = None
        await pool.close()


def get_checkpointer() -> Any:
    """Return the server-scoped checkpointer opened in app lifespan."""
    return _checkpointer
