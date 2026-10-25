"""Question bank tools. Export returns a short preview, not a file."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.admin.tools.factory import (
    EmptyArgs,
    register_mutation,
    register_read,
)
from app.api.schemas import (
    ImportQuestionIn,
    ImportQuestionsIn,
    QuestionCreateIn,
    QuestionPatchIn,
)
from app.database.repositories.question import questions_repository
from app.services.admin import (
    CreateQuestionService,
    ListAdminQuestionsService,
    question_row,
)
from app.services.console import (
    DeleteQuestionsService,
    GetQuestionFormOptionsService,
    ImportQuestionsService,
    UpdateQuestionService,
)
from app.services.question_coverage import GetQuestionCoverageService


class ListQuestionsArgs(BaseModel):
    page: int = Field(default=1, ge=1, le=20)
    subjectId: str | None = None
    examType: str | None = None
    examYear: int | None = None
    difficulty: str | None = None
    search: str | None = None


class QuestionIdArgs(BaseModel):
    questionId: str = Field(min_length=1, max_length=64)


class UpdateQuestionArgs(QuestionPatchIn):
    questionId: str = Field(min_length=1, max_length=64)


class DeleteQuestionsArgs(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class ImportQuestionsArgs(BaseModel):
    questions: list[ImportQuestionIn] = Field(min_length=1, max_length=500)
    skipDuplicates: bool = True


def _clip_question(row: dict, *, full: bool = False) -> dict:
    limit = 500 if full else 180
    return {
        "id": row.get("id"),
        "subjectId": row.get("subjectId"),
        "topicId": row.get("topicId"),
        "examType": row.get("examType"),
        "examYear": row.get("examYear"),
        "questionNumber": row.get("questionNumber"),
        "questionText": str(row.get("questionText") or "")[:limit],
        "questionType": row.get("questionType"),
        "correctAnswer": row.get("correctAnswer") if full else None,
        "explanation": str(row.get("explanation") or "")[:400] if full else None,
        "difficulty": row.get("difficulty"),
        "marks": row.get("marks"),
    }


def _filters(args: ListQuestionsArgs) -> dict:
    return {
        "subjectId": args.subjectId,
        "examType": args.examType,
        "examYear": args.examYear,
        "difficulty": args.difficulty,
        "search": args.search,
    }


async def _list(ctx, args: ListQuestionsArgs) -> dict:
    data = await ListAdminQuestionsService(
        ctx.session, args.page, 20, _filters(args)
    ).process()
    if not isinstance(data, dict):
        return {"questions": [], "pagination": None}
    rows = data.get("questions") or []
    clipped = [_clip_question(row) for row in rows if isinstance(row, dict)]
    return {"questions": clipped, "pagination": data.get("pagination")}


async def _get(ctx, args: QuestionIdArgs) -> dict:
    question = await questions_repository.by_id(ctx.session, args.questionId)
    if question is None:
        return {"ok": False, "error": "Question not found"}
    return _clip_question(question_row(question), full=True)


async def _form_options(ctx, args: EmptyArgs) -> dict:
    return await GetQuestionFormOptionsService(ctx.session).process()


async def _coverage(ctx, args: EmptyArgs) -> dict:
    return await GetQuestionCoverageService(ctx.session).process()


async def _preview(ctx, args: ListQuestionsArgs) -> dict:
    data = await ListAdminQuestionsService(ctx.session, 1, 5, _filters(args)).process()
    rows = data.get("questions") if isinstance(data, dict) else []
    preview = [_clip_question(row) for row in (rows or []) if isinstance(row, dict)]
    pagination = data.get("pagination") if isinstance(data, dict) else {}
    total = pagination.get("total") if isinstance(pagination, dict) else None
    return {
        "total": total,
        "preview": preview[:5],
        "note": "This is a preview. Download the full file from question export.",
    }


async def _create(ctx, args: QuestionCreateIn) -> dict:
    return await CreateQuestionService(ctx.session, ctx.admin_id, args).process()


async def _update(ctx, args: UpdateQuestionArgs) -> dict:
    body = QuestionPatchIn.model_validate(args.model_dump(exclude={"questionId"}))
    return await UpdateQuestionService(
        ctx.session, ctx.admin_id, args.questionId, body
    ).process()


async def _delete(ctx, args: DeleteQuestionsArgs) -> dict:
    return await DeleteQuestionsService(ctx.session, ctx.admin_id, args.ids).process()


async def _import(ctx, args: ImportQuestionsArgs) -> dict:
    body = ImportQuestionsIn(
        questions=args.questions, skipDuplicates=args.skipDuplicates
    )
    return await ImportQuestionsService(ctx.session, ctx.admin_id, body).process()


def register_question_tools() -> None:
    register_read(
        "list_questions",
        "questions",
        "List questions with ids, exam, year, and a short stem. Page size is 20.",
        ListQuestionsArgs,
        _list,
    )
    register_read(
        "get_question",
        "questions",
        "One question, including the answer and a shortened explanation.",
        QuestionIdArgs,
        _get,
    )
    register_read(
        "question_form_options",
        "questions",
        "Subject and topic options for creating a question.",
        EmptyArgs,
        _form_options,
    )
    register_read(
        "question_coverage",
        "questions",
        "How many questions exist per subject and year, plus provider status.",
        EmptyArgs,
        _coverage,
    )
    register_read(
        "preview_question_export",
        "questions",
        "Count and a five-row preview for an export. Does not build a CSV or ZIP.",
        ListQuestionsArgs,
        _preview,
    )
    register_mutation(
        "create_question",
        "questions",
        "Propose a new question. Subject and topic ids must come from a read tool.",
        QuestionCreateIn,
        _create,
    )
    register_mutation(
        "update_question",
        "questions",
        "Propose edits to one question. The question id cannot be changed on the card.",
        UpdateQuestionArgs,
        _update,
    )
    register_mutation(
        "delete_questions",
        "questions",
        "Propose deleting up to 100 questions. Questions already used in attempts "
        "are refused by the service. Ids cannot be edited on the card.",
        DeleteQuestionsArgs,
        _delete,
        destructive=True,
    )
    register_mutation(
        "import_questions",
        "questions",
        "Propose importing 1 to 500 questions. The card cannot be edited; "
        "reject it and send a corrected batch instead.",
        ImportQuestionsArgs,
        _import,
        destructive=True,
    )
