import time

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.responses import CronDrainOut, CronReminderOut
from app.core.config import settings
from app.core.errors import ApiError
from app.database.db import AnSession
from app.services.auth.rules import cron_decision
from app.services.push import BUDGET_SECONDS, DrainPushService, SendRemindersService

router = APIRouter(prefix="/push", tags=["Cron"])


def _guard(request: Request) -> int | None:
    header = request.headers.get("authorization") or ""
    bearer = header[7:] if header.lower().startswith("bearer ") else None
    return cron_decision(request.method, settings.cron_secret, bearer)


def _stopped(decision: int | None):
    if decision == 405:
        raise ApiError(405, "Method not allowed")
    if decision == 401:
        raise ApiError(401, "Unauthorized")
    if decision == 204 or not settings.push_enabled:
        return Response(status_code=204)
    return None


@router.post("/drain", response_model=CronDrainOut)
async def drain(request: Request, session: AnSession):
    stopped = _stopped(_guard(request))
    if stopped is not None:
        return stopped
    return await DrainPushService(session, time.monotonic() + BUDGET_SECONDS).process()


@router.post("/morning", response_model=CronReminderOut)
async def morning(request: Request, session: AnSession):
    return await _reminder(request, session, "morning")


@router.post("/streak", response_model=CronReminderOut)
async def streak(request: Request, session: AnSession):
    return await _reminder(request, session, "streak")


async def _reminder(request: Request, session: AnSession, kind: str):
    stopped = _stopped(_guard(request))
    if stopped is not None:
        return stopped
    return await SendRemindersService(
        session, kind, time.monotonic() + BUDGET_SECONDS
    ).process()
