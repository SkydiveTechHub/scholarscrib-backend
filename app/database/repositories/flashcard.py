"""Repositories for flashcard decks and reviews."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Flashcard,
    FlashcardDeck,
    FlashcardEnrollment,
    FlashcardReview,
    FlashcardReviewLog,
)
from app.database.repositories.base import BaseRepository


class FlashcardDeckRepository(BaseRepository[FlashcardDeck]):
    model = FlashcardDeck

    async def owned_by(
        self, session: AsyncSession, user_id: str
    ) -> list[FlashcardDeck]:
        return await self.list_where(
            session,
            FlashcardDeck.created_by == user_id,
            order_by=(FlashcardDeck.created_at.desc(),),
        )

    async def enrolled_for(
        self, session: AsyncSession, student_id: str
    ) -> list[FlashcardDeck]:
        return await self.many(
            session,
            select(FlashcardDeck)
            .join(
                FlashcardEnrollment,
                FlashcardEnrollment.deck_id == FlashcardDeck.id,
            )
            .where(FlashcardEnrollment.student_id == student_id),
        )

    async def for_lesson(
        self, session: AsyncSession, lesson_id: str
    ) -> FlashcardDeck | None:
        return await self.first(
            session,
            FlashcardDeck.lesson_id == lesson_id,
            FlashcardDeck.source == "LESSON",
        )


class FlashcardRepository(BaseRepository[Flashcard]):
    model = Flashcard

    async def for_deck(self, session: AsyncSession, deck_id: str) -> list[Flashcard]:
        return await self.list_where(session, Flashcard.deck_id == deck_id)

    async def count_for_deck(self, session: AsyncSession, deck_id: str) -> int:
        return await self.count(session, Flashcard.deck_id == deck_id)


class FlashcardReviewRepository(BaseRepository[FlashcardReview]):
    model = FlashcardReview

    async def for_student(
        self, session: AsyncSession, student_id: str
    ) -> list[FlashcardReview]:
        return await self.list_where(session, FlashcardReview.student_id == student_id)

    async def for_card(
        self,
        session: AsyncSession,
        student_id: str,
        flashcard_id: str,
    ) -> FlashcardReview | None:
        return await self.first(
            session,
            FlashcardReview.student_id == student_id,
            FlashcardReview.flashcard_id == flashcard_id,
        )

    async def for_cards(
        self,
        session: AsyncSession,
        student_id: str,
        flashcard_ids: list[str],
    ) -> list[FlashcardReview]:
        ids = flashcard_ids or [""]
        return await self.list_where(
            session,
            FlashcardReview.student_id == student_id,
            FlashcardReview.flashcard_id.in_(ids),
        )

    async def low_retention(
        self,
        session: AsyncSession,
        student_id: str,
        *,
        threshold: float = 0.75,
    ) -> list[FlashcardReview]:
        return await self.list_where(
            session,
            FlashcardReview.student_id == student_id,
            FlashcardReview.retention < threshold,
        )


class FlashcardReviewLogRepository(BaseRepository[FlashcardReviewLog]):
    model = FlashcardReviewLog


class FlashcardEnrollmentRepository(BaseRepository[FlashcardEnrollment]):
    model = FlashcardEnrollment

    async def for_student_deck(
        self,
        session: AsyncSession,
        student_id: str,
        deck_id: str,
    ) -> FlashcardEnrollment | None:
        return await self.first(
            session,
            FlashcardEnrollment.student_id == student_id,
            FlashcardEnrollment.deck_id == deck_id,
        )


decks_repository = FlashcardDeckRepository()
cards_repository = FlashcardRepository()
reviews_repository = FlashcardReviewRepository()
review_logs_repository = FlashcardReviewLogRepository()
enrollments_repository = FlashcardEnrollmentRepository()
