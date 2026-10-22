"""Student-facing AI tutor chat services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi_pagination.ext.sqlalchemy import apaginate
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    TutorActionResumeIn,
    TutorFeedbackIn,
    TutorMessageIn,
    TutorThreadCreateIn,
)
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.db import async_session
from app.database.models.chat import ChatFeedback, ChatMessage, ChatThread
from app.database.repositories.chat import (
    chat_feedback_repository,
    chat_messages_repository,
    chat_threads_repository,
)
from app.database.repositories.curriculum import (
    lessons_repository,
    topics_repository,
)
from app.database.repositories.question import questions_repository
from app.services.rag.checkpointer import get_checkpointer
from app.services.rag.graph import TutorState, get_tutor_graph
from app.services.rag.llm import get_grader_model
from app.services.rag.message_text import visible_assistant_text
from app.services.rag.prompts import TITLE_PROMPT
from app.services.rag.store import get_memory_store
from app.services.rag.stream_control import (
    begin_stream,
    end_stream,
    request_stop,
    should_stop,
)
from app.services.rag.tools.context import (
    load_tool_context,
    reset_tool_context,
    set_tool_context,
)
from app.services.rag.tutor_limits import enforce_daily_message_limit
from app.services.rag.vectorstore import require_rag

_ANSWER_NODES = frozenset(
    {"generate_answer", "chitchat", "sql_agent", "action_agent", "action_outcome"}
)


def _thread_out(thread: ChatThread, messages: list[ChatMessage] | None = None) -> dict:
    payload: dict[str, Any] = {
        "id": thread.id,
        "title": thread.title,
        "subjectId": thread.subject_id,
        "topicId": thread.topic_id,
        "questionId": thread.question_id,
        "lessonId": thread.lesson_id,
        "archived": thread.archived,
        "createdAt": thread.created_at.isoformat() if thread.created_at else None,
        "updatedAt": thread.updated_at.isoformat() if thread.updated_at else None,
    }
    if messages is not None:
        payload["messages"] = [_message_out(m) for m in messages]
    return payload


def _message_out(message: ChatMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "groundedOn": message.grounded_on or [],
        "createdAt": message.created_at.isoformat() if message.created_at else None,
    }


async def _auto_title(text: str) -> str:
    try:
        model = get_grader_model()
        response = model.invoke(
            [{"role": "user", "content": TITLE_PROMPT.format(message=text[:500])}]
        )
        content = response.content
        title = content if isinstance(content, str) else str(content)
        cleaned = visible_assistant_text(title).strip().strip('"')[:80]
        return cleaned or (text or "New chat")[:60]
    except Exception:  # noqa: BLE001
        return (text or "New chat")[:60]


async def _ensure_thread_title(
    session: AsyncSession, thread: ChatThread, text: str
) -> None:
    """Replace the placeholder title and flush so list views see it immediately."""
    if thread.title and thread.title.strip() and thread.title.strip() != "New chat":
        return
    thread.title = await _auto_title(text)
    thread.updated_at = utcnow()
    await session.flush()


class LessonTutorService:
    """Start a lesson-scoped tutor thread (lesson-engine contract)."""

    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        lesson_id: str,
        body: TutorThreadCreateIn,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.lesson_id = lesson_id
        self.body = body

    async def process(self) -> dict:
        from sqlalchemy import select

        from app.database.models import Lesson, Subtopic, Topic

        row = (
            await self.session.execute(
                select(Lesson.id, Topic.id, Topic.subject_id)
                .join(Subtopic, Subtopic.id == Lesson.subtopic_id)
                .join(Topic, Topic.id == Subtopic.topic_id)
                .where(Lesson.id == self.lesson_id)
            )
        ).one_or_none()
        if row is None:
            raise ApiError(404, "Lesson not found")
        _, topic_id, subject_id = row
        payload = self.body.model_copy(
            update={
                "lessonId": self.lesson_id,
                "topicId": self.body.topicId or topic_id,
                "subjectId": self.body.subjectId or subject_id,
            }
        )
        return await CreateTutorThreadService(
            self.session, self.user_id, payload
        ).process()


class CreateTutorThreadService:
    def __init__(
        self, session: AsyncSession, user_id: str, body: TutorThreadCreateIn
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.body = body

    async def process(self) -> dict:
        require_rag()
        await self._validate_refs()
        title = self.body.title
        first = (self.body.firstMessage or "").strip()
        wrong = (self.body.wrongAnswerContext or "").strip()
        if first:
            enforce_daily_message_limit(self.user_id)
        if not title and first:
            title = await _auto_title(first)
        thread = ChatThread(
            id=cuid(),
            user_id=self.user_id,
            title=title or "New chat",
            subject_id=self.body.subjectId,
            topic_id=self.body.topicId,
            question_id=self.body.questionId,
            lesson_id=self.body.lessonId,
        )
        await chat_threads_repository.add(self.session, thread, flush=True)

        if wrong and self.body.questionId:
            system = ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="system",
                content=(
                    "The student answered a practice question incorrectly. "
                    f"Their answer was: {wrong}. Help them understand the mistake "
                    "without simply restating the answer key unless they ask."
                ),
            )
            await chat_messages_repository.add(self.session, system, flush=True)

        assistant_payload = None
        if first:
            user_msg = ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="user",
                content=first,
            )
            await chat_messages_repository.add(self.session, user_msg, flush=True)
            assistant_payload = await _run_graph_and_persist(
                self.session, thread, first
            )

        messages = await chat_messages_repository.for_thread(self.session, thread.id)
        out = _thread_out(thread, messages)
        if assistant_payload:
            out["assistantMessage"] = assistant_payload
        return out

    async def _validate_refs(self) -> None:
        if self.body.topicId:
            topic = await topics_repository.by_id(self.session, self.body.topicId)
            if topic is None:
                raise ApiError(400, "Unknown topic")
            if self.body.subjectId and topic.subject_id != self.body.subjectId:
                raise ApiError(400, "Topic does not belong to that subject")
        if self.body.questionId:
            question = await questions_repository.by_id(
                self.session, self.body.questionId
            )
            if question is None:
                raise ApiError(400, "Unknown question")
        if self.body.lessonId:
            lesson = await lessons_repository.by_id(self.session, self.body.lessonId)
            if lesson is None:
                raise ApiError(400, "Unknown lesson")


class ListTutorThreadsService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        require_rag()
        rows = await chat_threads_repository.for_user(
            self.session, self.user_id, only_started=True
        )
        return {"threads": [_thread_out(row) for row in rows]}


class GetTutorThreadService:
    def __init__(self, session: AsyncSession, user_id: str, thread_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.thread_id = thread_id

    async def process(self) -> dict:
        require_rag()
        thread = await _owned_thread(self.session, self.user_id, self.thread_id)
        messages = await chat_messages_repository.for_thread(self.session, thread.id)
        return _thread_out(thread, messages)


class ListTutorMessagesService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        thread_id: str,
        *,
        order_by: str = "asc",
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.thread_id = thread_id
        self.order_by = order_by if order_by in {"asc", "desc"} else "asc"

    async def process(self):
        require_rag()
        thread = await _owned_thread(self.session, self.user_id, self.thread_id)
        if self.order_by == "desc":
            ordering = (ChatMessage.created_at.desc(), ChatMessage.id.desc())
        else:
            ordering = (ChatMessage.created_at.asc(), ChatMessage.id.asc())
        query = (
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread.id)
            .order_by(*ordering)
        )
        return await apaginate(
            self.session,
            query,
            transformer=lambda rows: [_message_out(message) for message in rows],
        )


class DeleteTutorThreadService:
    def __init__(self, session: AsyncSession, user_id: str, thread_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.thread_id = thread_id

    async def process(self) -> dict:
        require_rag()
        thread = await _owned_thread(self.session, self.user_id, self.thread_id)
        thread.archived = True
        thread.updated_at = utcnow()
        await self.session.flush()
        return {"ok": True}


class PostTutorMessageService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        thread_id: str,
        body: TutorMessageIn,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.thread_id = thread_id
        self.body = body

    async def process(self) -> dict:
        require_rag()
        enforce_daily_message_limit(self.user_id)
        thread = await _owned_thread(self.session, self.user_id, self.thread_id)
        content = self.body.content.strip()
        if not content:
            raise ApiError(400, "content is required")
        user_msg = ChatMessage(
            id=cuid(),
            thread_id=thread.id,
            role="user",
            content=content,
        )
        await chat_messages_repository.add(self.session, user_msg, flush=True)
        await _ensure_thread_title(self.session, thread, content)
        assistant = await _run_graph_and_persist(self.session, thread, content)
        return {
            "userMessage": _message_out(user_msg),
            "assistantMessage": assistant,
        }


class TutorFeedbackService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        message_id: str,
        body: TutorFeedbackIn,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.message_id = message_id
        self.body = body

    async def process(self) -> dict:
        message = await chat_messages_repository.by_id(self.session, self.message_id)
        if message is None:
            raise ApiError(404, "Message not found")
        thread = await chat_threads_repository.by_id(self.session, message.thread_id)
        if thread is None or thread.user_id != self.user_id:
            raise ApiError(404, "Message not found")
        row = ChatFeedback(
            id=cuid(),
            message_id=message.id,
            user_id=self.user_id,
            rating=self.body.rating,
            comment=self.body.comment,
        )
        await chat_feedback_repository.add(self.session, row, flush=True)
        if settings.langsmith_tracing and settings.langsmith_api_key:
            # Best-effort: local row is source of truth; LangSmith is optional.
            pass
        return {"ok": True, "id": row.id}


async def _owned_thread(
    session: AsyncSession, user_id: str, thread_id: str
) -> ChatThread:
    thread = await chat_threads_repository.by_id(session, thread_id)
    if thread is None or thread.user_id != user_id or thread.archived:
        raise ApiError(404, "Thread not found")
    return thread


def _graph_config(thread: ChatThread) -> RunnableConfig:
    return {"configurable": {"thread_id": thread.id, "user_id": thread.user_id}}


def _graph_input(thread: ChatThread, messages: list, question: str) -> TutorState:
    return {
        "messages": messages,
        "question": question,
        "documents": [],
        "citations": [],
        "rewrite_count": 0,
        "subject_id": thread.subject_id,
        "topic_id": thread.topic_id,
        "question_id": thread.question_id,
        "lesson_id": thread.lesson_id,
        "user_id": thread.user_id,
        "route": None,
        "summary": "",
        "sql_result": "",
        "long_term_context": "",
        "decision_trace": [],
        "decision_meta": {},
        "skip_rewrite": False,
        "document_scores": [],
        "pending_action": None,
        "action_decision": None,
        "action_result": None,
    }


def _interrupt_value(snapshot: Any) -> dict | None:
    interrupts = getattr(snapshot, "interrupts", None) or ()
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", None)
    return value if isinstance(value, dict) else None


def _checkpoint_id(snapshot: Any) -> str | None:
    config = getattr(snapshot, "config", None) or {}
    configurable = config.get("configurable") or {}
    checkpoint_id = configurable.get("checkpoint_id")
    return str(checkpoint_id) if checkpoint_id else None


def _proposal_payload(pending: dict, checkpoint_id: str | None) -> dict:
    return {
        "actionId": pending.get("actionId"),
        "checkpointId": checkpoint_id,
        "tool": pending.get("tool"),
        "args": pending.get("args") or {},
        "summary": pending.get("summary") or "Please confirm this change.",
        "destructive": bool(pending.get("destructive")),
        "kind": "actionProposal",
        "status": "pending",
    }


def _settled_grounding(
    grounded_on: list | None, action_id: str, status: str
) -> list | None:
    """Drop pending ``actionProposal`` kind so reloads do not redraw the card.

    The summary stays on the message. The matching grounding entry keeps its
    fields, loses ``kind``, and records ``status`` as ``executed`` or ``rejected``.
    """
    if not isinstance(grounded_on, list):
        return None
    changed = False
    settled: list = []
    for item in grounded_on:
        if not isinstance(item, dict) or item.get("kind") != "actionProposal":
            settled.append(item)
            continue
        if item.get("status") not in (None, "pending"):
            settled.append(item)
            continue
        if action_id and item.get("actionId") not in (None, action_id):
            settled.append(item)
            continue
        updated = {key: value for key, value in item.items() if key != "kind"}
        updated["status"] = status
        settled.append(updated)
        changed = True
    return settled if changed else None


async def _settle_proposal_message(
    session: AsyncSession, thread_id: str, action_id: str, status: str
) -> ChatMessage | None:
    rows = await chat_messages_repository.for_thread(session, thread_id)
    for message in reversed(rows):
        grounded = _settled_grounding(message.grounded_on, action_id, status)
        if grounded is None:
            continue
        message.grounded_on = grounded
        return message
    return None


async def _assert_no_pending(graph: Any, config: RunnableConfig) -> None:
    snapshot = await graph.aget_state(config)
    if _interrupt_value(snapshot):
        raise ApiError(
            409,
            "Approve or dismiss the pending action before sending another message",
        )


def _history_messages(rows: list[ChatMessage]) -> list:
    messages = []
    for msg in rows:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    return messages


async def _run_graph_and_persist(
    session: AsyncSession, thread: ChatThread, user_text: str
) -> dict:
    history = await chat_messages_repository.for_thread(session, thread.id)
    lc_messages = []
    for msg in history:
        if msg.role == "user":
            lc_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            lc_messages.append(AIMessage(content=msg.content))

    checkpointer = get_checkpointer()
    store = get_memory_store()
    graph = get_tutor_graph(checkpointer=checkpointer, store=store)
    config = _graph_config(thread)
    token = set_tool_context(await load_tool_context(session, thread.user_id))
    pending: dict | None = None
    proposal: dict | None = None
    try:
        await _assert_no_pending(graph, config)
        result = await graph.ainvoke(
            _graph_input(thread, lc_messages, user_text), config=config
        )
        snapshot = await graph.aget_state(config)
        pending = _interrupt_value(snapshot)
        if pending:
            proposal = _proposal_payload(pending, _checkpoint_id(snapshot))
            assistant = ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="assistant",
                content=str(proposal["summary"]),
                grounded_on=[proposal],
            )
        else:
            reply_msg = result["messages"][-1]
            content = visible_assistant_text(getattr(reply_msg, "content", ""))
            assistant = ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="assistant",
                content=content,
                grounded_on=result.get("citations") or [],
            )
    finally:
        reset_tool_context(token)
    await chat_messages_repository.add(session, assistant, flush=True)
    thread.updated_at = utcnow()
    await session.flush()
    payload = _message_out(assistant)
    if pending and proposal:
        payload["paused"] = True
        payload["pendingAction"] = proposal
    return payload


async def stop_tutor_stream(
    session: AsyncSession,
    user_id: str,
    thread_id: str,
    *,
    stream_id: str | None = None,
) -> dict:
    """Signal the active SSE stream for this thread to stop."""
    require_rag()
    await _owned_thread(session, user_id, thread_id)
    return request_stop(thread_id, stream_id)


async def stream_tutor_reply(
    _request_session: AsyncSession, user_id: str, thread_id: str, content: str
) -> AsyncIterator[dict]:
    """Yield SSE-friendly dict events while running the graph.

    The request-scoped session is committed and closed before a streaming
    body runs, so the stream owns its own session and commits as it goes.
    """
    async with async_session() as session:
        async for event in _stream_tutor_reply(session, user_id, thread_id, content):
            yield event


async def prepare_new_thread_stream(
    session: AsyncSession, user_id: str, body: TutorThreadCreateIn
) -> TutorThreadCreateIn:
    """Validate a create-and-stream request before the response starts.

    Nothing is written: the thread is created by the stream itself, together
    with the student's first message.
    """
    require_rag()
    if not (body.firstMessage or "").strip():
        raise ApiError(400, "firstMessage is required")
    enforce_daily_message_limit(user_id)
    if body.lessonId:
        from app.database.models import Lesson, Subtopic, Topic

        row = (
            await session.execute(
                select(Topic.id, Topic.subject_id)
                .select_from(Lesson)
                .join(Subtopic, Subtopic.id == Lesson.subtopic_id)
                .join(Topic, Topic.id == Subtopic.topic_id)
                .where(Lesson.id == body.lessonId)
            )
        ).one_or_none()
        if row is None:
            raise ApiError(404, "Lesson not found")
        body = body.model_copy(
            update={
                "topicId": body.topicId or row[0],
                "subjectId": body.subjectId or row[1],
            }
        )
    await CreateTutorThreadService(session, user_id, body)._validate_refs()
    return body


async def stream_new_tutor_thread(
    body: TutorThreadCreateIn, user_id: str
) -> AsyncIterator[dict]:
    """Create the thread with the first message, then stream the reply."""
    async with async_session() as session:
        async for event in _stream_tutor_reply(
            session, user_id, None, body.firstMessage or "", create=body
        ):
            yield event


async def _stream_tutor_reply(
    session: AsyncSession,
    user_id: str,
    thread_id: str | None,
    content: str,
    *,
    create: TutorThreadCreateIn | None = None,
) -> AsyncIterator[dict]:
    require_rag()
    if create is None:
        enforce_daily_message_limit(user_id)
        thread = await _owned_thread(session, user_id, thread_id or "")
    else:
        thread = ChatThread(
            id=cuid(),
            user_id=user_id,
            title=create.title or "New chat",
            subject_id=create.subjectId,
            topic_id=create.topicId,
            question_id=create.questionId,
            lesson_id=create.lessonId,
        )
        await chat_threads_repository.add(session, thread, flush=True)
        wrong = (create.wrongAnswerContext or "").strip()
        if wrong and create.questionId:
            await chat_messages_repository.add(
                session,
                ChatMessage(
                    id=cuid(),
                    thread_id=thread.id,
                    role="system",
                    content=(
                        "The student answered a practice question incorrectly. "
                        f"Their answer was: {wrong}. Help them understand the "
                        "mistake without simply restating the answer key unless "
                        "they ask."
                    ),
                ),
                flush=True,
            )
    text = content.strip()
    if not text:
        raise ApiError(400, "content is required")
    if create is None:
        existing = get_tutor_graph(
            checkpointer=get_checkpointer(), store=get_memory_store()
        )
        await _assert_no_pending(existing, _graph_config(thread))

    stream_id = cuid()
    user_msg = ChatMessage(
        id=cuid(),
        thread_id=thread.id,
        role="user",
        content=text,
    )
    await chat_messages_repository.add(session, user_msg, flush=True)
    await _ensure_thread_title(session, thread, text)
    await session.commit()
    begin_stream(thread.id, stream_id)
    yield {
        "event": "streamStarted",
        "data": {
            "streamId": stream_id,
            "threadId": thread.id,
            "userMessageId": user_msg.id,
            "threadTitle": thread.title,
        },
    }
    user_payload = _message_out(user_msg)
    user_payload["streamId"] = stream_id
    yield {"event": "userMessage", "data": user_payload}

    cancelled = False
    try:
        history = await chat_messages_repository.for_thread(session, thread.id)
        lc_messages = []
        for msg in history:
            if msg.role == "user":
                lc_messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                lc_messages.append(AIMessage(content=msg.content))

        checkpointer = get_checkpointer()
        store = get_memory_store()
        graph = get_tutor_graph(checkpointer=checkpointer, store=store)
        config = _graph_config(thread)
        final_text_parts: list[str] = []
        citations: list = []
        token = set_tool_context(await load_tool_context(session, thread.user_id))
        try:
            async for event in graph.astream_events(
                _graph_input(thread, lc_messages, text),
                config=config,
                version="v2",
            ):
                if should_stop(thread.id, stream_id):
                    cancelled = True
                    break
                kind = event.get("event")
                name = event.get("name")
                node = (event.get("metadata") or {}).get("langgraph_node")
                if kind == "on_chat_model_stream" and node in _ANSWER_NODES:
                    chunk = event.get("data", {}).get("chunk")
                    piece = None
                    if chunk is not None:
                        piece = getattr(chunk, "content", None)
                    piece_text = visible_assistant_text(piece, strip=False)
                    if piece_text:
                        final_text_parts.append(piece_text)
                        yield {"event": "token", "data": {"text": piece_text}}
                elif kind == "on_chain_end" and name in _ANSWER_NODES:
                    output = event.get("data", {}).get("output") or {}
                    citations = output.get("citations") or citations
                    messages = output.get("messages") or []
                    if messages:
                        final = visible_assistant_text(
                            getattr(messages[-1], "content", "")
                        )
                        if final:
                            final_text_parts = [final]
            snapshot = await graph.aget_state(config)
        finally:
            reset_tool_context(token)

        if cancelled or should_stop(thread.id, stream_id):
            await session.delete(user_msg)
            await session.commit()
            yield {
                "event": "done",
                "data": {
                    "ok": False,
                    "cancelled": True,
                    "streamId": stream_id,
                    "userMessageId": user_msg.id,
                },
            }
            return

        pending = _interrupt_value(snapshot)
        if pending:
            proposal = _proposal_payload(pending, _checkpoint_id(snapshot))
            assistant = ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="assistant",
                content=str(proposal["summary"]),
                grounded_on=[proposal],
            )
            await chat_messages_repository.add(session, assistant, flush=True)
            thread.updated_at = utcnow()
            await session.commit()
            yield {"event": "actionProposal", "data": proposal}
            message = _message_out(assistant)
            message["paused"] = True
            message["pendingAction"] = proposal
            yield {"event": "assistantMessage", "data": message}
            yield {
                "event": "done",
                "data": {
                    "ok": True,
                    "cancelled": False,
                    "paused": True,
                    "streamId": stream_id,
                    "pendingAction": proposal,
                },
            }
            return

        full = "".join(final_text_parts).strip()
        if not full:
            assistant = await _run_graph_and_persist(session, thread, text)
            await session.commit()
            yield {"event": "assistantMessage", "data": assistant}
            yield {
                "event": "done",
                "data": {"ok": True, "cancelled": False, "streamId": stream_id},
            }
            return

        assistant = ChatMessage(
            id=cuid(),
            thread_id=thread.id,
            role="assistant",
            content=full,
            grounded_on=citations,
        )
        await chat_messages_repository.add(session, assistant, flush=True)
        thread.updated_at = utcnow()
        await session.commit()
        yield {"event": "assistantMessage", "data": _message_out(assistant)}
        yield {
            "event": "done",
            "data": {"ok": True, "cancelled": False, "streamId": stream_id},
        }
    finally:
        end_stream(thread.id, stream_id)


def _outcome_text(result: dict) -> str:
    messages = result.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return visible_assistant_text(getattr(message, "content", ""))
    return ""


async def _resume_graph(
    session: AsyncSession, thread: ChatThread, body: TutorActionResumeIn
) -> tuple[dict, dict | None, dict | None]:
    graph = get_tutor_graph(checkpointer=get_checkpointer(), store=get_memory_store())
    config = _graph_config(thread)
    snapshot = await graph.aget_state(config)
    pending = _interrupt_value(snapshot)
    if pending is None:
        raise ApiError(409, "No action waiting for approval")
    if body.actionId and pending.get("actionId") != body.actionId:
        raise ApiError(409, "That action is no longer pending")
    payload: dict[str, Any] = {"approved": body.approved}
    if body.args is not None:
        payload["args"] = body.args
    token = set_tool_context(await load_tool_context(session, thread.user_id))
    try:
        result = await graph.ainvoke(Command(resume=payload), config=config)
    finally:
        reset_tool_context(token)
    text = _outcome_text(result) or (
        "Done." if body.approved else "Okay — I won't make that change."
    )
    status = "executed" if body.approved else "rejected"
    action_id = str(pending.get("actionId") or body.actionId or "")
    settled = await _settle_proposal_message(session, thread.id, action_id, status)
    assistant = ChatMessage(
        id=cuid(),
        thread_id=thread.id,
        role="assistant",
        content=text,
        grounded_on=[],
    )
    await chat_messages_repository.add(session, assistant, flush=True)
    thread.updated_at = utcnow()
    await session.flush()
    action_result = result.get("action_result")
    if not isinstance(action_result, dict):
        action_result = None
    return (
        _message_out(assistant),
        action_result,
        (_message_out(settled) if settled is not None else None),
    )


async def resume_tutor_action(
    session: AsyncSession, user_id: str, thread_id: str, body: TutorActionResumeIn
) -> dict:
    """Approve or reject a paused study-plan proposal. Does not spend a message."""
    require_rag()
    thread = await _owned_thread(session, user_id, thread_id)
    message, action_result, proposal_message = await _resume_graph(
        session, thread, body
    )
    return {
        "ok": True,
        "approved": body.approved,
        "assistantMessage": message,
        "proposalMessage": proposal_message,
        "actionResult": action_result,
    }


async def stream_resume_tutor_action(
    user_id: str, thread_id: str, body: TutorActionResumeIn
) -> AsyncIterator[dict]:
    async with async_session() as session:
        require_rag()
        thread = await _owned_thread(session, user_id, thread_id)
        message, action_result, proposal_message = await _resume_graph(
            session, thread, body
        )
        await session.commit()
        event = "actionExecuted" if body.approved else "actionRejected"
        yield {
            "event": event,
            "data": {
                **(action_result or {"approved": body.approved}),
                "proposalMessage": proposal_message,
            },
        }
        if proposal_message is not None:
            yield {"event": "proposalSettled", "data": proposal_message}
        yield {"event": "assistantMessage", "data": message}
        yield {
            "event": "done",
            "data": {
                "ok": True,
                "cancelled": False,
                "paused": False,
                "approved": body.approved,
            },
        }
