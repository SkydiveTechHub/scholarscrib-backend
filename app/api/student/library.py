from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, library_student
from app.api.responses import LibraryOut
from app.database.db import AnSession
from app.services.learning import GetLibraryService

router = APIRouter(prefix="/library", tags=["Student / Library"])


@router.get("", response_model=LibraryOut)
async def library(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(library_student)],
    subjectId: str | None = None,
):
    return await GetLibraryService(
        session, student.id, student.tier, student.track, subjectId
    ).process()
