"""Keep today's session stable.

The first dashboard load of a Lagos day composes the session and stores it;
later loads read it back and only stamp newly finished items. A failed write
must not fail the page: the composed session is still returned and the next
load tries again.
"""

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.core.timeutil import lagos_day_start
from app.database.models import DailySession
from app.database.repositories.learning import (
    daily_sessions_repository,
    learning_events_repository,
)
from app.services.learning.session import DayEvent, mark_done, reconcile_plan, summarise


async def todays_session(
    session: AsyncSession,
    student_id: str,
    day: str,
    *,
    compose: Callable[[], Awaitable[list[dict]]],
    plan_status: Mapping[str, str],
    plan_items: Sequence[dict] | None = None,
    now: datetime,
) -> dict:
    row = await daily_sessions_repository.for_day(session, student_id, day)
    items = copy.deepcopy(row.items) if row is not None else await compose()
    # A replan gives plan items new ids; follow them so they can still tick.
    repointed = (
        row is not None and plan_items is not None and reconcile_plan(items, plan_items)
    )
    if not items:
        if repointed:
            await _save(session, student_id, day, row, items)
        return summarise([])
    events = [
        DayEvent(event.topic_id, event.kind)
        for event in await learning_events_repository.since(
            session, student_id, lagos_day_start(day)
        )
    ]
    changed = mark_done(items, events, plan_status, now)
    if row is None or changed or repointed:
        await _save(session, student_id, day, row, items)
    return summarise(items)


async def _save(
    session: AsyncSession,
    student_id: str,
    day: str,
    row: DailySession | None,
    items: list[dict],
) -> None:
    try:
        async with session.begin_nested():
            if row is None:
                await daily_sessions_repository.add(
                    session, DailySession(student_id=student_id, day=day, items=items)
                )
            else:
                # A new list object, so SQLAlchemy sees the JSONB change.
                row.items = items
            await session.flush()
    except Exception:
        logger.exception("Daily session save failed for %s", student_id)
