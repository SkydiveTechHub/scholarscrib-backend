"""Tutor Centers domain tables (assignment-scoped visibility)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
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
    AssignmentLifecycle,
    AssignmentTargetKind,
    AssignmentTaskType,
    BillingPeriod,
    CenterAbuseReportKind,
    CenterAbuseReportStatus,
    CenterCheckoutStatus,
    CenterInviteKind,
    CenterMembershipKind,
    CenterMembershipStatus,
    CenterPlan,
    CenterTokenLedgerKind,
    RecommendationStatus,
    TutorWalletLedgerKind,
    TutorWithdrawStatus,
)


class Center(Base):
    __tablename__ = "Center"
    __table_args__ = (Index("Center_ownerId_idx", "ownerId"),)

    id: Mapped[str] = id_column()
    owner_id: Mapped[str] = mapped_column(
        "ownerId", ForeignKey("User.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String)
    lga: Mapped[str | None] = mapped_column(String)
    subjects: Mapped[list | None] = mapped_column(JSONB)
    levels: Mapped[list | None] = mapped_column(JSONB)
    plan: Mapped[str] = mapped_column(CenterPlan, default="FREE")
    plan_paid_until: Mapped[datetime | None] = mapped_column(
        "planPaidUntil", DateTime(timezone=True)
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        "archivedAt", DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class CenterPlanCheckout(Base):
    """Pending/active Paystack checkout for center Free→Pro/Team upgrades."""

    __tablename__ = "CenterPlanCheckout"
    __table_args__ = (
        Index("CenterPlanCheckout_centerId_idx", "centerId"),
        Index("CenterPlanCheckout_userId_status_idx", "userId", "status"),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    plan: Mapped[str] = mapped_column(CenterPlan)
    period: Mapped[str] = mapped_column(BillingPeriod, default="YEARLY")
    reference: Mapped[str] = mapped_column(String, unique=True)
    amount_kobo: Mapped[int] = mapped_column("amountKobo", Integer)
    currency: Mapped[str] = mapped_column(String, default="NGN")
    status: Mapped[str] = mapped_column(CenterCheckoutStatus, default="PENDING")
    channel: Mapped[str | None] = mapped_column(String)
    paid_at: Mapped[datetime | None] = mapped_column("paidAt", DateTime(timezone=True))
    starts_at: Mapped[datetime | None] = mapped_column(
        "startsAt", DateTime(timezone=True)
    )
    ends_at: Mapped[datetime | None] = mapped_column("endsAt", DateTime(timezone=True))
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class CenterAbuseReport(Base):
    """X-7: student reports of tutors / invites / recommendations (admin queue)."""

    __tablename__ = "CenterAbuseReport"
    __table_args__ = (
        Index("CenterAbuseReport_status_createdAt_idx", "status", "createdAt"),
        Index("CenterAbuseReport_kind_idx", "kind"),
    )

    id: Mapped[str] = id_column()
    reporter_id: Mapped[str] = mapped_column(
        "reporterId", ForeignKey("User.id", ondelete="CASCADE")
    )
    center_id: Mapped[str | None] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(CenterAbuseReportKind)
    target_id: Mapped[str] = mapped_column("targetId", String)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(CenterAbuseReportStatus, default="OPEN")
    resolved_at: Mapped[datetime | None] = mapped_column(
        "resolvedAt", DateTime(timezone=True)
    )
    resolved_by_id: Mapped[str | None] = mapped_column(
        "resolvedById", ForeignKey("Admin.id", ondelete="SET NULL")
    )
    resolution_note: Mapped[str | None] = mapped_column("resolutionNote", Text)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class CenterMembership(Base):
    __tablename__ = "CenterMembership"
    __table_args__ = (
        UniqueConstraint("centerId", "userId", name="CenterMembership_center_user"),
        Index("CenterMembership_userId_idx", "userId"),
        Index("CenterMembership_center_status_idx", "centerId", "status"),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(CenterMembershipKind)
    status: Mapped[str] = mapped_column(CenterMembershipStatus, default="INVITED")
    consent: Mapped[dict | None] = mapped_column(JSONB)
    joined_at: Mapped[datetime | None] = mapped_column(
        "joinedAt", DateTime(timezone=True)
    )
    left_at: Mapped[datetime | None] = mapped_column("leftAt", DateTime(timezone=True))
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class CenterGroup(Base):
    __tablename__ = "CenterGroup"
    __table_args__ = (
        UniqueConstraint("centerId", "name", name="CenterGroup_center_name"),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class CenterGroupMember(Base):
    __tablename__ = "CenterGroupMember"
    __table_args__ = (
        UniqueConstraint(
            "groupId", "membershipId", name="CenterGroupMember_group_membership"
        ),
    )

    id: Mapped[str] = id_column()
    group_id: Mapped[str] = mapped_column(
        "groupId", ForeignKey("CenterGroup.id", ondelete="CASCADE")
    )
    membership_id: Mapped[str] = mapped_column(
        "membershipId", ForeignKey("CenterMembership.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class CenterInvite(Base):
    __tablename__ = "CenterInvite"
    __table_args__ = (
        UniqueConstraint("code", name="CenterInvite_code"),
        Index("CenterInvite_centerId_idx", "centerId"),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    created_by_id: Mapped[str] = mapped_column(
        "createdById", ForeignKey("User.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(CenterInviteKind, default="STUDENT")
    code: Mapped[str] = mapped_column(String)
    token: Mapped[str] = mapped_column(String, unique=True)
    bound_email: Mapped[str | None] = mapped_column("boundEmail", String)
    bound_phone: Mapped[str | None] = mapped_column("boundPhone", String)
    use_limit: Mapped[int | None] = mapped_column("useLimit", Integer)
    use_count: Mapped[int] = mapped_column("useCount", Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(
        "expiresAt", DateTime(timezone=True)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        "revokedAt", DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class Assignment(Base):
    __tablename__ = "Assignment"
    __table_args__ = (
        Index("Assignment_centerId_status_idx", "centerId", "status"),
        Index("Assignment_centerId_createdAt_idx", "centerId", "createdAt"),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    created_by_id: Mapped[str] = mapped_column(
        "createdById", ForeignKey("User.id", ondelete="CASCADE")
    )
    assessment_id: Mapped[str | None] = mapped_column(
        "assessmentId", ForeignKey("Assessment.id")
    )
    title: Mapped[str] = mapped_column(String)
    task_type: Mapped[str] = mapped_column("taskType", AssignmentTaskType)
    status: Mapped[str] = mapped_column(AssignmentLifecycle, default="DRAFT")
    settings: Mapped[dict | None] = mapped_column(JSONB)
    opens_at: Mapped[datetime | None] = mapped_column(
        "opensAt", DateTime(timezone=True)
    )
    due_at: Mapped[datetime | None] = mapped_column("dueAt", DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(
        "closedAt", DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class AssignmentTarget(Base):
    __tablename__ = "AssignmentTarget"
    __table_args__ = (
        Index("AssignmentTarget_assignmentId_idx", "assignmentId"),
        UniqueConstraint(
            "assignmentId",
            "kind",
            "targetId",
            name="AssignmentTarget_assignment_kind_target",
        ),
    )

    id: Mapped[str] = id_column()
    assignment_id: Mapped[str] = mapped_column(
        "assignmentId", ForeignKey("Assignment.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(AssignmentTargetKind)
    # Null when kind=CENTER (whole roster); otherwise membership or group id.
    target_id: Mapped[str | None] = mapped_column("targetId", String)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class Recommendation(Base):
    __tablename__ = "Recommendation"
    __table_args__ = (
        Index("Recommendation_studentId_idx", "studentId"),
        Index("Recommendation_centerId_idx", "centerId"),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    assignment_id: Mapped[str | None] = mapped_column(
        "assignmentId", ForeignKey("Assignment.id", ondelete="SET NULL")
    )
    author_id: Mapped[str] = mapped_column(
        "authorId", ForeignKey("User.id", ondelete="CASCADE")
    )
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    body: Mapped[str] = mapped_column(Text)
    # Optional: topicIds, lessonId, practiceSet, assignmentId attachments (C-1).
    attachments: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(RecommendationStatus, default="DELIVERED")
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class TutorAccessAudit(Base):
    __tablename__ = "TutorAccessAudit"
    __table_args__ = (
        Index("TutorAccessAudit_centerId_createdAt_idx", "centerId", "createdAt"),
        Index("TutorAccessAudit_studentId_idx", "studentId"),
        Index("TutorAccessAudit_actorId_idx", "actorId"),
    )

    id: Mapped[str] = id_column()
    actor_id: Mapped[str] = mapped_column(
        "actorId", ForeignKey("User.id", ondelete="CASCADE")
    )
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    student_id: Mapped[str | None] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String)
    resource: Mapped[str] = mapped_column(String)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class CenterTokenPool(Base):
    """Per-center Astra token balance. Facilitators draw from the same pool."""

    __tablename__ = "CenterTokenPool"

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId",
        ForeignKey("Center.id", ondelete="CASCADE"),
        unique=True,
    )
    balance: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class CenterTokenLedger(Base):
    """Append-only ledger for token purchases, spends, starter credit, wallet (M7)."""

    __tablename__ = "CenterTokenLedger"
    __table_args__ = (
        Index("CenterTokenLedger_centerId_createdAt_idx", "centerId", "createdAt"),
        Index("CenterTokenLedger_idempotencyKey_idx", "idempotencyKey", unique=True),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    actor_id: Mapped[str | None] = mapped_column(
        "actorId", ForeignKey("User.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(CenterTokenLedgerKind)
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column("balanceAfter", Integer)
    pack_code: Mapped[str | None] = mapped_column("packCode", String)
    reference: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column("idempotencyKey", String)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class CenterTokenCheckout(Base):
    """Pending/active Paystack one-off checkout for AI token packs."""

    __tablename__ = "CenterTokenCheckout"
    __table_args__ = (
        Index("CenterTokenCheckout_centerId_idx", "centerId"),
        Index("CenterTokenCheckout_userId_status_idx", "userId", "status"),
    )

    id: Mapped[str] = id_column()
    center_id: Mapped[str] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="CASCADE")
    )
    pack_code: Mapped[str] = mapped_column("packCode", String)
    tokens: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String, unique=True)
    amount_kobo: Mapped[int] = mapped_column("amountKobo", Integer)
    currency: Mapped[str] = mapped_column(String, default="NGN")
    status: Mapped[str] = mapped_column(CenterCheckoutStatus, default="PENDING")
    channel: Mapped[str | None] = mapped_column(String)
    paid_at: Mapped[datetime | None] = mapped_column("paidAt", DateTime(timezone=True))
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class TutorWallet(Base):
    """Owner referral wallet (M7). One balance per tutor owner."""

    __tablename__ = "TutorWallet"

    id: Mapped[str] = id_column()
    owner_id: Mapped[str] = mapped_column(
        "ownerId",
        ForeignKey("User.id", ondelete="CASCADE"),
        unique=True,
    )
    balance_kobo: Mapped[int] = mapped_column("balanceKobo", Integer, default=0)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)


class TutorWalletLedger(Base):
    """Append-only owner wallet ledger (commission, spend, withdraw holds)."""

    __tablename__ = "TutorWalletLedger"
    __table_args__ = (
        Index("TutorWalletLedger_ownerId_createdAt_idx", "ownerId", "createdAt"),
        Index("TutorWalletLedger_idempotencyKey_idx", "idempotencyKey", unique=True),
        Index("TutorWalletLedger_subscriptionId_idx", "subscriptionId"),
    )

    id: Mapped[str] = id_column()
    owner_id: Mapped[str] = mapped_column(
        "ownerId", ForeignKey("User.id", ondelete="CASCADE")
    )
    wallet_id: Mapped[str] = mapped_column(
        "walletId", ForeignKey("TutorWallet.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(TutorWalletLedgerKind)
    delta_kobo: Mapped[int] = mapped_column("deltaKobo", Integer)
    balance_after_kobo: Mapped[int] = mapped_column("balanceAfterKobo", Integer)
    center_id: Mapped[str | None] = mapped_column(
        "centerId", ForeignKey("Center.id", ondelete="SET NULL")
    )
    student_id: Mapped[str | None] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="SET NULL")
    )
    subscription_id: Mapped[str | None] = mapped_column(
        "subscriptionId", ForeignKey("Subscription.id", ondelete="SET NULL")
    )
    reference: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column("idempotencyKey", String)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = timestamp_column("createdAt")


class TutorWithdrawRequest(Base):
    """D11: withdraw request + ops fulfilment (not instant payout)."""

    __tablename__ = "TutorWithdrawRequest"
    __table_args__ = (
        Index("TutorWithdrawRequest_ownerId_createdAt_idx", "ownerId", "createdAt"),
        Index("TutorWithdrawRequest_status_createdAt_idx", "status", "createdAt"),
    )

    id: Mapped[str] = id_column()
    owner_id: Mapped[str] = mapped_column(
        "ownerId", ForeignKey("User.id", ondelete="CASCADE")
    )
    wallet_id: Mapped[str] = mapped_column(
        "walletId", ForeignKey("TutorWallet.id", ondelete="CASCADE")
    )
    amount_kobo: Mapped[int] = mapped_column("amountKobo", Integer)
    status: Mapped[str] = mapped_column(TutorWithdrawStatus, default="PENDING")
    hold_ledger_id: Mapped[str | None] = mapped_column(
        "holdLedgerId", ForeignKey("TutorWalletLedger.id", ondelete="SET NULL")
    )
    payout_details: Mapped[dict[str, Any] | None] = mapped_column(
        "payoutDetails", JSONB
    )
    note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(
        "resolvedAt", DateTime(timezone=True)
    )
    resolved_by_id: Mapped[str | None] = mapped_column(
        "resolvedById", ForeignKey("Admin.id", ondelete="SET NULL")
    )
    resolution_note: Mapped[str | None] = mapped_column("resolutionNote", Text)
    created_at: Mapped[datetime] = timestamp_column("createdAt")
    updated_at: Mapped[datetime] = timestamp_column("updatedAt", onupdate=True)
