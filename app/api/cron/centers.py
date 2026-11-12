"""Cron: assignment due reminders (N-1)."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import settings
from app.core.errors import ApiError
from app.database.db import AnSession
from app.services.auth.rules import cron_decision
from app.services.centers.assignments import send_due_reminders

router = APIRouter(prefix="/centers", tags=["Cron"])


def _guard(request: Request) -> int | None:
    header = request.headers.get("authorization") or ""
    bearer = header[7:] if header.lower().startswith("bearer ") else None
    return cron_decision(request.method, settings.cron_secret, bearer)


@router.post("/assignment-due-reminders")
async def assignment_due_reminders(request: Request, session: AnSession):
    decision = _guard(request)
    if decision == 405:
        raise ApiError(405, "Method not allowed")
    if decision == 401:
        raise ApiError(401, "Unauthorized")
    if decision == 503:
        raise ApiError(503, "Cron is not configured")
    if decision == 204:
        return Response(status_code=204)
    return await send_due_reminders(session)
