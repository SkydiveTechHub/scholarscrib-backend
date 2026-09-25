from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import AnnouncementsOut, OkOut
from app.core.errors import ApiError
from app.database.db import AnSession
from app.database.repositories.identity import users_repository
from app.services.console import (
    DismissAnnouncementService,
    GetStudentAnnouncementsService,
)

router = APIRouter(prefix="/announcements", tags=["Student / Announcements"])


@router.get("", response_model=AnnouncementsOut)
async def announcements(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    user = await users_repository.by_id(session, student.id)
    if user is None:
        raise ApiError(401, "Unauthorized")
    return await GetStudentAnnouncementsService(session, user).process()


@router.post("/{announcement_id}/dismiss", response_model=OkOut)
async def dismiss_announcement(
    announcement_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await DismissAnnouncementService(
        session, student.id, announcement_id
    ).process()
