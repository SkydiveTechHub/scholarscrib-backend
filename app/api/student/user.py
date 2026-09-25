from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import (
    AvatarOut,
    CompleteProfileOut,
    MessageOut,
    PreferencesOut,
    ProfileUpdateOut,
    RevokedOut,
    SettingsProfileOut,
)
from app.api.schemas import (
    CompleteProfileIn,
    DeviceIn,
    PasswordIn,
    PreferencePatch,
    ProfilePatch,
)
from app.api.student.cookies import _cookie
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.database.repositories.identity import users_repository
from app.services.auth import (
    ChangePasswordService,
    CompleteProfileService,
    GetSettingsProfileService,
    RevokeDevicesService,
    SetAvatarService,
    UpdateNotificationPreferencesService,
    UpdateProfileService,
)

router = APIRouter(prefix="/user", tags=["Student / User"])


@router.patch("/profile", response_model=ProfileUpdateOut)
async def update_profile(
    body: ProfilePatch,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await UpdateProfileService(session, student.id, body).process()


@router.get("/profile", response_model=SettingsProfileOut)
async def get_profile(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetSettingsProfileService(session, student.id).process()


@router.post("/complete-profile", response_model=CompleteProfileOut)
async def complete_profile(
    body: CompleteProfileIn,
    response: Response,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    result = await CompleteProfileService(
        session, student.id, body, student.device_id
    ).process()
    if result.get("accessToken"):
        _cookie(response, result["accessToken"])
    return {"message": result["message"]}


@router.post("/password", response_model=MessageOut)
async def change_password(
    body: PasswordIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    user = await users_repository.by_id(session, student.id)
    if user is None:
        raise ApiError(404, "Account not found")
    return await ChangePasswordService(
        session, user, body.currentPassword, body.newPassword, student.device_id
    ).process()


@router.post("/devices", response_model=RevokedOut)
async def revoke_device(
    body: DeviceIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await RevokeDevicesService(
        session, student.id, student.device_id, body
    ).process()


@router.patch("/notification-preferences", response_model=PreferencesOut)
async def preferences(
    body: PreferencePatch,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await UpdateNotificationPreferencesService(
        session, student.id, body
    ).process()


@router.post("/avatar", response_model=AvatarOut)
async def avatar(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    file: Annotated[UploadFile, File()],
):
    hit(f"avatar:{student.id}", 10, 3600)
    data = await file.read()
    return await SetAvatarService(
        session, student.id, file.content_type, data
    ).process()
