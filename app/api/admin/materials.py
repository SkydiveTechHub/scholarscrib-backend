from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.ai.rag.ingest import schedule_index
from app.api.deps import require_admin
from app.api.responses import MaterialOut, MaterialSubjectsOut, OkOut, SignUploadOut
from app.api.schemas import MaterialCreateIn, MaterialPatchIn, SignMaterialIn
from app.database.db import AnSession
from app.database.models import Admin
from app.services.admin import SignMaterialUploadService
from app.services.console import (
    CreateMaterialService,
    DeleteMaterialService,
    GetMaterialSubjectsService,
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


@router.get("/subjects", response_model=MaterialSubjectsOut)
async def material_subjects(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetMaterialSubjectsService(session).process()


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
    background: BackgroundTasks,
):
    result = await CreateMaterialService(session, admin.id, body).process()
    material_id = result.get("id")
    if material_id:
        schedule_index(background, "material", material_id)
    return result


@router.patch("/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: str,
    body: MaterialPatchIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    background: BackgroundTasks,
):
    result = await UpdateMaterialService(session, admin.id, material_id, body).process()
    schedule_index(background, "material", material_id)
    return result


@router.delete("/{material_id}", response_model=OkOut)
async def delete_material(
    material_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    background: BackgroundTasks,
):
    result = await DeleteMaterialService(session, admin.id, material_id).process()
    schedule_index(background, "material_delete", material_id)
    return result
