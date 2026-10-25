from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.services.planner.days import (
    DayKey,
    add_days,
    is_weekend,
    iso_weekday,
    monday_of,
)

SESSION_MINUTES = 30
SHORT_SESSION_MIN = 15


@dataclass
class SlotAllocation:
    """A subject the student put on a weekday (see planner/timetable.py)."""

    day: int
    subject_id: str
    minutes: int | None = None
    strength: str = "PREFERRED"

    @property
    def sessions(self) -> int:
        if self.minutes is None:
            return 1
        return max(1, self.minutes // SESSION_MINUTES)


@dataclass
class Availability:
    study_days: list[int]
    weekday_minutes: int
    weekend_minutes: int
    day_minutes: dict[int, int] = field(default_factory=dict)
    allocations: list[SlotAllocation] = field(default_factory=list)


@dataclass
class Slot:
    date: DayKey
    minutes: int
    short: bool
    catch_up: bool = False
    # Set when a timetable allocation reserves this slot for a subject.
    subject_id: str | None = None
    strength: str | None = None


@dataclass
class UnreservedSession:
    """An allocated session that found no slot on its day."""

    date: DayKey
    subject_id: str
    strength: str


def day_budget(date: DayKey, availability: Availability) -> int:
    weekday = iso_weekday(date)
    if weekday not in availability.study_days:
        return 0
    if weekday in availability.day_minutes:
        return availability.day_minutes[weekday]
    if is_weekend(date):
        return availability.weekend_minutes
    return availability.weekday_minutes


def build_slots(start: DayKey, days: int, availability: Availability) -> list[Slot]:
    slots: list[Slot] = []
    for offset in range(days):
        date = add_days(start, offset)
        budget = day_budget(date, availability)
        full = budget // SESSION_MINUTES
        for _ in range(full):
            slots.append(Slot(date=date, minutes=SESSION_MINUTES, short=False))
        rest = budget - full * SESSION_MINUTES
        if rest >= SHORT_SESSION_MIN:
            slots.append(Slot(date=date, minutes=rest, short=True))
    last_day: dict[DayKey, DayKey] = {}
    for slot in slots:
        if not slot.short:
            last_day[monday_of(slot.date)] = slot.date
    for date in last_day.values():
        first = next(
            (slot for slot in slots if slot.date == date and not slot.short), None
        )
        if first:
            first.catch_up = True
    return slots


def reserve_slots(
    days: Sequence[tuple[DayKey, list[Slot]]],
    allocations: Sequence[SlotAllocation],
    settled: Mapping[tuple[DayKey, str], int],
) -> list[UnreservedSession]:
    """Reserve each day's full slots for the subjects allocated to that weekday.

    Pinned allocations come first, then by subject id, so the same timetable
    always reserves the same slots. The weekly catch-up slot is reserved last,
    and only when the day has no other full slot to give; a reserved catch-up
    slot stops being one. `settled` is sessions already done today per subject
    (completed work), which are not reserved a second time. Short slots are never
    reserved. Sessions that do not fit are returned, never dropped silently.
    """
    by_weekday: dict[int, list[SlotAllocation]] = {}
    for row in allocations:
        by_weekday.setdefault(row.day, []).append(row)
    unreserved: list[UnreservedSession] = []
    for date, day_slots in days:
        rows = by_weekday.get(iso_weekday(date))
        if not rows:
            continue
        full = [slot for slot in day_slots if not slot.short]
        ordered = [s for s in full if not s.catch_up] + [s for s in full if s.catch_up]
        cursor = 0
        for row in sorted(rows, key=lambda r: (r.strength != "PINNED", r.subject_id)):
            wanted = row.sessions - settled.get((date, row.subject_id), 0)
            for _ in range(max(0, wanted)):
                if cursor >= len(ordered):
                    unreserved.append(
                        UnreservedSession(date, row.subject_id, row.strength)
                    )
                    continue
                slot = ordered[cursor]
                cursor += 1
                slot.subject_id = row.subject_id
                slot.strength = row.strength
                slot.catch_up = False
    return unreserved
