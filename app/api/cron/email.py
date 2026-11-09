from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import read_bearer
from app.database.db import AnSession
from app.services.auth.rules import cron_decision
from app.services.email import EmailService

router = APIRouter(prefix="/email", tags=["Cron / Email"])


def _auth(request: Request) -> int | None:
    bearer = read_bearer(request.headers.get("authorization"))
    return cron_decision(request.method, settings.cron_secret, bearer)


@router.post("/drain")
async def email_drain(
    request: Request,
    session: AnSession,
):
    decision = _auth(request)
    if decision == 405:
        raise ApiError(405, "Method not allowed")
    if decision == 401:
        raise ApiError(401, "Unauthorized")
    if decision == 503:
        raise ApiError(503, "Cron is not configured")
    if decision == 204:
        return Response(status_code=204)
    result = await EmailService.drain_outbox(session)
    return {"ok": True, **result}
