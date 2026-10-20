"""Flashcard statistics, computed from plain rows so they can be tested alone.

Days are Lagos civil days, like streaks and plan days elsewhere. Retention is a
0..1 fraction; a leech's success rate is a whole percent, as the page prints it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.timeutil import as_utc, lagos_day_key
from app.services.learning.evidence import retention
from app.services.srs.scheduler import is_leech, seed_difficulty

ACTIVITY_DAYS = 14
WEEK_DAYS = 7
RETENTION_WINDOW_DAYS = 30
MAX_LEECHES = 10
# Reviews older than this are not read for activity or streaks.
LOG_WINDOW_DAYS = 120

EASY_BELOW = 4
HARD_FROM = 7

# A card is overdue once it is this many intervals (at least a week) past due, and
# fading once its predicted recall has dropped below the floor.
OVERDUE_MIN_DAYS = 7
LOW_RECALL = 0.75


@dataclass
class DeckRow:
    id: str
    title: str


@dataclass
class ReviewSnapshot:
    state: str
    difficulty: float
    stability: float
    interval_days: float
    lapses: int
    due_at: datetime | None
    last_reviewed_at: datetime | None

    @property
    def started(self) -> bool:
        return self.state != "NEW"


@dataclass
class CardRow:
    id: str
    deck_id: str
    front: str
    authored_difficulty: str | None
    review: ReviewSnapshot | None

    @property
    def started(self) -> bool:
        return self.review is not None and self.review.started


@dataclass
class LogRow:
    flashcard_id: str
    reviewed_at: datetime
    rating: str

    @property
    def successful(self) -> bool:
        return self.rating != "AGAIN"


def recall(review: ReviewSnapshot, now: datetime) -> float | None:
    """Predicted recall right now; None for a card that has no memory yet."""
    if review.stability <= 0 or review.last_reviewed_at is None:
        return None
    days = max(0.0, (now - as_utc(review.last_reviewed_at)).total_seconds() / 86400)
    return retention(days, review.stability)


def review_health(
    review: ReviewSnapshot, outcome: tuple[int, int], now: datetime
) -> str | None:
    """The most serious problem a started card has: leech, overdue, fading or None."""
    reviews, successes = outcome
    if is_leech(review.lapses, reviews, successes):
        return "leech"
    if review.due_at is not None and now - as_utc(review.due_at) > timedelta(
        days=max(review.interval_days * 2, OVERDUE_MIN_DAYS)
    ):
        return "overdue"
    current = recall(review, now)
    if current is not None and current < LOW_RECALL:
        return "fading"
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _day_keys(today: str, count: int) -> list[str]:
    start = datetime.strptime(today, "%Y-%m-%d")
    return [
        (start - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(count - 1, -1, -1)
    ]


def _streak(active_days: set[str], today: str) -> int:
    days = _day_keys(today, LOG_WINDOW_DAYS)[::-1]
    # A student who has not studied yet today keeps yesterday's streak.
    if today not in active_days:
        days = days[1:]
    count = 0
    for day in days:
        if day not in active_days:
            break
        count += 1
    return count


def _difficulty_bucket(card: CardRow) -> str:
    if card.started and card.review is not None:
        value = card.review.difficulty
    else:
        value = seed_difficulty(card.authored_difficulty)
    if value < EASY_BELOW:
        return "easy"
    return "hard" if value >= HARD_FROM else "medium"


def build_stats(
    *,
    now: datetime,
    decks: list[DeckRow],
    cards: list[CardRow],
    logs: list[LogRow],
    outcomes: dict[str, tuple[int, int]],
) -> dict:
    """`outcomes` maps card id to all-time (reviews, successful reviews)."""
    today = lagos_day_key(now)
    per_day: dict[str, list[int]] = {}
    for log in logs:
        counts = per_day.setdefault(lagos_day_key(log.reviewed_at), [0, 0])
        counts[0] += 1
        counts[1] += int(log.successful)

    activity = [
        {
            "date": day,
            "reviews": per_day.get(day, [0, 0])[0],
            "successful": per_day.get(day, [0, 0])[1],
        }
        for day in _day_keys(today, ACTIVITY_DAYS)
    ]
    week = _day_keys(today, WEEK_DAYS)

    recent_cutoff = _day_keys(today, RETENTION_WINDOW_DAYS)[0]
    recent = [log for log in logs if lagos_day_key(log.reviewed_at) >= recent_cutoff]

    graduated = [c.review for c in cards if c.review and c.review.state == "REVIEW"]

    by_deck: dict[str, list[CardRow]] = {deck.id: [] for deck in decks}
    for card in cards:
        by_deck.setdefault(card.deck_id, []).append(card)

    deck_rows = []
    for deck in decks:
        members = by_deck[deck.id]
        started = [c for c in members if c.started]
        recalls = [
            r
            for c in started
            if c.review is not None and (r := recall(c.review, now)) is not None
        ]
        deck_rows.append(
            {
                "deckId": deck.id,
                "title": deck.title,
                "total": len(members),
                "due": sum(
                    1
                    for c in started
                    if c.review is not None
                    and c.review.due_at is not None
                    and as_utc(c.review.due_at) <= now
                ),
                "fresh": len(members) - len(started),
                "reviewed": len(started),
                "retention": _mean(recalls),
            }
        )

    started_cards = [c for c in cards if c.started and c.review is not None]
    all_recalls: list[float] = []
    for card in started_cards:
        review = card.review
        if review is None:
            continue
        recalled = recall(review, now)
        if recalled is not None:
            all_recalls.append(recalled)
    mix = {"easy": 0, "medium": 0, "hard": 0}
    for card in cards:
        mix[_difficulty_bucket(card)] += 1

    deck_titles = {deck.id: deck.title for deck in decks}
    leeches = []
    for card in started_cards:
        review = card.review
        if review is None:
            continue
        reviews, successes = outcomes.get(card.id, (0, 0))
        if is_leech(review.lapses, reviews, successes):
            leeches.append(
                {
                    "cardId": card.id,
                    "prompt": card.front,
                    "deckTitle": deck_titles.get(card.deck_id, ""),
                    "lapses": review.lapses,
                    "reviews": reviews,
                    "successRate": round(successes / reviews * 100) if reviews else 0,
                }
            )
    leeches.sort(key=lambda row: (-row["lapses"], row["successRate"]))

    return {
        "reviewsThisWeek": sum(per_day.get(day, [0, 0])[0] for day in week),
        "cardsLearned": len(graduated),
        "measuredRetention": (
            sum(1 for log in recent if log.successful) / len(recent) if recent else None
        ),
        "predictedRetention": _mean(all_recalls),
        "streak": _streak({day for day, counts in per_day.items() if counts[0]}, today),
        "activity": activity,
        "decks": deck_rows,
        "difficultyMix": mix,
        "leechCards": leeches[:MAX_LEECHES],
        "totalDue": sum(row["due"] for row in deck_rows),
        "totalNew": sum(row["fresh"] for row in deck_rows),
    }
