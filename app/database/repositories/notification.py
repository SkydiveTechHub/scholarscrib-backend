"""Repositories for push, announcements, and reminder logs."""

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import (
    Announcement,
    AnnouncementDelivery,
    AnnouncementDismissal,
    InAppNotification,
    NotificationPreference,
    PushSubscription,
    ReminderLog,
    User,
)
from app.database.repositories.base import BaseRepository
from app.database.repositories.identity import user_audience_filters


class NotificationPreferenceRepository(BaseRepository[NotificationPreference]):
    model = NotificationPreference


class PushSubscriptionRepository(BaseRepository[PushSubscription]):
    model = PushSubscription

    async def by_endpoint(
        self, session: AsyncSession, endpoint: str
    ) -> PushSubscription | None:
        return await self.first(session, PushSubscription.endpoint == endpoint)

    async def for_user(
        self, session: AsyncSession, user_id: str
    ) -> list[PushSubscription]:
        return await self.list_where(session, PushSubscription.user_id == user_id)

    async def delete_for_devices(
        self, session: AsyncSession, device_ids: list[str]
    ) -> None:
        if device_ids:
            await self.delete_where(session, PushSubscription.device_id.in_(device_ids))

    async def subscribe(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        endpoint: str,
        p256dh: str | None,
        auth: str | None,
        device_id: str | None,
    ) -> PushSubscription:
        row = await self.by_endpoint(session, endpoint)
        if row is None:
            row = PushSubscription(
                id=cuid(),
                user_id=user_id,
                endpoint=endpoint,
                p256dh=p256dh if p256dh is not None else "",
                auth=auth if auth is not None else "",
                device_id=device_id,
            )
            await self.add(session, row)
            return row
        row.user_id = user_id
        if p256dh is not None:
            row.p256dh = p256dh
        if auth is not None:
            row.auth = auth
        row.device_id = device_id
        return row

    async def unsubscribe(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        endpoint: str,
    ) -> None:
        row = await self.first(
            session,
            PushSubscription.endpoint == endpoint,
            PushSubscription.user_id == user_id,
        )
        await self.remove(session, row)

    async def for_audience(
        self, session: AsyncSession, audience: dict[str, Any]
    ) -> list[PushSubscription]:
        statement = (
            select(PushSubscription)
            .join(User, User.id == PushSubscription.user_id)
            .outerjoin(
                NotificationPreference,
                NotificationPreference.user_id == User.id,
            )
            .where(
                User.role == "STUDENT",
                User.is_active.is_(True),
                or_(
                    NotificationPreference.user_id.is_(None),
                    NotificationPreference.announcements.is_(True),
                ),
                *user_audience_filters(audience),
            )
        )
        return await self.many(session, statement)


class AnnouncementRepository(BaseRepository[Announcement]):
    model = Announcement

    async def recent(
        self, session: AsyncSession, *, limit: int = 50
    ) -> list[Announcement]:
        return await self.list_where(
            session,
            order_by=(Announcement.created_at.desc(),),
            limit=limit,
        )

    async def active_not_cancelled(
        self, session: AsyncSession, *, now: datetime
    ) -> list[Announcement]:
        return await self.list_where(
            session,
            Announcement.status != "CANCELLED",
            Announcement.expires_at > now,
            order_by=(Announcement.created_at.desc(),),
        )


class AnnouncementDeliveryRepository(BaseRepository[AnnouncementDelivery]):
    model = AnnouncementDelivery

    async def claim_pending(
        self,
        session: AsyncSession,
        stale_before: datetime,
        *,
        limit: int = 100,
    ) -> list[AnnouncementDelivery]:
        """Claim PENDING rows with FOR UPDATE SKIP LOCKED."""
        statement = (
            select(AnnouncementDelivery)
            .where(
                AnnouncementDelivery.status == "PENDING",
                (AnnouncementDelivery.claimed_at.is_(None))
                | (AnnouncementDelivery.claimed_at < stale_before),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return await self.many(session, statement)

    async def pending_for(
        self, session: AsyncSession, announcement_id: str
    ) -> list[AnnouncementDelivery]:
        return await self.list_where(
            session,
            AnnouncementDelivery.announcement_id == announcement_id,
            AnnouncementDelivery.status == "PENDING",
        )


class AnnouncementDismissalRepository(BaseRepository[AnnouncementDismissal]):
    model = AnnouncementDismissal

    async def ids_for_user(self, session: AsyncSession, user_id: str) -> set[str]:
        rows = await session.scalars(
            select(AnnouncementDismissal.announcement_id).where(
                AnnouncementDismissal.user_id == user_id
            )
        )
        return set(rows.all())


class ReminderLogRepository(BaseRepository[ReminderLog]):
    model = ReminderLog

    async def try_claim(
        self,
        session: AsyncSession,
        user_id: str,
        kind: str,
        day_key: str,
    ) -> bool:
        """Insert a reminder log; ON CONFLICT DO NOTHING. True if claimed."""
        stmt = (
            insert(ReminderLog)
            .values(user_id=user_id, kind=kind, day_key=day_key)
            .on_conflict_do_nothing(
                index_elements=[
                    ReminderLog.user_id,
                    ReminderLog.kind,
                    ReminderLog.day_key,
                ]
            )
            .returning(ReminderLog.user_id)
        )
        result = await self.rows(session, stmt)
        return result.first() is not None

    async def unnotified_students(
        self,
        session: AsyncSession,
        kind: str,
        day_key: str,
        *,
        limit: int,
    ) -> list[User]:
        """Active students with the kind preference on, not yet claimed today.

        Does not require a push subscription — in-app inbox is written regardless.
        """
        flag = (
            NotificationPreference.study_reminders
            if kind == "morning"
            else NotificationPreference.streak_reminders
        )
        statement = (
            select(User)
            .outerjoin(
                NotificationPreference, NotificationPreference.user_id == User.id
            )
            .where(
                User.role == "STUDENT",
                User.is_active.is_(True),
                (NotificationPreference.user_id.is_(None)) | (flag.is_(True)),
                User.id.not_in(
                    select(ReminderLog.user_id).where(
                        ReminderLog.kind == kind, ReminderLog.day_key == day_key
                    )
                ),
            )
            .limit(limit)
        )
        return list((await session.scalars(statement)).all())


class InAppNotificationRepository(BaseRepository[InAppNotification]):
    model = InAppNotification

    async def upsert_many(
        self,
        session: AsyncSession,
        rows: list[dict[str, Any]],
    ) -> int:
        """Insert notifications; skip duplicates on (userId, sourceKey)."""
        if not rows:
            return 0
        values = [
            {
                "id": cuid(),
                "user_id": row["user_id"],
                "type": row["type"],
                "title": row["title"],
                "body": row["body"],
                "url": row.get("url"),
                "source_key": row["source_key"],
            }
            for row in rows
        ]
        stmt = (
            insert(InAppNotification)
            .values(values)
            .on_conflict_do_nothing(constraint="InAppNotification_userId_sourceKey_key")
            .returning(InAppNotification.id)
        )
        result = await self.rows(session, stmt)
        return len(result.all())

    async def upsert_one(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        type: str,
        title: str,
        body: str,
        url: str | None,
        source_key: str,
    ) -> bool:
        """True if a new row was inserted."""
        inserted = await self.upsert_many(
            session,
            [
                {
                    "user_id": user_id,
                    "type": type,
                    "title": title,
                    "body": body,
                    "url": url,
                    "source_key": source_key,
                }
            ],
        )
        return inserted > 0

    async def list_for_user(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        limit: int,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
    ) -> list[InAppNotification]:
        statement = select(InAppNotification).where(
            InAppNotification.user_id == user_id
        )
        if cursor_created_at is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    InAppNotification.created_at < cursor_created_at,
                    and_(
                        InAppNotification.created_at == cursor_created_at,
                        InAppNotification.id < cursor_id,
                    ),
                )
            )
        statement = statement.order_by(
            InAppNotification.created_at.desc(),
            InAppNotification.id.desc(),
        ).limit(limit)
        return await self.many(session, statement)

    async def unread_count(self, session: AsyncSession, user_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(InAppNotification)
            .where(
                InAppNotification.user_id == user_id,
                InAppNotification.read_at.is_(None),
            )
        )
        result = await session.scalar(statement)
        return int(result or 0)

    async def mark_read(
        self, session: AsyncSession, user_id: str, notification_id: str
    ) -> bool:
        row = await self.first(
            session,
            InAppNotification.id == notification_id,
            InAppNotification.user_id == user_id,
        )
        if row is None:
            return False
        if row.read_at is None:
            row.read_at = utcnow()
        return True

    async def mark_all_read(self, session: AsyncSession, user_id: str) -> int:
        stmt = (
            update(InAppNotification)
            .where(
                InAppNotification.user_id == user_id,
                InAppNotification.read_at.is_(None),
            )
            .values(read_at=utcnow())
            .returning(InAppNotification.id)
        )
        result = await session.execute(stmt)
        return len(result.all())


preferences_repository = NotificationPreferenceRepository()
push_subscriptions_repository = PushSubscriptionRepository()
announcements_repository = AnnouncementRepository()
announcement_deliveries_repository = AnnouncementDeliveryRepository()
announcement_dismissals_repository = AnnouncementDismissalRepository()
reminder_logs_repository = ReminderLogRepository()
in_app_notifications_repository = InAppNotificationRepository()
