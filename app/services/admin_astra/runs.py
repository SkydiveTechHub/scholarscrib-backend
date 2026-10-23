"""In-process admin Astra runs. Separate hub from the student tutor."""

from __future__ import annotations

from sqlalchemy import update

from app.database.db import async_session
from app.database.models.admin_chat import AdminChatThread
from app.services.rag.runs import RunHub, RunLog
from app.services.rag.stream_events import is_stale_run

hub = RunHub()


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


def should_clear_stored_run(thread: AdminChatThread) -> bool:
    stored = thread.active_run if isinstance(thread.active_run, dict) else None
    if not stored or stored.get("status") != "running":
        return False
    if not is_stale_run(stored):
        return False
    live = hub.for_thread(thread.id)
    return live is None
