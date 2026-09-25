from dataclasses import dataclass

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
class Availability:
    study_days: list[int]
    weekday_minutes: int
    weekend_minutes: int


@dataclass
class Slot:
    date: DayKey
    minutes: int
    short: bool
    catch_up: bool = False


def day_budget(date: DayKey, availability: Availability) -> int:
    if iso_weekday(date) not in availability.study_days:
        return 0
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
