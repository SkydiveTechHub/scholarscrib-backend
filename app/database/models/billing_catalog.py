"""Editable plan catalog: plans, features, and per-plan entitlements."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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


class BillingPlan(Base):
    __tablename__ = "BillingPlan"

    id: Mapped[str] = id_column()
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column("displayName", String)
    amount_kobo: Mapped[int] = mapped_column("amountKobo", Integer, default=0)
    currency: Mapped[str] = mapped_column(String, default="NGN")
    interval: Mapped[str] = mapped_column(String, default="monthly")
    lago_plan_code: Mapped[str] = mapped_column("lagoPlanCode", String)
    paystack_plan_code: Mapped[str | None] = mapped_column(
        "paystackPlanCode", String, nullable=True
    )
    is_purchasable: Mapped[bool] = mapped_column(
        "isPurchasable", Boolean, default=False
    )
    rank: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    blurb: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class BillingFeature(Base):
    __tablename__ = "BillingFeature"

    id: Mapped[str] = id_column()
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    value_type: Mapped[str] = mapped_column("valueType", String, default="boolean")
    select_options: Mapped[list | None] = mapped_column("selectOptions", JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    # Maps to legacy require_feature keys (studyPlanner, aiTutor, …).
    gate_key: Mapped[str | None] = mapped_column("gateKey", String, unique=True)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class BillingPlanEntitlement(Base):
    __tablename__ = "BillingPlanEntitlement"
    __table_args__ = (
        UniqueConstraint(
            "planId", "featureId", name="BillingPlanEntitlement_plan_feature"
        ),
    )

    id: Mapped[str] = id_column()
    plan_id: Mapped[str] = mapped_column(
        "planId", ForeignKey("BillingPlan.id", ondelete="CASCADE"), index=True
    )
    feature_id: Mapped[str] = mapped_column(
        "featureId", ForeignKey("BillingFeature.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    privilege_value: Mapped[Any | None] = mapped_column("privilegeValue", JSONB)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class LagoEvent(Base):
    __tablename__ = "LagoEvent"

    event_key: Mapped[str] = mapped_column("eventKey", String, primary_key=True)
    webhook_type: Mapped[str | None] = mapped_column("webhookType", String)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class BillingReminderLog(Base):
    """Dedupes renewal/intro emails and PostHog reminder events."""

    __tablename__ = "BillingReminderLog"
    __table_args__ = (
        UniqueConstraint(
            "userId",
            "kind",
            "leadDays",
            "periodKey",
            name="BillingReminderLog_unique",
        ),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String)  # intro_ending | renewal
    lead_days: Mapped[int] = mapped_column("leadDays", Integer)
    period_key: Mapped[str] = mapped_column("periodKey", String)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
