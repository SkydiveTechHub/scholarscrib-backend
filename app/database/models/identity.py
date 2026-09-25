"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import (
    ClassLevel,
    Role,
    SchoolType,
    SubscriptionTier,
    Track,
)


class User(Base):
    __tablename__ = "User"
    __table_args__ = (
        Index("User_classLevel_track_idx", "classLevel", "track"),
        Index("User_tier_idx", "tier"),
        Index("User_isActive_idx", "isActive"),
        Index("User_state_idx", "state"),
    )

    id: Mapped[str] = id_column()
    email: Mapped[str | None] = mapped_column(String, unique=True)
    phone: Mapped[str | None] = mapped_column(String, unique=True)
    password_hash: Mapped[str | None] = mapped_column("passwordHash", String)
    role: Mapped[str] = mapped_column(Role, default="STUDENT")
    first_name: Mapped[str | None] = mapped_column("firstName", String)
    last_name: Mapped[str | None] = mapped_column("lastName", String)
    image: Mapped[str | None] = mapped_column(String)
    class_level: Mapped[str | None] = mapped_column("classLevel", ClassLevel)
    track: Mapped[str | None] = mapped_column(Track)
    state: Mapped[str | None] = mapped_column(String)
    school_id: Mapped[str | None] = mapped_column("schoolId", ForeignKey("School.id"))
    tier: Mapped[str] = mapped_column(SubscriptionTier, default="FREEMIUM")
    tier_updated_at: Mapped[datetime | None] = mapped_column(
        "tierUpdatedAt", DateTime(timezone=True)
    )
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True)
    suspended_at: Mapped[datetime | None] = mapped_column(
        "suspendedAt", DateTime(timezone=True)
    )
    suspended_reason: Mapped[str | None] = mapped_column("suspendedReason", Text)
    sessions_valid_from: Mapped[datetime | None] = mapped_column(
        "sessionsValidFrom", DateTime(timezone=True)
    )
    email_verified: Mapped[datetime | None] = mapped_column(
        "emailVerified", DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class Account(Base):
    __tablename__ = "Account"
    __table_args__ = (UniqueConstraint("provider", "providerAccountId"),)

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    provider_account_id: Mapped[str] = mapped_column("providerAccountId", String)
    refresh_token: Mapped[str | None] = mapped_column("refresh_token", Text)
    access_token: Mapped[str | None] = mapped_column("access_token", Text)
    expires_at: Mapped[int | None] = mapped_column("expires_at", Integer)
    token_type: Mapped[str | None] = mapped_column("token_type", String)
    scope: Mapped[str | None] = mapped_column(String)
    id_token: Mapped[str | None] = mapped_column("id_token", Text)
    session_state: Mapped[str | None] = mapped_column("session_state", String)


class AuthSession(Base):
    __tablename__ = "Session"

    id: Mapped[str] = id_column()
    session_token: Mapped[str] = mapped_column("sessionToken", String, unique=True)
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VerificationToken(Base):
    __tablename__ = "VerificationToken"
    __table_args__ = (UniqueConstraint("identifier", "token"),)

    identifier: Mapped[str] = mapped_column(String, primary_key=True)
    token: Mapped[str] = mapped_column(String, primary_key=True)
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserDevice(Base):
    __tablename__ = "UserDevice"
    __table_args__ = (Index("UserDevice_userId_revokedAt_idx", "userId", "revokedAt"),)

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String, default="Browser")
    last_seen_at: Mapped[datetime] = mapped_column(
        "lastSeenAt", DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        "revokedAt", DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class School(Base):
    __tablename__ = "School"

    id: Mapped[str] = id_column()
    name: Mapped[str] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String)
    school_type: Mapped[str | None] = mapped_column("schoolType", SchoolType)
