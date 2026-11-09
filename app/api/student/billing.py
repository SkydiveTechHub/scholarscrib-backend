import hashlib
import json
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
from app.database.repositories.billing import subscriptions_repository
from app.database.repositories.identity import users_repository
from app.services.billing import (
    ApplyChargeSuccessService,
    CancelSubscriptionService,
    CheckoutService,
    ForgetPaystackEventService,
    HandleLagoWebhookService,
    ListPublicPlansService,
    RecordLagoEventService,
    RecordPaystackEventService,
    ResumeSubscriptionService,
    VerifyPaymentService,
    VerifyPaystackSignatureService,
)
from app.services.billing.lago_client import verify_hmac_signature

router = APIRouter(prefix="/billing", tags=["Student / Billing"])


@router.get("/plans")
async def list_plans(session: AnSession):
    """Public catalog for landing /pricing and settings."""
    return {"plans": await ListPublicPlansService(session).process()}


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
    period = getattr(body, "period", None) or "MONTHLY"
    return await CheckoutService(session, user, body.tier, period).process()


@router.post("/cancel")
async def cancel_subscription(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """End renewal: keep paid access until next_billing_at, then Freemium."""
    hit(f"billing-cancel:{student.id}", 5, 60)
    user = await users_repository.by_id(session, student.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await CancelSubscriptionService(session, user).process()


@router.post("/resume")
async def resume_subscription(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """Undo a scheduled cancel while the paid term is still live."""
    hit(f"billing-resume:{student.id}", 5, 60)
    user = await users_repository.by_id(session, student.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await ResumeSubscriptionService(session, user).process()


def _frontend_billing_url(status: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}/settings/billing?status={status}"


def _settle_status(outcome: str) -> str:
    if outcome in {"activate", "already-applied"}:
        return "success"
    if outcome == "not-successful":
        return "failed"
    return "pending"


@router.get("/verify")
async def billing_verify(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    reference: str | None = None,
):
    """Settle a Paystack return for the signed-in student (JSON for the frontend)."""
    if not reference:
        return {"status": "missing"}
    row = await subscriptions_repository.by_reference(session, reference)
    if row is not None and row.user_id != student.id:
        raise ApiError(403, "That payment does not belong to this account")
    transaction = await VerifyPaymentService(session, reference).process()
    if not transaction:
        return {"status": "pending"}
    outcome = await ApplyChargeSuccessService(session, reference, transaction).process()
    return {"status": _settle_status(outcome), "outcome": outcome}


@router.get("/callback")
async def billing_callback(
    session: AnSession, reference: str | None = None
) -> RedirectResponse:
    """Legacy Paystack callback — redirects to the frontend billing page."""
    if not reference:
        return RedirectResponse(_frontend_billing_url("missing"))
    transaction = await VerifyPaymentService(session, reference).process()
    if not transaction:
        return RedirectResponse(_frontend_billing_url("pending"))
    outcome = await ApplyChargeSuccessService(session, reference, transaction).process()
    return RedirectResponse(_frontend_billing_url(_settle_status(outcome)))


def _paystack_event_key(event_type: str, payload: dict, data) -> str | None:
    """Idempotency key — prefer Paystack event/object ids so repeats are unique."""
    # Top-level id when Paystack includes one (rare); still useful.
    top_id = payload.get("id")
    if top_id is not None:
        return f"{event_type}:{top_id}"
    if isinstance(data, list):
        # Batch payloads (e.g. expiring_cards): hash contents, not just length.
        digest = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()[:24]
        return f"batch:{event_type}:{digest}"
    if not isinstance(data, dict):
        return None
    # Prefer reference / subscription_code (+ stamp) over bare data["id"].
    # Paystack reuses the same subscription id across cancel → resume → cancel;
    # a key of only event_type:id would drop the second disable as a duplicate.
    if data.get("reference"):
        return f"{event_type}:{data['reference']}"
    if data.get("subscription_code"):
        stamp = data.get("updated_at") or data.get("created_at") or ""
        return f"{event_type}:{data['subscription_code']}:{stamp}"
    sub = data.get("subscription") or {}
    if isinstance(sub, dict) and sub.get("subscription_code"):
        stamp = (
            sub.get("updated_at") or data.get("paid_at") or data.get("created_at") or ""
        )
        return f"{event_type}:{sub['subscription_code']}:{stamp}"
    obj_id = data.get("id")
    if obj_id is not None:
        stamp = (
            data.get("updated_at")
            or data.get("created_at")
            or payload.get("created_at")
            or ""
        )
        if stamp:
            return f"{event_type}:{obj_id}:{stamp}"
        # No stamp → fall through rather than lock out re-cancellation.
    txn = data.get("transaction") or {}
    if isinstance(txn, dict) and txn.get("reference"):
        return f"{event_type}:{txn['reference']}"
    invoice = data.get("invoice_code")
    if invoice:
        return f"{event_type}:{invoice}"
    return None


@router.post("/paystack/webhook", response_model=WebhookOut)
@router.post("/webhook", response_model=WebhookOut, include_in_schema=False)
async def billing_webhook(request: Request, session: AnSession):
    from app.services.billing.providers import get_billing_provider

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
    event_type = payload.get("event") or payload.get("type") or "unknown"
    event_key = _paystack_event_key(event_type, payload, data)
    if not event_key:
        return {"received": True}
    inserted = await RecordPaystackEventService(
        session, event_key, event_type, payload
    ).process()
    if not inserted:
        return {"received": True, "duplicate": True}

    provider = get_billing_provider()
    try:
        # Provider-specific subscription lifecycle (Paystack) or no-op (Lago).
        handled = await provider.handle_paystack_event(session, event_type, data)
        # Renewal charge.success returns a result; first-checkout PENDING
        # rows return None so we fall through to ApplyChargeSuccessService.
        if handled is not None and (
            event_type != "charge.success" or handled.get("renewal")
        ):
            return {"received": True, **handled}

        if event_type != "charge.success":
            return {"received": True}

        reference = (
            data.get("reference") if isinstance(data, dict) else None
        ) or event_key
        transaction = await VerifyPaymentService(session, reference).process()
        if not transaction:
            raise ApiError(500, "Could not verify transaction")
        outcome = await ApplyChargeSuccessService(
            session, reference, transaction
        ).process()
    except Exception as exc:
        await ForgetPaystackEventService(session, event_key, event_type).process()
        raise ApiError(500, "Could not apply payment") from exc
    return {"received": True, "outcome": outcome}


@router.post("/lago/webhook", response_model=WebhookOut)
async def lago_webhook(request: Request, session: AnSession):
    raw = await request.body()
    signature = request.headers.get("x-lago-signature")
    algo = (request.headers.get("x-lago-signature-algorithm") or "hmac").lower()
    # Reject unknown algorithms so attackers cannot skip verification by
    # sending e.g. x-lago-signature-algorithm: sha256.
    if algo != "hmac":
        raise ApiError(401, f"Unsupported signature algorithm: {algo}")
    # Allow unsigned only in local/dev when hmac key unset and Lago disabled.
    must_verify = bool(settings.lago_hmac_key or settings.lago_enabled)
    if must_verify and not verify_hmac_signature(
        raw, signature, settings.lago_hmac_key
    ):
        raise ApiError(401, "Invalid Lago signature")
    try:
        payload = await request.json()
    except Exception as exc:
        raise ApiError(400, "Malformed JSON") from exc

    webhook_type = payload.get("webhook_type") or "unknown"
    unique = request.headers.get("x-lago-unique-key")
    if not unique:
        object_type = payload.get("object_type") or ""
        obj = payload.get(object_type) if object_type else None
        if isinstance(obj, dict):
            unique = str(obj.get("lago_id") or obj.get("id") or "")
        if not unique:
            unique = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()[:24]
    event_key = f"{unique}:{webhook_type}"
    inserted = await RecordLagoEventService(
        session, event_key, payload.get("webhook_type") or "", payload
    ).process()
    if not inserted:
        return {"received": True, "duplicate": True}
    result = await HandleLagoWebhookService(session, payload).process()
    return {"received": True, **result}
