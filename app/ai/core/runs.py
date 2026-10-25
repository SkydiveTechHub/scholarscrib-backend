"""In-process agent runs that outlive the HTTP connection that started them.

``AgentRun`` / ``RunHub`` are shared by student tutor and admin Astra.
Product modules subclass ``RunLog`` to persist onto their thread table.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from app.ai.core.stream_events import cursor_after
from app.core.logger import logger
from app.core.timeutil import utcnow

_REPLAY_GRACE_SECONDS = 1.0


class AgentRun:
    """One graph turn and the SSE frames produced for it."""

    def __init__(self, run_id: str, user_id: str, thread_id: str | None) -> None:
        self.run_id = run_id
        self.user_id = user_id
        self.thread_id = thread_id
        self.status = "running"
        self.events: list[dict] = []
        self.closed = False
        self._seq = 0
        self._cond = asyncio.Condition()
        self.task: asyncio.Task | None = None

    async def publish(self, event: str, data: dict) -> dict:
        async with self._cond:
            self._seq += 1
            frame = {"id": str(self._seq), "event": event, "data": data}
            self.events.append(frame)
            self._cond.notify_all()
            return frame

    async def seal(self) -> None:
        async with self._cond:
            self.closed = True
            self._cond.notify_all()

    async def iter_after(self, after: int) -> AsyncIterator[dict]:
        index = 0
        while True:
            async with self._cond:
                while index >= len(self.events) and not self.closed:
                    await self._cond.wait()
                if index >= len(self.events):
                    return
                batch = list(self.events[index:])
                index = len(self.events)
            for frame in batch:
                if int(frame["id"]) > after:
                    yield frame


# Back-compat alias for call sites / tests that still say TutorRun.
TutorRun = AgentRun


class RunLog:
    """Copy live frames onto a thread so another worker can rejoin.

    Writes go through a fresh session. The graph session is also used by
    read tools, and committing it while a tool is querying raises inside
    the tool.

    Subclasses implement ``_write`` against the product thread table.
    """

    def __init__(self, thread_id: str, run: AgentRun) -> None:
        self.thread_id = thread_id
        self.run = run
        self._last = 0.0
        self._dirty = 0

    def _active(self) -> dict:
        status = "paused" if self.run.status == "paused" else "running"
        return {
            "runId": self.run.run_id,
            "status": status,
            "beat": utcnow().isoformat(),
        }

    async def note(self, *, force: bool = False) -> None:
        self._dirty += 1
        now = time.monotonic()
        if not force and self._dirty < 16 and now - self._last < 0.25:
            return
        await self.flush()

    async def _write(self, *, active_run: dict | None, run_events: list | None) -> None:
        raise NotImplementedError

    async def flush(self) -> None:
        events = [dict(frame) for frame in self.run.events]
        try:
            await self._write(active_run=self._active(), run_events=events)
        except Exception:
            logger.exception("agent run log flush failed")
            return
        self._last = time.monotonic()
        self._dirty = 0

    async def clear(self) -> None:
        try:
            await self._write(active_run=None, run_events=None)
        except Exception:
            logger.exception("agent run log clear failed")


class RunHub:
    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._by_thread: dict[str, str] = {}

    def create(self, user_id: str, thread_id: str | None, run_id: str) -> AgentRun:
        run = AgentRun(run_id, user_id, thread_id)
        self._runs[run.run_id] = run
        if thread_id:
            self._by_thread[thread_id] = run.run_id
        return run

    def bind(self, run: AgentRun, thread_id: str) -> None:
        run.thread_id = thread_id
        self._by_thread[thread_id] = run.run_id

    def get(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def for_thread(self, thread_id: str) -> AgentRun | None:
        run_id = self._by_thread.get(thread_id)
        if not run_id:
            return None
        run = self._runs.get(run_id)
        if run is None or run.status == "finished":
            return None
        return run

    def drop(self, run: AgentRun) -> None:
        self._runs.pop(run.run_id, None)
        if run.thread_id and self._by_thread.get(run.thread_id) == run.run_id:
            self._by_thread.pop(run.thread_id, None)

    def reset(self) -> None:
        self._runs.clear()
        self._by_thread.clear()


def visible_active_run_for(
    hub: RunHub,
    thread_id: str,
    active_run: Any,
    *,
    is_stale_run: Any,
) -> dict | None:
    """Shared ``activeRun`` visibility for thread GET responses."""
    live = hub.for_thread(thread_id)
    if live is not None and live.status in {"running", "paused"}:
        return {"runId": live.run_id, "status": live.status}
    stored = active_run if isinstance(active_run, dict) else None
    if not stored or not stored.get("runId"):
        return None
    local = hub.get(str(stored.get("runId")))
    if local is not None and local.status == "finished":
        return None
    status = stored.get("status")
    if status == "paused":
        return {"runId": str(stored["runId"]), "status": "paused"}
    if status == "running" and not is_stale_run(stored):
        return {"runId": str(stored["runId"]), "status": "running"}
    return None


def should_clear_stored_run_for(
    hub: RunHub,
    thread_id: str,
    active_run: Any,
    *,
    is_stale_run: Any,
) -> bool:
    """Drop a running row whose producer has stopped heartbeating."""
    stored = active_run if isinstance(active_run, dict) else None
    if not stored or stored.get("status") != "running":
        return False
    if not is_stale_run(stored):
        return False
    live = hub.for_thread(thread_id)
    return live is None


def replay_cursor(header: str | None, frames: list | None) -> int:
    rows = frames if isinstance(frames, list) else []
    return cursor_after(header, rows)


async def grace_before_clear() -> None:
    """Leave the event log readable briefly so a rejoin can see ``done``."""
    await asyncio.sleep(_REPLAY_GRACE_SECONDS)
