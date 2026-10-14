import re
from datetime import timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    subjects_repository,
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
from app.database.repositories.learning import (
    learning_events_repository,
    progress_repository,
)
from app.services.learning.evidence import CARD_OUTCOMES
from app.services.srs.analytics import (
    LOG_WINDOW_DAYS,
    CardRow,
    DeckRow,
    LogRow,
    ReviewSnapshot,
    build_stats,
)
from app.services.srs.scheduler import (
    DAILY_NEW_BUDGET,
    INITIAL_EASE,
    CardSchedule,
    is_leech,
    review_card,
    seed_difficulty,
)
from app.services.srs.sync import ExistingCard, plan_card_sync

OVERDUE_MIN_DAYS = 7
LOW_RETENTION = 0.75
MAX_RECOMMENDATIONS = 4
MAX_UNBUILT_LESSONS = 1
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_NOT_STUDY_CONTENT = re.compile(
    r"^(?:(?:learning\s+)?objectives?|quiz\b|worked\s+examples?\b)", re.IGNORECASE
)


def _card_faces(block: dict) -> tuple[str, str] | None:
    """The (front, back) a block should study as, or None if it is not study content.

    Objectives and the Quiz / Worked Examples instruction cards describe the
    lesson rather than teach it, so they never become cards.
    """
    kind = (block.get("type") or "").lower()
    text = (block.get("text") or block.get("body") or "").strip()
    title = (block.get("title") or "").strip()

    if kind == "concept":
        if _NOT_STUDY_CONTENT.match(title):
            return None
        reveal = (block.get("reveal") or "").strip()
        if reveal and not title:  # a short-answer question: ask it, answer on the back
            return text, reveal
        back = f"{text}\n\n{reveal}" if reveal else text
        return (title or text[:80]), back
    if kind == "tip":
        return text[:80], text
    if kind == "short":
        question = (block.get("question") or text).strip()
        return question, (block.get("answer") or "").strip()
    if kind == "mnemonic":
        phrase = (block.get("phrase") or text).strip()
        encoded = [str(e).strip() for e in block.get("encoded") or [] if str(e).strip()]
        return phrase, "\n".join(encoded)
    if kind == "example":
        problem = (block.get("problem") or text).strip()
        steps = [str(s).strip() for s in block.get("steps") or [] if str(s).strip()]
        answer = (block.get("answer") or "").strip()
        return problem, "\n".join([*steps, f"Answer: {answer}"] if answer else steps)
    if kind == "check":
        question = (block.get("question") or text).strip()
        options = block.get("options") or {}
        answer = str(block.get("answer") or "").strip()
        if not answer or answer not in options:
            return None
        back = f"{answer}) {options[answer]}"
        explanation = (block.get("explanation") or "").strip()
        return question, f"{back}\n\n{explanation}" if explanation else back
    if kind == "mistake":
        return (block.get("wrong") or text).strip(), (block.get("right") or "").strip()
    return None


def cards_from_blocks(blocks: list[dict]) -> list[dict]:
    cards_out = []
    seen_keys: set[str] = set()
    for index, block in enumerate(blocks or []):
        kind = (block.get("type") or "").lower()
        faces = _card_faces(block)
        if faces is None:
            continue
        front, text = faces[0].strip(), faces[1].strip()
        if not front or not text or len(text.split()) > 120:
            continue
        if kind in {"concept", "mnemonic", "tip", "short"}:
            card_type = "DEFINITION"
        elif kind in {"check", "example"}:
            card_type = "SCENARIO"
        elif kind == "mistake":
            card_type = "TRUE_FALSE"
        else:
            continue
        # (deckId, sourceKey) is unique; two quiz sections with the same heading
        # share a block id, so a repeat is suffixed rather than failing the insert.
        key = str(block.get("id") or f"{kind}-{index}")
        if key in seen_keys:
            key = f"{key}-{index}"
        seen_keys.add(key)
        cards_out.append(
            {
                "cardType": card_type,
                "sourceKey": key,
                "payload": {
                    "front": front,
                    "back": text,
                    "type": card_type,
                },
            }
        )
    return cards_out


def _next_intervals(
    review: FlashcardReview | None, difficulty: str | None, now
) -> dict[str, float]:
    """Days until the next review for each rating, so the buttons can say so."""
    if review is None:
        base = CardSchedule(difficulty=seed_difficulty(difficulty))
    else:
        base = CardSchedule(
            state=review.state,
            ease_factor=review.ease_factor,
            stability=review.stability,
            difficulty=review.difficulty,
            interval_days=review.interval_days,
            repetitions=review.repetitions,
            lapses=review.lapses,
        )
    out = {}
    for rating in ("AGAIN", "HARD", "GOOD", "EASY"):
        scheduled = review_card(base, rating, now)
        # Short learning steps are stored as a due time, not an interval.
        out[rating] = (
            (scheduled.due_at - now).total_seconds() / 86400
            if scheduled.due_at
            else scheduled.interval_days
        )
    return out


async def can_review(session: AsyncSession, user_id: str, deck: FlashcardDeck) -> bool:
    if deck.created_by == user_id:
        return True
    enrollment = await enrollments_repository.for_student_deck(
        session, user_id, deck.id
    )
    return enrollment is not None


async def ensure_enrolled(
    session: AsyncSession, user_id: str, deck: FlashcardDeck
) -> None:
    """Gives a student access to a deck. Safe to repeat and to race."""
    await session.execute(
        pg_insert(FlashcardEnrollment)
        .values(id=cuid(), student_id=user_id, deck_id=deck.id)
        .on_conflict_do_nothing(
            index_elements=[FlashcardEnrollment.student_id, FlashcardEnrollment.deck_id]
        )
    )


async def adopt_shared_deck(session: AsyncSession, deck: FlashcardDeck) -> None:
    """Turns a lesson deck made under the old owner model into a shared one.

    Decks built from a lesson used to record whoever built them first as owner,
    and only that student could open it. Shared decks have no owner, so the
    creator is enrolled like everyone else and the owner link is cleared, which
    also lets them leave the deck without deleting it for the cohort.
    """
    if deck.source != "LESSON" or deck.created_by is None:
        return
    await ensure_enrolled(session, deck.created_by, deck)
    deck.created_by = None


class ListFlashcardDecksService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        owned = await decks_repository.owned_by(self.session, self.user_id)
        for deck in owned:
            await adopt_shared_deck(self.session, deck)
        await self.session.flush()
        owned = [deck for deck in owned if deck.created_by == self.user_id]
        enrolled = await decks_repository.enrolled_for(self.session, self.user_id)
        seen = {deck.id for deck in owned}
        followed = {deck.id for deck in enrolled}
        now = utcnow()
        payload = []
        for deck in [*owned, *[d for d in enrolled if d.id not in seen]]:
            cards = await cards_repository.for_deck(self.session, deck.id)
            reviews = await reviews_repository.for_cards(
                self.session, self.user_id, [card.id for card in cards]
            )
            by_card = {review.flashcard_id: review for review in reviews}
            started = [r for r in by_card.values() if r.state != "NEW"]
            subject = (
                await subjects_repository.by_id(self.session, deck.subject_id)
                if deck.subject_id
                else None
            )
            topic = (
                await topics_repository.by_id(self.session, deck.topic_id)
                if deck.topic_id
                else None
            )
            last = max(
                (r.last_reviewed_at for r in started if r.last_reviewed_at),
                default=None,
            )
            payload.append(
                {
                    "id": deck.id,
                    "title": deck.title,
                    "source": deck.source,
                    "cardCount": len(cards),
                    "totalCards": len(cards),
                    "due": sum(1 for r in started if r.due_at and r.due_at <= now),
                    "fresh": sum(
                        1
                        for card in cards
                        if card.id not in by_card or by_card[card.id].state == "NEW"
                    ),
                    "reviewed": len(started),
                    "enrolled": deck.id in followed,
                    "subjectName": subject.name if subject else None,
                    "topicTitle": topic.title if topic else None,
                    "lastReviewedAt": last.isoformat() if last else None,
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
        await adopt_shared_deck(self.session, deck)
        await ensure_enrolled(self.session, self.user_id, deck)
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
            # A deck built from a lesson is shared, so it has no owner. Two
            # students building it at once is normal, and the loser must find the
            # winner's deck rather than fail on the (lesson, source) uniqueness.
            await self.session.execute(
                pg_insert(FlashcardDeck)
                .values(
                    id=cuid(),
                    title=lesson.title,
                    lesson_id=self.lesson_id,
                    topic_id=topic.id if topic else None,
                    subject_id=topic.subject_id if topic else None,
                    source="LESSON",
                    created_by=None,
                )
                .on_conflict_do_nothing(
                    index_elements=[FlashcardDeck.lesson_id, FlashcardDeck.source]
                )
            )
            deck = await decks_repository.for_lesson(self.session, self.lesson_id)
        if deck is None:
            raise ApiError(409, "This deck is being built. Try again in a moment.")
        return deck

    async def _sync_cards(self, deck: FlashcardDeck, preview_cards: list[dict]) -> None:
        existing = await cards_repository.for_deck(self.session, deck.id)
        by_id = {card.id: card for card in existing}
        plan = plan_card_sync(
            [
                ExistingCard(
                    id=card.id,
                    source_key=card.source_key,
                    front=str((card.payload or {}).get("front") or ""),
                )
                for card in existing
            ],
            preview_cards,
        )
        for card_id, key in plan.adopt.items():
            by_id[card_id].source_key = key
        for card_id, fresh in plan.update.items():
            by_id[card_id].payload = fresh["payload"]
            by_id[card_id].card_type = fresh["cardType"]
        for fresh in plan.insert:
            # Concurrent syncs may both add a card; the loser's insert is a no-op.
            await self.session.execute(
                pg_insert(Flashcard)
                .values(
                    id=cuid(),
                    deck_id=deck.id,
                    card_type=fresh["cardType"],
                    payload=fresh["payload"],
                    source_key=fresh["sourceKey"],
                )
                .on_conflict_do_nothing(
                    index_elements=[Flashcard.deck_id, Flashcard.source_key]
                )
            )
        for card_id in plan.delete:
            await cards_repository.remove(self.session, by_id[card_id])


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
                # Column defaults only apply at INSERT, but the scheduler reads
                # these fields before the row is flushed, so state them here.
                state="NEW",
                ease_factor=INITIAL_EASE,
                stability=0.0,
                difficulty=seed_difficulty(card.difficulty),
                interval_days=0.0,
                repetitions=0,
                lapses=0,
                retention=0.0,
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
        if deck.source == "LESSON":
            # Shared with the cohort: leaving removes it from this student's
            # list, never from everyone's. Their review history is kept.
            await adopt_shared_deck(self.session, deck)
            existing = await enrollments_repository.for_student_deck(
                self.session, self.user_id, deck.id
            )
            await enrollments_repository.remove(self.session, existing)
            return {"deckId": self.deck_id}
        if deck.created_by != self.user_id:
            raise ApiError(403, "Not permitted")
        await decks_repository.remove(self.session, deck)
        return {"deckId": self.deck_id}


class GetFlashcardStatsService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        now = utcnow()
        owned = await decks_repository.owned_by(self.session, self.user_id)
        enrolled = await decks_repository.enrolled_for(self.session, self.user_id)
        seen = {deck.id for deck in owned}
        decks = [*owned, *[deck for deck in enrolled if deck.id not in seen]]
        cards = await cards_repository.list_where(
            self.session, Flashcard.deck_id.in_([deck.id for deck in decks])
        )
        reviews = await reviews_repository.for_cards(
            self.session, self.user_id, [card.id for card in cards]
        )
        by_card = {review.flashcard_id: review for review in reviews}
        logs = await review_logs_repository.since(
            self.session, self.user_id, now - timedelta(days=LOG_WINDOW_DAYS)
        )
        return {
            "stats": build_stats(
                now=now,
                decks=[DeckRow(deck.id, deck.title) for deck in decks],
                cards=[
                    CardRow(
                        card.id,
                        card.deck_id,
                        str((card.payload or {}).get("front") or ""),
                        card.difficulty,
                        _snapshot(by_card.get(card.id)),
                    )
                    for card in cards
                ],
                logs=[
                    LogRow(
                        log.flashcard_id,
                        log.reviewed_at,
                        log.rating,
                        log.response_time_ms,
                    )
                    for log in logs
                ],
                outcomes=await review_logs_repository.outcomes(
                    self.session, self.user_id
                ),
            )
        }


def _snapshot(review: FlashcardReview | None) -> ReviewSnapshot | None:
    if review is None:
        return None
    return ReviewSnapshot(
        state=review.state,
        difficulty=review.difficulty,
        stability=review.stability,
        interval_days=review.interval_days,
        lapses=review.lapses,
        due_at=review.due_at,
        last_reviewed_at=review.last_reviewed_at,
    )


class GetFlashcardRecommendationsService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        recommendations = [
            *await self._review_health(),
            *await self._unbuilt_lessons(),
        ]
        recommendations.sort(key=lambda rec: _PRIORITY_ORDER[rec["priority"]])
        return {"recommendations": recommendations[:MAX_RECOMMENDATIONS]}

    async def _review_health(self) -> list[dict]:
        """Leeches, overdue and fading cards, rolled up to one item per deck."""
        now = utcnow()
        started = [
            review
            for review in await reviews_repository.for_student(
                self.session, self.user_id
            )
            if review.state != "NEW"
        ]
        if not started:
            return []
        cards = await cards_repository.list_where(
            self.session, Flashcard.id.in_([r.flashcard_id for r in started])
        )
        deck_of = {card.id: card.deck_id for card in cards}
        outcomes = await review_logs_repository.outcomes(self.session, self.user_id)

        # Each card lands in its most serious bucket only, so one weak card is
        # never reported three times.
        buckets: dict[str, dict[str, int]] = {}
        for review in started:
            deck_id = deck_of.get(review.flashcard_id)
            if deck_id is None:
                continue
            reviews, successes = outcomes.get(review.flashcard_id, (0, 0))
            if is_leech(review.lapses, reviews, successes):
                kind = "leech"
            elif review.due_at and now - review.due_at > timedelta(
                days=max(review.interval_days * 2, OVERDUE_MIN_DAYS)
            ):
                kind = "overdue"
            elif review.retention < LOW_RETENTION:
                kind = "fading"
            else:
                continue
            counts = buckets.setdefault(deck_id, {})
            counts[kind] = counts.get(kind, 0) + 1

        decks = {
            deck.id: deck
            for deck in await decks_repository.list_where(
                self.session, FlashcardDeck.id.in_(list(buckets))
            )
        }
        out = []
        for deck_id, counts in buckets.items():
            deck = decks.get(deck_id)
            if deck is None:
                continue
            href = f"/flashcards/{deck_id}"
            if counts.get("leech"):
                n = counts["leech"]
                out.append(
                    {
                        "id": f"leech:{deck_id}",
                        "priority": "high",
                        "title": f"Relearn the hard cards in {deck.title}",
                        "rationale": f"{n} card{'s' if n != 1 else ''} keep slipping "
                        "after repeated misses. Revisit the lesson, then retry them.",
                        "href": href,
                        "deckId": deck_id,
                    }
                )
            if counts.get("overdue"):
                n = counts["overdue"]
                out.append(
                    {
                        "id": f"overdue:{deck_id}",
                        "priority": "high",
                        "title": f"Catch up on {deck.title}",
                        "rationale": f"{n} card{'s are' if n != 1 else ' is'} well "
                        "past due. The longer you wait, the more you forget.",
                        "href": href,
                        "deckId": deck_id,
                    }
                )
            if counts.get("fading"):
                n = counts["fading"]
                out.append(
                    {
                        "id": f"fading:{deck_id}",
                        "priority": "medium",
                        "title": f"Refresh {deck.title}",
                        "rationale": f"Your recall on {n} card{'s' if n != 1 else ''} "
                        "has dropped below 75%. A quick review will lock it back in.",
                        "href": href,
                        "deckId": deck_id,
                    }
                )
        return out

    async def _unbuilt_lessons(self) -> list[dict]:
        """Finished lessons that were never turned into cards."""
        completed = await progress_repository.completed_for_student(
            self.session, self.user_id
        )
        out = []
        for progress in completed:
            if await decks_repository.for_lesson(self.session, progress.lesson_id):
                continue
            lesson = await lessons_repository.by_id(self.session, progress.lesson_id)
            if lesson is None:
                continue
            out.append(
                {
                    "id": f"build:{lesson.id}",
                    "priority": "medium",
                    "title": f'Turn "{lesson.title}" into cards',
                    "rationale": "You finished this lesson. Cards help you remember "
                    "it weeks from now, not just today.",
                    "href": "/flashcards#build",
                }
            )
            if len(out) == MAX_UNBUILT_LESSONS:
                break
        return out


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
        followers = await enrollments_repository.count(
            self.session, FlashcardEnrollment.deck_id == deck.id
        )
        return {
            "deck": {
                "id": deck.id,
                "title": deck.title,
                "lessonId": deck.lesson_id,
                "source": deck.source,
                # Only a deck you made yourself is yours to delete. A deck built
                # from a lesson belongs to everyone; you can only leave it.
                "isOwner": deck.source != "LESSON" and deck.created_by == self.user_id,
                "followerCount": followers,
            },
            "queue": [
                {
                    "id": card.id,
                    "type": card.card_type,
                    "cardId": card.id,
                    "cardType": card.card_type,
                    "payload": card.payload,
                    "authoredDifficulty": card.difficulty or "INTERMEDIATE",
                    "nextIntervals": _next_intervals(
                        by_card.get(card.id), card.difficulty, now
                    ),
                }
                for card in queue
            ],
            "dueCount": len(due),
            "newCount": min(len(fresh), new_budget),
        }
