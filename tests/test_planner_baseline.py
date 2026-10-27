"""Baseline for the deterministic planner before the configurable timetable.

These pin today's scheduling rules through `plan_window`, so a timetable-aware
layout can be checked against them. They describe current behavior; they are
not a statement that every rule is ideal.
"""

from collections import Counter, defaultdict

from app.services.learning.graph import GraphEdge, GraphNode, build_graph
from app.services.learning.mastery import TopicState
from app.services.planner.days import is_weekend, iso_weekday, monday_of
from app.services.planner.layout import RevisionDue
from app.services.planner.slots import Availability
from app.services.planner.term_context import TermRange, resolve_term_context
from app.services.planner.term_plan import PlannerInput, PlannerSubject, plan_window
from app.services.planner.topics import CarryOver, PlanTopic

TODAY = "2026-09-14"  # a Monday
IN_TERM = resolve_term_context(
    TODAY, [TermRange("2026/2027", "FIRST", "2026-09-07", "2026-11-29")]
)


def topic(
    topic_id: str, subject: str = "maths", minutes: int = 30, **extra
) -> PlanTopic:
    data = dict(
        id=topic_id,
        subject_id=subject,
        title=f"Topic {topic_id}",
        slug=topic_id,
        order_index=int(topic_id[1:]) if topic_id[1:].isdigit() else 0,
        estimated_minutes=minutes,
        waec_weight=1,
        jamb_weight=1,
        prerequisite_topic_id=None,
        class_level="SS1",
        term="FIRST",
    )
    data.update(extra)
    return PlanTopic(**data)


def nodes_of(subjects: list[PlannerSubject]) -> list[GraphNode]:
    return [
        GraphNode(
            t.id,
            t.subject_id,
            t.title,
            t.slug,
            t.order_index,
            t.estimated_minutes,
            t.waec_weight,
            t.jamb_weight,
            t.prerequisite_topic_id,
        )
        for s in subjects
        for t in s.topics
    ]


def plan(subjects, *, edges=(), availability=None, **overrides):
    availability = availability or Availability([1, 2, 3, 4, 6], 60, 120)
    fields = dict(
        today=TODAY,
        plan_start=TODAY,
        mode="TERM",
        class_level="SS1",
        term="FIRST",
        home_class_level="SS1",
        target_date=None,
        term_context=IN_TERM,
        availability=availability,
        subjects=subjects,
        graph=build_graph(nodes_of(subjects), list(edges)),
        state={},
    )
    fields.update(overrides)
    return plan_window(PlannerInput(**fields))


def maths_and_english(count: int = 6) -> list[PlannerSubject]:
    return [
        PlannerSubject(
            "maths",
            "Mathematics",
            [topic(f"m{n}", "maths") for n in range(1, count + 1)],
        ),
        PlannerSubject(
            "eng", "English", [topic(f"e{n}", "eng") for n in range(1, count + 1)]
        ),
    ]


def by_date(items):
    days = defaultdict(list)
    for item in items:
        days[item.date].append(item)
    return days


def signature(output):
    return [
        (i.date, i.subject_id, i.topic_id, i.activity_type, i.duration_minutes, i.notes)
        for i in output["items"]
    ]


# --- determinism, availability, no fabricated work ---------------------------


def test_same_inputs_give_the_same_plan():
    assert signature(plan(maths_and_english())) == signature(plan(maths_and_english()))


def test_nothing_is_planned_on_a_day_the_student_does_not_study():
    output = plan(maths_and_english(), availability=Availability([1, 3], 60, 120))
    assert output["items"]
    assert {iso_weekday(i.date) for i in output["items"]} <= {1, 3}


def test_a_day_is_never_filled_past_its_minutes():
    availability = Availability([1, 2, 3, 4, 6], 45, 90)
    output = plan(maths_and_english(), availability=availability)
    for date, items in by_date(output["items"]).items():
        budget = 90 if is_weekend(date) else 45
        assert sum(i.duration_minutes for i in items) <= budget


def test_no_topics_means_no_invented_tasks():
    empty = [PlannerSubject("maths", "Mathematics", [])]
    output = plan(empty)
    assert output["items"] == []
    assert output["overload"] is None


def test_the_window_is_fourteen_days_in_term_mode():
    output = plan(maths_and_english())
    assert output["plannedThrough"] == "2026-09-27"
    assert all(TODAY <= i.date <= "2026-09-27" for i in output["items"])


# --- subject caps and short slots --------------------------------------------


def test_a_day_holds_at_most_two_subjects_on_weekdays_and_three_on_weekends():
    subjects = [
        PlannerSubject(s, s.title(), [topic(f"{s[0]}{n}", s) for n in range(1, 5)])
        for s in ("maths", "eng", "phy", "chem", "bio")
    ]
    output = plan(subjects, availability=Availability([1, 2, 3, 4, 5, 6, 7], 120, 240))
    for date, items in by_date(output["items"]).items():
        cap = 3 if is_weekend(date) else 2
        assert len({i.subject_id for i in items}) <= cap, date


def test_a_short_slot_only_ever_carries_revision():
    # 45 minutes is one 30-minute session and one 15-minute short slot.
    output = plan(
        maths_and_english(), availability=Availability([1, 2, 3, 4, 6], 45, 45)
    )
    short = [i for i in output["items"] if i.duration_minutes == 15]
    assert short, "expected revision to use the short slot"
    assert {i.activity_type for i in short} == {"REVISION"}


# --- lesson, practice, mastery -----------------------------------------------


def test_lessons_follow_the_topic_length_and_practice_comes_last():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1", minutes=65)])]
    output = plan(subjects)
    units = [
        i.activity_type
        for i in output["items"]
        if i.topic_id == "m1" and i.activity_type != "REVISION"
    ]
    # ceil(65 / 30) = 3 lessons. A topic with no mastery data counts as weak
    # (mastery 0 < WEAK_MASTERY), so it gets two practice sessions.
    assert units == ["LESSON", "LESSON", "LESSON", "PRACTICE", "PRACTICE"]


def test_a_topic_with_strong_enough_mastery_gets_one_practice_session():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1")])]
    steady = {"m1": TopicState("m1", mastery=55, observations=5)}
    output = plan(subjects, state=steady)
    assert (
        Counter(i.activity_type for i in output["items"] if i.topic_id == "m1")[
            "PRACTICE"
        ]
        == 1
    )


def test_a_weak_topic_gets_two_practice_sessions():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1")])]
    weak = {"m1": TopicState("m1", mastery=20, observations=3)}
    output = plan(subjects, state=weak)
    assert (
        Counter(i.activity_type for i in output["items"] if i.topic_id == "m1")[
            "PRACTICE"
        ]
        == 2
    )


def test_a_passed_pretest_skips_the_lessons():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1", minutes=90)])]
    output = plan(subjects, pretest_passed={"m1"})
    kinds = {i.activity_type for i in output["items"] if i.topic_id == "m1"}
    assert "LESSON" not in kinds
    assert "PRACTICE" in kinds


def test_practice_never_shares_a_day_with_the_topics_last_lesson():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1", minutes=60)])]
    output = plan(subjects)
    lessons = [
        i.date
        for i in output["items"]
        if i.topic_id == "m1" and i.activity_type == "LESSON"
    ]
    practice = [
        i.date
        for i in output["items"]
        if i.topic_id == "m1" and i.activity_type == "PRACTICE"
    ]
    assert lessons and practice
    assert min(practice) > max(lessons)


def test_a_mastered_topic_is_not_scheduled():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1"), topic("m2")])]
    state = {"m1": TopicState("m1", mastery=95, observations=40, evidence_days=3)}
    output = plan(subjects, state=state)
    assert "m1" not in {
        i.topic_id for i in output["items"] if i.activity_type != "REVISION"
    }


# --- prerequisites -----------------------------------------------------------


def test_a_dependent_topic_waits_for_its_prerequisites_lessons():
    subjects = [
        PlannerSubject("maths", "Mathematics", [topic("m1", minutes=60), topic("m2")])
    ]
    edge = GraphEdge("e1", "m1", "m2")
    # Put the student on m2 so it would otherwise be scheduled first.
    output = plan(subjects, edges=[edge], positions={"maths": "m2"})
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


def test_an_unmastered_prerequisite_outside_the_term_is_filled_in_first():
    earlier = topic("p1", class_level="SS1", term="FIRST")
    current = topic("c1", term="SECOND", class_level="SS1")
    subjects = [PlannerSubject("maths", "Mathematics", [earlier, current])]
    second = resolve_term_context(
        "2027-01-20", [TermRange("2026/2027", "SECOND", "2027-01-06", "2027-04-10")]
    )
    output = plan(
        subjects,
        edges=[GraphEdge("e1", "p1", "c1")],
        today="2027-01-20",
        plan_start="2027-01-20",
        term="SECOND",
        term_context=second,
    )
    gap = [
        i for i in output["items"] if i.topic_id == "p1" and i.activity_type == "LESSON"
    ]
    assert gap
    assert all(i.notes == "Foundation for Topic c1" for i in gap)
    # The foundation is taught before the topic it unlocks.
    unlocked = [
        i.date
        for i in output["items"]
        if i.topic_id == "c1" and i.activity_type != "REVISION"
    ]
    assert not unlocked or min(unlocked) > max(i.date for i in gap)


# --- carry-over and catch-up -------------------------------------------------


def test_a_missed_topic_returns_as_a_catch_up_with_its_original_date():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1"), topic("m2")])]
    carry = [CarryOver("m2", "maths", "2026-09-12")]
    output = plan(subjects, carry_over=carry)
    carried = [i for i in output["items"] if i.topic_id == "m2" and i.carried_from]
    assert carried
    assert {i.carried_from for i in carried} == {"2026-09-12"}
    assert any(i.notes == "Catch-up — from a missed session" for i in carried)


def test_carry_over_prefers_the_weekly_catch_up_slot():
    subjects = [
        PlannerSubject("maths", "Mathematics", [topic(f"m{n}") for n in range(1, 5)])
    ]
    carry = [CarryOver("m4", "maths", "2026-09-12")]
    output = plan(subjects, carry_over=carry)
    first_carry = min(i.date for i in output["items"] if i.topic_id == "m4")
    # The catch-up slot is the first full slot of the week's last study day (Sat).
    assert iso_weekday(first_carry) == 6


# --- revision ----------------------------------------------------------------


def test_finishing_a_topic_queues_revision_passes_after_the_last_lesson():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1")])]
    output = plan(subjects)
    last_lesson = max(
        i.date
        for i in output["items"]
        if i.topic_id == "m1" and i.activity_type == "LESSON"
    )
    revisions = [
        i
        for i in output["items"]
        if i.topic_id == "m1" and i.activity_type == "REVISION"
    ]
    assert revisions
    assert all(r.date > last_lesson for r in revisions)
    assert all(r.notes and r.notes.startswith("Revision pass") for r in revisions)


def test_a_fading_topic_is_revised_without_a_new_lesson():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1"), topic("m2")])]
    due = [
        RevisionDue("m1", "maths", "Topic m1", "Fading from memory, time to refresh it")
    ]
    state = {"m1": TopicState("m1", mastery=95, observations=40, evidence_days=3)}
    output = plan(subjects, revision_due=due, state=state)
    m1 = [i for i in output["items"] if i.topic_id == "m1"]
    assert m1 and {i.activity_type for i in m1} == {"REVISION"}


def _revision_counts(output):
    full: Counter = Counter()
    revision: Counter = Counter()
    for item in output["items"]:
        if item.duration_minutes != 30:
            continue
        week = monday_of(item.date)
        full[week] += 1
        if item.activity_type == "REVISION":
            revision[week] += 1
    return full, revision


def test_revision_is_capped_near_a_fifth_of_a_week_while_topic_work_remains():
    plentiful = [
        PlannerSubject(
            "maths", "Mathematics", [topic(f"m{n}", "maths") for n in range(1, 31)]
        ),
        PlannerSubject("eng", "English", [topic(f"e{n}", "eng") for n in range(1, 31)]),
    ]
    output = plan(plentiful, availability=Availability([1, 2, 3, 4, 6], 60, 120))
    full, revision = _revision_counts(output)
    assert sum(revision.values()) > 0
    for week, total in full.items():
        assert revision[week] <= total * 0.2 + 1, week


def test_revision_fills_slots_left_over_once_topic_work_runs_out():
    # Current behavior, kept deliberately: the 20% cap only applies while there
    # is topic work to place. Spare slots then go to revision, never to waste.
    output = plan(
        [PlannerSubject("maths", "Mathematics", [topic("m1"), topic("m2")])],
        availability=Availability([1, 2, 3, 4, 6], 60, 120),
    )
    full, revision = _revision_counts(output)
    assert sum(revision.values()) / sum(full.values()) > 0.2


# --- gap-fill cap and overload -----------------------------------------------


def test_gap_fill_stays_within_a_fifth_of_the_windows_full_session_minutes():
    earlier = [topic(f"p{n}", term="FIRST", class_level="SS1") for n in range(1, 11)]
    current = topic("c1", term="SECOND", class_level="SS1")
    subjects = [PlannerSubject("maths", "Mathematics", [*earlier, current])]
    edges = [GraphEdge(f"e{n}", f"p{n}", "c1") for n in range(1, 11)]
    second = resolve_term_context(
        "2027-01-20", [TermRange("2026/2027", "SECOND", "2027-01-06", "2027-04-10")]
    )
    availability = Availability([1, 2, 3, 4, 6], 60, 120)
    output = plan(
        subjects,
        edges=edges,
        availability=availability,
        today="2027-01-20",
        plan_start="2027-01-20",
        term="SECOND",
        term_context=second,
    )
    gap_minutes = sum(
        i.duration_minutes
        for i in output["items"]
        if i.notes
        and i.notes.startswith("Foundation for")
        and i.activity_type != "REVISION"
    )
    full_minutes = sum(
        i.duration_minutes for i in output["items"] if i.duration_minutes == 30
    )
    assert gap_minutes > 0
    assert gap_minutes <= 0.2 * max(full_minutes, 1) + 30


def test_too_little_time_is_reported_as_overload():
    heavy = [
        PlannerSubject(
            "maths", "Mathematics", [topic(f"m{n}", minutes=60) for n in range(1, 13)]
        )
    ]
    output = plan(heavy, availability=Availability([1], 30, 30))
    overload = output["overload"]
    assert overload is not None
    assert overload.topics_behind > 0
    assert overload.suggested_extra_minutes_per_week % 30 == 0


def test_enough_time_reports_no_overload():
    subjects = [PlannerSubject("maths", "Mathematics", [topic("m1")])]
    assert plan(subjects)["overload"] is None


# --- exam modes --------------------------------------------------------------


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


def _ss3(mode: str, target: str = "2026-10-12", **extra):
    return plan(
        _ss3_subjects(),
        mode=mode,
        class_level="SS3",
        home_class_level="SS3",
        target_date=target,
        **extra,
    )


def test_term_mode_never_plans_exam_work():
    output = plan(maths_and_english())
    assert {i.activity_type for i in output["items"]}.isdisjoint(
        {"MOCK_EXAM", "PAST_QUESTIONS"}
    )
    assert output["runwayStart"] is None


def test_the_window_stops_at_the_exam_date():
    output = _ss3("BLENDED", target="2026-09-20")
    assert output["plannedThrough"] == "2026-09-20"
    assert all(i.date <= "2026-09-20" for i in output["items"])


def test_the_runway_has_two_mocks_and_no_new_lessons():
    # A long runway (21 days) starts on 09-15, inside the 14-day window.
    output = _ss3("BLENDED", target="2026-10-05", plan_start="2026-06-01")
    runway = output["runwayStart"]
    assert runway == "2026-09-15"
    inside = [i for i in output["items"] if i.date >= runway]
    assert Counter(i.activity_type for i in inside)["MOCK_EXAM"] == 2
    assert sum(i.activity_type == "MOCK_EXAM" for i in output["items"]) == 2
    assert {i.activity_type for i in inside} <= {
        "MOCK_EXAM",
        "PAST_QUESTIONS",
        "REVISION",
    }


def test_exam_mode_keeps_new_term_lessons_out_of_the_plan():
    output = _ss3("EXAM", target="2026-12-20")
    assert "LESSON" not in {i.activity_type for i in output["items"]}
    assert "PAST_QUESTIONS" in {i.activity_type for i in output["items"]}


def test_exam_mode_still_catches_up_a_missed_session():
    output = _ss3(
        "EXAM", target="2026-12-20", carry_over=[CarryOver("m2", "maths", "2026-09-12")]
    )
    carried = [i for i in output["items"] if i.topic_id == "m2" and i.carried_from]
    assert carried


def test_blended_mode_mixes_term_and_exam_work_before_the_runway():
    output = _ss3("BLENDED", target="2027-03-01")
    kinds = {i.activity_type for i in output["items"]}
    assert "LESSON" in kinds
    assert "PAST_QUESTIONS" in kinds or "PRACTICE" in kinds


# --- outline -----------------------------------------------------------------


def test_the_outline_starts_after_the_window_and_stays_inside_the_term():
    output = plan(maths_and_english())
    weeks = output["outline"]
    assert weeks
    assert weeks[0]["weekStart"] <= "2026-09-28"
    assert all(w["weekStart"] <= "2026-11-29" for w in weeks)
    assert all(w["topics"] for w in weeks)


def test_the_outline_labels_the_exam_runway_and_exam_mode_weeks():
    blended = _ss3("BLENDED", target="2026-12-20")
    assert any(
        w["label"] and w["label"].startswith("Exam runway") for w in blended["outline"]
    )
    exam = _ss3("EXAM", target="2026-12-20")
    assert {w["label"] for w in exam["outline"]} <= {
        "Exam revision",
        "Exam runway — mocks and past questions",
    }
