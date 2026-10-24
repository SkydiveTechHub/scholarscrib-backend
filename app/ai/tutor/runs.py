"""Student tutor run hub and ChatThread persistence."""

from __future__ import annotations

from sqlalchemy import update

from app.ai.core.runs import (
    AgentRun,
    RunHub,
    grace_before_clear,
    replay_cursor,
    should_clear_stored_run_for,
    visible_active_run_for,
)
from app.ai.core.runs import (
    RunLog as BaseRunLog,
)
from app.ai.core.stream_events import is_stale_run
from app.database.db import async_session
from app.database.models.chat import ChatThread

# Re-export alias used throughout the tutor service.
TutorRun = AgentRun

hub = RunHub()


class RunLog(BaseRunLog):
    """Persist live frames onto ``ChatThread`` for reconnect / replay."""

    async def _write(self, *, active_run: dict | None, run_events: list | None) -> None:
        async with async_session() as session:
            await session.execute(
                update(ChatThread)
                .where(ChatThread.id == self.thread_id)
                .values(active_run=active_run, run_events=run_events)
            )
            await session.commit()


TutorRunLog = RunLog


def visible_active_run(thread: ChatThread) -> dict | None:
    return visible_active_run_for(
        hub, thread.id, thread.active_run, is_stale_run=is_stale_run
    )


def should_clear_stored_run(thread: ChatThread) -> bool:
    return should_clear_stored_run_for(
        hub, thread.id, thread.active_run, is_stale_run=is_stale_run
    )


__all__ = [
    "TutorRun",
    "AgentRun",
    "RunLog",
    "TutorRunLog",
    "hub",
    "visible_active_run",
    "should_clear_stored_run",
    "replay_cursor",
    "grace_before_clear",
]
