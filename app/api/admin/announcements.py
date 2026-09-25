from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import require_admin
from app.api.responses import (
    AnnouncementCreatedOut,
    AnnouncementOut,
    AnnouncementTestOut,
    AudiencePreviewOut,
    OkOut,
)
from app.api.schemas import (
    AnnouncementCreateIn,
    AnnouncementPreviewIn,
    AnnouncementTestIn,
)
from app.core.config import settings
from app.database.db import AnSession
from app.database.models import Admin
from app.services.console import (
    CancelAnnouncementService,
    CreateAnnouncementService,
    ListAnnouncementsService,
    PreviewAnnouncementAudienceService,
    TestAnnouncementService,
)
from app.services.push import DrainPushService

router = APIRouter(prefix="/announcements", tags=["Admin / Announcements"])


@router.get("", response_model=list[AnnouncementOut])
async def admin_announcements(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await ListAnnouncementsService(session).process()


@router.post("", status_code=201, response_model=AnnouncementCreatedOut)
async def create_announcement(
    body: AnnouncementCreateIn,
    background: BackgroundTasks,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    created = await CreateAnnouncementService(session, admin.id, body).process()
    if created["recipientCount"] and settings.push_enabled:
        background.add_task(_drain_push)
    return created


@router.post("/preview", response_model=AudiencePreviewOut)
async def preview_announcement(
    body: AnnouncementPreviewIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await PreviewAnnouncementAudienceService(session, body.audience).process()


@router.post("/test", response_model=AnnouncementTestOut)
async def test_announcement(
    body: AnnouncementTestIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await TestAnnouncementService(session, admin.id, body).process()


@router.post("/{announcement_id}/cancel", response_model=OkOut)
async def cancel_announcement(
    announcement_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await CancelAnnouncementService(session, admin.id, announcement_id).process()


async def _drain_push() -> None:
    import time

    from app.database.db import get_session

    async with get_session() as session:
        try:
            await DrainPushService(session, time.monotonic() + 40).process()
            await session.commit()
        except Exception:
            await session.rollback()
