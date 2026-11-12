"""Owner referral wallet: balance, spend → tokens, withdraw requests (M7)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import TutorPrincipal, require_tutor
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.database.repositories.identity import users_repository
from app.services.centers.wallet import (
    GetOwnerWalletService,
    RequestWithdrawService,
    SpendWalletOnTokensService,
)

router = APIRouter(tags=["Center / Wallet"])


class SpendWalletTokensIn(BaseModel):
    packCode: str = Field(min_length=1, max_length=64)


class WithdrawRequestIn(BaseModel):
    amountKobo: int = Field(gt=0)
    payoutDetails: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=500)


@router.get("/wallet")
async def get_wallet(
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """Owner wallet balance + ledger + withdraw history."""
    return await GetOwnerWalletService(session, tutor.id).process()


@router.post("/centers/{center_id}/wallet/spend-tokens")
async def spend_wallet_tokens(
    center_id: str,
    body: SpendWalletTokensIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """Owner-only: apply wallet balance to a center token pack."""
    hit(f"center-wallet-spend:{tutor.id}", 20, 60)
    return await SpendWalletOnTokensService(
        session, tutor.id, center_id, body.packCode
    ).process()


@router.post("/wallet/withdraw")
async def request_withdraw(
    body: WithdrawRequestIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """Owner withdraw request (ops fulfilment; requires tutor verification)."""
    hit(f"center-wallet-withdraw:{tutor.id}", 5, 60)
    user = await users_repository.by_id(session, tutor.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await RequestWithdrawService(
        session,
        user,
        body.amountKobo,
        payout_details=body.payoutDetails,
        note=body.note,
    ).process()
