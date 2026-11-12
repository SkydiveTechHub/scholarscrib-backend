"""Student realm: preview/accept invites, my centers, leave, Assigned inbox."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import StudentPrincipal, require_student
from app.api.student.cookies import _ip
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.services.centers.admin import CreateAbuseReportService
from app.services.centers.assignments import (
    GetStudentAssignmentService,
    ListAssignedInboxService,
    StartAssignedAttemptService,
)
from app.services.centers.recommendations import (
    ListStudentRecommendationsService,
    UpdateStudentRecommendationStatusService,
)
from app.services.centers.service import (
    AcceptInviteService,
    LeaveCenterService,
    ListMyCentersService,
    PreviewInviteService,
)

router = APIRouter(prefix="/centers", tags=["Student / Centers"])


class AcceptInviteIn(BaseModel):
    accepted: bool = False
    version: str = Field(default="v1", max_length=16)
    acknowledgedVisibility: bool = True
    acknowledgedLeave: bool = True


class RecommendationStatusIn(BaseModel):
    status: Literal["seen", "acted"]


class AbuseReportIn(BaseModel):
    kind: Literal["TUTOR", "INVITE", "RECOMMENDATION"]
    targetId: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3, max_length=1000)
    centerId: str | None = Field(default=None, max_length=64)


@router.get("/invites/{code}")
async def preview_invite(code: str, session: AnSession):
    return await PreviewInviteService(session, code).process()


@router.post("/invites/{code}/accept")
async def accept_invite(
    code: str,
    body: AcceptInviteIn,
    request: Request,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await AcceptInviteService(
        session,
        student.id,
        code,
        body,
        ip=_ip(request),
    ).process()


@router.get("/assigned")
async def list_assigned(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """S-6 Assigned inbox: open assignments by due date."""
    return await ListAssignedInboxService(session, student.id).process()


@router.get("/assignments/{assignment_id}")
async def get_assigned(
    assignment_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetStudentAssignmentService(
        session, student.id, assignment_id
    ).process()


@router.post("/assignments/{assignment_id}/start")
async def start_assigned(
    assignment_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """Start/resume an assignment attempt. D6: skips student tier gates."""
    return await StartAssignedAttemptService(
        session, student.id, assignment_id
    ).process()


@router.get("/recommendations")
async def list_recommendations(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """C-2 / C-3: student recommendations inbox."""
    return await ListStudentRecommendationsService(session, student.id).process()


@router.patch("/recommendations/{recommendation_id}")
async def update_recommendation_status(
    recommendation_id: str,
    body: RecommendationStatusIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await UpdateStudentRecommendationStatusService(
        session, student.id, recommendation_id, body.status
    ).process()


@router.get("")
async def list_my_centers(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await ListMyCentersService(session, student.id).process()


@router.post("/reports", status_code=201)
async def report_center_abuse(
    body: AbuseReportIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """X-7 / R-11: report a tutor, invite, or recommendation."""
    hit(f"center-report:{student.id}", 10, 3600)
    return await CreateAbuseReportService(
        session,
        student.id,
        kind=body.kind,
        target_id=body.targetId,
        reason=body.reason,
        center_id=body.centerId,
    ).process()


@router.post("/{center_id}/leave")
async def leave_center(
    center_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await LeaveCenterService(session, student.id, center_id).process()
