"""Student in-app notification inbox."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.models import InAppNotification
from app.database.repositories.notification import in_app_notifications_repository


def _encode_cursor(created_at: datetime, notification_id: str) -> str:
    return f"{created_at.isoformat()}|{notification_id}"


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        stamp, notification_id = cursor.split("|", 1)
        return datetime.fromisoformat(stamp), notification_id
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "Invalid cursor") from exc


class ListNotificationsService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        limit: int = 30,
        cursor: str | None = None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.limit = min(max(limit, 1), 50)
        self.cursor = cursor

    async def process(self) -> dict[str, Any]:
        cursor_created_at, cursor_id = _decode_cursor(self.cursor)
        rows = await in_app_notifications_repository.list_for_user(
            self.session,
            self.user_id,
            limit=self.limit + 1,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
        has_more = len(rows) > self.limit
        page = rows[: self.limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
        return {
            "notifications": [_serialize(row) for row in page],
            "nextCursor": next_cursor,
        }


class UnreadCountService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict[str, Any]:
        count = await in_app_notifications_repository.unread_count(
            self.session, self.user_id
        )
        return {"count": count}


class MarkNotificationReadService:
    def __init__(
        self, session: AsyncSession, user_id: str, notification_id: str
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.notification_id = notification_id

    async def process(self) -> dict[str, Any]:
        ok = await in_app_notifications_repository.mark_read(
            self.session, self.user_id, self.notification_id
        )
        if not ok:
            raise ApiError(404, "Notification not found")
        return {"ok": True}


class MarkAllNotificationsReadService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict[str, Any]:
        await in_app_notifications_repository.mark_all_read(self.session, self.user_id)
        return {"ok": True}


def _serialize(row: InAppNotification) -> dict[str, Any]:
    return {
        "id": row.id,
        "type": row.type,
        "title": row.title,
        "body": row.body,
        "url": row.url,
        "readAt": row.read_at.isoformat() if row.read_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }
