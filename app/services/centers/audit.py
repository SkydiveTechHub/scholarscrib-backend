"""X-2: TutorAccessAudit on every student-result read."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import cuid
from app.database.models import TutorAccessAudit
from app.database.repositories.centers import tutor_access_audits_repository


async def record_tutor_access(
    session: AsyncSession,
    *,
    actor_id: str,
    center_id: str,
    action: str,
    resource: str,
    student_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> TutorAccessAudit:
    row = TutorAccessAudit(
        id=cuid(),
        actor_id=actor_id,
        center_id=center_id,
        student_id=student_id,
        action=action,
        resource=resource,
        meta=meta,
    )
    await tutor_access_audits_repository.add(session, row, flush=True)
    return row
