"""Center AI token pool + Paystack pack top-up (M6 / AI-5)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import TutorPrincipal, require_tutor
from app.api.schemas import CenterTokenCheckoutIn, CenterTokenVerifyIn
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.database.repositories.identity import users_repository
from app.services.centers.tokens import (
    CenterTokenCheckoutService,
    GetCenterTokenPoolService,
    VerifyCenterTokenPaymentService,
    list_token_packs,
)

router = APIRouter(tags=["Center / Tokens"])


@router.get("/centers/{center_id}/tokens")
async def get_token_pool(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """Balance + packs + recent ledger. Facilitators can read (same pool)."""
    return await GetCenterTokenPoolService(session, tutor.id, center_id).process()


@router.get("/centers/{center_id}/tokens/packs")
async def list_packs(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    from app.services.centers.access import load_staff_center

    await load_staff_center(session, center_id, tutor.id)
    return {"packs": list_token_packs()}


@router.post("/centers/{center_id}/tokens/checkout")
async def token_checkout(
    center_id: str,
    body: CenterTokenCheckoutIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """Owner-only Paystack one-off for a token pack."""
    hit(f"center-token-checkout:{tutor.id}", 10, 60)
    user = await users_repository.by_id(session, tutor.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await CenterTokenCheckoutService(
        session, user, center_id, body.packCode
    ).process()


@router.post("/centers/{center_id}/tokens/verify")
async def token_verify(
    center_id: str,
    body: CenterTokenVerifyIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    hit(f"center-token-verify:{tutor.id}", 20, 60)
    return await VerifyCenterTokenPaymentService(
        session, tutor.id, center_id, body.reference
    ).process()
