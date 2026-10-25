"""Shared helpers for in-process graph runs that stream SSE frames.

Product ``service`` modules own thread CRUD and graph wiring; this module
owns the emit/heartbeat/spawn plumbing both tutor and admin reuse.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from sqlalchemy import text

from app.ai.core.runs import AgentRun, RunLog
from app.ai.core.stream_events import ToolTracker
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.db import async_session


class RunEmitter:
    """Publish SSE frames onto an ``AgentRun`` and optionally mirror to ``RunLog``."""

    def __init__(self, run: AgentRun, log: RunLog | None = None) -> None:
        self.run = run
        self.log = log
        self.tools = ToolTracker()

    def bind_log(self, log: RunLog) -> None:
        self.log = log

    async def emit(self, event: str, data: dict, *, force: bool = False) -> None:
        await self.run.publish(event, data)
        if self.log is not None:
            await self.log.note(force=force or event != "token")

    async def emit_tool_closes(self, phase: str) -> None:
        for payload in self.tools.close_remaining(phase):
            await self.emit("tool", payload, force=True)


async def beat_loop(
    thread_id: str,
    run: AgentRun,
    *,
    table: str,
    label: str,
) -> None:
    """Heartbeat ``activeRun.beat`` so other workers can detect a live producer.

    ``table`` must be a trusted identifier (``ChatThread`` / ``AdminChatThread``).
    """
    while run.status == "running" and not run.closed:
        await asyncio.sleep(5)
        if run.status != "running" or run.closed:
            return
        try:
            async with async_session() as session:
                await session.execute(
                    text(
                        f"""
                        UPDATE "{table}"
                        SET "activeRun" = jsonb_set(
                            COALESCE("activeRun", '{{}}'::jsonb),
                            '{{beat}}',
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
            logger.exception("%s run heartbeat failed", label)


def spawn_run(
    run: AgentRun,
    coro: Coroutine[Any, Any, None],
    *,
    label: str,
) -> AgentRun:
    """Attach a background task to the run and log unexpected failures."""
    task = asyncio.create_task(coro)
    run.task = task

    def _done(finished: asyncio.Task) -> None:
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.error("%s run %s failed", label, run.run_id, exc_info=exc)

    task.add_done_callback(_done)
    return run
