"""Study days and streaks. Pure.

A study day is a Lagos civil day with a qualifying learning action, never a
mere app open: a completed assessment attempt, or a real flashcard review
session (at least CARD_REVIEWS_FOR_STUDY_DAY cards). Lagos has no daylight
saving, so a civil day is always 24 hours.
"""

from collections.abc import Mapping

from app.services.auth.rules import current_streak
from app.services.planner.days import add_days

CARD_REVIEWS_FOR_STUDY_DAY = 5
CALENDAR_WEEKS = 12


def study_days(attempt_days: set[str], card_counts: Mapping[str, int]) -> set[str]:
    days = set(attempt_days)
    days.update(
        day for day, count in card_counts.items() if count >= CARD_REVIEWS_FOR_STUDY_DAY
    )
    return days


def display_streak(days: set[str], today: str) -> int:
    """The streak as shown to the student.

    Today still counts if it is not over: a streak that reached yesterday is
    alive until today ends, so it is shown rather than reset early.
    """
    if today in days:
        return current_streak(days, today)
    return current_streak(days, add_days(today, -1))


def longest_streak(days: set[str]) -> int:
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and add_days(previous, 1) == day else 1
        best = max(best, run)
        previous = day
    return best


def calendar_days(days: set[str], today: str, weeks: int = CALENDAR_WEEKS) -> list[str]:
    start = add_days(today, -(weeks * 7 - 1))
    return sorted(day for day in days if start <= day <= today)


def return_state(days: set[str], today: str) -> dict:
    past = [day for day in days if day <= today]
    if not past:
        return {"studiedToday": False, "daysSinceLastStudy": None}
    last = max(past)
    gap = 0
    cursor = last
    while cursor != today:
        cursor = add_days(cursor, 1)
        gap += 1
    return {"studiedToday": today in days, "daysSinceLastStudy": gap}
