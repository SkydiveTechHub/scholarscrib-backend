"""Timetable configuration: validation, storage shape and legacy compatibility.

Phase 2 persists the student's intent only. The planner's output is pinned
separately in test_planner_baseline.py and must not change with it.
"""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

from app.api.schemas import StudyPlanIn, TimetableIn
from app.core.errors import ApiError
from app.services.planner import service
from app.services.planner.mode import PlanSettingsCheck, plan_settings_problem
from app.services.planner.timetable import (
    load_timetable,
    stored_timetable,
    timetable_problem,
)

PLAN = dict(
    study_days=[1, 2, 3, 4, 6],
    weekday_minutes=60,
    weekend_minutes=120,
    subject_ids=["maths", "eng"],
)


def timetable(**fields) -> TimetableIn:
    return TimetableIn.model_validate(fields)


def custom(*allocations, **fields) -> TimetableIn:
    return timetable(mode="CUSTOM", allocations=list(allocations), **fields)


def alloc(day=1, subject="maths", **fields) -> dict:
    return {"day": day, "subjectId": subject, **fields}


def problem(value, **overrides):
    return timetable_problem(value, **{**PLAN, **overrides})


# --- schema shape -------------------------------------------------------------


def test_defaults_describe_a_smart_timetable():
    parsed = TimetableIn()
    assert (parsed.mode, parsed.allocations, parsed.dayMinutes) == ("SMART", [], [])


@pytest.mark.parametrize(
    "bad",
    [
        {"allocations": [alloc(day=8)]},
        {"allocations": [alloc(day=0)]},
        {"allocations": [alloc(minutes=0)]},
        {"allocations": [alloc(minutes=15)]},
        {"allocations": [alloc(strength="MAYBE")]},
        {"allocations": [alloc(subject="")]},
        {"mode": "MANUAL"},
        {"version": 2},
        {"dayMinutes": [{"day": 1, "minutes": 10}]},
    ],
)
def test_the_schema_rejects_malformed_timetables(bad):
    with pytest.raises(ValidationError):
        TimetableIn.model_validate(bad)


def test_strength_and_mode_accept_any_casing():
    parsed = TimetableIn.model_validate(
        {"mode": "custom", "allocations": [alloc(strength="pinned")]}
    )
    assert parsed.mode == "CUSTOM"
    assert parsed.allocations[0].strength == "PINNED"


# --- plan-aware validation ----------------------------------------------------


def test_no_timetable_and_an_empty_smart_one_are_valid():
    assert problem(None) is None
    assert problem(TimetableIn()) is None


def test_one_subject_on_several_days_and_several_subjects_on_one_day():
    value = custom(
        alloc(1, "maths"),
        alloc(3, "maths"),
        alloc(1, "eng", minutes=30, strength="PINNED"),
    )
    assert problem(value) is None


def test_a_rest_day_cannot_take_a_subject_or_study_time():
    assert "rest day" in problem(custom(alloc(day=5)))
    assert "rest day" in problem(timetable(dayMinutes=[{"day": 7, "minutes": 60}]))


def test_a_subject_outside_the_plan_is_rejected():
    assert "not part of this plan" in problem(custom(alloc(subject="chem")))


def test_the_same_subject_twice_on_a_day_is_rejected():
    assert "more than once" in problem(custom(alloc(1), alloc(1)))


def test_the_same_day_cannot_be_given_two_times():
    both = timetable(dayMinutes=[{"day": 1, "minutes": 60}, {"day": 1, "minutes": 90}])
    assert "more than one time" in problem(both)


def test_allocation_minutes_must_be_whole_sessions():
    assert "30 minutes" in problem(custom(alloc(minutes=45)))
    assert problem(custom(alloc(minutes=60))) is None


def test_smart_mode_takes_no_allocations_and_custom_needs_one():
    assert "custom" in problem(timetable(mode="SMART", allocations=[alloc()]))
    assert "at least one subject" in problem(timetable(mode="CUSTOM"))


def test_a_day_cannot_hold_more_than_its_study_time():
    # Monday has 60 minutes: two 30-minute sessions fit, a third does not.
    assert problem(custom(alloc(1, "maths"), alloc(1, "eng"))) is None
    over = custom(alloc(1, "maths", minutes=60), alloc(1, "eng"))
    assert "Monday" in problem(over)


def test_a_day_override_changes_the_capacity_for_that_day_only():
    longer = [{"day": 1, "minutes": 90}]
    assert problem(custom(alloc(1, "maths", minutes=90), dayMinutes=longer)) is None
    other_day = custom(alloc(2, "maths", minutes=90), dayMinutes=longer)
    assert "Tuesday" in problem(other_day)


def test_capacity_is_whole_sessions_so_a_short_remainder_does_not_count():
    # 45 minutes is one 30-minute session plus a 15-minute short slot.
    two = custom(alloc(1, "maths"), alloc(1, "eng"))
    assert "Monday" in problem(two, weekday_minutes=45)


# --- stored shape ---------------------------------------------------------------


def test_a_smart_timetable_with_nothing_in_it_is_stored_as_null():
    assert stored_timetable(None) is None
    assert stored_timetable(TimetableIn()) is None


def test_day_minutes_alone_are_stored_on_a_smart_plan():
    stored = stored_timetable(timetable(dayMinutes=[{"day": 3, "minutes": 90}]))
    assert stored["mode"] == "SMART"
    assert stored["dayMinutes"] == [{"day": 3, "minutes": 90}]


def test_equal_intent_is_stored_as_equal_data_and_round_trips():
    a = custom(alloc(3, "eng"), alloc(1, "maths", strength="PINNED"))
    b = custom(alloc(1, "maths", strength="PINNED"), alloc(3, "eng"))
    assert stored_timetable(a) == stored_timetable(b)
    assert [r["day"] for r in stored_timetable(a)["allocations"]] == [1, 3]
    assert stored_timetable(load_timetable(stored_timetable(a))) == stored_timetable(a)


def test_a_legacy_row_loads_as_no_timetable():
    assert load_timetable(None) is None
    assert load_timetable({}) is None


# --- request compatibility ----------------------------------------------------


def body(**extra) -> StudyPlanIn:
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


def test_a_legacy_client_body_is_valid_and_leaves_the_timetable_unset():
    legacy = body()
    assert legacy.timetable is None
    assert "timetable" not in legacy.model_fields_set


def test_an_explicit_null_is_distinguishable_from_omitted():
    assert "timetable" in body(timetable=None).model_fields_set


def test_day_minutes_count_toward_having_study_time():
    check = PlanSettingsCheck(
        class_level="SS1",
        target_date=None,
        force_exam_mode=False,
        study_days=[1],
        weekday_minutes=0,
        weekend_minutes=0,
        today="2026-10-25",
    )
    expected = "Choose at least one study day that has study time on it."
    assert plan_settings_problem(check) == expected
    check.day_minutes = {1: 60}
    assert plan_settings_problem(check) is None


def test_check_rejects_an_invalid_timetable_with_a_400():
    request = body(timetable={"mode": "CUSTOM", "allocations": [alloc(day=5)]})
    with pytest.raises(ApiError) as raised:
        service._check(request, request.timetable)
    assert raised.value.status_code == 400
    assert "rest day" in raised.value.payload["error"]


def test_check_explains_when_a_kept_timetable_no_longer_fits():
    stored = custom(alloc(day=6))
    request = body(studyDays=[1, 2])
    with pytest.raises(ApiError) as raised:
        service._check(request, stored, kept=True)
    message = raised.value.payload["error"]
    assert message.startswith("Your saved timetable no longer fits")


# --- services -------------------------------------------------------------------


class _Replanned:
    runs = 0

    def __init__(self, *_args, **_kwargs):
        pass

    async def process(self):
        _Replanned.runs += 1


def _stored_plan(stored=None):
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
        timetable=stored,
    )


@pytest.fixture
def update(monkeypatch):
    plan = _stored_plan(stored_timetable(custom(alloc(1, "maths", strength="PINNED"))))

    async def by_id(_, _plan_id):
        return plan

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service.plans_repository, "by_id", by_id)
    monkeypatch.setattr(service, "_ensure_subjects", no_op)
    monkeypatch.setattr(service, "_drop_positions_outside_scope", no_op)
    monkeypatch.setattr(service, "ReplanStudyPlanService", _Replanned)
    _Replanned.runs = 0

    async def run(request):
        await service.UpdateStudyPlanSettingsService(
            None, "s1", "SS1", "p1", request
        ).process()
        return plan

    return run


async def test_an_older_client_update_keeps_the_saved_timetable(update):
    plan = await update(body(weekdayMinutes=90))
    assert plan.weekday_minutes == 90
    assert plan.timetable["allocations"][0]["strength"] == "PINNED"
    assert _Replanned.runs == 1


async def test_an_explicit_null_returns_the_plan_to_smart(update):
    plan = await update(body(timetable=None))
    assert plan.timetable is None


async def test_a_new_timetable_replaces_the_saved_one(update):
    request = body(timetable={"mode": "CUSTOM", "allocations": [alloc(2, "eng")]})
    plan = await update(request)
    rows = [(r["day"], r["subjectId"]) for r in plan.timetable["allocations"]]
    assert rows == [(2, "eng")]


async def test_an_older_client_cannot_break_the_saved_timetable(update):
    with pytest.raises(ApiError) as raised:
        await update(body(studyDays=[2, 3, 4]))
    assert raised.value.status_code == 400
    assert "no longer fits" in raised.value.payload["error"]


async def test_create_stores_the_timetable_and_a_legacy_create_stores_none(monkeypatch):
    added = []

    async def none(*_args, **_kwargs):
        return []

    async def add(_session, row, flush=False):
        added.append(row)

    monkeypatch.setattr(service.plans_repository, "list_active", none)
    monkeypatch.setattr(service.plans_repository, "add", add)
    monkeypatch.setattr(service, "_ensure_subjects", none)
    monkeypatch.setattr(service, "ReplanStudyPlanService", _Replanned)

    async def create(request):
        await service.CreateStudyPlanService(None, "s1", "SS1", request).process()
        return added[-1]

    legacy = await create(body())
    assert legacy.timetable is None
    chosen = await create(
        body(timetable={"mode": "CUSTOM", "allocations": [alloc(1, "maths")]})
    )
    assert chosen.timetable["allocations"] == [
        {"day": 1, "subjectId": "maths", "minutes": None, "strength": "PREFERRED"}
    ]


# --- tutor path -------------------------------------------------------------------


def _tutor_args(**extra):
    return {
        "classLevel": "SS1",
        "term": "FIRST",
        "subjectIds": ["maths", "eng"],
        "studyDays": [1, 2, 3, 4, 6],
        "weekdayMinutes": 60,
        "weekendMinutes": 120,
        **extra,
    }


async def test_a_tutor_proposal_without_a_timetable_never_carries_one():
    from app.ai.core.hitl import decode_proposal
    from app.ai.tutor.tools import ensure_tools
    from app.ai.tutor.tools.study_plan import (
        _update_study_plan,
        parse_validated_args,
    )

    ensure_tools()

    raw = await _update_study_plan(
        planId="p1", summary="Change minutes", **_tutor_args()
    )
    proposal = decode_proposal(raw)
    assert proposal is not None
    assert "timetable" not in proposal["args"]
    # Resume re-validates edits; that must not turn "unset" into "reset to Smart".
    resumed = parse_validated_args("update_study_plan", proposal["args"])
    assert "timetable" not in resumed
    assert "timetable" not in parse_validated_args("create_study_plan", _tutor_args())


async def test_a_tutor_proposal_can_carry_a_valid_timetable_and_rejects_a_bad_one():
    from app.ai.core.hitl import decode_proposal
    from app.ai.tutor.tools import ensure_tools
    from app.ai.tutor.tools.study_plan import _create_study_plan

    ensure_tools()
    good = {"mode": "CUSTOM", "allocations": [alloc(1, "maths")]}
    proposal = decode_proposal(
        await _create_study_plan(
            **_tutor_args(timetable=good), summary="Maths on Monday"
        )
    )
    assert proposal["args"]["timetable"]["allocations"][0]["subjectId"] == "maths"

    bad = {"mode": "CUSTOM", "allocations": [alloc(5, "maths")]}
    raw = await _create_study_plan(
        **_tutor_args(timetable=bad), summary="Maths on Friday"
    )
    assert decode_proposal(raw) is None
    assert "rest day" in raw


# --- migration ----------------------------------------------------------------------


def test_the_timetable_migrations_are_a_single_chain_on_top_of_the_old_head():
    root = Path(__file__).resolve().parents[1]
    scripts = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision("e2c5b8d1a7f3").down_revision == "d7a3f5c1e208"
    assert scripts.get_revision("d7a3f5c1e208").down_revision == "c6d8e0f2a413"
