"""In-process admin Astra runs. Separate hub from the student tutor."""

from __future__ import annotations

from sqlalchemy import update

from app.ai.core.runs import (
    AgentRun,
    RunHub,
    RunLog,
    should_clear_stored_run_for,
    visible_active_run_for,
)
from app.ai.core.stream_events import is_stale_run
from app.database.db import async_session
from app.database.models.admin_chat import AdminChatThread

hub = RunHub()

# Service historically typed runs as TutorRun; AgentRun is the shared type.
TutorRun = AgentRun


class AdminRunLog(RunLog):
    """Copy live frames onto the admin thread so another worker can rejoin."""

    async def _write(self, *, active_run: dict | None, run_events: list | None) -> None:
        async with async_session() as session:
            await session.execute(
                update(AdminChatThread)
                .where(AdminChatThread.id == self.thread_id)
                .values(active_run=active_run, run_events=run_events)
            )
            await session.commit()


def visible_active_run(thread: AdminChatThread) -> dict | None:
    """``activeRun`` for thread GET. Hides a turn this process has already finished."""
    return visible_active_run_for(
        hub, thread.id, thread.active_run, is_stale_run=is_stale_run
    )


def should_clear_stored_run(thread: AdminChatThread) -> bool:
    return should_clear_stored_run_for(
        hub, thread.id, thread.active_run, is_stale_run=is_stale_run
    )
