import math
from dataclasses import dataclass

from app.services.learning.evidence import GATE, WEAK_MASTERY
from app.services.learning.graph import KnowledgeGraph, incoming_edges
from app.services.learning.mastery import TopicStateMap
from app.services.planner.days import (
    DayKey,
    add_days,
    days_between,
    is_weekend,
    monday_of,
)
from app.services.planner.mode import MOCK_COUNT, REVISION_OFFSETS, exam_share
from app.services.planner.slots import (
    SESSION_MINUTES,
    Slot,
    SlotAllocation,
    reserve_slots,
)
from app.services.planner.topics import SubjectSelection, TopicCandidate

GAP_FILL_SHARE = 0.2
REVISION_SHARE = 0.2
MOCK_MINUTES_CAP = 180
PAST_QUESTIONS_EVERY = 3


@dataclass
class WindowItemDraft:
    date: DayKey
    subject_id: str
    topic_id: str | None
    activity_type: str
    duration_minutes: int
    notes: str | None
    carried_from: DayKey | None = None


@dataclass
class FixedItem:
    date: DayKey
    subject_id: str
    duration_minutes: int


@dataclass
class RevisionDue:
    topic_id: str
    subject_id: str
    title: str
    reason: str


@dataclass
class UnmetAllocation:
    """A timetable session the planner could not give to its subject."""

    date: DayKey
    subject_id: str
    strength: str
    minutes: int
    reason: str


@dataclass
class Overload:
    topics_behind: int
    suggested_extra_minutes_per_week: int


@dataclass
class _Queue:
    candidate: TopicCandidate
    exam: bool
    rank: int
    units: list[str]
    lessons_left: int
    last_lesson_date: DayKey | None = None
    started: bool = False


@dataclass
class _PoolEntry:
    due_on: DayKey
    order: int
    topic_id: str
    subject_id: str
    note: str


def subject_cap(date: DayKey) -> int:
    return 3 if is_weekend(date) else 2


def _group_by_date(slots: list[Slot]) -> list[tuple[DayKey, list[Slot]]]:
    days: dict[DayKey, list[Slot]] = {}
    for slot in slots:
        days.setdefault(slot.date, []).append(
            Slot(slot.date, slot.minutes, slot.short, slot.catch_up)
        )
    return list(days.items())


def _free_days(
    slots: list[Slot], fixed: list[FixedItem]
) -> list[tuple[DayKey, list[Slot]]]:
    settled: dict[DayKey, list[FixedItem]] = {}
    for item in fixed:
        settled.setdefault(item.date, []).append(item)
    result = []
    for date, day_slots in _group_by_date(slots):
        for item in settled.get(date, []):
            # A full session uses full slots; a shorter one used the short slot.
            wanted = max(1, item.duration_minutes // SESSION_MINUTES)
            for _ in range(wanted):
                use = [s for s in day_slots if s.short == (item.duration_minutes < 30)]
                if not use:
                    use = day_slots
                if use:
                    day_slots.remove(use[-1])
        result.append((date, day_slots))
    return result


def _note_for(candidate: TopicCandidate, unit: str) -> str:
    title = candidate.topic.title
    if candidate.reason == "EXAM":
        return f"Exam prep — practise {title}"
    if unit == "PRACTICE":
        return f"Practise {title} questions"
    if candidate.reason == "CARRY_OVER":
        return "Catch-up — from a missed session"
    if candidate.reason == "GAP_FILL":
        return (
            f"Foundation for {candidate.unlocks}"
            if candidate.unlocks
            else "Build this foundation first"
        )
    if candidate.reason == "CURRENT":
        return "Your class is on this topic now"
    if candidate.reason == "CATCH_UP":
        return "Catch up with your class"
    return "Get ahead — coming up next in class"


def layout_window(
    *,
    mode: str,
    slots: list[Slot],
    target_date: DayKey | None,
    runway_start: DayKey | None,
    selections: list[SubjectSelection],
    exam_candidates: list[TopicCandidate],
    subject_ids: list[str],
    subject_names: dict[str, str],
    graph: KnowledgeGraph,
    state: TopicStateMap,
    pretest_passed: set[str],
    revision_due: list[RevisionDue],
    fixed: list[FixedItem],
    lessons_done: dict[str, int] | None = None,
    allocations: list[SlotAllocation] | None = None,
) -> tuple[list[WindowItemDraft], Overload | None, list[UnmetAllocation]]:
    days = _free_days(slots, fixed)
    done = lessons_done or {}
    settled: dict[tuple[DayKey, str], int] = {}
    for item in fixed:
        key = (item.date, item.subject_id)
        settled[key] = settled.get(key, 0) + item.duration_minutes // SESSION_MINUTES
    unmet: list[UnmetAllocation] = [
        UnmetAllocation(
            u.date, u.subject_id, u.strength, SESSION_MINUTES, "NO_CAPACITY"
        )
        for u in reserve_slots(days, allocations or [], settled)
    ]
    day_reserved: dict[DayKey, list[str]] = {
        date: sorted({s.subject_id for s in day_slots if s.subject_id})
        for date, day_slots in days
    }

    def cap_of(date: DayKey) -> int:
        # A day never allows fewer subjects than the student put on it.
        return max(subject_cap(date), len(day_reserved.get(date, ())))

    items: list[WindowItemDraft] = []
    day_subjects: dict[DayKey, set[str]] = {}

    def subjects_on(date: DayKey) -> set[str]:
        found = day_subjects.get(date)
        if found is None:
            found = set()
            day_subjects[date] = found
        return found

    for item in fixed:
        subjects_on(item.date).add(item.subject_id)

    def allow_subject(date: DayKey, subject_id: str) -> bool:
        chosen = subjects_on(date)
        return subject_id in chosen or len(chosen) < cap_of(date)

    def place(slot: Slot, draft: dict) -> None:
        subjects_on(slot.date).add(draft["subject_id"])
        items.append(
            WindowItemDraft(
                date=slot.date,
                duration_minutes=slot.minutes,
                subject_id=draft["subject_id"],
                topic_id=draft["topic_id"],
                activity_type=draft["activity_type"],
                notes=draft["notes"],
                carried_from=draft["carried_from"],
            )
        )

    queues: dict[str, _Queue] = {}
    behind_by: dict[str, int] = {}
    rank = 0

    def add_queue(candidate: TopicCandidate, exam: bool) -> None:
        nonlocal rank
        topic_id = candidate.topic.id
        if topic_id in queues:
            return
        mastery = state[topic_id].mastery if topic_id in state else 0
        # Lessons the student already finished in this plan are not taught again.
        lessons = (
            0
            if exam or topic_id in pretest_passed
            else max(
                0,
                max(1, math.ceil(candidate.topic.estimated_minutes / SESSION_MINUTES))
                - done.get(topic_id, 0),
            )
        )
        practice = 1 if exam else (2 if mastery < WEAK_MASTERY else 1)
        queues[topic_id] = _Queue(
            candidate=candidate,
            exam=exam,
            rank=rank,
            units=["LESSON"] * lessons + ["PRACTICE"] * practice,
            lessons_left=lessons,
        )
        rank += 1

    for selection in selections:
        behind_by[selection.subject_id] = selection.behind_by
        for candidate in selection.candidates:
            add_queue(candidate, False)
    for candidate in exam_candidates:
        add_queue(candidate, True)

    full_minutes = sum(
        slot.minutes
        for _date, day_slots in days
        for slot in day_slots
        if not slot.short
    )
    gap_minutes_left = math.floor(full_minutes * GAP_FILL_SHARE)

    def prereqs_ready(queue: _Queue, date: DayKey) -> bool:
        for edge in incoming_edges(graph, queue.candidate.topic.id):
            if edge.kind != "PREREQUISITE":
                continue
            planned = queues.get(edge.source)
            if planned and not planned.exam:
                if planned.lessons_left > 0:
                    return False
                if (
                    planned.last_lesson_date is not None
                    and planned.last_lesson_date >= date
                ):
                    return False
                continue
            mastery = state[edge.source].mastery if edge.source in state else 0
            if mastery < GATE * edge.strength and edge.source not in pretest_passed:
                return False
        return True

    def eligible(queue: _Queue, date: DayKey, slot: Slot) -> bool:
        if not queue.units:
            return False
        if not allow_subject(date, queue.candidate.topic.subject_id):
            return False
        if queue.candidate.reason == "GAP_FILL" and gap_minutes_left < slot.minutes:
            return False
        if not queue.started and not prereqs_ready(queue, date):
            return False
        unit = queue.units[0]
        return not (
            unit == "PRACTICE"
            and queue.last_lesson_date is not None
            and queue.last_lesson_date >= date
        )

    week_uses: dict[str, int] = {}

    def uses_of(date: DayKey, subject_id: str) -> int:
        return week_uses.get(f"{monday_of(date)}:{subject_id}", 0)

    catch_up_dates = {
        slot.date for _date, day_slots in days for slot in day_slots if slot.catch_up
    }

    def catch_up_ahead(slot: Slot) -> bool:
        return (not slot.catch_up) and any(
            date >= slot.date and monday_of(date) == monday_of(slot.date)
            for date in catch_up_dates
        )

    def pick_topic(
        slot: Slot, exam: bool, only: str | None = None, subject: str | None = None
    ) -> _Queue | None:
        options = [
            queue
            for queue in queues.values()
            if queue.exam == exam
            and (subject is None or queue.candidate.topic.subject_id == subject)
            and (
                queue.candidate.reason == only
                if only
                else queue.candidate.reason != "CARRY_OVER" or not catch_up_ahead(slot)
            )
            and eligible(queue, slot.date, slot)
        ]
        options.sort(
            key=lambda queue: (
                int(queue.candidate.reason == "PREVIEW"),
                uses_of(slot.date, queue.candidate.topic.subject_id),
                -(behind_by.get(queue.candidate.topic.subject_id, 0)),
                queue.candidate.topic.subject_id,
                -int(queue.started),
                queue.rank,
            )
        )
        return options[0] if options else None

    pool_order = 0
    first_date = slots[0].date if slots else ""
    pool: list[_PoolEntry] = []
    for due in revision_due:
        pool.append(
            _PoolEntry(first_date, pool_order, due.topic_id, due.subject_id, due.reason)
        )
        pool_order += 1
    revised_on: set[str] = set()

    def take_revision(date: DayKey, subject: str | None = None) -> _PoolEntry | None:
        options = [
            entry
            for entry in pool
            if (subject is None or entry.subject_id == subject)
            and entry.due_on <= date
            and f"{date}:{entry.topic_id}" not in revised_on
            and allow_subject(date, entry.subject_id)
        ]
        options.sort(key=lambda entry: (entry.due_on, entry.order))
        if not options:
            return None
        entry = options[0]
        pool.remove(entry)
        revised_on.add(f"{date}:{entry.topic_id}")
        return entry

    week_revisions: dict[DayKey, int] = {}
    week_slots: dict[DayKey, int] = {}

    def place_revision(slot: Slot, entry: _PoolEntry) -> None:
        week = monday_of(slot.date)
        week_revisions[week] = week_revisions.get(week, 0) + 1
        place(
            slot,
            {
                "subject_id": entry.subject_id,
                "topic_id": entry.topic_id,
                "activity_type": "REVISION",
                "notes": entry.note,
                "carried_from": None,
            },
        )

    def place_unit(slot: Slot, queue: _Queue) -> None:
        nonlocal gap_minutes_left, pool_order
        unit = queue.units.pop(0)
        topic = queue.candidate.topic
        queue.started = True
        if queue.candidate.reason == "GAP_FILL":
            gap_minutes_left -= slot.minutes
        key = f"{monday_of(slot.date)}:{topic.subject_id}"
        week_uses[key] = week_uses.get(key, 0) + 1
        if unit == "LESSON":
            queue.lessons_left -= 1
            queue.last_lesson_date = slot.date
            if queue.lessons_left == 0:
                for offset in REVISION_OFFSETS:
                    pool.append(
                        _PoolEntry(
                            add_days(slot.date, offset),
                            pool_order,
                            topic.id,
                            topic.subject_id,
                            f"Revision pass — {topic.title} (+{offset}d)",
                        )
                    )
                    pool_order += 1
        place(
            slot,
            {
                "subject_id": topic.subject_id,
                "topic_id": topic.id,
                "activity_type": unit,
                "notes": _note_for(queue.candidate, unit),
                "carried_from": queue.candidate.carried_from,
            },
        )

    subject_turn = 0

    def place_past_questions(slot: Slot, only: str | None = None) -> bool:
        nonlocal subject_turn
        if not subject_ids:
            return False
        for offset in range(len(subject_ids)):
            subject_id = subject_ids[(subject_turn + offset) % len(subject_ids)]
            if only is not None and subject_id != only:
                continue
            if not allow_subject(slot.date, subject_id):
                continue
            subject_turn = (subject_turn + offset + 1) % len(subject_ids)
            name = subject_names.get(subject_id)
            place(
                slot,
                {
                    "subject_id": subject_id,
                    "topic_id": None,
                    "activity_type": "PAST_QUESTIONS",
                    "notes": f"Past questions — {name}"
                    if name
                    else "Past questions practice",
                    "carried_from": None,
                },
            )
            return True
        return False

    pending_mocks: list[DayKey] = []
    if mode != "TERM" and runway_start and target_date:
        runway_length = days_between(runway_start, target_date) + 1
        for index in range(MOCK_COUNT):
            pending_mocks.append(
                add_days(runway_start, math.floor((runway_length * index) / MOCK_COUNT))
            )

    def in_runway(date: DayKey) -> bool:
        return mode != "TERM" and runway_start is not None and date >= runway_start

    def share_for(date: DayKey) -> float:
        if mode == "TERM":
            return 0
        if mode == "EXAM" or not target_date:
            return 1
        return exam_share(days_between(date, target_date))

    exam_credit = 0.0
    exam_slots = 0

    def place_work(slot: Slot, exam: bool, subject: str | None = None) -> bool:
        nonlocal exam_slots
        if exam:
            if mode == "TERM":
                return False
            exam_slots += 1
            if exam_slots % PAST_QUESTIONS_EVERY == 0 and place_past_questions(
                slot, subject
            ):
                return True
            queue = pick_topic(slot, True, subject=subject)
            if queue:
                place_unit(slot, queue)
                return True
            return place_past_questions(slot, subject)
        week = monday_of(slot.date)
        revisions_so_far = week_revisions.get(week, 0)
        if revisions_so_far + 1 <= (week_slots.get(week, 0)) * REVISION_SHARE:
            entry = take_revision(slot.date, subject)
            if entry:
                place_revision(slot, entry)
                return True
        queue = pick_topic(slot, False, subject=subject)
        if not queue:
            return False
        place_unit(slot, queue)
        return True

    def serve(slot: Slot, subject: str, want_exam: bool) -> bool:
        """Work for one subject in a reserved slot, revision last."""
        if place_work(slot, want_exam, subject) or place_work(
            slot, not want_exam, subject
        ):
            return True
        entry = take_revision(slot.date, subject)
        if entry:
            place_revision(slot, entry)
            return True
        return False

    def place_reserved(slot: Slot, want_exam: bool) -> None:
        """Fill a slot the student reserved for a subject.

        PINNED stays that subject's: with no eligible work it is left empty and
        reported. PREFERRED tries the day's other chosen subjects, then lets the
        planner choose. Either way an allocation that was not served is reported.
        """
        reserved = slot.subject_id or ""
        order = [reserved]
        if slot.strength != "PINNED":
            order += [s for s in day_reserved[slot.date] if s != reserved]
        served = next((s for s in order if serve(slot, s, want_exam)), None)
        if served != reserved:
            unmet.append(
                UnmetAllocation(
                    slot.date,
                    reserved,
                    slot.strength or "PREFERRED",
                    slot.minutes,
                    "NO_ELIGIBLE_WORK",
                )
            )
        if served is None and slot.strength != "PINNED":
            if place_work(slot, want_exam) or place_work(slot, not want_exam):
                return
            entry = take_revision(slot.date)
            if entry:
                place_revision(slot, entry)

    for date, day_slots in days:
        if not day_slots:
            continue
        if in_runway(date):
            if pending_mocks and pending_mocks[0] <= date:
                pending_mocks.pop(0)
                minutes = min(MOCK_MINUTES_CAP, sum(slot.minutes for slot in day_slots))
                if subject_ids:
                    subjects_on(date).add(subject_ids[0])
                    items.append(
                        WindowItemDraft(
                            date=date,
                            subject_id=subject_ids[0],
                            topic_id=None,
                            activity_type="MOCK_EXAM",
                            duration_minutes=minutes,
                            notes="Full mock exam — timed conditions",
                        )
                    )
                continue
            for slot in day_slots:
                entry = take_revision(date)
                if entry:
                    place_revision(slot, entry)
                elif not slot.short:
                    place_past_questions(slot)
            continue
        for slot in day_slots:
            if slot.short:
                entry = take_revision(date)
                if entry:
                    place_revision(slot, entry)
                continue
            week = monday_of(date)
            week_slots[week] = week_slots.get(week, 0) + 1
            if slot.subject_id is not None:
                nonlocal_credit = exam_credit + share_for(date)
                want_exam = nonlocal_credit >= 1
                exam_credit = nonlocal_credit - 1 if want_exam else nonlocal_credit
                place_reserved(slot, want_exam)
                continue
            if slot.catch_up:
                carried = pick_topic(slot, False, "CARRY_OVER")
                if carried:
                    place_unit(slot, carried)
                    continue
                entry = take_revision(date)
                if entry:
                    place_revision(slot, entry)
                    continue
            nonlocal_credit = exam_credit + share_for(date)
            want_exam = nonlocal_credit >= 1
            exam_credit = nonlocal_credit - 1 if want_exam else nonlocal_credit
            if place_work(slot, want_exam) or place_work(slot, not want_exam):
                continue
            entry = take_revision(date)
            if entry:
                place_revision(slot, entry)

    unfinished = [
        queue
        for queue in queues.values()
        if not queue.exam and queue.candidate.reason != "PREVIEW" and queue.units
    ]
    overload = None
    if unfinished and slots:
        unplaced = sum(len(queue.units) * SESSION_MINUTES for queue in unfinished)
        window_days = days_between(slots[0].date, slots[-1].date) + 1
        weeks = max(1, math.ceil(window_days / 7))
        overload = Overload(
            topics_behind=len(unfinished),
            suggested_extra_minutes_per_week=math.ceil(
                unplaced / weeks / SESSION_MINUTES
            )
            * SESSION_MINUTES,
        )
    # The exam runway replaces the timetable, so nothing is owed on those days.
    unmet = [u for u in unmet if not in_runway(u.date)]
    return items, overload, unmet
