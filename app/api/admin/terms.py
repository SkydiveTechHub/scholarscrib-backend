from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import OkOut, TermOut
from app.api.schemas import AcademicTermIn
from app.database.db import AnSession
from app.database.models import Admin
from app.database.repositories.planner import academic_terms_repository
from app.services.console import (
    CreateAcademicTermService,
    DeleteAcademicTermService,
    UpdateAcademicTermService,
)

router = APIRouter(prefix="/academic-terms", tags=["Admin / Academic terms"])


@router.get("", response_model=list[TermOut])
async def list_terms(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    rows = await academic_terms_repository.ordered(session)
    return [
        {
            "id": row.id,
            "session": row.session,
            "term": row.term,
            "startsOn": str(row.starts_on)[:10],
            "endsOn": str(row.ends_on)[:10],
        }
        for row in rows
    ]


@router.post("", status_code=201, response_model=TermOut)
async def create_term(
    body: AcademicTermIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await CreateAcademicTermService(session, admin.id, body).process()


@router.patch("/{term_id}", response_model=TermOut)
async def update_term(
    term_id: str,
    body: AcademicTermIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await UpdateAcademicTermService(session, admin.id, term_id, body).process()


@router.delete("/{term_id}", response_model=OkOut)
async def delete_term(
    term_id: str, session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await DeleteAcademicTermService(session, admin.id, term_id).process()
