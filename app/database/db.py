import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.util import await_only

from app.core.config import settings
from app.core.logger import logger

async_engine = create_async_engine(
    url=settings.database_url,
    echo=settings.db_echo,
    future=True,
    pool_pre_ping=True,
    # A scale-to-zero database (Neon) takes a few seconds to resume; wait for it
    # instead of failing the connect.
    connect_args={"timeout": 15},
)

# Waits between attempts to reach a sleeping database; one more attempt than waits.
_WAKE_BACKOFF = (0.5, 1.0)

async_session = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@event.listens_for(async_engine.sync_engine, "do_connect")
def _connect_with_retry(dialect, conn_rec, cargs, cparams):
    """Open a physical connection, riding out a database cold start.

    Runs only when the pool needs a new connection (first use, or after pre-ping
    discarded a dead one), so requests that never query pay nothing. After the
    last attempt it re-raises; the app maps that to a retryable 503.
    """
    for wait in (*_WAKE_BACKOFF, None):
        try:
            return dialect.connect(*cargs, **cparams)
        except Exception as exc:
            logger.warning("Database not reachable (%s)", type(exc).__name__)
            if wait is None:
                raise
            await_only(asyncio.sleep(wait))


async def db_session() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_session() -> AsyncSession:
    return async_session()


class Base(AsyncAttrs, DeclarativeBase):
    def to_dict(self):
        return {
            column.key: getattr(self, column.key)
            for column in inspect(self).mapper.column_attrs
        }

    def to_json(self):
        return json.loads(json.dumps(self.to_dict(), default=str))


# scope="function": commit before the response is sent, so a client that reads
# right after a write sees it.
AnSession = Annotated[AsyncSession, Depends(db_session, scope="function")]
