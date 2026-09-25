"""Announcement drain and study reminders.

Web Push is sent when pywebpush is installed. Without it, a configured
environment still claims rows and records a retry instead of marking them sent.
"""

import json
import time
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutil import lagos_day_key, previous_day_key, utcnow
from app.database.models import (
    Announcement,
    AnnouncementDelivery,
    AssessmentAttempt,
    FlashcardReview,
    PushSubscription,
    StudyPlan,
    StudyPlanItem,
)
from app.database.repositories.assessment import attempts_repository
from app.database.repositories.flashcard import reviews_repository
from app.database.repositories.notification import (
    announcement_deliveries_repository,
    announcements_repository,
    push_subscriptions_repository,
    reminder_logs_repository,
)
from app.database.repositories.planner import plan_items_repository, plans_repository
from app.services.auth.rules import current_streak

BUDGET_SECONDS = 40
PAGE_SIZE = 200


def send_notification(subscription: PushSubscription, payload: dict) -> str:
    """Return sent, gone, invalid, or retry."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return "retry"
    try:
        subject = settings.vapid_subject
        private_key = settings.vapid_private_key
        if not subject or not private_key:
            return "retry"
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": subscription.auth,
                },
            },
            data=json.dumps(payload),
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            ttl=12 * 60 * 60,
            timeout=10,
        )
        return "sent"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {404, 410}:
            return "gone"
        if status in {400, 413}:
            return "invalid"
        return "retry"
    except Exception:
        return "retry"


class DrainPushService:
    def __init__(self, session: AsyncSession, deadline: float) -> None:
        self.session = session
        self.deadline = deadline

    async def process(self) -> dict:
        await self._expire_announcements()
        claimed = sent = failed = gone = retrying = 0
        stale = utcnow() - timedelta(minutes=5)
        rows = await announcement_deliveries_repository.claim_pending(
            self.session, stale, limit=100
        )
        for delivery in rows:
            if time.monotonic() >= self.deadline:
                break
            outcome = await self._deliver(delivery)
            if outcome is None:
                continue
            claimed += 1
            sent += outcome == "sent"
            failed += outcome == "invalid"
            gone += outcome == "gone"
            retrying += outcome == "retry"
        await self._complete_announcements()
        return {
            "claimed": claimed,
            "sent": sent,
            "failed": failed,
            "gone": gone,
            "retrying": retrying,
        }

    async def _expire_announcements(self) -> None:
        now = utcnow()
        expired = await announcements_repository.list_where(
            self.session,
            Announcement.expires_at <= now,
            Announcement.status.in_(("QUEUED", "SENDING")),
        )
        for row in expired:
            row.status = "SENT"
            row.completed_at = now

    async def _deliver(self, delivery) -> str | None:
        announcement = await announcements_repository.by_id(
            self.session, delivery.announcement_id
        )
        if announcement is None or announcement.status not in {
            "QUEUED",
            "SENDING",
        }:
            return None
        subscription = await push_subscriptions_repository.by_id(
            self.session, delivery.subscription_id
        )
        delivery.claimed_at = utcnow()
        if announcement.status == "QUEUED":
            announcement.status = "SENDING"
        if subscription is None:
            delivery.status = "GONE"
            return "gone"
        outcome = send_notification(
            subscription,
            {
                "title": announcement.title,
                "body": announcement.body,
                "url": announcement.url or "/dashboard",
                "tag": f"announcement-{announcement.id}",
            },
        )
        await self._apply(delivery, announcement, subscription, outcome)
        return outcome

    async def _apply(self, delivery, announcement, subscription, outcome: str) -> None:
        if outcome == "sent":
            delivery.status = "SENT"
            announcement.sent_count += 1
            return
        if outcome == "gone":
            delivery.status = "GONE"
            await push_subscriptions_repository.remove(self.session, subscription)
            return
        delivery.attempts += 1
        subscription.failure_count += 1
        if (
            outcome == "invalid"
            or delivery.attempts >= 3
            or subscription.failure_count >= 5
        ):
            delivery.status = "FAILED"
            announcement.failed_count += 1
            delivery.claimed_at = None
            if subscription.failure_count >= 5:
                await push_subscriptions_repository.remove(self.session, subscription)
            return
        delivery.status = "PENDING"
        delivery.claimed_at = None

    async def _complete_announcements(self) -> None:
        sending = await announcements_repository.list_where(
            self.session, Announcement.status == "SENDING"
        )
        for announcement in sending:
            pending = await announcement_deliveries_repository.first(
                self.session,
                AnnouncementDelivery.announcement_id == announcement.id,
                AnnouncementDelivery.status == "PENDING",
            )
            if pending is None:
                announcement.status = "SENT"
                announcement.completed_at = utcnow()


class SendRemindersService:
    def __init__(self, session: AsyncSession, kind: str, deadline: float) -> None:
        self.session = session
        self.kind = kind
        self.deadline = deadline

    async def process(self) -> dict:
        today = lagos_day_key()
        candidates = await reminder_logs_repository.unnotified_students(
            self.session, self.kind, today, limit=PAGE_SIZE
        )
        processed = notified = sent = skipped = 0
        done = True
        for user in candidates:
            if time.monotonic() >= self.deadline:
                done = False
                break
            inserted = await reminder_logs_repository.try_claim(
                self.session, user.id, self.kind, today
            )
            if not inserted:
                continue
            processed += 1
            subscriptions = await push_subscriptions_repository.for_user(
                self.session, user.id
            )
            message = await self._message(user.id, today)
            if message is None or not subscriptions:
                skipped += 1
                continue
            notified += 1
            for subscription in subscriptions:
                outcome = send_notification(subscription, message)
                if outcome == "sent":
                    sent += 1
                elif outcome == "gone":
                    await push_subscriptions_repository.remove(
                        self.session, subscription
                    )
        if len(candidates) == PAGE_SIZE:
            done = False
        return {
            "processed": processed,
            "notified": notified,
            "sent": sent,
            "skipped": skipped,
            "done": done,
        }

    async def _message(self, user_id: str, today: str) -> dict | None:
        if self.kind == "morning":
            return await self._morning_message(user_id, today)
        return await self._streak_message(user_id, today)

    async def _morning_message(self, user_id: str, today: str) -> dict | None:
        plan = await plans_repository.first(
            self.session,
            StudyPlan.student_id == user_id,
            StudyPlan.is_active.is_(True),
        )
        pending = 0
        if plan is not None:
            items = await plan_items_repository.list_where(
                self.session, StudyPlanItem.study_plan_id == plan.id
            )
            pending = sum(
                1
                for item in items
                if str(item.date)[:10] == today and item.status == "PENDING"
            )
        due = await reviews_repository.first(
            self.session,
            FlashcardReview.student_id == user_id,
            FlashcardReview.due_at <= utcnow(),
        )
        if pending == 0 and due is None:
            return None
        if due is None:
            body = f"{pending} plan items are waiting today."
        elif pending == 0:
            body = "You have flashcards due today."
        else:
            body = f"{pending} plan items and flashcards are waiting today."
        return {
            "title": "Study plan",
            "body": body,
            "url": "/dashboard",
            "tag": f"morning-{today}",
        }

    async def _streak_message(self, user_id: str, today: str) -> dict | None:
        rows = await attempts_repository.list_where(
            self.session,
            AssessmentAttempt.student_id == user_id,
            AssessmentAttempt.status == "COMPLETED",
            AssessmentAttempt.completed_at.is_not(None),
        )
        days = {
            lagos_day_key(moment) for moment in (r.completed_at for r in rows) if moment
        }
        if today in days:
            return None
        streak = current_streak(days, previous_day_key(today))
        if streak < 2:
            return None
        return {
            "title": "Keep your streak",
            "body": f"Keep your {streak}-day streak",
            "url": "/dashboard",
            "tag": f"streak-{today}",
        }
