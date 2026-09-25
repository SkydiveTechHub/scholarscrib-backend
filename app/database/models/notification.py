"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import (
    AnnouncementStatus,
    DeliveryStatus,
)


class PushSubscription(Base):
    __tablename__ = "PushSubscription"

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    endpoint: Mapped[str] = mapped_column(String, unique=True)
    p256dh: Mapped[str] = mapped_column(String)
    auth: Mapped[str] = mapped_column(String)
    failure_count: Mapped[int] = mapped_column("failureCount", Integer, default=0)
    device_id: Mapped[str | None] = mapped_column("deviceId", String)


class NotificationPreference(Base):
    __tablename__ = "NotificationPreference"

    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE"), primary_key=True
    )
    study_reminders: Mapped[bool] = mapped_column(
        "studyReminders", Boolean, default=True
    )
    streak_reminders: Mapped[bool] = mapped_column(
        "streakReminders", Boolean, default=True
    )
    announcements: Mapped[bool] = mapped_column(Boolean, default=True)


class Announcement(Base):
    __tablename__ = "Announcement"

    id: Mapped[str] = id_column()
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String)
    audience: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(AnnouncementStatus, default="QUEUED")
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime(timezone=True))
    recipient_count: Mapped[int] = mapped_column("recipientCount", Integer, default=0)
    sent_count: Mapped[int] = mapped_column("sentCount", Integer, default=0)
    failed_count: Mapped[int] = mapped_column("failedCount", Integer, default=0)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True)
    )


class AnnouncementDelivery(Base):
    __tablename__ = "AnnouncementDelivery"
    __table_args__ = (UniqueConstraint("announcementId", "subscriptionId"),)

    id: Mapped[str] = id_column()
    announcement_id: Mapped[str] = mapped_column(
        "announcementId", ForeignKey("Announcement.id", ondelete="CASCADE")
    )
    subscription_id: Mapped[str] = mapped_column("subscriptionId", String)
    status: Mapped[str] = mapped_column(DeliveryStatus, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(
        "claimedAt", DateTime(timezone=True)
    )


class AnnouncementDismissal(Base):
    __tablename__ = "AnnouncementDismissal"

    announcement_id: Mapped[str] = mapped_column(
        "announcementId",
        ForeignKey("Announcement.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE"), primary_key=True
    )


class ReminderLog(Base):
    __tablename__ = "ReminderLog"

    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    day_key: Mapped[str] = mapped_column("dayKey", String, primary_key=True)
