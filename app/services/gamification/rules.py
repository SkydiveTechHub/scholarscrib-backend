"""What earns XP, and how much. Tune the numbers here and nowhere else.

The amounts are initial product proposals. The server computes every award;
no client ever supplies an amount.
"""

XP_AMOUNTS: dict[str, int] = {
    "lesson_completed": 30,
    "practice_set_completed": 20,
    "daily_quests_completed": 30,
    "topic_mastery_demonstrated": 50,
    "answer_corrected": 5,
}

# A practice set must have real substance, and a day only pays so many of them,
# so rapid-fire tiny attempts cannot farm XP.
MIN_PRACTICE_QUESTIONS = 5
PRACTICE_AWARDS_PER_DAY = 5

# Correcting a missed question pays once per question, only when the miss was on
# an earlier day (no instant wrong-then-right), and only so many a day.
CORRECTIONS_PER_DAY = 10

REASONS: dict[str, str] = {
    "lesson_completed": "Lesson completed",
    "practice_set_completed": "Practice set completed",
    "daily_quests_completed": "All daily quests completed",
    "topic_mastery_demonstrated": "Topic mastery demonstrated",
    "answer_corrected": "Corrected a missed question",
}


def practice_xp_eligible(answered: int, awarded_today: int) -> bool:
    return (
        answered >= MIN_PRACTICE_QUESTIONS and awarded_today < PRACTICE_AWARDS_PER_DAY
    )


def corrections_allowed(awarded_today: int) -> int:
    return max(CORRECTIONS_PER_DAY - awarded_today, 0)


def idempotency_key(event: str, student_id: str, source_id: str) -> str:
    """One key per rewarded thing. A repeat of the same source collides on it."""
    if event not in XP_AMOUNTS:
        raise KeyError(event)
    return f"{event}:{student_id}:{source_id}"


def events_for_attempt(
    *,
    completed: bool,
    exit_passed: bool | None,
    answered: int,
    practice_awarded_today: int,
) -> list[str]:
    """Which XP events a just-submitted attempt qualifies for.

    A lesson's practice exit pays lesson XP when passed and nothing when failed;
    any other attempt is an ordinary practice set.
    """
    if not completed:
        return []
    if exit_passed is True:
        return ["lesson_completed"]
    if exit_passed is False:
        return []
    if practice_xp_eligible(answered, practice_awarded_today):
        return ["practice_set_completed"]
    return []
