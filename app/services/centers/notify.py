"""In-app + push notifications for center roster events (N-1 / N-2 subset)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.notification import (
    in_app_notifications_repository,
    push_subscriptions_repository,
)
from app.services.push import send_notification


async def notify_user(
    session: AsyncSession,
    *,
    user_id: str,
    type: str,
    title: str,
    body: str,
    url: str,
    source_key: str,
) -> None:
    await in_app_notifications_repository.upsert_one(
        session,
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        url=url,
        source_key=source_key,
    )
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "url": url,
        "type": type,
    }
    subscriptions = await push_subscriptions_repository.for_user(session, user_id)
    for subscription in subscriptions:
        outcome = send_notification(subscription, payload)
        if outcome == "gone":
            await push_subscriptions_repository.remove(session, subscription)


async def notify_invite_accepted(
    session: AsyncSession,
    *,
    owner_id: str,
    student_name: str,
    center_name: str,
    center_id: str,
    membership_id: str,
) -> None:
    await notify_user(
        session,
        user_id=owner_id,
        type="CENTER_INVITE_ACCEPTED",
        title="Student joined your center",
        body=f"{student_name} accepted your invite to {center_name}.",
        url=f"/center/roster?centerId={center_id}",
        source_key=f"center-invite-accepted:{membership_id}",
    )


async def notify_student_removed(
    session: AsyncSession,
    *,
    student_id: str,
    center_name: str,
    center_id: str,
    membership_id: str,
) -> None:
    await notify_user(
        session,
        user_id=student_id,
        type="CENTER_REMOVED",
        title="Removed from a center",
        body=f"You were removed from {center_name}.",
        url="/settings/my-centers",
        source_key=f"center-removed:{membership_id}:{center_id}",
    )


async def notify_new_assignment(
    session: AsyncSession,
    *,
    student_id: str,
    center_name: str,
    assignment_id: str,
    title: str,
    due_at: Any | None = None,
) -> None:
    due_bit = ""
    if due_at is not None:
        due_bit = f" Due {due_at.isoformat()[:16]}."
    await notify_user(
        session,
        user_id=student_id,
        type="CENTER_ASSIGNMENT_NEW",
        title="New assignment",
        body=f"{center_name}: {title}.{due_bit}",
        url=f"/assigned/{assignment_id}",
        source_key=f"center-assignment-new:{assignment_id}:{student_id}",
    )


async def notify_assignment_closed(
    session: AsyncSession,
    *,
    student_id: str,
    center_name: str,
    assignment_id: str,
    title: str,
) -> None:
    await notify_user(
        session,
        user_id=student_id,
        type="CENTER_ASSIGNMENT_CLOSED",
        title="Assignment closed",
        body=f"{center_name}: {title} was closed by your tutor.",
        url="/assigned",
        source_key=f"center-assignment-closed:{assignment_id}:{student_id}",
    )


async def notify_recommendation(
    session: AsyncSession,
    *,
    student_id: str,
    center_name: str,
    recommendation_id: str,
    preview: str,
) -> None:
    await notify_user(
        session,
        user_id=student_id,
        type="CENTER_RECOMMENDATION",
        title="New recommendation from your tutor",
        body=f"{center_name}: {preview}",
        url="/recommendations",
        source_key=f"center-recommendation:{recommendation_id}",
    )


async def notify_wallet_credit(
    session: AsyncSession,
    *,
    owner_id: str,
    amount_kobo: int,
    center_id: str,
    ledger_id: str,
) -> None:
    naira = amount_kobo / 100
    await notify_user(
        session,
        user_id=owner_id,
        type="CENTER_WALLET_CREDIT",
        title="Referral wallet credited",
        body=f"₦{naira:,.0f} was added to your referral wallet.",
        url=f"/center/billing?centerId={center_id}&tab=wallet",
        source_key=f"center-wallet-credit:{ledger_id}",
    )


async def notify_withdraw_status(
    session: AsyncSession,
    *,
    owner_id: str,
    request_id: str,
    status: str,
    amount_kobo: int,
) -> None:
    naira = amount_kobo / 100
    status_l = status.lower()
    if status_l == "pending":
        title = "Withdrawal requested"
        body = f"Your ₦{naira:,.0f} withdrawal request is pending review."
    elif status_l == "fulfilled":
        title = "Withdrawal fulfilled"
        body = f"Your ₦{naira:,.0f} withdrawal has been paid out."
    elif status_l == "rejected":
        title = "Withdrawal rejected"
        body = (
            f"Your ₦{naira:,.0f} withdrawal request was rejected. Funds were returned."
        )
    else:
        title = "Withdrawal update"
        body = f"Your ₦{naira:,.0f} withdrawal is now {status_l}."
    await notify_user(
        session,
        user_id=owner_id,
        type="CENTER_WALLET_WITHDRAW",
        title=title,
        body=body,
        url="/center/billing?tab=wallet",
        source_key=f"center-wallet-withdraw:{request_id}:{status_l}",
    )


async def notify_assignment_due(
    session: AsyncSession,
    *,
    student_id: str,
    center_name: str,
    assignment_id: str,
    title: str,
    due_at: Any,
    kind: str,
) -> bool:
    """Return True when a new reminder row was inserted (deduped by source_key)."""
    day = due_at.date().isoformat() if hasattr(due_at, "date") else str(due_at)[:10]
    if kind == "due_today":
        notif_type = "CENTER_ASSIGNMENT_DUE_TODAY"
        headline = "Assignment due today"
    else:
        notif_type = "CENTER_ASSIGNMENT_DUE_SOON"
        headline = "Assignment due soon"
    return await in_app_notifications_repository.upsert_one(
        session,
        user_id=student_id,
        type=notif_type,
        title=headline,
        body=f"{center_name}: {title}",
        url=f"/assigned/{assignment_id}",
        source_key=f"center-assignment-{kind}:{assignment_id}:{student_id}:{day}",
    )
