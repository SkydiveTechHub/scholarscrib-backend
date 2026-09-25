from fastapi import APIRouter

from app.api.responses import PapersOut, QuestionPageOut
from app.database.db import AnSession
from app.database.repositories.question import questions_repository
from app.services.catalogue import ListPastPapersService, public_question

router = APIRouter(prefix="/questions", tags=["Student / Questions"])


@router.get("", response_model=QuestionPageOut)
async def list_questions(
    session: AnSession,
    subjectId: str | None = None,
    topicId: str | None = None,
    examType: str | None = None,
    examYear: int | None = None,
    difficulty: str | None = None,
    page: int = 1,
    limit: int = 20,
):
    limit = min(max(limit, 1), 50)
    page = max(page, 1)
    rows, total = await questions_repository.list_page(
        session,
        subject_id=subjectId,
        topic_id=topicId,
        exam_type=examType,
        exam_year=examYear,
        difficulty=difficulty,
        page=page,
        limit=limit,
    )
    pages = max(1, (total + limit - 1) // limit)
    return {
        "questions": [public_question(row, include_answers=True) for row in rows],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": pages,
        },
    }


@router.get("/past-papers", response_model=PapersOut)
async def past_papers(
    session: AnSession,
    examType: str | None = None,
    subjectId: str | None = None,
):
    return await ListPastPapersService(session, examType, subjectId).process()
