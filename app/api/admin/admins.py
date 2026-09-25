from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_owner
from app.api.responses import AdminCreatedOut, AdminsOut, OkOut
from app.api.schemas import ActiveIn, AdminCreateIn
from app.database.db import AnSession
from app.database.models import Admin
from app.database.repositories.admin import admins_repository
from app.services.admin import CreateAdminService, SetAdminStatusService, admin_row

router = APIRouter(prefix="/admins", tags=["Admin / Admins"])


@router.get("", response_model=AdminsOut)
async def list_admins(
    session: AnSession, admin: Annotated[Admin, Depends(require_owner)]
):
    rows = await admins_repository.list_created(session)
    return {"admins": [admin_row(row) for row in rows]}


@router.post("", status_code=201, response_model=AdminCreatedOut)
async def create_admin(
    body: AdminCreateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_owner)],
):
    return await CreateAdminService(
        session, admin.id, body.identifier, body.password
    ).process()


@router.patch("/{admin_id}/status", response_model=OkOut)
async def admin_status(
    admin_id: str,
    body: ActiveIn,
    session: AnSession,
    actor: Annotated[Admin, Depends(require_owner)],
):
    return await SetAdminStatusService(
        session, actor.id, admin_id, body.isActive
    ).process()
