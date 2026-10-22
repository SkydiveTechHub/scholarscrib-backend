"""Admin Astra chat. Student tutor limits, scope, and tools do not apply."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi_pagination.ext.sqlalchemy import apaginate
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AdminAstraMessageIn,
    AdminAstraResumeIn,
    AdminAstraThreadCreateIn,
)
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.db import async_session
from app.database.models.admin_chat import AdminChatMessage, AdminChatThread
from app.database.repositories.admin_chat import (
    admin_chat_messages_repository,
    admin_chat_threads_repository,
)
from app.services.admin_astra.context import (
    load_admin_tool_context,
    reset_admin_tool_context,
    set_admin_tool_context,
)
from app.services.admin_astra.graph import (
    RECURSION_LIMIT,
    AdminAstraState,
    get_admin_graph,
)
from app.services.admin_astra.prompts import TITLE_PROMPT
from app.services.admin_astra.proposals import public_proposal, resume_command
from app.services.admin_astra.runs import (
    AdminRunLog,
    hub,
    should_clear_stored_run,
    visible_active_run,
)
from app.services.rag.checkpointer import get_checkpointer
from app.services.rag.llm import get_grader_model
from app.services.rag.message_text import visible_assistant_text
from app.services.rag.runs import TutorRun, grace_before_clear, replay_cursor
from app.services.rag.stream_control import (
    begin_stream,
    end_stream,
    request_stop,
    should_stop,
)
from app.services.rag.stream_events import (
    ToolTracker,
    interpret_graph_event,
    is_stale_run,
    partial_from_events,
)

_ANSWER_NODES = frozenset({"chitchat", "action_agent", "action_outcome"})


def _thread_out(
    thread: AdminChatThread, messages: list[AdminChatMessage] | None = None
) -> dict:
    payload: dict[str, Any] = {
        "id": thread.id,
        "title": thread.title,
        "archived": thread.archived,
        "activeRun": visible_active_run(thread),
        "createdAt": thread.created_at.isoformat() if thread.created_at else None,
        "updatedAt": thread.updated_at.isoformat() if thread.updated_at else None,
    }
    if messages is not None:
        payload["messages"] = [_message_out(message) for message in messages]
    return payload


def _message_out(message: AdminChatMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "groundedOn": message.grounded_on or [],
        "ui": list(message.ui or []),
        "tools": list(message.tools or []),
        "createdAt": message.created_at.isoformat() if message.created_at else None,
    }


async def _auto_title(text_value: str) -> str:
    try:
        response = get_grader_model().invoke(
            [{"role": "user", "content": TITLE_PROMPT.format(message=text_value[:500])}]
        )
        cleaned = visible_assistant_text(response.content).strip().strip('"')[:80]
        return cleaned or (text_value or "New chat")[:60]
    except Exception:
        logger.exception("admin astra title failed")
        return (text_value or "New chat")[:60]


async def _ensure_thread_title(
    session: AsyncSession, thread: AdminChatThread, text_value: str
) -> None:
    if thread.title and thread.title.strip() and thread.title.strip() != "New chat":
        return
    thread.title = await _auto_title(text_value)
    thread.updated_at = utcnow()
    await session.flush()


async def _owned_thread(
    session: AsyncSession, admin_id: str, thread_id: str
) -> AdminChatThread:
    thread = await admin_chat_threads_repository.owned(session, admin_id, thread_id)
    if thread is None:
        raise ApiError(404, "Thread not found")
    return thread


def _graph_config(thread: AdminChatThread) -> RunnableConfig:
    return {
        "configurable": {"thread_id": thread.id, "admin_id": thread.admin_id},
        "recursion_limit": RECURSION_LIMIT,
    }


def _graph_input(messages: list, question: str) -> AdminAstraState:
    return {
        "messages": messages,
        "question": question,
        "route": None,
        "domains": [],
        "decision_meta": {},
        "pending_action": None,
        "action_decision": None,
        "action_result": None,
    }


def _history_messages(rows: list[AdminChatMessage]) -> list:
    messages = []
    for message in rows:
        if message.role == "user":
            messages.append(HumanMessage(content=message.content))
        elif message.role == "assistant":
            messages.append(AIMessage(content=message.content))
    return messages


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
) -> AdminChatMessage | None:
    rows = await admin_chat_messages_repository.for_thread(session, thread_id)
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


def _reject_if_running(thread: AdminChatThread) -> None:
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


def _last_assistant_text(snapshot: Any) -> str:
    values = getattr(snapshot, "values", None) or {}
    messages = values.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return visible_assistant_text(getattr(message, "content", ""))
    return ""


async def _run_graph_and_persist(
    session: AsyncSession, thread: AdminChatThread, user_text: str
) -> dict:
    history = await admin_chat_messages_repository.for_thread(session, thread.id)
    graph = get_admin_graph(checkpointer=get_checkpointer())
    config = _graph_config(thread)
    token = set_admin_tool_context(
        await load_admin_tool_context(session, thread.admin_id)
    )
    pending: dict | None = None
    proposal: dict | None = None
    try:
        await _assert_no_pending(graph, config)
        result = await graph.ainvoke(
            _graph_input(_history_messages(history), user_text), config=config
        )
        snapshot = await graph.aget_state(config)
        pending = _interrupt_value(snapshot)
        if pending:
            proposal = _proposal_payload(pending, _checkpoint_id(snapshot))
            content = str(proposal["summary"])
            grounded: list | None = [proposal]
        else:
            reply = result["messages"][-1]
            content = visible_assistant_text(getattr(reply, "content", ""))
            grounded = []
        assistant = AdminChatMessage(
            id=cuid(),
            thread_id=thread.id,
            role="assistant",
            content=content,
            grounded_on=grounded,
        )
    finally:
        reset_admin_tool_context(token)
    await admin_chat_messages_repository.add(session, assistant, flush=True)
    thread.updated_at = utcnow()
    await session.flush()
    payload = _message_out(assistant)
    if pending and proposal:
        payload["paused"] = True
        payload["pendingAction"] = proposal
    return payload


class CreateAdminAstraThreadService:
    def __init__(
        self, session: AsyncSession, admin_id: str, body: AdminAstraThreadCreateIn
    ) -> None:
        self.session = session
        self.admin_id = admin_id
        self.body = body

    async def process(self) -> dict:
        first = (self.body.firstMessage or "").strip()
        title = self.body.title
        if not title and first:
            title = await _auto_title(first)
        thread = AdminChatThread(
            id=cuid(),
            admin_id=self.admin_id,
            title=title or "New chat",
        )
        await admin_chat_threads_repository.add(self.session, thread, flush=True)
        assistant_payload = None
        if first:
            await admin_chat_messages_repository.add(
                self.session,
                AdminChatMessage(
                    id=cuid(), thread_id=thread.id, role="user", content=first
                ),
                flush=True,
            )
            assistant_payload = await _run_graph_and_persist(
                self.session, thread, first
            )
        messages = await admin_chat_messages_repository.for_thread(
            self.session, thread.id
        )
        out = _thread_out(thread, messages)
        if assistant_payload:
            out["assistantMessage"] = assistant_payload
        return out


class ListAdminAstraThreadsService:
    def __init__(self, session: AsyncSession, admin_id: str) -> None:
        self.session = session
        self.admin_id = admin_id

    async def process(self) -> dict:
        rows = await admin_chat_threads_repository.for_admin(
            self.session, self.admin_id
        )
        return {"threads": [_thread_out(row) for row in rows]}


class GetAdminAstraThreadService:
    def __init__(self, session: AsyncSession, admin_id: str, thread_id: str) -> None:
        self.session = session
        self.admin_id = admin_id
        self.thread_id = thread_id

    async def process(self) -> dict:
        thread = await _owned_thread(self.session, self.admin_id, self.thread_id)
        if await _materialise_stale_run(self.session, thread):
            await self.session.flush()
        messages = await admin_chat_messages_repository.for_thread(
            self.session, thread.id
        )
        return _thread_out(thread, messages)


class ListAdminAstraMessagesService:
    def __init__(
        self,
        session: AsyncSession,
        admin_id: str,
        thread_id: str,
        *,
        order_by: str = "asc",
    ) -> None:
        self.session = session
        self.admin_id = admin_id
        self.thread_id = thread_id
        self.order_by = order_by if order_by in {"asc", "desc"} else "asc"

    async def process(self):
        thread = await _owned_thread(self.session, self.admin_id, self.thread_id)
        if self.order_by == "desc":
            ordering = (AdminChatMessage.created_at.desc(), AdminChatMessage.id.desc())
        else:
            ordering = (AdminChatMessage.created_at.asc(), AdminChatMessage.id.asc())
        query = (
            select(AdminChatMessage)
            .where(AdminChatMessage.thread_id == thread.id)
            .order_by(*ordering)
        )
        return await apaginate(
            self.session,
            query,
            transformer=lambda rows: [_message_out(message) for message in rows],
        )


class DeleteAdminAstraThreadService:
    def __init__(self, session: AsyncSession, admin_id: str, thread_id: str) -> None:
        self.session = session
        self.admin_id = admin_id
        self.thread_id = thread_id

    async def process(self) -> dict:
        thread = await _owned_thread(self.session, self.admin_id, self.thread_id)
        thread.archived = True
        thread.updated_at = utcnow()
        await self.session.flush()
        return {"ok": True}


class PostAdminAstraMessageService:
    def __init__(
        self,
        session: AsyncSession,
        admin_id: str,
        thread_id: str,
        body: AdminAstraMessageIn,
    ) -> None:
        self.session = session
        self.admin_id = admin_id
        self.thread_id = thread_id
        self.body = body

    async def process(self) -> dict:
        thread = await _owned_thread(self.session, self.admin_id, self.thread_id)
        content = self.body.content.strip()
        if not content:
            raise ApiError(400, "content is required")
        _reject_if_running(thread)
        user_msg = AdminChatMessage(
            id=cuid(), thread_id=thread.id, role="user", content=content
        )
        await admin_chat_messages_repository.add(self.session, user_msg, flush=True)
        await _ensure_thread_title(self.session, thread, content)
        assistant = await _run_graph_and_persist(self.session, thread, content)
        return {"userMessage": _message_out(user_msg), "assistantMessage": assistant}


async def _commit_assistant(
    session: AsyncSession,
    thread: AdminChatThread,
    *,
    content: str,
    grounded_on: list | None,
    tools: list | None = None,
) -> dict:
    assistant = AdminChatMessage(
        id=cuid(),
        thread_id=thread.id,
        role="assistant",
        content=content,
        grounded_on=grounded_on or [],
        tools=list(tools) if tools else None,
    )
    await admin_chat_messages_repository.add(session, assistant, flush=True)
    thread.updated_at = utcnow()
    await session.commit()
    return _message_out(assistant)


async def _materialise_stale_run(
    session: AsyncSession, thread: AdminChatThread
) -> bool:
    if not should_clear_stored_run(thread):
        return False
    frames = thread.run_events if isinstance(thread.run_events, list) else []
    content, ui, tools = partial_from_events(frames)
    messages = await admin_chat_messages_repository.for_thread(session, thread.id)
    last_user = -1
    for index, message in enumerate(messages):
        if message.role == "user":
            last_user = index
    already = any(message.role == "assistant" for message in messages[last_user + 1 :])
    if not already and (content or ui):
        await admin_chat_messages_repository.add(
            session,
            AdminChatMessage(
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


async def _finish_paused_run(thread: AdminChatThread) -> None:
    live = hub.for_thread(thread.id)
    if live is not None and live.status == "paused":
        live.status = "finished"
        await live.seal()
        hub.drop(live)
    thread.active_run = None
    thread.run_events = None


async def _beat_loop(thread_id: str, run: TutorRun) -> None:
    while run.status == "running" and not run.closed:
        await asyncio.sleep(5)
        if run.status != "running" or run.closed:
            return
        try:
            async with async_session() as session:
                await session.execute(
                    text(
                        """
                        UPDATE "AdminChatThread"
                        SET "activeRun" = jsonb_set(
                            COALESCE("activeRun", '{}'::jsonb),
                            '{beat}',
                            to_jsonb(CAST(:beat AS text)),
                            true
                        )
                        WHERE id = :id
                          AND "activeRun" IS NOT NULL
                          AND "activeRun"->>'runId' = :run_id
                        """
                    ),
                    {
                        "beat": utcnow().isoformat(),
                        "id": thread_id,
                        "run_id": run.run_id,
                    },
                )
                await session.commit()
        except Exception:
            logger.exception("admin astra heartbeat failed")


def _spawn(run: TutorRun, coro: Any) -> TutorRun:
    task = asyncio.create_task(coro)
    run.task = task

    def _done(finished: asyncio.Task) -> None:
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.error("admin astra run %s failed", run.run_id, exc_info=exc)

    task.add_done_callback(_done)
    return run


async def stop_admin_astra_stream(
    session: AsyncSession,
    admin_id: str,
    thread_id: str,
    *,
    stream_id: str | None = None,
) -> dict:
    thread = await _owned_thread(session, admin_id, thread_id)
    result = request_stop(thread.id, stream_id)
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    live = hub.for_thread(thread.id)
    matches = stream_id is None or (stored or {}).get("runId") == stream_id
    if live is None and matches and should_clear_stored_run(thread):
        await _materialise_stale_run(session, thread)
    return result


async def start_message_run(
    session: AsyncSession, admin_id: str, thread_id: str, content: str
) -> TutorRun:
    text_value = content.strip()
    if not text_value:
        raise ApiError(400, "content is required")
    thread = await _owned_thread(session, admin_id, thread_id)
    _reject_if_running(thread)
    existing = get_admin_graph(checkpointer=get_checkpointer())
    await _assert_no_pending(existing, _graph_config(thread))
    run = hub.create(admin_id, thread.id, cuid())
    return _spawn(run, _produce_run(run, admin_id, thread.id, text_value))


async def _produce_run(
    run: TutorRun, admin_id: str, thread_id: str, content: str
) -> None:
    tools = ToolTracker()
    heartbeat: asyncio.Task | None = None
    log: AdminRunLog | None = None
    thread: AdminChatThread | None = None
    begun = False
    final_text_parts: list[str] = []
    async with async_session() as session:

        async def emit(event: str, data: dict, *, force: bool = False) -> None:
            await run.publish(event, data)
            if log is not None:
                await log.note(force=force or event != "token")

        async def emit_closes(phase: str) -> None:
            for payload in tools.close_remaining(phase):
                await emit("tool", payload, force=True)

        try:
            text_value = content.strip()
            thread = await _owned_thread(session, admin_id, thread_id)
            if not text_value:
                raise ApiError(400, "content is required")
            user_msg = AdminChatMessage(
                id=cuid(), thread_id=thread.id, role="user", content=text_value
            )
            await admin_chat_messages_repository.add(session, user_msg, flush=True)
            await _ensure_thread_title(session, thread, text_value)
            await session.commit()
            log = AdminRunLog(thread.id, run)
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

            history = await admin_chat_messages_repository.for_thread(
                session, thread.id
            )
            graph = get_admin_graph(checkpointer=get_checkpointer())
            config = _graph_config(thread)
            token = set_admin_tool_context(
                await load_admin_tool_context(session, thread.admin_id)
            )
            cancelled = False
            snapshot = None
            try:
                async for event in graph.astream_events(
                    _graph_input(_history_messages(history), text_value),
                    config=config,
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
                            await emit("token", {"text": piece_text})
                    elif graph_kind == "on_chain_end" and name in _ANSWER_NODES:
                        output = event.get("data", {}).get("output") or {}
                        messages = output.get("messages") or []
                        if messages:
                            final = visible_assistant_text(
                                getattr(messages[-1], "content", "")
                            )
                            if final:
                                final_text_parts = [final]
                snapshot = await graph.aget_state(config)
            finally:
                reset_admin_tool_context(token)

            if cancelled or should_stop(thread.id, run.run_id):
                await emit_closes("failed")
                full = "".join(final_text_parts).strip()
                if full:
                    message = await _commit_assistant(
                        session,
                        thread,
                        content=full,
                        grounded_on=[],
                        tools=_tool_snapshot(tools),
                    )
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
                assistant = AdminChatMessage(
                    id=cuid(),
                    thread_id=thread.id,
                    role="assistant",
                    content=str(proposal["summary"]),
                    grounded_on=[proposal],
                    tools=_tool_snapshot(tools) or None,
                )
                await admin_chat_messages_repository.add(session, assistant, flush=True)
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
            full = "".join(final_text_parts).strip() or _last_assistant_text(snapshot)
            message = await _commit_assistant(
                session,
                thread,
                content=full or "Done.",
                grounded_on=[],
                tools=_tool_snapshot(tools),
            )
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
            logger.exception("admin astra run failed")
            if thread is not None and final_text_parts:
                with contextlib.suppress(Exception):
                    await _commit_assistant(
                        session,
                        thread,
                        content="".join(final_text_parts).strip(),
                        grounded_on=[],
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
    thread: AdminChatThread, messages: list[AdminChatMessage]
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
    cursor = after
    saw_done = False
    while True:
        async with async_session() as session:
            thread = await admin_chat_threads_repository.by_id(session, thread_id)
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
    admin_id: str,
    thread_id: str,
    run_id: str,
    last_event_id: str | None,
) -> AsyncIterator[dict]:
    thread = await _owned_thread(session, admin_id, thread_id)
    run = hub.get(run_id)
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    if (
        run is not None
        and run.status == "paused"
        and (stored is None or stored.get("runId") != run.run_id)
    ):
        hub.drop(run)
        run = None
    if (
        run is not None
        and run.user_id == admin_id
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
        messages = await admin_chat_messages_repository.for_thread(session, thread.id)
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
    session: AsyncSession, thread: AdminChatThread, body: AdminAstraResumeIn
) -> tuple[dict, dict | None, dict | None]:
    graph = get_admin_graph(checkpointer=get_checkpointer())
    config = _graph_config(thread)
    snapshot = await graph.aget_state(config)
    pending = _interrupt_value(snapshot)
    if pending is None:
        raise ApiError(409, "No action waiting for approval")
    if body.actionId and pending.get("actionId") != body.actionId:
        raise ApiError(409, "That action is no longer pending")
    payload = resume_command(pending, body.approved, body.args)
    token = set_admin_tool_context(
        await load_admin_tool_context(session, thread.admin_id)
    )
    try:
        result = await graph.ainvoke(Command(resume=payload), config=config)
    finally:
        reset_admin_tool_context(token)
    text_value = _outcome_text(result) or (
        "Done." if body.approved else "Okay — I won't make that change."
    )
    status = "executed" if body.approved else "rejected"
    action_id = str(pending.get("actionId") or body.actionId or "")
    settled = await _settle_proposal_message(session, thread.id, action_id, status)
    await _finish_paused_run(thread)
    assistant = AdminChatMessage(
        id=cuid(),
        thread_id=thread.id,
        role="assistant",
        content=text_value,
        grounded_on=[],
    )
    await admin_chat_messages_repository.add(session, assistant, flush=True)
    thread.updated_at = utcnow()
    await session.flush()
    action_result = result.get("action_result")
    if not isinstance(action_result, dict):
        action_result = None
    return (
        _message_out(assistant),
        action_result,
        _message_out(settled) if settled is not None else None,
    )


async def resume_admin_astra_action(
    session: AsyncSession, admin_id: str, thread_id: str, body: AdminAstraResumeIn
) -> dict:
    thread = await _owned_thread(session, admin_id, thread_id)
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


async def stream_resume_admin_astra_action(
    admin_id: str, thread_id: str, body: AdminAstraResumeIn
) -> AsyncIterator[dict]:
    async with async_session() as session:
        thread = await _owned_thread(session, admin_id, thread_id)
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
