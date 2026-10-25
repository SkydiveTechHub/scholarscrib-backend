"""The student's timetable intent: validation and storage shape.

A timetable never holds generated tasks; those are `StudyPlanItem` rows. It holds
what the student asked for, and the planner reads it (Phase 4):

- `dayMinutes` overrides the plan's weekday/weekend minutes for single days. The
  plan's `studyDays` stay the one list of study days; a weekday outside it is a
  rest day, so there is no separate rest-day field to keep in sync.
- `allocations` put a subject on a weekday, PREFERRED (the planner may move it)
  or PINNED (the planner keeps it).

`NULL` in `StudyPlan.timetable` is a Smart plan. A stored Smart timetable with
nothing in it is never written; it is normalized to `NULL`.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from app.api.schemas import TimetableIn
from app.services.planner.slots import SESSION_MINUTES

DAY_NAMES = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}


def load_timetable(raw: Mapping | None) -> TimetableIn | None:
    """A stored timetable as a model; `None` for Smart plans and legacy rows."""
    if not raw:
        return None
    return TimetableIn.model_validate(raw)


def stored_timetable(timetable: TimetableIn | None) -> dict | None:
    """Canonical JSON for the column: sorted, so equal intent is equal data."""
    if timetable is None:
        return None
    if (
        timetable.mode == "SMART"
        and not timetable.allocations
        and not timetable.dayMinutes
    ):
        return None
    ordered = timetable.model_copy(
        update={
            "dayMinutes": sorted(timetable.dayMinutes, key=lambda row: row.day),
            "allocations": sorted(
                timetable.allocations, key=lambda row: (row.day, row.subjectId)
            ),
        }
    )
    return ordered.model_dump(mode="json")


def day_minutes_overrides(timetable: TimetableIn | None) -> dict[int, int]:
    if timetable is None:
        return {}
    return {row.day: row.minutes for row in timetable.dayMinutes}


def minutes_for_day(
    day: int,
    weekday_minutes: int,
    weekend_minutes: int,
    overrides: Mapping[int, int],
) -> int:
    if day in overrides:
        return overrides[day]
    return weekend_minutes if day >= 6 else weekday_minutes


def day_capacity(
    day: int,
    weekday_minutes: int,
    weekend_minutes: int,
    overrides: Mapping[int, int],
) -> int:
    """Minutes of whole sessions a day holds; a short remainder carries revision."""
    budget = minutes_for_day(day, weekday_minutes, weekend_minutes, overrides)
    return budget // SESSION_MINUTES * SESSION_MINUTES


def allocation_minutes(minutes: int | None) -> int:
    """Capacity an allocation reserves; unset means one planner session."""
    return SESSION_MINUTES if minutes is None else minutes


@dataclass(frozen=True)
class Issue:
    """One thing wrong with, or worth knowing about, a timetable."""

    code: str
    message: str
    day: int | None = None
    subject_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "day": self.day,
            "subjectId": self.subject_id,
        }


def timetable_issues(
    timetable: TimetableIn | None,
    *,
    study_days: Collection[int],
    weekday_minutes: int,
    weekend_minutes: int,
    subject_ids: Collection[str],
) -> list[Issue]:
    """Every reason a timetable cannot be saved, in a stable order.

    Shape (ranges, enums, list sizes) is checked by the schema. This covers the
    rules that need the rest of the plan.
    """
    if timetable is None:
        return []
    issues: list[Issue] = []
    overrides: dict[int, int] = {}
    seen_days: set[int] = set()
    for row in timetable.dayMinutes:
        if row.day in seen_days:
            issues.append(
                Issue(
                    "DAY_TIME_DUPLICATE",
                    f"{DAY_NAMES[row.day]} has more than one time set.",
                    day=row.day,
                )
            )
        elif row.day not in study_days:
            issues.append(
                Issue(
                    "REST_DAY",
                    f"{DAY_NAMES[row.day]} is a rest day, so it cannot have "
                    "study time.",
                    day=row.day,
                )
            )
        else:
            overrides[row.day] = row.minutes
        seen_days.add(row.day)
    if timetable.mode == "SMART" and timetable.allocations:
        issues.append(
            Issue("NEEDS_CUSTOM", "Subjects on days need the custom subjects option.")
        )
    if timetable.mode == "CUSTOM" and not timetable.allocations:
        issues.append(
            Issue(
                "NO_ALLOCATIONS",
                "Put at least one subject on a day, or keep the smart timetable.",
            )
        )
    seen: set[tuple[int, str]] = set()
    reserved: dict[int, int] = {}
    for row in timetable.allocations:
        key = (row.day, row.subjectId)
        if row.day not in study_days:
            issues.append(
                Issue(
                    "REST_DAY",
                    f"{DAY_NAMES[row.day]} is a rest day, so it cannot have a subject.",
                    day=row.day,
                    subject_id=row.subjectId,
                )
            )
        elif row.subjectId not in subject_ids:
            issues.append(
                Issue(
                    "SUBJECT_NOT_IN_PLAN",
                    "A subject on your timetable is not part of this plan.",
                    day=row.day,
                    subject_id=row.subjectId,
                )
            )
        elif key in seen:
            issues.append(
                Issue(
                    "DUPLICATE_ALLOCATION",
                    f"A subject is on {DAY_NAMES[row.day]} more than once.",
                    day=row.day,
                    subject_id=row.subjectId,
                )
            )
        elif row.minutes is not None and row.minutes % SESSION_MINUTES:
            issues.append(
                Issue(
                    "BAD_STEP",
                    f"Subject time must be in steps of {SESSION_MINUTES} minutes.",
                    day=row.day,
                    subject_id=row.subjectId,
                )
            )
        else:
            seen.add(key)
            reserved[row.day] = reserved.get(row.day, 0) + allocation_minutes(
                row.minutes
            )
    for day, total in sorted(reserved.items()):
        capacity = day_capacity(day, weekday_minutes, weekend_minutes, overrides)
        if total > capacity:
            issues.append(
                Issue(
                    "OVER_CAPACITY",
                    f"{DAY_NAMES[day]} has {total} minutes of subjects but only "
                    f"{capacity} minutes of study time.",
                    day=day,
                )
            )
    return issues


def timetable_problem(
    timetable: TimetableIn | None,
    *,
    study_days: Collection[int],
    weekday_minutes: int,
    weekend_minutes: int,
    subject_ids: Collection[str],
) -> str | None:
    """The first reason a timetable cannot be saved, or None."""
    issues = timetable_issues(
        timetable,
        study_days=study_days,
        weekday_minutes=weekday_minutes,
        weekend_minutes=weekend_minutes,
        subject_ids=subject_ids,
    )
    return issues[0].message if issues else None


def timetable_summary(
    timetable: TimetableIn | None,
    *,
    study_days: Collection[int],
    weekday_minutes: int,
    weekend_minutes: int,
    subject_ids: Collection[str],
) -> dict:
    """The week as the student asked for it: time, allocations, rest days.

    Assumes the timetable is valid. It describes the request, not the schedule
    the planner will produce from it.
    """
    overrides = day_minutes_overrides(timetable)
    allocations = timetable.allocations if timetable else []
    days = []
    for day in sorted(study_days):
        rows = [row for row in allocations if row.day == day]
        available = minutes_for_day(day, weekday_minutes, weekend_minutes, overrides)
        allocated = sum(allocation_minutes(row.minutes) for row in rows)
        days.append(
            {
                "day": day,
                "name": DAY_NAMES[day],
                "availableMinutes": available,
                "allocatedMinutes": allocated,
                "unallocatedMinutes": max(0, available - allocated),
                "subjects": [
                    {
                        "subjectId": row.subjectId,
                        "minutes": allocation_minutes(row.minutes),
                        "strength": row.strength,
                    }
                    for row in sorted(rows, key=lambda row: row.subjectId)
                ],
            }
        )
    coverage = []
    for subject_id in subject_ids:
        rows = [row for row in allocations if row.subjectId == subject_id]
        coverage.append(
            {
                "subjectId": subject_id,
                "daysPerWeek": len(rows),
                "minutesPerWeek": sum(allocation_minutes(row.minutes) for row in rows),
                "pinnedDays": sum(row.strength == "PINNED" for row in rows),
            }
        )
    return {
        "mode": timetable.mode if timetable else "SMART",
        "weeklyAvailableMinutes": sum(day["availableMinutes"] for day in days),
        "weeklyAllocatedMinutes": sum(day["allocatedMinutes"] for day in days),
        "days": days,
        "restDays": [
            {"day": day, "name": DAY_NAMES[day]}
            for day in sorted(DAY_NAMES)
            if day not in study_days
        ],
        "coverage": coverage,
    }


def timetable_warnings(
    timetable: TimetableIn | None,
    *,
    study_days: Collection[int],
    weekday_minutes: int,
    weekend_minutes: int,
    subject_names: Mapping[str, str],
    runway_days: int | None = None,
) -> list[Issue]:
    """Things a valid timetable does not make obvious. None blocks saving."""
    warnings: list[Issue] = []
    overrides = day_minutes_overrides(timetable)
    if timetable is not None and timetable.mode == "CUSTOM":
        placed = {row.subjectId for row in timetable.allocations}
        for subject_id, name in subject_names.items():
            if subject_id not in placed:
                warnings.append(
                    Issue(
                        "SUBJECT_NOT_ON_TIMETABLE",
                        f"{name} is not on your timetable yet, so it only gets "
                        "time that is left over.",
                        subject_id=subject_id,
                    )
                )
        reserved: dict[int, int] = {}
        for row in timetable.allocations:
            reserved[row.day] = reserved.get(row.day, 0) + allocation_minutes(
                row.minutes
            )
        for day, total in sorted(reserved.items()):
            capacity = day_capacity(day, weekday_minutes, weekend_minutes, overrides)
            if capacity and total >= capacity:
                warnings.append(
                    Issue(
                        "DAY_FULLY_BOOKED",
                        f"{DAY_NAMES[day]} is fully booked, so there is no room "
                        "for revision or catching up that day.",
                        day=day,
                    )
                )
    if runway_days:
        warnings.append(
            Issue(
                "RUNWAY_REPLACES_TIMETABLE",
                f"In the last {runway_days} days before your exam, mock exams "
                "and past questions replace your usual timetable.",
            )
        )
    return warnings
