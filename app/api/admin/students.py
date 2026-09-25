from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin, require_super_admin
from app.api.responses import OkOut, StudentDetailOut, StudentsPageOut
from app.api.schemas import StudentProfileIn, StudentStatusIn, StudentTierIn
from app.database.db import AnSession
from app.database.models import Admin
from app.services.admin import (
    DeleteStudentService,
    ForceSignOutService,
    GetStudentDetailService,
    SearchStudentsService,
    SetStudentStatusService,
    SetStudentTierService,
    UpdateStudentService,
)

router = APIRouter(prefix="/students", tags=["Admin / Students"])


@router.get("", response_model=StudentsPageOut)
async def students(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    q: str | None = None,
    page: int = 1,
    pageSize: int = 20,
):
    return await SearchStudentsService(session, q, page, min(pageSize, 100)).process()


@router.get("/{user_id}", response_model=StudentDetailOut)
async def student_detail(
    user_id: str, session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetStudentDetailService(session, user_id).process()


@router.patch("/{user_id}", response_model=OkOut)
async def update_student(
    user_id: str,
    body: StudentProfileIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await UpdateStudentService(session, admin.id, user_id, body).process()


@router.delete("/{user_id}", response_model=OkOut)
async def delete_student(
    user_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await DeleteStudentService(session, admin.id, user_id).process()


@router.post("/{user_id}/status", response_model=OkOut)
async def student_status(
    user_id: str,
    body: StudentStatusIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await SetStudentStatusService(session, admin.id, user_id, body).process()


@router.post("/{user_id}/tier", response_model=OkOut)
async def student_tier(
    user_id: str,
    body: StudentTierIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await SetStudentTierService(session, admin.id, user_id, body).process()


@router.post("/{user_id}/force-signout", response_model=OkOut)
async def force_signout(
    user_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await ForceSignOutService(session, admin.id, user_id).process()
