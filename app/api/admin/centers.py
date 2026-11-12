"""Admin tutor verify/suspend, reports, audit, withdrawals (X-2/X-6/X-7/D11)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.database.db import AnSession
from app.database.models import Admin
from app.services.centers.admin import (
    GetTutorDetailService,
    ListAbuseReportsService,
    ListTutorAccessAuditService,
    SearchTutorsService,
    SetTutorStatusService,
    UpdateAbuseReportService,
    VerifyTutorService,
)
from app.services.centers.wallet import FulfilWithdrawService, ListWithdrawQueueService

router = APIRouter(prefix="/centers", tags=["Admin / Centers"])


class TutorStatusIn(BaseModel):
    isActive: bool
    reason: str | None = Field(default=None, max_length=500)


class TutorVerifyIn(BaseModel):
    verified: bool = True


class AbuseReportUpdateIn(BaseModel):
    status: Literal["OPEN", "REVIEWING", "RESOLVED", "DISMISSED"]
    note: str | None = Field(default=None, max_length=1000)


class WithdrawFulfilIn(BaseModel):
    fulfil: bool = True
    note: str | None = Field(default=None, max_length=1000)


@router.get("/tutors")
async def list_tutors(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    q: str | None = None,
    page: int = 1,
    pageSize: int = 20,
    status: str | None = None,
    state: str | None = None,
    verified: bool | None = None,
):
    return await SearchTutorsService(
        session,
        q,
        max(page, 1),
        min(max(pageSize, 1), 100),
        status=status,
        state=state,
        verified=verified,
    ).process()


@router.get("/tutors/{user_id}")
async def tutor_detail(
    user_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await GetTutorDetailService(session, user_id).process()


@router.post("/tutors/{user_id}/status")
async def tutor_status(
    user_id: str,
    body: TutorStatusIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await SetTutorStatusService(
        session,
        admin.id,
        user_id,
        is_active=body.isActive,
        reason=body.reason,
    ).process()


@router.post("/tutors/{user_id}/verify")
async def tutor_verify(
    user_id: str,
    body: TutorVerifyIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await VerifyTutorService(
        session, admin.id, user_id, verified=body.verified
    ).process()


@router.get("/reports")
async def list_reports(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    page: int = 1,
    pageSize: int = 50,
    status: str | None = None,
    kind: str | None = None,
):
    return await ListAbuseReportsService(
        session,
        max(page, 1),
        min(max(pageSize, 1), 100),
        status=status,
        kind=kind,
    ).process()


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: str,
    body: AbuseReportUpdateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await UpdateAbuseReportService(
        session,
        admin.id,
        report_id,
        status=body.status,
        note=body.note,
    ).process()


@router.get("/audit")
async def tutor_access_audit(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    page: int = 1,
    pageSize: int = 50,
    centerId: Annotated[str | None, Query()] = None,
    actorId: Annotated[str | None, Query()] = None,
):
    """X-2: summary of tutor access to student results."""
    return await ListTutorAccessAuditService(
        session,
        max(page, 1),
        min(max(pageSize, 1), 100),
        center_id=centerId,
        actor_id=actorId,
    ).process()


@router.get("/withdrawals")
async def list_withdrawals(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    page: int = 1,
    pageSize: int = 50,
    status: str | None = None,
):
    """D11: ops queue for referral wallet withdraw requests."""
    return await ListWithdrawQueueService(
        session,
        page=max(page, 1),
        page_size=min(max(pageSize, 1), 100),
        status=status,
    ).process()


@router.post("/withdrawals/{request_id}/fulfil")
async def fulfil_withdrawal(
    request_id: str,
    body: WithdrawFulfilIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    """Mark a withdraw request fulfilled (or rejected → refund hold)."""
    return await FulfilWithdrawService(
        session,
        admin.id,
        request_id,
        fulfil=body.fulfil,
        note=body.note,
    ).process()
