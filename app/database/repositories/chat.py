"""Repositories for tutor chat and RAG index jobs."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.chat import ChatFeedback, ChatMessage, ChatThread, RagIndexJob
from app.database.repositories.base import BaseRepository


class ChatThreadRepository(BaseRepository[ChatThread]):
    model = ChatThread

    async def for_user(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        include_archived: bool = False,
        only_started: bool = False,
    ) -> list[ChatThread]:
        filters = [ChatThread.user_id == user_id]
        if only_started:
            # A thread only counts once the student has sent a message.
            filters.append(
                exists().where(
                    ChatMessage.thread_id == ChatThread.id,
                    ChatMessage.role == "user",
                )
            )
        if not include_archived:
            filters.append(ChatThread.archived.is_(False))
        return await self.list_where(
            session, *filters, order_by=(ChatThread.updated_at.desc(),)
        )

    async def for_user_question(
        self,
        session: AsyncSession,
        user_id: str,
        question_id: str,
    ) -> ChatThread | None:
        """Return the most recent non-archived thread for this user + question."""
        return await self.first(
            session,
            ChatThread.user_id == user_id,
            ChatThread.question_id == question_id,
            ChatThread.archived.is_(False),
        )


class ChatMessageRepository(BaseRepository[ChatMessage]):
    model = ChatMessage

    async def for_thread(
        self, session: AsyncSession, thread_id: str
    ) -> list[ChatMessage]:
        return await self.list_where(
            session,
            ChatMessage.thread_id == thread_id,
            order_by=(ChatMessage.created_at,),
        )


class RagIndexJobRepository(BaseRepository[RagIndexJob]):
    model = RagIndexJob

    async def recent(self, session: AsyncSession, limit: int = 50) -> list[RagIndexJob]:
        return list(
            (
                await session.scalars(
                    select(RagIndexJob)
                    .order_by(RagIndexJob.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )


class ChatFeedbackRepository(BaseRepository[ChatFeedback]):
    model = ChatFeedback


chat_threads_repository = ChatThreadRepository()
chat_messages_repository = ChatMessageRepository()
rag_index_jobs_repository = RagIndexJobRepository()
chat_feedback_repository = ChatFeedbackRepository()
