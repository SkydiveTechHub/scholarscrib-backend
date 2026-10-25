"""Replan lifecycle with a timetable: what is kept, regenerated, reported and undone.

The repositories are faked with a small in-memory store, so these run the real
`ReplanStudyPlanService` and `PreviewStudyPlanService` code without a database.
"""

from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.api.schemas import StudyPlanIn
from app.services.planner import service
from app.services.planner.timetable import stored_timetable
from tests.test_planner_baseline import IN_TERM, TODAY, maths_and_english, nodes_of
from tests.test_planner_timetable import alloc as alloc_row
from tests.test_planner_timetable import custom as custom_timetable

PAST = "2026-09-12"


class Store:
    """Just enough of the plan item repository for a replan."""

    def __init__(self, items=()):
        self.items = list(items)
        self.removed = []

    async def for_plan(self, _session, _plan_id):
        return list(self.items)

    async def pending_for_plan(self, _session, _plan_id):
        return [i for i in self.items if i.status == "PENDING"]

    async def remove(self, _session, item):
        self.removed.append(item)
        self.items.remove(item)

    async def add(self, _session, item, flush=False):
        self.items.append(item)


def item(
    day, subject="maths", topic="m1", activity="LESSON", status="PENDING", minutes=30
):
    return NS(
        id=f"{day}-{subject}-{topic}-{activity}",
        date=date.fromisoformat(day),
        subject_id=subject,
        topic_id=topic,
        activity_type=activity,
        duration_minutes=minutes,
        status=status,
        notes=None,
        carried_from_date=None,
    )


def plan_row(timetable=None):
    return NS(
        id="p1",
        student_id="s1",
        subject_ids=["maths", "eng"],
        target_exam=None,
        target_date=None,
        force_exam_mode=False,
        study_days=[1, 2, 3, 4, 6],
        weekday_minutes=60,
        weekend_minutes=120,
        scope_class_level="SS1",
        scope_term="FIRST",
        timetable=timetable,
        unmet=None,
        outline=None,
        overload=None,
        planned_through=None,
        last_replanned_at=None,
        created_at=None,
    )


@pytest.fixture
def world(monkeypatch):
    store = Store()
    subjects = maths_and_english()

    async def noop(*_args, **_kwargs):
        return None

    async def build_subjects(self):
        return subjects, nodes_of(subjects)

    async def edges(self, _nodes):
        return []

    async def learning(self, _nodes, _in_scope):
        return {}, set(), []

    async def term_context(_session, _today):
        return IN_TERM

    async def no_positions(*_args, **_kwargs):
        return []

    replanner = service.ReplanStudyPlanService
    monkeypatch.setattr(service, "lagos_day_key", lambda: TODAY)
    monkeypatch.setattr(service, "_term_context", term_context)
    monkeypatch.setattr(service.plans_repository, "lock", noop)
    monkeypatch.setattr(service.plan_positions_repository, "for_plan", no_positions)
    monkeypatch.setattr(service.plan_items_repository, "for_plan", store.for_plan)
    monkeypatch.setattr(
        service.plan_items_repository, "pending_for_plan", store.pending_for_plan
    )
    monkeypatch.setattr(service.plan_items_repository, "remove", store.remove)
    monkeypatch.setattr(service.plan_items_repository, "add", store.add)
    monkeypatch.setattr(replanner, "_build_subjects", build_subjects)
    monkeypatch.setattr(replanner, "_edges", edges)
    monkeypatch.setattr(replanner, "_learning_inputs", learning)
    return NS(store=store)


async def replan(plan, **kwargs):
    await service.ReplanStudyPlanService(None, plan, "SS1", **kwargs).process()


def generated(store):
    return sorted(
        (str(i.date), i.subject_id, i.topic_id, i.activity_type, i.duration_minutes)
        for i in store.items
        if i.status == "PENDING"
    )


# --- availability from the stored timetable ------------------------------------------


def test_a_smart_plan_has_no_allocations_and_no_overrides():
    availability = service._availability(plan_row())
    assert availability.allocations == []
    assert availability.day_minutes == {}


def test_a_custom_timetable_becomes_allocations_and_day_overrides():
    stored = stored_timetable(
        custom_timetable(
            alloc_row(1, "maths", minutes=60, strength="PINNED"),
            alloc_row(3, "eng"),
            dayMinutes=[{"day": 1, "minutes": 90}],
        )
    )
    availability = service._availability(plan_row(stored))
    assert availability.day_minutes == {1: 90}
    assert [
        (a.day, a.subject_id, a.minutes, a.strength) for a in availability.allocations
    ] == [
        (1, "maths", 60, "PINNED"),
        (3, "eng", None, "PREFERRED"),
    ]


def test_day_minutes_apply_to_a_smart_plan_but_allocations_need_custom():
    from app.api.schemas import TimetableIn

    smart = TimetableIn.model_validate({"dayMinutes": [{"day": 2, "minutes": 90}]})
    availability = service._availability(plan_row(stored_timetable(smart)))
    assert availability.day_minutes == {2: 90}
    assert availability.allocations == []


# --- the replan lifecycle -------------------------------------------------------------


async def test_a_replan_follows_the_timetable_and_stores_what_it_could_not_give(world):
    stored = stored_timetable(
        custom_timetable(alloc_row(1, "maths", strength="PINNED"), alloc_row(2, "eng"))
    )
    plan = plan_row(stored)
    await replan(plan)
    mondays = [i for i in world.store.items if str(i.date) == TODAY]
    assert any(i.subject_id == "maths" for i in mondays)
    assert plan.unmet is None  # everything was served
    assert plan.last_replanned_at is not None

    empty = plan_row(
        stored_timetable(custom_timetable(alloc_row(1, "ghost", strength="PINNED")))
    )
    await replan(empty)
    assert {u["subjectId"] for u in empty.unmet} == {"ghost"}
    assert all(u["strength"] == "PINNED" for u in empty.unmet)


async def test_missed_sessions_are_kept_as_history_and_carried_over(world):
    missed = item(PAST, topic="m1")
    done = item(PAST, topic="m2", status="COMPLETED")
    future = item("2026-09-16", topic="m3")
    world.store.items += [missed, done, future]
    await replan(plan_row())
    assert missed.status == "MISSED"
    assert done.status == "COMPLETED"
    assert future in world.store.removed  # pending future work is regenerated
    assert missed in world.store.items and done in world.store.items
    carried = [i for i in world.store.items if i.carried_from_date]
    assert carried and {i.topic_id for i in carried} == {"m1"}


async def test_completed_and_skipped_work_is_never_removed_or_rewritten(world):
    keep = [
        item(TODAY, topic="m1", status="COMPLETED"),
        item("2026-09-13", topic="m2", status="SKIPPED"),
        item(PAST, topic="m3", status="MISSED"),
    ]
    world.store.items += keep
    await replan(plan_row())
    assert all(k in world.store.items for k in keep)
    assert [k.status for k in keep] == ["COMPLETED", "SKIPPED", "MISSED"]


async def test_work_finished_today_keeps_its_time_so_the_day_is_not_overbooked(world):
    world.store.items.append(item(TODAY, topic="m1", status="COMPLETED", minutes=30))
    await replan(plan_row())
    minutes_today = sum(
        i.duration_minutes for i in world.store.items if str(i.date) == TODAY
    )
    assert minutes_today <= 60  # Monday's budget, completed session included


async def test_a_second_replan_with_the_same_inputs_changes_nothing(world):
    stored = stored_timetable(
        custom_timetable(alloc_row(1, "eng", strength="PINNED"), alloc_row(3, "maths"))
    )
    plan = plan_row(stored)
    await replan(plan)
    first = generated(world.store)
    count = len(world.store.items)
    await replan(plan)
    assert generated(world.store) == first
    assert len(world.store.items) == count  # no duplicates


async def test_a_replan_after_a_settings_change_keeps_history_and_regenerates_the_rest(
    world,
):
    plan = plan_row()
    await replan(plan)
    first = [i for i in world.store.items if str(i.date) == TODAY][0]
    first.status = "COMPLETED"
    plan.timetable = stored_timetable(
        custom_timetable(alloc_row(2, "eng", strength="PINNED"))
    )
    await replan(plan)
    assert first in world.store.items and first.status == "COMPLETED"
    tuesday = [i for i in world.store.items if str(i.date) == "2026-09-15"]
    assert any(i.subject_id == "eng" for i in tuesday)


async def test_a_failing_planner_leaves_the_plan_unwritten_and_raises(
    world, monkeypatch
):
    def boom(_input):
        raise RuntimeError("planner failed")

    monkeypatch.setattr(service, "plan_window", boom)
    plan = plan_row()
    with pytest.raises(RuntimeError):
        await replan(plan)
    # Nothing was persisted. The request's transaction (db_session) rolls back the
    # sessions marked missed or removed before the failure.
    assert plan.last_replanned_at is None
    assert plan.unmet is None
    assert plan.planned_through is None


# --- read-only mode -------------------------------------------------------------------


class FakeSavepoint:
    def __init__(self):
        self.rolled_back = False

    async def rollback(self):
        self.rolled_back = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


async def test_read_only_loads_learning_state_inside_a_savepoint_it_rolls_back(
    monkeypatch,
):
    savepoint = FakeSavepoint()
    session = NS(begin_nested=lambda: savepoint)

    class FakeMastery:
        def __init__(self, *_args, **_kwargs):
            pass

        async def process(self):
            return {"m1": "state"}

    async def passed(*_args, **_kwargs):
        return ["m1"]

    monkeypatch.setattr(service, "GetTopicMasteryService", FakeMastery)
    monkeypatch.setattr(service.metrics_repository, "passed_topic_ids", passed)
    nodes = nodes_of(maths_and_english())[:1]

    live = service.ReplanStudyPlanService(session, plan_row(), "SS1")
    state, _, _ = await live._learning_inputs(nodes, set())
    assert state == {"m1": "state"} and savepoint.rolled_back is False

    preview = service.ReplanStudyPlanService(session, plan_row(), "SS1", read_only=True)
    state, pretest, _ = await preview._learning_inputs(nodes, set())
    assert state == {"m1": "state"} and pretest == {"m1"}
    assert savepoint.rolled_back is True


# --- the preview window ----


def body(**extra):
    return StudyPlanIn.model_validate(
        {
            "classLevel": "SS1",
            "term": "FIRST",
            "subjectIds": ["maths", "eng"],
            "studyDays": [1, 2, 3, 4, 6],
            "weekdayMinutes": 60,
            "weekendMinutes": 120,
            **extra,
        }
    )


@pytest.fixture
def preview(world, monkeypatch):
    added = []

    async def active(*_args, **_kwargs):
        return []

    async def by_id(_session, subject_id):
        names = {"maths": "Mathematics", "eng": "English"}
        return NS(
            id=subject_id, name=names[subject_id], is_active=True, track_category="CORE"
        )

    async def no_topics(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service.plans_repository, "list_active", active)
    monkeypatch.setattr(
        service.plans_repository, "add", lambda *a, **k: added.append(a)
    )
    monkeypatch.setattr(service.subjects_repository, "by_id", by_id)
    monkeypatch.setattr(service.plan_items_repository, "remove", no_topics)

    async def run(request):
        return await service.PreviewStudyPlanService(
            None, "s1", "SS1", request
        ).process()

    run.added = added
    return run


async def test_the_preview_window_shows_the_timetable_and_changes_nothing(
    preview, world
):
    request = body(
        timetable={
            "mode": "CUSTOM",
            "allocations": [
                alloc_row(1, "eng", strength="PINNED"),
                alloc_row(2, "maths"),
            ],
        }
    )
    result = await preview(request)
    assert result["valid"] is True
    window = result["window"]
    assert window["plannedThrough"] == "2026-09-27"
    monday = [i for i in window["items"] if i["date"] == TODAY]
    assert any(
        i["subjectId"] == "eng" and i["subjectName"] == "English" for i in monday
    )
    assert all(i["topicTitle"] for i in window["items"] if i["topicId"])
    assert window["unmet"] == [] and window["overload"] is None
    # Nothing stored, removed or marked.
    assert world.store.items == [] and world.store.removed == []
    assert preview.added == []


async def test_the_preview_window_honors_a_pinned_session_on_a_one_day_week(preview):
    request = body(
        timetable={
            "mode": "CUSTOM",
            "allocations": [alloc_row(1, "eng", minutes=30, strength="PINNED")],
        },
        studyDays=[1],
        weekdayMinutes=30,
    )
    result = await preview(request)
    assert result["valid"] is True
    window = result["window"]
    assert {i["date"] for i in window["items"]} <= {TODAY, "2026-09-21"}
    assert {i["subjectId"] for i in window["items"]} == {"eng"}
    assert window["unmet"] == []


async def test_an_invalid_timetable_has_no_window(preview):
    request = body(timetable={"mode": "CUSTOM", "allocations": [alloc_row(5, "maths")]})
    result = await preview(request)
    assert result["valid"] is False
    assert result["window"] is None
