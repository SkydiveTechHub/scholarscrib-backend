from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import StudentPrincipal, require_feature, require_student
from app.api.responses import (
    DeckCreatedOut,
    DecksOut,
    DeletedDeckOut,
    EnrollOut,
    FlashcardStatsOut,
    GeneratedDeckOut,
    PreviewOut,
    RecommendationsOut,
    ReviewOut,
    StudyQueueOut,
)
from app.api.schemas import DeckIn, EnrollIn, LessonRef, ReviewIn
from app.core.rate_limit import hit
from app.database.db import AnSession, get_session
from app.services.planner import MarkStudyPlanItemService
from app.services.srs import (
    CreateFlashcardDeckService,
    DeleteFlashcardDeckService,
    EnrollFlashcardDeckService,
    GenerateFlashcardsService,
    GetFlashcardRecommendationsService,
    GetFlashcardStatsService,
    GetFlashcardStudyQueueService,
    ListFlashcardDecksService,
    PreviewFlashcardsService,
    ReviewFlashcardService,
)

router = APIRouter(prefix="/flashcards", tags=["Student / Flashcards"])


@router.get(
    "",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=DecksOut,
)
async def flashcards(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await ListFlashcardDecksService(session, student.id).process()


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=DeckCreatedOut,
)
async def create_deck(
    body: DeckIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await CreateFlashcardDeckService(
        session, student.id, body.title, body.subjectId
    ).process()


@router.get(
    "/stats",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=FlashcardStatsOut,
)
async def flashcard_stats(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetFlashcardStatsService(session, student.id).process()


@router.get(
    "/recommendations",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=RecommendationsOut,
)
async def flashcard_recommendations(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetFlashcardRecommendationsService(session, student.id).process()


@router.get(
    "/preview",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=PreviewOut,
)
async def flashcard_preview(
    lessonId: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"flashcard-preview:{student.id}", 40, 60)
    return await PreviewFlashcardsService(session, lessonId).process()


@router.post(
    "/generate",
    status_code=201,
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=GeneratedDeckOut,
)
async def flashcard_generate(
    body: LessonRef,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GenerateFlashcardsService(session, student.id, body.lessonId).process()


@router.post(
    "/review",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=ReviewOut,
)
async def flashcard_review(
    body: ReviewIn,
    background: BackgroundTasks,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    result = await ReviewFlashcardService(session, student.id, body).process()
    background.add_task(_mark_revision, student.id)
    return result


async def _mark_revision(student_id: str) -> None:
    async with get_session() as session:
        try:
            await MarkStudyPlanItemService(session, student_id, "REVISION").process()
            await session.commit()
        except Exception:
            await session.rollback()


@router.post(
    "/decks/{deck_id}/enroll",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=EnrollOut,
)
async def enroll(
    deck_id: str,
    body: EnrollIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await EnrollFlashcardDeckService(
        session, student.id, deck_id, body.enrolled
    ).process()


@router.delete(
    "/decks/{deck_id}",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=DeletedDeckOut,
)
async def delete_deck(
    deck_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await DeleteFlashcardDeckService(session, student.id, deck_id).process()


@router.get(
    "/decks/{deck_id}",
    dependencies=[Depends(require_feature("flashcards"))],
    response_model=StudyQueueOut,
)
async def deck_page(
    deck_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetFlashcardStudyQueueService(session, student.id, deck_id).process()
