"""Timetable-aware layout.

Rules under test (see the plan document, section 10):

- An allocation reserves whole 30-minute sessions on its weekday; PINNED first.
- A PINNED slot is only ever its subject's. With no eligible work it stays empty
  and is reported. A PREFERRED slot tries the day's other chosen subjects, then
  the planner's normal choice. Any allocation not served is reported.
- Topic selection, prerequisites, practice timing, revision and exam rules are
  unchanged: allocations only decide which subject a slot may serve.
- The exam runway replaces the timetable.
"""

from collections import Counter

from app.services.learning.graph import GraphEdge
from app.services.planner.days import iso_weekday
from app.services.planner.layout import FixedItem
from app.services.planner.slots import Availability, SlotAllocation
from app.services.planner.term_plan import PlannerSubject
from app.services.planner.topics import CarryOver
from tests.test_planner_baseline import (
    TODAY,
    by_date,
    maths_and_english,
    plan,
    signature,
    topic,
)

DAYS = [1, 2, 3, 4, 6]


def custom(*allocations, day_minutes=None, weekday=60, weekend=120) -> Availability:
    return Availability(DAYS, weekday, weekend, day_minutes or {}, list(allocations))


def alloc(day, subject, minutes=None, strength="PREFERRED") -> SlotAllocation:
    return SlotAllocation(day, subject, minutes, strength)


def on_weekday(output, weekday):
    return [i for i in output["items"] if iso_weekday(i.date) == weekday]


def subjects_of(items):
    return {i.subject_id for i in items}


# --- Smart plans are untouched ----


def test_no_allocations_and_no_overrides_plan_exactly_as_before():
    plain = plan(maths_and_english())
    explicit = plan(
        maths_and_english(), availability=Availability(DAYS, 60, 120, {}, [])
    )
    assert signature(plain) == signature(explicit)
    assert plain["unmet"] == []


# --- reservation ----


def test_a_preferred_subject_gets_its_day():
    output = plan(
        maths_and_english(),
        availability=custom(
            alloc(1, "eng"), alloc(1, "eng") if False else alloc(2, "maths")
        ),
    )
    monday = [i for i in on_weekday(output, 1) if i.duration_minutes == 30]
    tuesday = [i for i in on_weekday(output, 2) if i.duration_minutes == 30]
    # Monday has two full sessions: one reserved for English, one free.
    assert "eng" in subjects_of(monday)
    assert "maths" in subjects_of(tuesday)
    assert output["unmet"] == []


def test_every_reserved_session_goes_to_its_subject_on_that_day():
    availability = custom(
        alloc(1, "eng", strength="PINNED"), alloc(1, "maths", strength="PINNED")
    )
    output = plan(maths_and_english(), availability=availability)
    for date, items in by_date(output["items"]).items():
        if iso_weekday(date) == 1:
            assert subjects_of(items) == {"maths", "eng"}
            assert Counter(i.subject_id for i in items) == {"maths": 1, "eng": 1}


def test_one_subject_can_take_several_days_and_several_sessions():
    availability = custom(
        alloc(1, "maths", 60, "PINNED"), alloc(3, "maths", 30, "PINNED")
    )
    output = plan(maths_and_english(), availability=availability)
    assert {
        i.subject_id for i in on_weekday(output, 1) if i.duration_minutes == 30
    } == {"maths"}
    per_monday = Counter(
        i.date for i in on_weekday(output, 1) if i.subject_id == "maths"
    )
    assert set(per_monday.values()) == {2}
    assert len(per_monday) == 2  # both Mondays in the window


def test_a_subject_not_on_the_timetable_still_gets_the_time_left_over():
    # Monday: one session reserved, one free; English is not allocated anywhere.
    output = plan(maths_and_english(), availability=custom(alloc(1, "maths")))
    assert "eng" in subjects_of(output["items"])


def test_rest_days_stay_empty_with_a_timetable():
    output = plan(
        maths_and_english(), availability=custom(alloc(1, "maths"), alloc(6, "eng"))
    )
    assert {iso_weekday(i.date) for i in output["items"]} <= set(DAYS)


def test_a_day_is_never_filled_past_its_minutes_with_a_timetable():
    availability = custom(alloc(1, "maths", 30), alloc(1, "eng", 30), weekday=60)
    output = plan(maths_and_english(), availability=availability)
    for date, items in by_date(output["items"]).items():
        budget = 120 if iso_weekday(date) >= 6 else 60
        assert sum(i.duration_minutes for i in items) <= budget


def test_a_day_override_changes_that_days_sessions_only():
    availability = custom(day_minutes={1: 90})
    output = plan(maths_and_english(), availability=availability)
    monday = [i for i in on_weekday(output, 1) if i.duration_minutes == 30]
    tuesday = [i for i in on_weekday(output, 2) if i.duration_minutes == 30]
    assert len({i.date for i in monday}) == 2
    assert Counter(i.date for i in monday)["2026-09-14"] == 3
    assert Counter(i.date for i in tuesday)["2026-09-15"] == 2


def test_the_same_timetable_always_gives_the_same_plan():
    availability = custom(
        alloc(1, "eng", strength="PINNED"), alloc(2, "maths"), alloc(6, "eng", 60)
    )
    first = plan(maths_and_english(), availability=availability)
    second = plan(maths_and_english(), availability=availability)
    assert signature(first) == signature(second)
    assert first["unmet"] == second["unmet"]


# --- subject cap ----


def test_the_cap_rises_to_the_number_of_subjects_the_student_chose():
    subjects = [
        PlannerSubject(s, s.title(), [topic(f"{s[0]}{n}", s) for n in range(1, 4)])
        for s in ("maths", "eng", "phy")
    ]
    availability = custom(
        alloc(1, "maths", strength="PINNED"),
        alloc(1, "eng", strength="PINNED"),
        alloc(1, "phy", strength="PINNED"),
        weekday=90,
    )
    output = plan(subjects, availability=availability)
    assert subjects_of(on_weekday(output, 1)) == {"maths", "eng", "phy"}


def test_smart_plans_keep_the_two_subject_weekday_cap():
    subjects = [
        PlannerSubject(s, s.title(), [topic(f"{s[0]}{n}", s) for n in range(1, 8)])
        for s in ("maths", "eng", "phy")
    ]
    output = plan(subjects, availability=Availability(DAYS, 90, 120))
    for date, items in by_date(output["items"]).items():
        if iso_weekday(date) < 6:
            assert len(subjects_of(items)) <= 2


# --- unmet allocations and no fabricated work ----


def test_a_pinned_slot_with_no_work_is_left_empty_and_reported():
    subjects = [
        PlannerSubject("maths", "Mathematics", [topic("m1")]),
        PlannerSubject("eng", "English", [topic(f"e{n}", "eng") for n in range(1, 8)]),
        PlannerSubject("art", "Art", []),
    ]
    availability = custom(alloc(1, "art", strength="PINNED"))
    output = plan(subjects, availability=availability)
    assert "art" not in subjects_of(output["items"])
    reported = [u for u in output["unmet"] if u["subjectId"] == "art"]
    assert {u["strength"] for u in reported} == {"PINNED"}
    assert all(u["reason"] == "NO_ELIGIBLE_WORK" for u in reported)
    assert {iso_weekday(u["date"]) for u in reported} == {1}
    # The reserved slot was not given to anyone else.
    mondays = by_date([i for i in output["items"] if iso_weekday(i.date) == 1])
    for items in mondays.values():
        assert sum(i.duration_minutes == 30 for i in items) <= 1


def test_a_preferred_slot_with_no_work_goes_to_the_days_other_subject():
    subjects = [
        PlannerSubject("art", "Art", []),
        PlannerSubject("eng", "English", [topic(f"e{n}", "eng") for n in range(1, 8)]),
    ]
    availability = custom(alloc(1, "art"), alloc(1, "eng"))
    output = plan(subjects, availability=availability)
    assert "art" not in subjects_of(output["items"])
    monday = [i for i in on_weekday(output, 1) if i.duration_minutes == 30]
    assert {i.subject_id for i in monday} == {"eng"}
    assert all(u["strength"] == "PREFERRED" for u in output["unmet"])
    assert {u["subjectId"] for u in output["unmet"]} == {"art"}


def test_an_unserved_preferred_slot_is_released_to_the_planner():
    subjects = [
        PlannerSubject("art", "Art", []),
        PlannerSubject("eng", "English", [topic(f"e{n}", "eng") for n in range(1, 8)]),
    ]
    output = plan(subjects, availability=custom(alloc(1, "art")))
    monday = [i for i in on_weekday(output, 1) if i.duration_minutes == 30]
    assert {i.subject_id for i in monday} == {"eng"}
    assert {u["subjectId"] for u in output["unmet"]} == {"art"}


def test_a_subject_with_no_topics_never_gets_invented_tasks():
    subjects = [PlannerSubject("art", "Art", [])]
    output = plan(subjects, availability=custom(alloc(1, "art", strength="PINNED")))
    assert output["items"] == []
    assert output["unmet"]


# --- academic rules still hold under a timetable ----


def test_a_dependent_topic_still_waits_for_its_prerequisites_lessons():
    subjects = [
        PlannerSubject("maths", "Mathematics", [topic("m1", minutes=60), topic("m2")])
    ]
    availability = custom(alloc(1, "maths", 60, "PINNED"))
    output = plan(
        subjects,
        edges=[GraphEdge("e1", "m1", "m2")],
        availability=availability,
        positions={"maths": "m2"},
    )
    m1_lessons = [
        i.date
        for i in output["items"]
        if i.topic_id == "m1" and i.activity_type == "LESSON"
    ]
    m2_units = [
        i.date
        for i in output["items"]
        if i.topic_id == "m2" and i.activity_type != "REVISION"
    ]
    assert m1_lessons and m2_units
    assert min(m2_units) > max(m1_lessons)


def test_practice_never_shares_a_day_with_the_last_lesson_under_a_timetable():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1", minutes=60)])]
    availability = custom(
        alloc(1, "maths", 60, "PINNED"), alloc(2, "maths", 60, "PINNED")
    )
    output = plan(subjects, availability=availability)
    lessons = [i.date for i in output["items"] if i.activity_type == "LESSON"]
    practice = [i.date for i in output["items"] if i.activity_type == "PRACTICE"]
    assert lessons and practice
    assert min(practice) > max(lessons)


def test_a_passed_pretest_still_skips_lessons_under_a_timetable():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1", minutes=90)])]
    output = plan(
        subjects,
        availability=custom(alloc(1, "maths", 60, "PINNED")),
        pretest_passed={"m1"},
    )
    assert "LESSON" not in {i.activity_type for i in output["items"]}


def test_overload_is_still_reported_when_the_timetable_leaves_too_little_time():
    heavy = [
        PlannerSubject(
            "maths", "Mathematics", [topic(f"m{n}", minutes=60) for n in range(1, 13)]
        )
    ]
    output = plan(
        heavy,
        availability=Availability(
            [1], 30, 30, {}, [alloc(1, "maths", strength="PINNED")]
        ),
    )
    assert output["overload"] is not None
    assert output["overload"].topics_behind > 0


# --- carry-over and the catch-up slot ----


def test_the_catch_up_slot_stays_free_when_the_day_has_room():
    # Saturday is the week's last study day: 120 minutes = 4 sessions, 1 reserved.
    subjects = [
        PlannerSubject(
            "maths", "Mathematics", [topic(f"m{n}", minutes=30) for n in range(1, 6)]
        )
    ]
    output = plan(
        subjects,
        availability=custom(alloc(6, "maths", strength="PINNED")),
        carry_over=[CarryOver("m5", "maths", "2026-09-12")],
    )
    carried = [i for i in output["items"] if i.carried_from]
    assert carried
    assert {iso_weekday(i.date) for i in carried} == {6}


def test_a_reserved_catch_up_slot_stops_deferring_carry_over():
    # Saturday fully booked: its catch-up slot is reserved, so carry-over for the
    # week is placed earlier instead of waiting for a slot that no longer exists.
    subjects = [
        PlannerSubject("maths", "Mathematics", [topic(f"m{n}") for n in range(1, 6)]),
        PlannerSubject("eng", "English", [topic(f"e{n}", "eng") for n in range(1, 6)]),
    ]
    availability = custom(
        alloc(6, "eng", 120, "PINNED"),
    )
    output = plan(
        subjects,
        availability=availability,
        carry_over=[CarryOver("m5", "maths", "2026-09-12")],
    )
    carried = [i for i in output["items"] if i.carried_from]
    assert carried
    first_week_saturday = "2026-09-19"
    assert min(i.date for i in carried) < first_week_saturday


# --- exam modes ----


def _ss3_subjects():
    return [
        PlannerSubject(
            "maths",
            "Mathematics",
            [
                topic(f"m{n}", class_level="SS3", term="FIRST", waec_weight=n)
                for n in range(1, 6)
            ],
        ),
        PlannerSubject(
            "eng",
            "English",
            [
                topic(f"e{n}", "eng", class_level="SS3", term="FIRST", waec_weight=n)
                for n in range(1, 6)
            ],
        ),
    ]


def _ss3(mode, availability, target, **extra):
    return plan(
        _ss3_subjects(),
        availability=availability,
        mode=mode,
        class_level="SS3",
        home_class_level="SS3",
        target_date=target,
        **extra,
    )


def test_exam_mode_gives_a_reserved_slot_exam_work_for_that_subject():
    availability = custom(
        alloc(1, "eng", strength="PINNED"), alloc(2, "maths", strength="PINNED")
    )
    output = _ss3("EXAM", availability, "2026-12-20")
    monday = [i for i in on_weekday(output, 1) if i.duration_minutes == 30]
    assert monday
    assert any(i.subject_id == "eng" for i in monday)
    assert "LESSON" not in {i.activity_type for i in output["items"]}


def test_the_exam_runway_ignores_the_timetable():
    availability = custom(
        alloc(1, "eng", strength="PINNED"), alloc(2, "maths", strength="PINNED")
    )
    with_table = _ss3("BLENDED", availability, "2026-10-05", plan_start="2026-06-01")
    without = _ss3("BLENDED", custom(), "2026-10-05", plan_start="2026-06-01")
    runway = with_table["runwayStart"]
    assert runway == "2026-09-15"

    def runway_part(output):
        return [
            (i.date, i.activity_type, i.duration_minutes)
            for i in output["items"]
            if i.date >= runway
        ]

    assert runway_part(with_table) == runway_part(without)
    assert Counter(i[1] for i in runway_part(with_table))["MOCK_EXAM"] == 2
    assert all(u["date"] < runway for u in with_table["unmet"])


def test_blended_mode_before_the_runway_still_mixes_exam_and_term_work():
    availability = custom(
        alloc(1, "eng", strength="PINNED"), alloc(3, "maths", strength="PINNED")
    )
    output = _ss3("BLENDED", availability, "2027-03-01")
    kinds = {i.activity_type for i in output["items"]}
    assert "LESSON" in kinds
    assert "PAST_QUESTIONS" in kinds or "PRACTICE" in kinds


# --- settled work keeps its time ----


def test_finished_sessions_today_are_not_booked_a_second_time():
    plain = plan(maths_and_english())
    settled = [FixedItem(TODAY, "maths", 30)]
    again = plan(maths_and_english(), fixed=settled)
    today_before = sum(i.duration_minutes for i in plain["items"] if i.date == TODAY)
    today_after = sum(i.duration_minutes for i in again["items"] if i.date == TODAY)
    assert today_before - today_after >= 30
    # Done work plus new work still fits the day.
    assert today_after + 30 <= 60


def test_a_finished_allocated_session_is_not_reserved_again():
    availability = custom(alloc(1, "maths", 60, "PINNED"))
    settled = [FixedItem(TODAY, "maths", 30)]
    output = plan(maths_and_english(), availability=availability, fixed=settled)
    today = [i for i in output["items"] if i.date == TODAY and i.duration_minutes == 30]
    assert sum(1 for i in today if i.subject_id == "maths") <= 1
    assert output["unmet"] == []


def test_a_short_finished_item_uses_the_short_slot_not_a_full_session():
    availability = Availability(DAYS, 45, 120)
    plain = plan(maths_and_english(), availability=availability)
    short_done = plan(
        maths_and_english(),
        availability=availability,
        fixed=[FixedItem(TODAY, "maths", 15)],
    )

    def full(out):
        return [i for i in out["items"] if i.date == TODAY and i.duration_minutes == 30]

    assert len(full(plain)) == len(full(short_done))
