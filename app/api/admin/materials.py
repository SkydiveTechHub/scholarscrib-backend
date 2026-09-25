from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import MaterialOut, OkOut, SignUploadOut
from app.api.schemas import MaterialCreateIn, MaterialPatchIn, SignMaterialIn
from app.database.db import AnSession
from app.database.models import Admin
from app.services.admin import SignMaterialUploadService
from app.services.console import (
    CreateMaterialService,
    DeleteMaterialService,
    ListMaterialsService,
    UpdateMaterialService,
)

router = APIRouter(prefix="/materials", tags=["Admin / Materials"])


@router.post("/sign", response_model=SignUploadOut)
async def sign_material(
    body: SignMaterialIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await SignMaterialUploadService(body).process()


@router.get("", response_model=list[MaterialOut])
async def materials(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    subjectId: str | None = None,
):
    return await ListMaterialsService(session, subjectId).process()


@router.post("", status_code=201, response_model=MaterialOut)
async def create_material(
    body: MaterialCreateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await CreateMaterialService(session, admin.id, body).process()


@router.patch("/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: str,
    body: MaterialPatchIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await UpdateMaterialService(session, admin.id, material_id, body).process()


@router.delete("/{material_id}", response_model=OkOut)
async def delete_material(
    material_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await DeleteMaterialService(session, admin.id, material_id).process()
