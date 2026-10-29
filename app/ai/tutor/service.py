"""Student-facing AI tutor chat services."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi_pagination.ext.sqlalchemy import apaginate
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.core.checkpointer import get_checkpointer
from app.ai.core.guardrails import (
    apply_output_guard,
    canned_reply,
    guard_input,
    guard_output,
)
from app.ai.core.llm import get_grader_model
from app.ai.core.memory import get_memory_store
from app.ai.core.message_text import visible_assistant_text
from app.ai.core.stream_control import (
    begin_stream,
    end_stream,
    request_stop,
    should_stop,
)
from app.ai.core.stream_events import (
    ToolTracker,
    interpret_graph_event,
    is_stale_run,
    partial_from_events,
)
from app.ai.core.streaming import beat_loop, spawn_run
from app.ai.rag.vectorstore import require_rag
from app.ai.tutor.graph import TutorState, get_tutor_graph
from app.ai.tutor.limits import enforce_daily_message_limit
from app.ai.tutor.prompts import TITLE_PROMPT
from app.ai.tutor.proposals import public_proposal, resume_command
from app.ai.tutor.runs import (
    RunLog,
    TutorRun,
    grace_before_clear,
    hub,
    replay_cursor,
    should_clear_stored_run,
    visible_active_run,
)
from app.ai.tutor.tools.context import (
    load_tool_context,
    reset_tool_context,
    set_tool_context,
)
from app.ai.tutor.tracing import with_tutor_trace
from app.ai.tutor.ui_blocks import UiStreamFilter, split_ui
from app.api.schemas import (
    TutorActionResumeIn,
    TutorFeedbackIn,
    TutorMessageIn,
    TutorThreadCreateIn,
)
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.logger import logger
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
from app.services.product_events import capture

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
        "examType": thread.exam_type,
        "examYear": thread.exam_year,
        "archived": thread.archived,
        "activeRun": visible_active_run(thread),
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
        "ui": list(message.ui or []),
        "tools": list(message.tools or []),
        "createdAt": message.created_at.isoformat() if message.created_at else None,
    }


async def _auto_title(text: str) -> str:
    try:
        model = get_grader_model()
        response = model.invoke(
            [{"role": "user", "content": TITLE_PROMPT.format(message=text[:500])}]
        )
        cleaned = visible_assistant_text(response.content).strip().strip('"')[:80]
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
        question = await self._validate_refs()
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
            exam_type=question.exam_type if question else None,
            exam_year=question.exam_year if question else None,
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
        if first:
            capture(
                self.user_id,
                "tutor_message_sent",
                {"surface": "lesson" if thread.lesson_id else "tutor"},
            )
        return out

    async def _validate_refs(self):
        """Validate all referenced IDs and return the Question if one was provided."""
        if self.body.topicId:
            topic = await topics_repository.by_id(self.session, self.body.topicId)
            if topic is None:
                raise ApiError(400, "Unknown topic")
            if self.body.subjectId and topic.subject_id != self.body.subjectId:
                raise ApiError(400, "Topic does not belong to that subject")
        question = None
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
        return question


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
        if await _materialise_stale_run(self.session, thread):
            await self.session.flush()
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
        capture(
            self.user_id,
            "tutor_message_sent",
            {"surface": "lesson" if thread.lesson_id else "tutor"},
        )
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
    return public_proposal(pending, checkpoint_id)


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
    input_decision = await guard_input(user_text, audience="student")
    if input_decision.action != "pass":
        content = canned_reply(input_decision.action, "student")
        assistant = ChatMessage(
            id=cuid(),
            thread_id=thread.id,
            role="assistant",
            content=content,
            grounded_on=[],
        )
        await chat_messages_repository.add(session, assistant, flush=True)
        thread.updated_at = utcnow()
        await session.flush()
        return _message_out(assistant)

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
            _graph_input(thread, lc_messages, user_text),
            config=with_tutor_trace(config, thread, kind="message"),
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
            content, blocks = split_ui(
                visible_assistant_text(getattr(reply_msg, "content", ""))
            )
            out_decision = await guard_output(content, audience="student")
            content = apply_output_guard(content, out_decision, audience="student")
            assistant = ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="assistant",
                content=content,
                grounded_on=(
                    []
                    if out_decision.action != "pass"
                    else (result.get("citations") or [])
                ),
                ui=None if out_decision.action != "pass" else (blocks or None),
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


def _reject_if_running(thread: ChatThread) -> None:
    live = hub.for_thread(thread.id)
    if live is not None and live.status == "running":
        raise ApiError(409, "A reply is still running")
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    if (
        isinstance(stored, dict)
        and stored.get("status") == "running"
        and not is_stale_run(stored)
        and live is None
    ):
        raise ApiError(409, "A reply is still running")


def _tool_snapshot(tools: ToolTracker) -> list[dict]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "phase": item["phase"],
            "summary": item["summary"],
        }
        for item in tools.records
    ]


async def _commit_assistant(
    session: AsyncSession,
    thread: ChatThread,
    *,
    content: str,
    grounded_on: list | None,
    ui: list | None = None,
    tools: list | None = None,
) -> tuple[dict, list]:
    visible, blocks = split_ui(content)
    out_decision = await guard_output(visible, audience="student")
    if out_decision.action != "pass":
        visible = apply_output_guard(visible, out_decision, audience="student")
        blocks = []
        grounded_on = []
        ui = None
    saved_ui = blocks or list(ui or [])
    assistant = ChatMessage(
        id=cuid(),
        thread_id=thread.id,
        role="assistant",
        content=visible,
        grounded_on=grounded_on or [],
        ui=saved_ui or None,
        tools=list(tools) if tools else None,
    )
    await chat_messages_repository.add(session, assistant, flush=True)
    thread.updated_at = utcnow()
    await session.commit()
    return _message_out(assistant), saved_ui


async def _materialise_stale_run(session: AsyncSession, thread: ChatThread) -> bool:
    """Store whatever a dead run already showed, then clear ``activeRun``."""
    if not should_clear_stored_run(thread):
        return False
    frames = thread.run_events if isinstance(thread.run_events, list) else []
    content, ui, tools = partial_from_events(frames)
    messages = await chat_messages_repository.for_thread(session, thread.id)
    last_user = -1
    for index, message in enumerate(messages):
        if message.role == "user":
            last_user = index
    already = any(message.role == "assistant" for message in messages[last_user + 1 :])
    if not already and (content or ui):
        await chat_messages_repository.add(
            session,
            ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="assistant",
                content=content,
                grounded_on=[],
                ui=ui or None,
                tools=tools or None,
            ),
            flush=True,
        )
    thread.active_run = None
    thread.run_events = None
    thread.updated_at = utcnow()
    return True


async def _finish_paused_run(thread: ChatThread) -> None:
    """Resume settled the card, so the paused run is no longer rejoinable."""
    live = hub.for_thread(thread.id)
    if live is not None and live.status == "paused":
        live.status = "finished"
        await live.seal()
        hub.drop(live)
    thread.active_run = None
    thread.run_events = None


async def _beat_loop(thread_id: str, run: TutorRun) -> None:
    await beat_loop(thread_id, run, table="ChatThread", label="tutor")


def _spawn(run: TutorRun, coro: Any) -> TutorRun:
    return spawn_run(run, coro, label="tutor")


async def stop_tutor_stream(
    session: AsyncSession,
    user_id: str,
    thread_id: str,
    *,
    stream_id: str | None = None,
) -> dict:
    """Signal the active SSE stream for this thread to stop.

    Closing the socket does not stop the run. This endpoint does.
    """
    require_rag()
    thread = await _owned_thread(session, user_id, thread_id)
    result = request_stop(thread.id, stream_id)
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    live = hub.for_thread(thread.id)
    matches = stream_id is None or (stored or {}).get("runId") == stream_id
    if live is None and matches and should_clear_stored_run(thread):
        await _materialise_stale_run(session, thread)
    return result


async def start_message_run(
    session: AsyncSession, user_id: str, thread_id: str, content: str
) -> TutorRun:
    """Validate the turn, then keep running after this request ends."""
    require_rag()
    enforce_daily_message_limit(user_id)
    text_value = content.strip()
    if not text_value:
        raise ApiError(400, "content is required")
    thread = await _owned_thread(session, user_id, thread_id)
    _reject_if_running(thread)
    existing = get_tutor_graph(
        checkpointer=get_checkpointer(), store=get_memory_store()
    )
    await _assert_no_pending(existing, _graph_config(thread))
    run = hub.create(user_id, thread.id, cuid())
    return _spawn(run, _produce_tutor_run(run, user_id, thread.id, text_value))


def start_new_thread_run(
    body: TutorThreadCreateIn,
    user_id: str,
    *,
    existing_thread_id: str | None = None,
) -> TutorRun:
    """Spawn a background run for a new (or resumed) thread stream.

    When ``existing_thread_id`` is provided the run attaches to that thread
    immediately instead of creating a fresh one inside the background task.
    """
    run = hub.create(user_id, existing_thread_id, cuid())
    return _spawn(
        run,
        _produce_tutor_run(
            run,
            user_id,
            existing_thread_id,
            body.firstMessage or "",
            create=body if existing_thread_id is None else None,
        ),
    )


async def prepare_new_thread_stream(
    session: AsyncSession, user_id: str, body: TutorThreadCreateIn
) -> tuple[TutorThreadCreateIn, str | None]:
    """Validate a create-and-stream request before the response starts.

    Returns ``(body, existing_thread_id)``.  When the student already has an
    open thread for the same question the caller should resume that thread
    rather than creating a new one; ``existing_thread_id`` will be non-None in
    that case.  Nothing is written to the database here — the thread is created
    (or the existing one reused) by the background task that handles the stream.
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

    # Check whether the student already has a thread for this question so the
    # stream can resume it instead of creating a duplicate.
    existing_thread_id: str | None = None
    if body.questionId:
        existing = await chat_threads_repository.for_user_question(
            session, user_id, body.questionId
        )
        if existing is not None:
            _reject_if_running(existing)
            existing_thread_id = existing.id

    return body, existing_thread_id


async def _produce_tutor_run(
    run: TutorRun,
    user_id: str,
    thread_id: str | None,
    content: str,
    *,
    create: TutorThreadCreateIn | None = None,
) -> None:
    """Run the graph on its own task. Disconnect does not cancel it."""
    tools = ToolTracker()
    ui_filter = UiStreamFilter()
    heartbeat: asyncio.Task | None = None
    log: RunLog | None = None
    thread: ChatThread | None = None
    begun = False
    final_text_parts: list[str] = []
    citations: list = []
    async with async_session() as session:

        async def emit(event: str, data: dict, *, force: bool = False) -> None:
            await run.publish(event, data)
            if log is not None:
                await log.note(force=force or event != "token")

        async def emit_closes(phase: str) -> None:
            for payload in tools.close_remaining(phase):
                await emit("tool", payload, force=True)

        try:
            require_rag()
            text_value = content.strip()
            if create is None:
                if thread_id is None:
                    raise ApiError(404, "Thread not found")
                thread = await _owned_thread(session, user_id, thread_id)
            else:
                # Fetch question to snapshot exam context on the new thread.
                q_exam_type: str | None = None
                q_exam_year: int | None = None
                if create.questionId:
                    q = await questions_repository.by_id(session, create.questionId)
                    if q is not None:
                        q_exam_type = q.exam_type
                        q_exam_year = q.exam_year
                thread = ChatThread(
                    id=cuid(),
                    user_id=user_id,
                    title=create.title or "New chat",
                    subject_id=create.subjectId,
                    topic_id=create.topicId,
                    question_id=create.questionId,
                    lesson_id=create.lessonId,
                    exam_type=q_exam_type,
                    exam_year=q_exam_year,
                )
                await chat_threads_repository.add(session, thread, flush=True)
                hub.bind(run, thread.id)
                wrong = (create.wrongAnswerContext or "").strip()
                if wrong and create.questionId:
                    await chat_messages_repository.add(
                        session,
                        ChatMessage(
                            id=cuid(),
                            thread_id=thread.id,
                            role="system",
                            content=(
                                "The student answered a practice question "
                                "incorrectly. "
                                f"Their answer was: {wrong}. Help them understand "
                                "the mistake without simply restating the answer "
                                "key unless they ask."
                            ),
                        ),
                        flush=True,
                    )
            if not text_value:
                raise ApiError(400, "content is required")
            user_msg = ChatMessage(
                id=cuid(),
                thread_id=thread.id,
                role="user",
                content=text_value,
            )
            await chat_messages_repository.add(session, user_msg, flush=True)
            await _ensure_thread_title(session, thread, text_value)
            await session.commit()
            capture(
                user_id,
                "tutor_message_sent",
                {"surface": "lesson" if thread.lesson_id else "tutor"},
            )
            log = RunLog(thread.id, run)
            begin_stream(thread.id, run.run_id)
            begun = True
            heartbeat = asyncio.create_task(_beat_loop(thread.id, run))
            await emit(
                "streamStarted",
                {
                    "streamId": run.run_id,
                    "runId": run.run_id,
                    "threadId": thread.id,
                    "userMessageId": user_msg.id,
                    "threadTitle": thread.title,
                },
                force=True,
            )
            user_payload = _message_out(user_msg)
            user_payload["streamId"] = run.run_id
            user_payload["runId"] = run.run_id
            await emit("userMessage", user_payload, force=True)

            input_decision = await guard_input(text_value, audience="student")
            if input_decision.action != "pass":
                message, blocks = await _commit_assistant(
                    session,
                    thread,
                    content=canned_reply(input_decision.action, "student"),
                    grounded_on=[],
                )
                for block in blocks:
                    await emit("ui", block, force=True)
                await emit("assistantMessage", message, force=True)
                await emit(
                    "done",
                    {
                        "ok": True,
                        "cancelled": False,
                        "streamId": run.run_id,
                        "runId": run.run_id,
                    },
                    force=True,
                )
                return

            history = await chat_messages_repository.for_thread(session, thread.id)
            lc_messages = _history_messages(history)
            checkpointer = get_checkpointer()
            store = get_memory_store()
            graph = get_tutor_graph(checkpointer=checkpointer, store=store)
            config = _graph_config(thread)
            token = set_tool_context(await load_tool_context(session, thread.user_id))
            cancelled = False
            try:
                async for event in graph.astream_events(
                    _graph_input(thread, lc_messages, text_value),
                    config=with_tutor_trace(config, thread, kind="stream"),
                    version="v2",
                ):
                    if should_stop(thread.id, run.run_id):
                        cancelled = True
                        break
                    for kind, data in interpret_graph_event(event, tools):
                        await emit(kind, data, force=True)
                    graph_kind = event.get("event")
                    name = event.get("name")
                    node = (event.get("metadata") or {}).get("langgraph_node")
                    if graph_kind == "on_chat_model_stream" and node in _ANSWER_NODES:
                        chunk = event.get("data", {}).get("chunk")
                        piece = None
                        if chunk is not None:
                            piece = getattr(chunk, "content", None)
                        piece_text = visible_assistant_text(piece, strip=False)
                        if piece_text:
                            final_text_parts.append(piece_text)
                            safe = ui_filter.push(piece_text)
                            if safe:
                                await emit("token", {"text": safe})
                    elif graph_kind == "on_chain_end" and name in _ANSWER_NODES:
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

            if cancelled or should_stop(thread.id, run.run_id):
                await emit_closes("failed")
                tail = ui_filter.flush_tail()
                if tail:
                    await emit("token", {"text": tail})
                full = "".join(final_text_parts).strip()
                if full:
                    message, blocks = await _commit_assistant(
                        session,
                        thread,
                        content=full,
                        grounded_on=citations,
                        tools=_tool_snapshot(tools),
                    )
                    for block in blocks:
                        await emit("ui", block, force=True)
                    await emit("assistantMessage", message, force=True)
                await emit(
                    "done",
                    {
                        "ok": False,
                        "cancelled": True,
                        "streamId": run.run_id,
                        "runId": run.run_id,
                        "userMessageId": user_msg.id,
                    },
                    force=True,
                )
                return

            pending = _interrupt_value(snapshot)
            if pending:
                await emit_closes("done")
                proposal = _proposal_payload(pending, _checkpoint_id(snapshot))
                run.status = "paused"
                assistant = ChatMessage(
                    id=cuid(),
                    thread_id=thread.id,
                    role="assistant",
                    content=str(proposal["summary"]),
                    grounded_on=[proposal],
                    tools=_tool_snapshot(tools) or None,
                )
                await chat_messages_repository.add(session, assistant, flush=True)
                thread.updated_at = utcnow()
                await session.commit()
                await emit("actionProposal", proposal, force=True)
                message = _message_out(assistant)
                message["paused"] = True
                message["pendingAction"] = proposal
                await emit("assistantMessage", message, force=True)
                await emit(
                    "done",
                    {
                        "ok": True,
                        "cancelled": False,
                        "paused": True,
                        "streamId": run.run_id,
                        "runId": run.run_id,
                        "pendingAction": proposal,
                    },
                    force=True,
                )
                return

            await emit_closes("done")
            tail = ui_filter.flush_tail()
            if tail:
                await emit("token", {"text": tail})
            full = "".join(final_text_parts).strip()
            if not full:
                assistant = await _run_graph_and_persist(session, thread, text_value)
                await session.commit()
                for block in assistant.get("ui") or []:
                    await emit("ui", block, force=True)
                await emit("assistantMessage", assistant, force=True)
                await emit(
                    "done",
                    {
                        "ok": True,
                        "cancelled": False,
                        "streamId": run.run_id,
                        "runId": run.run_id,
                    },
                    force=True,
                )
                return

            message, blocks = await _commit_assistant(
                session,
                thread,
                content=full,
                grounded_on=citations,
                tools=_tool_snapshot(tools),
            )
            for block in blocks:
                await emit("ui", block, force=True)
            await emit("assistantMessage", message, force=True)
            await emit(
                "done",
                {
                    "ok": True,
                    "cancelled": False,
                    "streamId": run.run_id,
                    "runId": run.run_id,
                },
                force=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("tutor run failed")
            if thread is not None and final_text_parts:
                with contextlib.suppress(Exception):
                    await _commit_assistant(
                        session,
                        thread,
                        content="".join(final_text_parts).strip(),
                        grounded_on=citations,
                        tools=_tool_snapshot(tools),
                    )
            with contextlib.suppress(Exception):
                await emit_closes("failed")
                await emit(
                    "done",
                    {
                        "ok": False,
                        "cancelled": False,
                        "streamId": run.run_id,
                        "runId": run.run_id,
                    },
                    force=True,
                )
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            if run.status != "paused":
                await run.seal()
                run.status = "finished"
                if log is not None:
                    await grace_before_clear()
                    await log.clear()
                hub.drop(run)
            else:
                await run.seal()
            if begun and thread is not None:
                end_stream(thread.id, run.run_id)


def _synthetic_paused_frames(
    thread: ChatThread, messages: list[ChatMessage]
) -> list[dict]:
    run_id = ""
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    if stored:
        run_id = str(stored.get("runId") or "")
    for message in reversed(messages):
        for item in message.grounded_on or []:
            if not isinstance(item, dict) or item.get("kind") != "actionProposal":
                continue
            if item.get("status") not in (None, "pending"):
                continue
            proposal = dict(item)
            body = _message_out(message)
            body["paused"] = True
            body["pendingAction"] = proposal
            return [
                {"id": "1", "event": "actionProposal", "data": proposal},
                {"id": "2", "event": "assistantMessage", "data": body},
                {
                    "id": "3",
                    "event": "done",
                    "data": {
                        "ok": True,
                        "cancelled": False,
                        "paused": True,
                        "streamId": run_id,
                        "runId": run_id,
                        "pendingAction": proposal,
                    },
                },
            ]
    return []


async def _iter_frames(frames: list[dict], after: int) -> AsyncIterator[dict]:
    for frame in frames:
        raw = frame.get("id")
        if isinstance(raw, str) and raw.isdigit() and int(raw) > after:
            yield frame


async def _iter_persisted_run(
    thread_id: str, run_id: str, after: int
) -> AsyncIterator[dict]:
    """Poll the thread row when this process is not the one running the graph."""
    cursor = after
    saw_done = False
    while True:
        async with async_session() as session:
            thread = await chat_threads_repository.by_id(session, thread_id)
            if thread is None:
                return
            frames = thread.run_events if isinstance(thread.run_events, list) else []
            stored = thread.active_run if isinstance(thread.active_run, dict) else None
        fresh: list[dict] = []
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            raw = frame.get("id")
            if not isinstance(raw, str) or not raw.isdigit():
                continue
            frame_id = int(raw)
            if frame_id > cursor:
                fresh.append(frame)
                cursor = frame_id
                if frame.get("event") == "done":
                    saw_done = True
        for frame in fresh:
            yield frame
        if saw_done:
            return
        if not stored or stored.get("runId") != run_id:
            return
        if stored.get("status") == "paused":
            return
        if is_stale_run(stored):
            return
        await asyncio.sleep(0.3)


async def open_run_replay(
    session: AsyncSession,
    user_id: str,
    thread_id: str,
    run_id: str,
    last_event_id: str | None,
) -> AsyncIterator[dict]:
    """Replay frames after ``Last-Event-ID``, then follow the live run."""
    thread = await _owned_thread(session, user_id, thread_id)
    run = hub.get(run_id)
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    # Resume clears the row. A paused buffer on this process is then stale.
    if (
        run is not None
        and run.status == "paused"
        and (stored is None or stored.get("runId") != run.run_id)
    ):
        hub.drop(run)
        run = None
    if (
        run is not None
        and run.user_id == user_id
        and run.thread_id == thread.id
        and run.status in {"running", "paused"}
    ):
        return run.iter_after(replay_cursor(last_event_id, run.events))
    if not stored or stored.get("runId") != run_id:
        raise ApiError(404, "Run not found")
    if stored.get("status") not in {"running", "paused"}:
        raise ApiError(404, "Run not found")
    if stored.get("status") == "running" and is_stale_run(stored):
        raise ApiError(404, "Run not found")
    frames = thread.run_events if isinstance(thread.run_events, list) else []
    if stored.get("status") == "paused" and not frames:
        messages = await chat_messages_repository.for_thread(session, thread.id)
        frames = _synthetic_paused_frames(thread, messages)
        if not frames:
            raise ApiError(404, "Run not found")
        return _iter_frames(frames, replay_cursor(last_event_id, frames))
    return _iter_persisted_run(thread.id, run_id, replay_cursor(last_event_id, frames))


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
    payload = resume_command(pending, body.approved, body.args)
    token = set_tool_context(await load_tool_context(session, thread.user_id))
    try:
        result = await graph.ainvoke(
            Command(resume=payload),
            config=with_tutor_trace(config, thread, kind="resume"),
        )
    finally:
        reset_tool_context(token)
    text = _outcome_text(result) or (
        "Done." if body.approved else "Okay — I won't make that change."
    )
    status = "executed" if body.approved else "rejected"
    action_id = str(pending.get("actionId") or body.actionId or "")
    settled = await _settle_proposal_message(session, thread.id, action_id, status)
    await _finish_paused_run(thread)
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
