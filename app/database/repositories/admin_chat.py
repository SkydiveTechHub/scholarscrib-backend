"""Repositories for admin Astra chat."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.admin_chat import AdminChatMessage, AdminChatThread
from app.database.repositories.base import BaseRepository


class AdminChatThreadRepository(BaseRepository[AdminChatThread]):
    model = AdminChatThread

    async def for_admin(
        self, session: AsyncSession, admin_id: str
    ) -> list[AdminChatThread]:
        return await self.list_where(
            session,
            AdminChatThread.admin_id == admin_id,
            AdminChatThread.archived.is_(False),
            order_by=(AdminChatThread.updated_at.desc(),),
        )

    async def owned(
        self, session: AsyncSession, admin_id: str, thread_id: str
    ) -> AdminChatThread | None:
        thread = await self.by_id(session, thread_id)
        if thread is None or thread.admin_id != admin_id or thread.archived:
            return None
        return thread


class AdminChatMessageRepository(BaseRepository[AdminChatMessage]):
    model = AdminChatMessage

    async def for_thread(
        self, session: AsyncSession, thread_id: str
    ) -> list[AdminChatMessage]:
        return await self.list_where(
            session,
            AdminChatMessage.thread_id == thread_id,
            order_by=(AdminChatMessage.created_at, AdminChatMessage.id),
        )


admin_chat_threads_repository = AdminChatThreadRepository()
admin_chat_messages_repository = AdminChatMessageRepository()
