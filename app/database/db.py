import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

async_engine = create_async_engine(
    url=settings.database_url,
    echo=settings.db_echo,
    future=True,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


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


AnSession = Annotated[AsyncSession, Depends(db_session)]
