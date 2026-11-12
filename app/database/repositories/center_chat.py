"""Repositories for center-scoped Tutor Astra chat."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.center_chat import CenterChatMessage, CenterChatThread
from app.database.repositories.base import BaseRepository


class CenterChatThreadRepository(BaseRepository[CenterChatThread]):
    model = CenterChatThread

    async def for_tutor_center(
        self, session: AsyncSession, center_id: str, tutor_id: str
    ) -> list[CenterChatThread]:
        return await self.list_where(
            session,
            CenterChatThread.center_id == center_id,
            CenterChatThread.tutor_id == tutor_id,
            CenterChatThread.archived.is_(False),
            order_by=(CenterChatThread.updated_at.desc(),),
        )

    async def owned(
        self,
        session: AsyncSession,
        center_id: str,
        tutor_id: str,
        thread_id: str,
    ) -> CenterChatThread | None:
        thread = await self.by_id(session, thread_id)
        if (
            thread is None
            or thread.center_id != center_id
            or thread.tutor_id != tutor_id
            or thread.archived
        ):
            return None
        return thread


class CenterChatMessageRepository(BaseRepository[CenterChatMessage]):
    model = CenterChatMessage

    async def for_thread(
        self, session: AsyncSession, thread_id: str
    ) -> list[CenterChatMessage]:
        return await self.list_where(
            session,
            CenterChatMessage.thread_id == thread_id,
            order_by=(CenterChatMessage.created_at, CenterChatMessage.id),
        )


center_chat_threads_repository = CenterChatThreadRepository()
center_chat_messages_repository = CenterChatMessageRepository()
