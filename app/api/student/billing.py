from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import CheckoutOut, WebhookOut
from app.api.schemas import CheckoutIn
from app.core.config import settings
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.database.repositories.identity import users_repository
from app.services.billing import (
    ApplyChargeSuccessService,
    CheckoutService,
    ForgetPaystackEventService,
    RecordPaystackEventService,
    VerifyPaymentService,
    VerifyPaystackSignatureService,
)

router = APIRouter(prefix="/billing", tags=["Student / Billing"])


@router.post("/checkout", response_model=CheckoutOut)
async def checkout(
    body: CheckoutIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"billing-checkout:{student.id}", 10, 60)
    user = await users_repository.by_id(session, student.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await CheckoutService(session, user, body.tier, body.period).process()


@router.get("/callback")
async def billing_callback(
    session: AnSession, reference: str | None = None
) -> RedirectResponse:
    base = f"{settings.app_url.rstrip('/')}/settings/billing"
    if not reference:
        return RedirectResponse(f"{base}?status=missing")
    transaction = await VerifyPaymentService(session, reference).process()
    if not transaction:
        return RedirectResponse(f"{base}?status=pending")
    outcome = await ApplyChargeSuccessService(session, reference, transaction).process()
    if outcome in {"activate", "already-applied"}:
        return RedirectResponse(f"{base}?status=success")
    if outcome == "not-successful":
        return RedirectResponse(f"{base}?status=failed")
    return RedirectResponse(f"{base}?status=pending")


@router.post("/webhook", response_model=WebhookOut)
async def billing_webhook(request: Request, session: AnSession):
    raw = await request.body()
    if not await VerifyPaystackSignatureService(
        raw, request.headers.get("x-paystack-signature")
    ).process():
        raise ApiError(401, "Invalid signature")
    try:
        payload = await request.json()
    except Exception as exc:
        raise ApiError(400, "Malformed JSON") from exc
    data = payload.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return {"received": True}
    event_type = payload.get("event") or payload.get("type") or "unknown"
    inserted = await RecordPaystackEventService(
        session, reference, event_type, payload
    ).process()
    if not inserted:
        return {"received": True, "duplicate": True}
    if event_type != "charge.success":
        return {"received": True}
    try:
        transaction = await VerifyPaymentService(session, reference).process()
        if not transaction:
            raise ApiError(500, "Could not verify transaction")
        outcome = await ApplyChargeSuccessService(
            session, reference, transaction
        ).process()
    except Exception as exc:
        await ForgetPaystackEventService(session, reference, event_type).process()
        raise ApiError(500, "Could not apply payment") from exc
    return {"received": True, "outcome": outcome}
