from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import (
    NotificationsOut,
    OkOut,
    UnreadCountOut,
)
from app.database.db import AnSession
from app.services.notifications import (
    ListNotificationsService,
    MarkAllNotificationsReadService,
    MarkNotificationReadService,
    UnreadCountService,
)

router = APIRouter(prefix="/notifications", tags=["Student / Notifications"])


@router.get("", response_model=NotificationsOut)
async def list_notifications(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query()] = None,
):
    return await ListNotificationsService(
        session, student.id, limit=limit, cursor=cursor
    ).process()


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await UnreadCountService(session, student.id).process()


@router.post("/read-all", response_model=OkOut)
async def read_all(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await MarkAllNotificationsReadService(session, student.id).process()


@router.post("/{notification_id}/read", response_model=OkOut)
async def read_one(
    notification_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await MarkNotificationReadService(
        session, student.id, notification_id
    ).process()
