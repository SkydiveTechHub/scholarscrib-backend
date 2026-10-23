from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import AdminQuestionsOut, ImportQuestionsOut
from app.api.schemas import ExamType, ImportCoverageQuestionsIn
from app.database.db import AnSession
from app.database.models import Admin
from app.services.coverage_import import (
    ImportCoverageQuestionsService,
    ListCoverageQuestionsService,
)

router = APIRouter(prefix="/coverage-questions", tags=["Admin / Coverage Questions"])


@router.get("", response_model=AdminQuestionsOut)
async def list_coverage_questions(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    page: int = 1,
    pageSize: int = 20,
    subjectId: str | None = None,
    examType: ExamType | None = None,
    examYear: int | None = None,
    search: str | None = None,
):
    return await ListCoverageQuestionsService(
        session,
        max(page, 1),
        min(max(pageSize, 1), 100),
        {
            "subjectId": subjectId,
            "examType": examType,
            "examYear": examYear,
            "search": search,
        },
    ).process()


@router.post("/import", response_model=ImportQuestionsOut)
async def import_coverage_questions(
    body: ImportCoverageQuestionsIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await ImportCoverageQuestionsService(session, admin.id, body).process()
