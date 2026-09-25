"""Learning-evidence math. SCORING_VERSION 2. Bump it to force a refold."""

SCORING_VERSION = 2
RECENCY_HALF_LIFE_DAYS = 45
PRIOR_STRENGTH = 4
PRIOR_OUTCOME = 0.45
RAPID_SECONDS = 3
RAPID_WEIGHT = 0.3
CONFIDENCE_FLOOR = 0.35
OBSERVATION_FLOOR = 3
ABANDONED_FLOOR = 2

DIFFICULTY_OUTCOMES = {
    "BASIC": (0.85, 0.0),
    "INTERMEDIATE": (1.0, 0.15),
    "ADVANCED": (1.0, 0.35),
}

CARD_OUTCOMES = {"AGAIN": 0.0, "HARD": 0.5, "GOOD": 0.85, "EASY": 1.0}

CHANNEL_WEIGHTS = {"acc": 0.45, "lesson": 0.35, "srs": 0.20}

TARGET = 70
GATE = 60
PRETEST_PASS = 80
STRONG_MASTERY = 85
DECAY_RETENTION = 0.85
WEAK_MASTERY = 50
GAP_RETENTION = 0.8
STALE_RETENTION = 0.9


def recency_weight(age_days: float) -> float:
    return 2 ** (-age_days / RECENCY_HALF_LIFE_DAYS)


def response_weight(age_days: float, seconds: float | None) -> float:
    weight = recency_weight(age_days)
    if seconds is not None and seconds < RAPID_SECONDS:
        return weight * RAPID_WEIGHT
    return weight


def question_outcome(difficulty: str | None, correct: bool) -> float:
    correct_value, wrong_value = DIFFICULTY_OUTCOMES.get(
        difficulty or "INTERMEDIATE",
        DIFFICULTY_OUTCOMES["INTERMEDIATE"],
    )
    return correct_value if correct else wrong_value


def channel_score(outcome: float, mass: float) -> tuple[float, float]:
    score = (outcome + PRIOR_STRENGTH * PRIOR_OUTCOME) / (mass + PRIOR_STRENGTH)
    confidence = mass / (mass + PRIOR_STRENGTH)
    return score, confidence


def mastery_level(score: int) -> tuple[str, int]:
    if score >= 85:
        return "STRONG", 60
    if score >= 70:
        return "COMPETENT", 30
    if score >= 50:
        return "DEVELOPING", 14
    return "WEAK", 5


def retention(days_since_effort: float, stability_days: float) -> float:
    if stability_days <= 0:
        return 0.0
    return (1 + (19 / 81) * (days_since_effort / stability_days)) ** -0.5
