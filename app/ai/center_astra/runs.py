"""In-process center Astra runs. Separate hub from student tutor and admin."""

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
from app.database.models.center_chat import CenterChatThread

hub = RunHub()

TutorRun = AgentRun


class CenterAstraRunLog(RunLog):
    async def _write(self, *, active_run: dict | None, run_events: list | None) -> None:
        async with async_session() as session:
            await session.execute(
                update(CenterChatThread)
                .where(CenterChatThread.id == self.thread_id)
                .values(active_run=active_run, run_events=run_events)
            )
            await session.commit()


def visible_active_run(thread: CenterChatThread) -> dict | None:
    return visible_active_run_for(
        hub, thread.id, thread.active_run, is_stale_run=is_stale_run
    )


def should_clear_stored_run(thread: CenterChatThread) -> bool:
    return should_clear_stored_run_for(
        hub, thread.id, thread.active_run, is_stale_run=is_stale_run
    )
