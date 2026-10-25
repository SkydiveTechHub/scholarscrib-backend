"""POST /api/study-plan/preview: authorization, contract and no side effects."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import StudentPrincipal, require_student
from app.database.db import db_session
from app.services.planner import service
from main import app

URL = "/api/study-plan/preview"


def principal(tier="PREMIUM", track="SCIENCE") -> StudentPrincipal:
    return StudentPrincipal(
        id="s1",
        email="a@example.com",
        tier=tier,
        device_id=None,
        class_level="SS1",
        track=track,
        state=None,
        first_name="Ada",
        last_name="O",
        image=None,
        has_password=True,
        issued_at=None,
    )


def settings_body(**extra) -> dict:
    return {
        "classLevel": "SS1",
        "term": "FIRST",
        "subjectIds": ["maths", "eng"],
        "studyDays": [1, 2, 3, 4, 6],
        "weekdayMinutes": 60,
        "weekendMinutes": 120,
        **extra,
    }


def allocation(day=1, subject="maths", **extra) -> dict:
    return {"day": day, "subjectId": subject, **extra}


@pytest.fixture
def writes(monkeypatch):
    """Everything a save would touch; a preview must call none of it."""
    touched = []

    def trap(name):
        async def fail(*_args, **_kwargs):
            touched.append(name)

        return fail

    async def active(*_args, **_kwargs):
        return []

    async def by_id(_session, subject_id):
        known = {"maths": "Mathematics", "eng": "English"}
        if subject_id not in known:
            return None
        return NS(
            id=subject_id, name=known[subject_id], is_active=True, track_category="CORE"
        )

    monkeypatch.setattr(service.plans_repository, "list_active", active)
    monkeypatch.setattr(service.subjects_repository, "by_id", by_id)
    monkeypatch.setattr(service.plans_repository, "add", trap("add"))
    monkeypatch.setattr(service.plans_repository, "lock", trap("lock"))
    monkeypatch.setattr(service.plan_items_repository, "add", trap("item"))
    monkeypatch.setattr(service.plan_items_repository, "remove", trap("remove"))
    monkeypatch.setattr(service, "ReplanStudyPlanService", trap("replan"))

    async def no_window(self, *_args, **_kwargs):
        return {"items": [], "unmet": [], "overload": None, "plannedThrough": None}

    # The contract tests below do not need a schedule; test_study_plan_window.py does.
    monkeypatch.setattr(service.PreviewStudyPlanService, "_window", no_window)
    return touched


@pytest.fixture
def client():
    session = AsyncMock()

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    app.dependency_overrides[require_student] = lambda: principal()
    try:
        test_client = TestClient(app)
        test_client.session = session
        yield test_client
    finally:
        app.dependency_overrides.pop(db_session, None)
        app.dependency_overrides.pop(require_student, None)


# --- authorization --------------------------------------------------------------


def test_preview_requires_a_signed_in_student(writes):
    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[db_session] = override_session
    try:
        assert TestClient(app).post(URL, json=settings_body()).status_code == 401
    finally:
        app.dependency_overrides.pop(db_session, None)


def test_preview_follows_the_study_planner_entitlement(client, monkeypatch, writes):
    from app import domain

    # The planner is open to every tier during the beta; end the beta for this test.
    monkeypatch.setitem(domain.ENTITLEMENTS, "studyPlanner", "STANDARD")
    monkeypatch.setattr(
        "app.api.deps.users_repository.by_id", AsyncMock(return_value=None)
    )
    app.dependency_overrides[require_student] = lambda: principal(tier="FREEMIUM")
    denied = client.post(URL, json=settings_body())
    assert denied.status_code == 403
    assert denied.json()["feature"] == "studyPlanner"

    app.dependency_overrides[require_student] = lambda: principal(tier="STANDARD")
    assert client.post(URL, json=settings_body()).status_code == 200


def test_preview_is_open_to_every_tier_while_the_planner_is_in_beta(client, writes):
    app.dependency_overrides[require_student] = lambda: principal(tier="FREEMIUM")
    assert client.post(URL, json=settings_body()).status_code == 200


# --- contract ---------------------------------------------------------------------


def test_a_legacy_body_without_a_timetable_previews_as_a_smart_week(client, writes):
    response = client.post(URL, json=settings_body())
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["errors"] == []
    assert data["warnings"] == []
    summary = data["summary"]
    assert summary["mode"] == "SMART"
    assert summary["weeklyAvailableMinutes"] == 4 * 60 + 120
    assert summary["weeklyAllocatedMinutes"] == 0
    assert [d["name"] for d in summary["restDays"]] == ["Friday", "Sunday"]
    assert [d["day"] for d in summary["days"]] == [1, 2, 3, 4, 6]


def test_a_custom_timetable_is_summarized_with_coverage_and_warnings(client, writes):
    timetable = {
        "mode": "CUSTOM",
        "allocations": [
            allocation(1, "maths", minutes=30, strength="PINNED"),
            allocation(1, "maths") if False else allocation(3, "maths"),
            allocation(2, "maths", minutes=60),
        ],
    }
    response = client.post(URL, json=settings_body(timetable=timetable))
    data = response.json()
    assert data["valid"] is True
    summary = data["summary"]
    monday = summary["days"][0]
    assert monday["allocatedMinutes"] == 30
    assert monday["unallocatedMinutes"] == 30
    assert monday["subjects"] == [
        {"subjectId": "maths", "minutes": 30, "strength": "PINNED"}
    ]
    maths = next(c for c in summary["coverage"] if c["subjectId"] == "maths")
    assert (maths["daysPerWeek"], maths["minutesPerWeek"], maths["pinnedDays"]) == (
        3,
        120,
        1,
    )
    codes = {(w["code"], w["day"], w["subjectId"]) for w in data["warnings"]}
    assert ("SUBJECT_NOT_ON_TIMETABLE", None, "eng") in codes
    assert ("DAY_FULLY_BOOKED", 2, None) in codes  # Tuesday: 60 of 60 minutes


def test_invalid_timetables_return_every_problem_not_just_the_first(client, writes):
    timetable = {
        "mode": "CUSTOM",
        "allocations": [
            allocation(5, "maths"),  # Friday is a rest day
            allocation(1, "chem"),  # not in the plan
            allocation(2, "maths", minutes=90),  # more than Tuesday holds
        ],
    }
    response = client.post(URL, json=settings_body(timetable=timetable))
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert data["summary"] is None
    assert [(e["code"], e["day"]) for e in data["errors"]] == [
        ("REST_DAY", 5),
        ("SUBJECT_NOT_IN_PLAN", 1),
        ("OVER_CAPACITY", 2),
    ]
    assert data["errors"][1]["subjectId"] == "chem"


def test_plan_settings_problems_come_back_as_structured_errors(client, writes):
    # Exam dates are for SS3 students.
    body = settings_body(targetExam="WAEC", targetDate="2099-01-01")
    data = client.post(URL, json=body).json()
    assert data["valid"] is False
    assert data["errors"][0]["code"] == "SETTINGS"
    assert "SS3" in data["errors"][0]["message"]


def test_an_unknown_subject_is_reported_not_raised(client, writes):
    data = client.post(URL, json=settings_body(subjectIds=["maths", "ghost"])).json()
    assert data["valid"] is False
    assert data["errors"][0]["code"] == "SUBJECTS"


def test_a_malformed_timetable_is_a_schema_error(client, writes):
    # This API reports schema failures as 400 "Validation failed".
    bad = {"mode": "CUSTOM", "allocations": [allocation(day=9)]}
    response = client.post(URL, json=settings_body(timetable=bad))
    assert response.status_code == 400
    assert response.json()["error"] == "Validation failed"


def test_an_exam_plan_warns_that_the_runway_replaces_the_timetable(client, writes):
    body = settings_body(
        classLevel="SS3",
        targetExam="WAEC",
        targetDate="2099-01-01",
        timetable={"mode": "CUSTOM", "allocations": [allocation(1, "maths")]},
    )
    data = client.post(URL, json=body).json()
    assert data["valid"] is True
    runway = [w for w in data["warnings"] if w["code"] == "RUNWAY_REPLACES_TIMETABLE"]
    assert len(runway) == 1
    assert "21 days" in runway[0]["message"]


def test_a_term_plan_has_no_runway_warning(client, writes):
    body = settings_body(
        timetable={"mode": "CUSTOM", "allocations": [allocation(1, "maths")]}
    )
    data = client.post(URL, json=body).json()
    assert "RUNWAY_REPLACES_TIMETABLE" not in {w["code"] for w in data["warnings"]}


# --- no side effects --------------------------------------------------------------


def test_a_preview_never_writes_or_replans(client, writes):
    timetable = {"mode": "CUSTOM", "allocations": [allocation(1, "maths")]}
    assert client.post(URL, json=settings_body(timetable=timetable)).status_code == 200
    assert client.post(URL, json=settings_body()).status_code == 200
    assert writes == []
    client.session.add.assert_not_called()
    client.session.flush.assert_not_called()
    client.session.commit.assert_not_called()


def test_the_preview_route_does_not_shadow_the_plan_routes(client, monkeypatch, writes):
    monkeypatch.setattr(
        "app.api.student.study_plan.UpdateStudyPlanSettingsService.process",
        AsyncMock(return_value={"ok": True}),
    )
    response = client.patch("/api/study-plan/p1", json=settings_body())
    assert response.status_code == 200
    assert response.json() == {"ok": True}
