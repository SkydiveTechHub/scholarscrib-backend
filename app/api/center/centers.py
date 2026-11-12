"""Tutor realm: centers, invites, roster, groups, facilitators, billing."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import TutorPrincipal, require_tutor
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.database.repositories.identity import users_repository
from app.domain import CenterLevel, NigerianState
from app.services.centers.billing import (
    CenterCheckoutService,
    GetCenterBillingService,
    VerifyCenterPaymentService,
)
from app.services.centers.facilitators import (
    AcceptFacilitatorInviteService,
    ArchiveCenterService,
    InviteFacilitatorService,
    ListFacilitatorsService,
    PreviewFacilitatorInviteService,
    RevokeFacilitatorService,
)
from app.services.centers.service import (
    AddGroupMembersService,
    CreateCenterService,
    CreateGroupService,
    CreateInviteService,
    DeleteGroupService,
    GetCenterService,
    ListCentersService,
    ListGroupsService,
    ListInvitesService,
    RegenerateInviteService,
    RemoveGroupMemberService,
    RemoveStudentService,
    RevokeInviteService,
    RosterService,
    UpdateCenterService,
    UpdateGroupService,
)

router = APIRouter(tags=["Center / Centers"])


class CenterCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    state: NigerianState | None = None
    lga: str | None = Field(default=None, max_length=80)
    subjects: list[str] = Field(default_factory=list, max_length=40)
    levels: list[CenterLevel] = Field(default_factory=list, max_length=10)


class CenterUpdateIn(BaseModel):
    """Partial profile update. Omitted fields are left unchanged."""

    name: str | None = Field(default=None, min_length=2, max_length=80)
    state: NigerianState | None = None
    lga: str | None = Field(default=None, max_length=80)
    subjects: list[str] | None = Field(default=None, max_length=40)
    levels: list[CenterLevel] | None = Field(default=None, max_length=10)


class InviteCreateIn(BaseModel):
    """Open link invite, or email-bound invite when `email` is set (R-2)."""

    email: EmailStr | None = None
    expiresInDays: int | None = Field(default=None, ge=1, le=365)
    useLimit: int | None = Field(default=None, ge=1, le=10_000)


class GroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class GroupUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class GroupMembersIn(BaseModel):
    membershipIds: list[str] = Field(min_length=1, max_length=200)


class CenterCheckoutIn(BaseModel):
    plan: str = Field(default="PRO", max_length=16)


class CenterVerifyIn(BaseModel):
    reference: str = Field(min_length=4, max_length=128)


class FacilitatorInviteCreateIn(BaseModel):
    email: EmailStr | None = None
    expiresInDays: int | None = Field(default=14, ge=1, le=90)


@router.post("/centers", status_code=201)
async def create_center(
    body: CenterCreateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await CreateCenterService(session, tutor.id, body).process()


@router.get("/centers")
async def list_centers(
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ListCentersService(session, tutor.id).process()


@router.get("/centers/{center_id}")
async def get_center(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await GetCenterService(session, tutor.id, center_id).process()


@router.patch("/centers/{center_id}")
async def update_center(
    center_id: str,
    body: CenterUpdateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await UpdateCenterService(session, tutor.id, center_id, body).process()


@router.post("/centers/{center_id}/invites", status_code=201)
async def create_invite(
    center_id: str,
    body: InviteCreateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await CreateInviteService(session, tutor, center_id, body).process()


@router.get("/centers/{center_id}/invites")
async def list_invites(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ListInvitesService(session, tutor.id, center_id).process()


@router.post("/centers/{center_id}/invites/{invite_id}/revoke")
async def revoke_invite(
    center_id: str,
    invite_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await RevokeInviteService(session, tutor.id, center_id, invite_id).process()


@router.post("/centers/{center_id}/invites/{invite_id}/regenerate", status_code=201)
async def regenerate_invite(
    center_id: str,
    invite_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await RegenerateInviteService(session, tutor, center_id, invite_id).process()


@router.get("/centers/{center_id}/roster")
async def roster(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await RosterService(session, tutor.id, center_id).process()


@router.delete("/centers/{center_id}/roster/{membership_id}")
async def remove_student(
    center_id: str,
    membership_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await RemoveStudentService(
        session, tutor.id, center_id, membership_id
    ).process()


@router.get("/centers/{center_id}/groups")
async def list_groups(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ListGroupsService(session, tutor.id, center_id).process()


@router.post("/centers/{center_id}/groups", status_code=201)
async def create_group(
    center_id: str,
    body: GroupCreateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await CreateGroupService(session, tutor.id, center_id, body).process()


@router.patch("/centers/{center_id}/groups/{group_id}")
async def update_group(
    center_id: str,
    group_id: str,
    body: GroupUpdateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await UpdateGroupService(
        session, tutor.id, center_id, group_id, body
    ).process()


@router.delete("/centers/{center_id}/groups/{group_id}")
async def delete_group(
    center_id: str,
    group_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await DeleteGroupService(session, tutor.id, center_id, group_id).process()


@router.post("/centers/{center_id}/groups/{group_id}/members")
async def add_group_members(
    center_id: str,
    group_id: str,
    body: GroupMembersIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await AddGroupMembersService(
        session, tutor.id, center_id, group_id, body
    ).process()


@router.delete("/centers/{center_id}/groups/{group_id}/members/{membership_id}")
async def remove_group_member(
    center_id: str,
    group_id: str,
    membership_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await RemoveGroupMemberService(
        session, tutor.id, center_id, group_id, membership_id
    ).process()


@router.get("/centers/{center_id}/facilitators")
async def list_facilitators(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ListFacilitatorsService(session, tutor.id, center_id).process()


@router.post("/centers/{center_id}/facilitators/invites", status_code=201)
async def invite_facilitator(
    center_id: str,
    body: FacilitatorInviteCreateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await InviteFacilitatorService(session, tutor, center_id, body).process()


@router.post("/centers/{center_id}/facilitators/{membership_id}/revoke")
async def revoke_facilitator(
    center_id: str,
    membership_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await RevokeFacilitatorService(
        session,
        tutor.id,
        center_id,
        membership_id=membership_id,
    ).process()


@router.post("/centers/{center_id}/facilitators/invites/{invite_id}/revoke")
async def revoke_facilitator_invite(
    center_id: str,
    invite_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await RevokeFacilitatorService(
        session,
        tutor.id,
        center_id,
        invite_id=invite_id,
    ).process()


@router.get("/facilitator-invites/{code}")
async def preview_facilitator_invite(code: str, session: AnSession):
    return await PreviewFacilitatorInviteService(session, code).process()


@router.post("/facilitator-invites/{code}/accept")
async def accept_facilitator_invite(
    code: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    user = await users_repository.by_id(session, tutor.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await AcceptFacilitatorInviteService(session, user, code).process()


@router.post("/centers/{center_id}/archive")
async def archive_center(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ArchiveCenterService(session, tutor.id, center_id).process()


@router.get("/centers/{center_id}/billing")
async def center_billing(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await GetCenterBillingService(session, tutor.id, center_id).process()


@router.post("/centers/{center_id}/billing/checkout")
async def center_checkout(
    center_id: str,
    body: CenterCheckoutIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    hit(f"center-checkout:{tutor.id}", 10, 60)
    user = await users_repository.by_id(session, tutor.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await CenterCheckoutService(session, user, center_id, body.plan).process()


@router.post("/centers/{center_id}/billing/verify")
async def center_verify_payment(
    center_id: str,
    body: CenterVerifyIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    hit(f"center-billing-verify:{tutor.id}", 20, 60)
    return await VerifyCenterPaymentService(
        session, tutor.id, center_id, body.reference
    ).process()
