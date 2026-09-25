"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column


class Admin(Base):
    __tablename__ = "Admin"

    id: Mapped[str] = id_column()
    email: Mapped[str | None] = mapped_column(String, unique=True)
    username: Mapped[str | None] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column("passwordHash", String)
    is_owner: Mapped[bool] = mapped_column("isOwner", Boolean, default=False)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        "lastLoginAt", DateTime(timezone=True)
    )
    created_by_id: Mapped[str | None] = mapped_column(
        "createdById", ForeignKey("Admin.id")
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class AdminAudit(Base):
    __tablename__ = "AdminAudit"

    id: Mapped[str] = id_column()
    actor_id: Mapped[str] = mapped_column(
        "actorId", ForeignKey("Admin.id", ondelete="CASCADE")
    )
    action: Mapped[str] = mapped_column(String)
    entity: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str | None] = mapped_column("entityId", String)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
