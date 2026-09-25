from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ReviewIn
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import (
    Flashcard,
    FlashcardDeck,
    FlashcardEnrollment,
    FlashcardReview,
    FlashcardReviewLog,
    LearningEvent,
)
from app.database.repositories.curriculum import (
    lessons_repository,
    subtopics_repository,
    topics_repository,
)
from app.database.repositories.flashcard import (
    cards_repository,
    decks_repository,
    enrollments_repository,
    review_logs_repository,
    reviews_repository,
)
from app.database.repositories.learning import learning_events_repository
from app.services.learning.evidence import CARD_OUTCOMES
from app.services.srs.scheduler import (
    DAILY_NEW_BUDGET,
    CardSchedule,
    review_card,
    seed_difficulty,
)


def cards_from_blocks(blocks: list[dict]) -> list[dict]:
    cards_out = []
    for index, block in enumerate(blocks or []):
        kind = (block.get("type") or "").lower()
        text = (block.get("text") or block.get("body") or "").strip()
        if not text or len(text.split()) > 120:
            continue
        if kind in {"concept", "mnemonic", "tip"}:
            card_type = "DEFINITION"
        elif kind in {"check", "example"}:
            card_type = "SCENARIO"
        elif kind == "mistake":
            card_type = "TRUE_FALSE"
        else:
            continue
        cards_out.append(
            {
                "cardType": card_type,
                "sourceKey": block.get("id") or f"{kind}-{index}",
                "payload": {
                    "front": block.get("title") or text[:80],
                    "back": text,
                    "type": card_type,
                },
            }
        )
    return cards_out


async def can_review(session: AsyncSession, user_id: str, deck: FlashcardDeck) -> bool:
    if deck.created_by == user_id:
        return True
    enrollment = await enrollments_repository.for_student_deck(
        session, user_id, deck.id
    )
    return enrollment is not None


class ListFlashcardDecksService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        owned = await decks_repository.owned_by(self.session, self.user_id)
        enrolled = await decks_repository.enrolled_for(self.session, self.user_id)
        seen = {deck.id for deck in owned}
        payload = []
        for deck in [*owned, *[d for d in enrolled if d.id not in seen]]:
            count = await cards_repository.count_for_deck(self.session, deck.id)
            payload.append(
                {
                    "id": deck.id,
                    "title": deck.title,
                    "source": deck.source,
                    "cardCount": count or 0,
                    "owned": deck.created_by == self.user_id,
                }
            )
        return {"decks": payload}


class CreateFlashcardDeckService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        title: str,
        subject_id: str | None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.title = title
        self.subject_id = subject_id

    async def process(self) -> dict:
        deck = FlashcardDeck(
            id=cuid(),
            title=self.title,
            subject_id=self.subject_id,
            source="AUTHORED",
            created_by=self.user_id,
        )
        await decks_repository.add(self.session, deck, flush=True)
        return {
            "deck": {
                "id": deck.id,
                "title": deck.title,
                "source": deck.source,
            }
        }


class PreviewFlashcardsService:
    def __init__(self, session: AsyncSession, lesson_id: str) -> None:
        self.session = session
        self.lesson_id = lesson_id

    async def process(self) -> dict:
        lesson = await lessons_repository.by_id(self.session, self.lesson_id)
        if lesson is None:
            raise ApiError(404, "Lesson not found")
        preview_cards = cards_from_blocks(
            lesson.blocks if isinstance(lesson.blocks, list) else []
        )
        return {
            "lessonId": lesson.id,
            "title": lesson.title,
            "cards": preview_cards,
            "cardCount": len(preview_cards),
        }


class GenerateFlashcardsService:
    def __init__(self, session: AsyncSession, user_id: str, lesson_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.lesson_id = lesson_id

    async def process(self) -> dict:
        preview = await PreviewFlashcardsService(self.session, self.lesson_id).process()
        if not preview["cards"]:
            raise ApiError(422, "This lesson did not produce any cards")
        lesson = await lessons_repository.by_id(self.session, self.lesson_id)
        if lesson is None:
            raise ApiError(404, "Lesson not found")
        deck = await self._deck_for(lesson)
        await self._sync_cards(deck, preview["cards"])
        await self.session.flush()
        return {
            "deck": {"id": deck.id, "title": deck.title},
            "counts": {"cards": len(preview["cards"])},
            "cardCount": len(preview["cards"]),
        }

    async def _deck_for(self, lesson) -> FlashcardDeck:
        subtopic = await subtopics_repository.by_id(self.session, lesson.subtopic_id)
        topic = (
            await topics_repository.by_id(self.session, subtopic.topic_id)
            if subtopic
            else None
        )
        deck = await decks_repository.for_lesson(self.session, self.lesson_id)
        if deck is None:
            deck = FlashcardDeck(
                id=cuid(),
                title=lesson.title,
                lesson_id=self.lesson_id,
                topic_id=topic.id if topic else None,
                subject_id=topic.subject_id if topic else None,
                source="LESSON",
                created_by=self.user_id,
            )
            await decks_repository.add(self.session, deck, flush=True)
        return deck

    async def _sync_cards(self, deck: FlashcardDeck, preview_cards: list[dict]) -> None:
        existing = await cards_repository.for_deck(self.session, deck.id)
        by_key = {card.source_key: card for card in existing if card.source_key}
        incoming_keys = set()
        for card in preview_cards:
            incoming_keys.add(card["sourceKey"])
            current = by_key.get(card["sourceKey"])
            if current:
                current.payload = card["payload"]
                current.card_type = card["cardType"]
            else:
                await cards_repository.add(
                    self.session,
                    Flashcard(
                        id=cuid(),
                        deck_id=deck.id,
                        card_type=card["cardType"],
                        payload=card["payload"],
                        source_key=card["sourceKey"],
                    ),
                )
        for card in existing:
            if card.source_key and card.source_key not in incoming_keys:
                await cards_repository.remove(self.session, card)


class ReviewFlashcardService:
    def __init__(self, session: AsyncSession, user_id: str, body: ReviewIn) -> None:
        self.session = session
        self.user_id = user_id
        self.body = body

    async def process(self) -> dict:
        card = await cards_repository.by_id(self.session, self.body.flashcardId)
        if card is None:
            raise ApiError(404, "Card not found")
        deck = await decks_repository.by_id(self.session, card.deck_id)
        if deck is None or not await can_review(self.session, self.user_id, deck):
            raise ApiError(404, "Card not found")
        row = await self._review_row(card)
        now = utcnow()
        schedule = review_card(
            CardSchedule(
                state=row.state,
                ease_factor=row.ease_factor,
                stability=row.stability,
                difficulty=row.difficulty,
                interval_days=row.interval_days,
                repetitions=row.repetitions,
                lapses=row.lapses,
            ),
            self.body.rating,
            now,
        )
        self._apply_schedule(row, schedule, now)
        await self._log_review(card.id, schedule)
        await self._record_event(deck, card.id, now)
        await self.session.flush()
        return {
            "outcome": schedule.state,
            "review": {
                "state": schedule.state,
                "dueAt": (schedule.due_at.isoformat() if schedule.due_at else None),
                "intervalDays": schedule.interval_days,
                "retention": schedule.retention,
            },
            "topicId": deck.topic_id,
        }

    async def _review_row(self, card: Flashcard) -> FlashcardReview:
        row = await reviews_repository.for_card(self.session, self.user_id, card.id)
        if row is None:
            row = FlashcardReview(
                id=cuid(),
                student_id=self.user_id,
                flashcard_id=card.id,
                difficulty=seed_difficulty(card.difficulty),
            )
            await reviews_repository.add(self.session, row)
        return row

    def _apply_schedule(self, row, schedule, now) -> None:
        row.state = schedule.state
        row.ease_factor = schedule.ease_factor
        row.stability = schedule.stability
        row.difficulty = schedule.difficulty
        row.interval_days = schedule.interval_days
        row.repetitions = schedule.repetitions
        row.lapses = schedule.lapses
        row.retention = schedule.retention
        row.due_at = schedule.due_at
        row.last_reviewed_at = now

    async def _log_review(self, flashcard_id: str, schedule) -> None:
        await review_logs_repository.add(
            self.session,
            FlashcardReviewLog(
                id=cuid(),
                student_id=self.user_id,
                flashcard_id=flashcard_id,
                rating=self.body.rating,
                response_time_ms=self.body.responseTimeMs,
                scheduled_days=schedule.interval_days,
                objective_correct=self.body.objectiveCorrect,
            ),
        )

    async def _record_event(self, deck, card_id: str, now) -> None:
        if not deck.topic_id:
            return
        topic = await topics_repository.by_id(self.session, deck.topic_id)
        await learning_events_repository.add(
            self.session,
            LearningEvent(
                student_id=self.user_id,
                subject_id=(topic.subject_id if topic else deck.subject_id),
                topic_id=deck.topic_id,
                kind="CARD_REVIEWED",
                score=CARD_OUTCOMES[self.body.rating],
                source_id=card_id,
                occurred_at=now,
            ),
        )


class EnrollFlashcardDeckService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        deck_id: str,
        enrolled: bool,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.deck_id = deck_id
        self.enrolled = enrolled

    async def process(self) -> dict:
        deck = await decks_repository.by_id(self.session, self.deck_id)
        if deck is None:
            raise ApiError(404, "Deck not found")
        existing = await enrollments_repository.for_student_deck(
            self.session, self.user_id, self.deck_id
        )
        if self.enrolled and existing is None:
            await enrollments_repository.add(
                self.session,
                FlashcardEnrollment(
                    id=cuid(),
                    student_id=self.user_id,
                    deck_id=self.deck_id,
                ),
            )
        if not self.enrolled and existing is not None:
            await enrollments_repository.remove(self.session, existing)
        return {"deckId": self.deck_id, "enrolled": self.enrolled}


class DeleteFlashcardDeckService:
    def __init__(self, session: AsyncSession, user_id: str, deck_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.deck_id = deck_id

    async def process(self) -> dict:
        deck = await decks_repository.by_id(self.session, self.deck_id)
        if deck is None:
            raise ApiError(404, "Deck not found")
        if deck.created_by != self.user_id:
            raise ApiError(403, "Not permitted")
        await decks_repository.remove(self.session, deck)
        return {"deckId": self.deck_id}


class GetFlashcardStatsService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        rows = await reviews_repository.for_student(self.session, self.user_id)
        now = utcnow()
        due = sum(1 for review in rows if review.due_at and review.due_at <= now)
        return {
            "stats": {
                "reviews": len(rows),
                "due": due,
                "learning": sum(
                    1 for review in rows if review.state in {"LEARNING", "RELEARNING"}
                ),
            }
        }


class GetFlashcardRecommendationsService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        rows = await reviews_repository.low_retention(self.session, self.user_id)
        return {
            "recommendations": [
                {
                    "flashcardId": review.flashcard_id,
                    "retention": review.retention,
                }
                for review in rows
            ]
        }


class GetFlashcardStudyQueueService:
    def __init__(self, session: AsyncSession, user_id: str, deck_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.deck_id = deck_id

    async def process(self) -> dict:
        deck = await decks_repository.by_id(self.session, self.deck_id)
        if deck is None or not await can_review(self.session, self.user_id, deck):
            raise ApiError(404, "Deck not found")
        deck_cards = await cards_repository.for_deck(self.session, self.deck_id)
        review_rows = await reviews_repository.for_cards(
            self.session, self.user_id, [card.id for card in deck_cards]
        )
        by_card = {review.flashcard_id: review for review in review_rows}
        now = utcnow()
        due = []
        fresh = []
        for card in deck_cards:
            review = by_card.get(card.id)
            if (
                review
                and review.due_at
                and review.due_at <= now
                and review.state != "NEW"
            ):
                due.append(card)
            elif review is None or review.state == "NEW":
                fresh.append(card)
        due.sort(key=lambda card: by_card[card.id].due_at or now)
        new_budget = max(0, DAILY_NEW_BUDGET - len(due))
        queue = due + fresh[:new_budget]
        return {
            "deck": {"id": deck.id, "title": deck.title},
            "queue": [
                {
                    "id": card.id,
                    "type": card.card_type,
                    "payload": card.payload,
                }
                for card in queue
            ],
            "dueCount": len(due),
            "newCount": min(len(fresh), new_budget),
        }
