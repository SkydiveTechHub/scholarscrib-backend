"""SM-2 ease plus an FSRS-style stability. Not stock SM-2 and not stock FSRS."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.learning.evidence import retention

EASE_FLOOR = 1.3
EASE_CEILING = 5.0
INITIAL_EASE = 2.5
DIFFICULTY_MIN = 1.0
DIFFICULTY_MAX = 10.0
INITIAL_DIFFICULTY = 5.0
MAX_INTERVAL_DAYS = 36500
MIN_INTERVAL_DAYS = 1 / 1440
LAPSE_STABILITY = 1.0
GRADUATE_REPS = 2

LEARNING_STEP_MINUTES = {"AGAIN": 1, "HARD": 5, "GOOD": 10, "EASY": 1440}
INITIAL_STABILITY = {"AGAIN": 0.1, "HARD": 0.5, "GOOD": 1.0, "EASY": 2.0}
LEARNING_GROWTH = {"HARD": 1.0, "GOOD": 1.6, "EASY": 2.5}
REVIEW_GROWTH = {"HARD": 0.8, "GOOD": 1.0, "EASY": 1.3}
QUALITY = {"AGAIN": 1, "HARD": 3, "GOOD": 4, "EASY": 5}
RATING_INDEX = {"AGAIN": 0, "HARD": 1, "GOOD": 2, "EASY": 3}
SEED_DIFFICULTY = {"BASIC": 3, "INTERMEDIATE": 5, "ADVANCED": 7}

DAILY_NEW_BUDGET = 20
LEECH_LAPSES = 4
LOW_RETENTION = 0.75


@dataclass
class CardSchedule:
    state: str = "NEW"
    ease_factor: float = INITIAL_EASE
    stability: float = 0.0
    difficulty: float = INITIAL_DIFFICULTY
    interval_days: float = 0.0
    repetitions: int = 0
    lapses: int = 0
    retention: float = 0.0
    due_at: datetime | None = None


def seed_difficulty(difficulty: str | None) -> float:
    value = SEED_DIFFICULTY.get(difficulty or "INTERMEDIATE", INITIAL_DIFFICULTY)
    return min(DIFFICULTY_MAX, max(DIFFICULTY_MIN, value))


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _update_ease(ease: float, rating: str) -> float:
    quality = QUALITY[rating]
    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    return _clamp(ease + delta, EASE_FLOOR, EASE_CEILING)


def _update_difficulty(difficulty: float, rating: str) -> float:
    delta = -0.7 * (RATING_INDEX[rating] - 2) + 0.02 * (5 - difficulty)
    return _clamp(difficulty + delta, DIFFICULTY_MIN, DIFFICULTY_MAX)


def _due(now: datetime, days: float) -> datetime:
    return now + timedelta(days=days)


def _remember(stability: float, interval_days: float) -> float:
    if stability <= 0:
        return 0.0
    return retention(interval_days, stability)


def review_card(card: CardSchedule, rating: str, now: datetime) -> CardSchedule:
    ease = _update_ease(card.ease_factor, rating)
    difficulty = _update_difficulty(card.difficulty, rating)
    state = card.state
    stability = card.stability
    repetitions = card.repetitions
    lapses = card.lapses

    if state == "NEW":
        stability = INITIAL_STABILITY[rating]
        if rating == "EASY":
            interval = max(1, round(stability))
            return CardSchedule(
                "REVIEW",
                ease,
                stability,
                difficulty,
                interval,
                1,
                lapses,
                _remember(stability, interval),
                _due(now, interval),
            )
        step = LEARNING_STEP_MINUTES[rating] / 1440
        return CardSchedule(
            "LEARNING",
            ease,
            stability,
            difficulty,
            step,
            0 if rating == "AGAIN" else 1,
            lapses,
            _remember(stability, step),
            _due(now, step),
        )

    if state in {"LEARNING", "RELEARNING"}:
        if rating == "AGAIN":
            stability = INITIAL_STABILITY["AGAIN"]
            step = LEARNING_STEP_MINUTES["AGAIN"] / 1440
            return CardSchedule(
                state,
                ease,
                stability,
                difficulty,
                step,
                0,
                lapses,
                _remember(stability, step),
                _due(now, step),
            )
        growth = LEARNING_GROWTH[rating]
        stability = max(stability, INITIAL_STABILITY[rating]) * growth
        repetitions += 1
        if repetitions >= GRADUATE_REPS:
            interval = max(1, round(stability))
            interval = min(MAX_INTERVAL_DAYS, interval)
            return CardSchedule(
                "REVIEW",
                ease,
                stability,
                difficulty,
                interval,
                repetitions,
                lapses,
                _remember(stability, interval),
                _due(now, interval),
            )
        step = max(MIN_INTERVAL_DAYS, LEARNING_STEP_MINUTES[rating] / 1440)
        return CardSchedule(
            state,
            ease,
            stability,
            difficulty,
            step,
            repetitions,
            lapses,
            _remember(stability, step),
            _due(now, step),
        )

    if rating == "AGAIN":
        step = LEARNING_STEP_MINUTES["AGAIN"] / 1440
        return CardSchedule(
            "RELEARNING",
            ease,
            LAPSE_STABILITY,
            difficulty,
            step,
            0,
            lapses + 1,
            _remember(LAPSE_STABILITY, step),
            _due(now, step),
        )
    growth = REVIEW_GROWTH[rating]
    stability = stability * ease * growth
    interval = min(MAX_INTERVAL_DAYS, max(1, round(stability)))
    return CardSchedule(
        "REVIEW",
        ease,
        stability,
        difficulty,
        interval,
        repetitions + 1,
        lapses,
        _remember(stability, interval),
        _due(now, interval),
    )


def is_leech(lapses: int, reviews: int, successes: int) -> bool:
    if lapses >= LEECH_LAPSES:
        return True
    return bool(reviews >= 8 and successes / reviews < 0.35)
