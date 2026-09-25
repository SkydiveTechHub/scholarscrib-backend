from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request

from app.api.deps import require_admin
from app.api.responses import (
    AdminQuestionsOut,
    DeleteQuestionsOut,
    IdOut,
    ImportQuestionsOut,
    QuestionOut,
    UsageOut,
)
from app.api.schemas import (
    DeleteQuestionsIn,
    ImportQuestionsIn,
    QuestionCreateIn,
    QuestionPatchIn,
)
from app.core.errors import ApiError
from app.database.db import AnSession
from app.database.models import Admin
from app.database.repositories.question import questions_repository
from app.services.admin import (
    CreateQuestionService,
    ListAdminQuestionsService,
    question_row,
)
from app.services.console import (
    DeleteQuestionsService,
    GetQuestionUsageService,
    ImportQuestionsService,
    UpdateQuestionService,
)

router = APIRouter(prefix="/questions", tags=["Admin / Questions"])


@router.get("", response_model=AdminQuestionsOut)
async def list_questions(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    page: int = 1,
    pageSize: int = 20,
    subjectId: str | None = None,
    examType: str | None = None,
    examYear: int | None = None,
    difficulty: str | None = None,
    search: str | None = None,
):
    page_size = min(max(pageSize, 1), 100)
    return await ListAdminQuestionsService(
        session,
        max(page, 1),
        page_size,
        {
            "subjectId": subjectId,
            "examType": examType,
            "examYear": examYear,
            "difficulty": difficulty,
            "search": search,
        },
    ).process()


@router.post("", status_code=201, response_model=IdOut)
async def create_question(
    body: QuestionCreateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await CreateQuestionService(session, admin.id, body).process()


@router.get("/{question_id}", response_model=QuestionOut)
async def get_question(
    question_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    question = await questions_repository.by_id(session, question_id)
    if question is None:
        raise ApiError(404, "Question not found")
    row = question_row(question)
    return row


@router.patch("/{question_id}", response_model=IdOut)
async def update_question(
    question_id: str,
    body: QuestionPatchIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await UpdateQuestionService(session, admin.id, question_id, body).process()


@router.get("/{question_id}/usage", response_model=UsageOut)
async def question_usage(
    question_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await GetQuestionUsageService(session, question_id).process()


@router.delete("", response_model=DeleteQuestionsOut)
async def delete_questions(
    request: Request,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    body: Annotated[DeleteQuestionsIn | None, Body()] = None,
):
    ids = request.query_params.getlist("id")
    if not ids and body is not None:
        ids = body.ids
    return await DeleteQuestionsService(session, admin.id, ids).process()


@router.post("/import", response_model=ImportQuestionsOut)
async def import_questions(
    body: ImportQuestionsIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await ImportQuestionsService(session, admin.id, body).process()
