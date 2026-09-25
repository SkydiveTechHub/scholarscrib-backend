"""Shared column helpers for ScholarsCrib tables."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import cuid


def id_column() -> Mapped[str]:
    return mapped_column(String, primary_key=True, default=cuid)


def timestamp_column(name: str, onupdate: bool = False) -> Mapped[datetime]:
    return mapped_column(
        name,
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now() if onupdate else None,
    )
