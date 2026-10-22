"""In-process tutor runs that outlive the HTTP connection that started them."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from sqlalchemy import update

from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.db import async_session
from app.database.models.chat import ChatThread
from app.services.rag.stream_events import cursor_after, is_stale_run

_REPLAY_GRACE_SECONDS = 1.0


class TutorRun:
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


class RunLog:
    """Copy the live frames onto the thread so another worker can rejoin.

    Writes go through a fresh session. The graph session is also used by
    read tools, and committing it while a tool is querying raises inside
    the tool.
    """

    def __init__(self, thread_id: str, run: TutorRun) -> None:
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
        async with async_session() as session:
            await session.execute(
                update(ChatThread)
                .where(ChatThread.id == self.thread_id)
                .values(active_run=active_run, run_events=run_events)
            )
            await session.commit()

    async def flush(self) -> None:
        events = [dict(frame) for frame in self.run.events]
        try:
            await self._write(active_run=self._active(), run_events=events)
        except Exception:
            logger.exception("tutor run log flush failed")
            return
        self._last = time.monotonic()
        self._dirty = 0

    async def clear(self) -> None:
        try:
            await self._write(active_run=None, run_events=None)
        except Exception:
            logger.exception("tutor run log clear failed")


class RunHub:
    def __init__(self) -> None:
        self._runs: dict[str, TutorRun] = {}
        self._by_thread: dict[str, str] = {}

    def create(self, user_id: str, thread_id: str | None, run_id: str) -> TutorRun:
        run = TutorRun(run_id, user_id, thread_id)
        self._runs[run.run_id] = run
        if thread_id:
            self._by_thread[thread_id] = run.run_id
        return run

    def bind(self, run: TutorRun, thread_id: str) -> None:
        run.thread_id = thread_id
        self._by_thread[thread_id] = run.run_id

    def get(self, run_id: str) -> TutorRun | None:
        return self._runs.get(run_id)

    def for_thread(self, thread_id: str) -> TutorRun | None:
        run_id = self._by_thread.get(thread_id)
        if not run_id:
            return None
        run = self._runs.get(run_id)
        if run is None or run.status == "finished":
            return None
        return run

    def drop(self, run: TutorRun) -> None:
        self._runs.pop(run.run_id, None)
        if run.thread_id and self._by_thread.get(run.thread_id) == run.run_id:
            self._by_thread.pop(run.thread_id, None)

    def reset(self) -> None:
        self._runs.clear()
        self._by_thread.clear()


hub = RunHub()


def visible_active_run(thread: ChatThread) -> dict | None:
    """``activeRun`` for thread GET. Hides a turn this process has already finished."""
    live = hub.for_thread(thread.id)
    if live is not None and live.status in {"running", "paused"}:
        return {"runId": live.run_id, "status": live.status}
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
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


def should_clear_stored_run(thread: ChatThread) -> bool:
    """Drop a running row whose producer has stopped heartbeating."""
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    if not stored or stored.get("status") != "running":
        return False
    if not is_stale_run(stored):
        return False
    live = hub.for_thread(thread.id)
    return live is None


def replay_cursor(header: str | None, frames: list | None) -> int:
    rows = frames if isinstance(frames, list) else []
    return cursor_after(header, rows)


async def grace_before_clear() -> None:
    """Leave the event log readable briefly so a rejoin can see ``done``."""
    await asyncio.sleep(_REPLAY_GRACE_SECONDS)
