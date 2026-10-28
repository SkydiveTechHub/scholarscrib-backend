"""How far along a student is toward one achievement.

Count criteria (questions answered, streak days, ...) progress toward the
catalogue value. The rest are earned by a single event, so they are 0 or 1.
"""

ALL_OR_NOTHING = frozenset({"perfect_score", "mock_score_70", "mock_improvement"})
COUNTED = frozenset(
    {
        "questions_answered",
        "lessons_completed",
        "streak_days",
        "subject_mastery",
        "cards_reviewed",
        "questions_corrected",
    }
)

# Which group of the Achievements page a criterion belongs to. This is the one
# place a badge's category is decided.
CATEGORY_FOR = {
    "streak_days": "consistency",
    "questions_answered": "practice",
    "perfect_score": "practice",
    "lessons_completed": "practice",
    "cards_reviewed": "revision",
    "questions_corrected": "improvement",
    "mock_improvement": "exam_readiness",
    "subject_mastery": "mastery",
    "mock_score_70": "exam_readiness",
}


def category_for(criteria: str) -> str:
    return CATEGORY_FOR.get(criteria, "practice")


def target_for(criteria: str, value: int) -> int:
    if criteria in ALL_OR_NOTHING:
        return 1
    return max(value, 1)


def progress_for(criteria: str, raw: int, value: int) -> int:
    """`raw` is the student's current figure; the result never exceeds the target."""
    if criteria in ALL_OR_NOTHING:
        return 1 if raw > 0 else 0
    if criteria not in COUNTED:
        return 0
    return min(max(raw, 0), target_for(criteria, value))
