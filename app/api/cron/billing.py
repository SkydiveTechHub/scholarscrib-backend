from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import read_bearer
from app.database.db import AnSession
from app.services.auth.rules import cron_decision
from app.services.billing import BillingRemindersService, PaystackReconcileService

router = APIRouter(prefix="/billing", tags=["Cron / Billing"])


def _auth(request: Request) -> int | None:
    bearer = read_bearer(request.headers.get("authorization"))
    return cron_decision(request.method, settings.cron_secret, bearer)


def _reject(decision: int | None) -> Response | None:
    if decision == 405:
        raise ApiError(405, "Method not allowed")
    if decision == 401:
        raise ApiError(401, "Unauthorized")
    if decision == 503:
        raise ApiError(503, "Cron is not configured")
    if decision == 204:
        return Response(status_code=204)
    return None


@router.post("/reconcile")
async def billing_reconcile(
    request: Request,
    session: AnSession,
    _status: int | None = Depends(_auth),
):
    """Advance next_billing_at from Paystack when renewal webhooks were missed."""
    early = _reject(_status)
    if early is not None:
        return early
    result = await PaystackReconcileService(session).process()
    return {"ok": True, **result}


@router.post("/reminders")
async def billing_reminders(
    request: Request,
    session: AnSession,
    _status: int | None = Depends(_auth),
):
    early = _reject(_status)
    if early is not None:
        return early
    # Reconcile first so a charged-but-missed renewal is not downgraded.
    reconcile = await PaystackReconcileService(session).process()
    sent = await BillingRemindersService(session).process()
    return {"ok": True, "reconcile": reconcile, "sent": sent}
