from collections.abc import Sequence
from dataclasses import dataclass

from app.domain import CLASS_LEVELS
from app.services.planner.days import DayKey, add_days, days_between

RUNWAY_FRACTION = 0.2
RUNWAY_MIN_DAYS = 14
RUNWAY_MAX_DAYS = 21
WINDOW_DAYS = 14
CARRY_OVER_DAYS = 14
MANUAL_LOOKBACK_DAYS = 7
MOCK_COUNT = 2
REVISION_OFFSETS = (1, 3, 7, 14)

PlanMode = str

DEFAULT_MINUTES = {
    "SS1": {"weekdayMinutes": 30, "weekendMinutes": 60},
    "SS2": {"weekdayMinutes": 45, "weekendMinutes": 90},
    "SS3": {"weekdayMinutes": 60, "weekendMinutes": 120},
}

EXAM_SHARE_MIN = 0.1
EXAM_SHARE_MAX = 0.5
RAMP_START_DAYS = 120
RAMP_END_DAYS = 42


def resolve_plan_mode(
    class_level: str | None, target_date: DayKey | None, force_exam_mode: bool
) -> PlanMode:
    if class_level != "SS3" or not target_date:
        return "TERM"
    return "EXAM" if force_exam_mode else "BLENDED"


def exam_share(days_to_exam: int) -> float:
    if days_to_exam >= RAMP_START_DAYS:
        return EXAM_SHARE_MIN
    if days_to_exam <= RAMP_END_DAYS:
        return EXAM_SHARE_MAX
    progress = (RAMP_START_DAYS - days_to_exam) / (RAMP_START_DAYS - RAMP_END_DAYS)
    return EXAM_SHARE_MIN + (EXAM_SHARE_MAX - EXAM_SHARE_MIN) * progress


def compute_runway_start(plan_start: DayKey, target_date: DayKey) -> DayKey:
    total_days = max(1, days_between(plan_start, target_date) + 1)
    runway_days = min(
        RUNWAY_MAX_DAYS,
        max(RUNWAY_MIN_DAYS, round(total_days * RUNWAY_FRACTION)),
        total_days,
    )
    return add_days(target_date, -(runway_days - 1))


@dataclass
class PlanSettingsCheck:
    class_level: str | None
    target_date: DayKey | None
    force_exam_mode: bool
    study_days: Sequence[int]
    weekday_minutes: int
    weekend_minutes: int
    today: DayKey


def plan_settings_problem(check: PlanSettingsCheck) -> str | None:
    if check.target_date and check.class_level != "SS3":
        return (
            "Exam dates are for SS3 students. Your plan will follow your school term."
        )
    if check.force_exam_mode and not check.target_date:
        return "Set an exam date before switching on exam mode."
    if check.target_date and check.target_date <= check.today:
        return "The exam date must be in the future."
    has_time = any(
        (check.weekend_minutes if day >= 6 else check.weekday_minutes) >= 30
        for day in check.study_days
    )
    if not has_time:
        return "Choose at least one study day that has study time on it."
    return None


def at_or_below_class(topic_level: str, class_level: str) -> bool:
    return CLASS_LEVELS.index(topic_level) <= CLASS_LEVELS.index(class_level)
