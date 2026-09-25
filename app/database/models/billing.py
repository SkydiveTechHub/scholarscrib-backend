"""SQLAlchemy models for this domain."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.database.models.columns import id_column, timestamp_column
from app.database.models.enums import (
    BillingPeriod,
    SubscriptionSource,
    SubscriptionStatus,
    SubscriptionTier,
)


class Subscription(Base):
    __tablename__ = "Subscription"
    __table_args__ = (
        Index("Subscription_user_ends_idx", "userId", "endsAt"),
        Index("Subscription_status_idx", "status"),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    tier: Mapped[str] = mapped_column(SubscriptionTier)
    period: Mapped[str] = mapped_column(BillingPeriod)
    reference: Mapped[str] = mapped_column(String, unique=True)
    amount_kobo: Mapped[int] = mapped_column("amountKobo", Integer)
    currency: Mapped[str] = mapped_column(String, default="NGN")
    channel: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(SubscriptionStatus, default="PENDING")
    source: Mapped[str] = mapped_column(SubscriptionSource, default="PAYSTACK")
    paid_at: Mapped[datetime | None] = mapped_column("paidAt", DateTime(timezone=True))
    starts_at: Mapped[datetime | None] = mapped_column(
        "startsAt", DateTime(timezone=True)
    )
    ends_at: Mapped[datetime | None] = mapped_column("endsAt", DateTime(timezone=True))
    granted_by_id: Mapped[str | None] = mapped_column(
        "grantedById", ForeignKey("Admin.id")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class PaystackEvent(Base):
    __tablename__ = "PaystackEvent"

    event_key: Mapped[str] = mapped_column("eventKey", String, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
