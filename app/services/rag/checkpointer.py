"""LangGraph Postgres checkpointer wiring."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.config import settings
from app.core.logger import logger

_checkpointer: Any = None
_setup_done = False


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
