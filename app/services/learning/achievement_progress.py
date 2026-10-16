"""How far along a student is toward one achievement.

Count criteria (questions answered, streak days, ...) progress toward the
catalogue value. The rest are earned by a single event, so they are 0 or 1.
"""

ALL_OR_NOTHING = frozenset({"perfect_score", "mock_score_70"})
COUNTED = frozenset(
    {"questions_answered", "lessons_completed", "streak_days", "subject_mastery"}
)


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
