"""M2 Tutor Centers: assignments, attempt.assignment_id, D6 bypass."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.services.centers.assignments import (
    CreateAssignmentService,
    ListAssignedInboxService,
    PublishAssignmentService,
    StartAssignedAttemptService,
    UpdateAssignmentService,
    _normalize_settings,
    maybe_open_scheduled,
    resolve_target_user_ids,
)


def _center(**extra):
    base = dict(
        id="center-1",
        owner_id="tutor-1",
        name="Lagos Prep",
        state="Lagos",
        lga="Ikeja",
        subjects=["Mathematics"],
        levels=["SS3"],
        plan="FREE",
        archived_at=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _assignment(**extra):
    base = dict(
        id="asg-1",
        center_id="center-1",
        created_by_id="tutor-1",
        assessment_id="assess-1",
        title="Simultaneous Equations quiz",
        task_type="TOPIC_QUIZ",
        status="DRAFT",
        settings={
            "instructions": "Show working",
            "attemptsAllowed": 1,
            "attemptScoring": "latest",
            "revealAnswers": "immediate",
            "latePolicy": "accepted_flagged",
            "filters": {"subjectId": "subj-1", "count": 10},
        },
        opens_at=None,
        due_at=utcnow() + timedelta(days=2),
        closed_at=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _target(**extra):
    base = dict(
        id="tgt-1",
        assignment_id="asg-1",
        kind="CENTER",
        target_id=None,
        created_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _membership(**extra):
    base = dict(
        id="mem-1",
        center_id="center-1",
        user_id="student-1",
        kind="STUDENT",
        status="ACTIVE",
        consent=None,
        joined_at=utcnow(),
        left_at=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _assessment(**extra):
    base = dict(
        id="assess-1",
        title="Simultaneous Equations quiz",
        subject_id="subj-1",
        assessment_type="TOPIC_QUIZ",
        exam_type=None,
        exam_year=None,
        total_marks=10,
        time_limit_minutes=15,
        pass_mark_percent=50,
        created_by="tutor-1",
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_normalize_settings_defaults():
    settings = _normalize_settings({})
    assert settings["attemptsAllowed"] == 1
    assert settings["latePolicy"] == "accepted_flagged"
    assert settings["revealAnswers"] == "immediate"


def test_normalize_settings_rejects_bad_late_policy():
    with pytest.raises(ApiError) as exc:
        _normalize_settings({"latePolicy": "ignore"})
    assert exc.value.status_code == 400


def test_m2_routes_are_mounted(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database.db import db_session
    from main import app

    session = AsyncMock()

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/center/api/centers/c1/assignments").status_code == 401
        assert (
            client.post("/center/api/centers/c1/assignments/a1/publish").status_code
            == 401
        )
        assert client.get("/api/centers/assigned").status_code == 401
        assert client.post("/api/centers/assignments/a1/start").status_code == 401
        assert client.post(
            "/api/cron/centers/assignment-due-reminders"
        ).status_code in {401, 503, 204}
    finally:
        app.dependency_overrides.pop(db_session, None)


@pytest.mark.asyncio
async def test_maybe_open_scheduled_transitions(monkeypatch):
    session = AsyncMock()
    assignment = _assignment(
        status="SCHEDULED", opens_at=utcnow() - timedelta(minutes=1)
    )
    await maybe_open_scheduled(session, assignment)
    assert assignment.status == "OPEN"
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_publish_fans_out_and_notifies(monkeypatch):
    session = AsyncMock()
    center = _center()
    assignment = _assignment(status="DRAFT", assessment_id="assess-1", opens_at=None)
    targets = [_target(kind="CENTER")]
    notified = []

    monkeypatch.setattr(
        "app.services.centers.access.load_staff_center",
        AsyncMock(
            return_value=(
                center,
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.by_center",
        AsyncMock(return_value=assignment),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assignment_targets_repository.for_assignment",
        AsyncMock(return_value=targets),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.resolve_target_user_ids",
        AsyncMock(return_value=["student-1", "student-2"]),
    )

    async def fake_notify(session, **kwargs):
        notified.append(kwargs["student_id"])

    monkeypatch.setattr(
        "app.services.centers.assignments.notify_new_assignment", fake_notify
    )

    result = await PublishAssignmentService(
        session, "tutor-1", "center-1", "asg-1"
    ).process()
    assert result["assignment"]["status"] == "open"
    assert assignment.status == "OPEN"
    assert notified == ["student-1", "student-2"]


@pytest.mark.asyncio
async def test_publish_schedules_when_opens_in_future(monkeypatch):
    session = AsyncMock()
    center = _center()
    opens = utcnow() + timedelta(days=1)
    assignment = _assignment(status="DRAFT", opens_at=opens, assessment_id="assess-1")
    monkeypatch.setattr(
        "app.services.centers.access.load_staff_center",
        AsyncMock(
            return_value=(
                center,
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.by_center",
        AsyncMock(return_value=assignment),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assignment_targets_repository.for_assignment",
        AsyncMock(return_value=[_target()]),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.notify_new_assignment",
        AsyncMock(),
    )
    result = await PublishAssignmentService(
        session, "tutor-1", "center-1", "asg-1"
    ).process()
    assert result["assignment"]["status"] == "scheduled"
    assert assignment.status == "SCHEDULED"


@pytest.mark.asyncio
async def test_open_edit_blocks_question_changes(monkeypatch):
    session = AsyncMock()
    assignment = _assignment(status="OPEN")
    monkeypatch.setattr(
        "app.services.centers.access.load_staff_center",
        AsyncMock(
            return_value=(
                _center(),
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.by_center",
        AsyncMock(return_value=assignment),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.attempts_repository.count",
        AsyncMock(return_value=0),
    )
    body = SimpleNamespace(
        title=None,
        instructions=None,
        dueAt=None,
        settings={"latePolicy": "blocked"},
        filters=None,
        targets=None,
        generate=False,
        opensAt=None,
        questionIds=None,
    )
    with pytest.raises(ApiError) as exc:
        await UpdateAssignmentService(
            session, "tutor-1", "center-1", "asg-1", body
        ).process()
    assert exc.value.status_code == 409
    assert exc.value.payload.get("code") == "ASSIGNMENT_NON_DESTRUCTIVE"


@pytest.mark.asyncio
async def test_student_start_sets_assignment_id_and_d6(monkeypatch):
    session = AsyncMock()
    assignment = _assignment(status="OPEN")
    assessment = _assessment()
    paper = [SimpleNamespace(id="q1", topic_id="t1", subject_id="subj-1")]
    added = []

    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.by_id",
        AsyncMock(return_value=assignment),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.memberships_repository.by_center_user",
        AsyncMock(return_value=_membership()),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.student_is_targeted",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("app.services.centers.assignments.reap_stale", AsyncMock())
    monkeypatch.setattr(
        "app.services.centers.assignments.attempts_repository.list_where",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assessments_repository.by_id",
        AsyncMock(return_value=assessment),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments._paper_questions",
        AsyncMock(return_value=paper),
    )

    async def fake_add(session, entity, flush=False):
        added.append(entity)
        return entity

    monkeypatch.setattr(
        "app.services.centers.assignments.attempts_repository.add", fake_add
    )
    monkeypatch.setattr(
        "app.services.centers.assignments._payload",
        lambda assessment, attempt, paper, resumed=False: {
            "attemptId": attempt.id,
            "assessmentId": assessment.id,
            "questions": [],
        },
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.capture", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.centers_repository.by_id",
        AsyncMock(return_value=_center()),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.users_repository.by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                id="tutor-1",
                first_name="Tolu",
                last_name="Ade",
                email="tutor@example.com",
            )
        ),
    )

    # Freemium student — start must succeed without any require_feature / can_gate.
    result = await StartAssignedAttemptService(
        session, "student-freemium", "asg-1"
    ).process()
    assert result["assignmentId"] == "asg-1"
    assert result["tierBypass"] is True
    assert result["settings"]["focusLossDisclosed"] is True
    assert result["sharedWith"]["tutorName"] == "Tolu Ade"
    assert result["sharedWith"]["centerName"] == "Lagos Prep"
    assert len(added) == 1
    assert added[0].assignment_id == "asg-1"
    assert added[0].student_id == "student-freemium"
    assert added[0].assessment_id == "assess-1"


@pytest.mark.asyncio
async def test_late_blocked_refuses_start(monkeypatch):
    session = AsyncMock()
    assignment = _assignment(
        status="OPEN",
        due_at=utcnow() - timedelta(hours=1),
        settings={
            "latePolicy": "blocked",
            "attemptsAllowed": 1,
            "filters": {},
        },
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.by_id",
        AsyncMock(return_value=assignment),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.memberships_repository.by_center_user",
        AsyncMock(return_value=_membership()),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.student_is_targeted",
        AsyncMock(return_value=True),
    )
    with pytest.raises(ApiError) as exc:
        await StartAssignedAttemptService(session, "student-1", "asg-1").process()
    assert exc.value.payload.get("code") == "ASSIGNMENT_LATE_BLOCKED"


@pytest.mark.asyncio
async def test_assigned_inbox_lists_open_by_due(monkeypatch):
    session = AsyncMock()
    center = _center()
    tutor = SimpleNamespace(
        id="tutor-1", first_name="Tolu", last_name="Ade", email="t@example.com"
    )
    early = _assignment(
        id="asg-early",
        status="OPEN",
        due_at=utcnow() + timedelta(hours=5),
        title="Soon",
    )
    later = _assignment(
        id="asg-later",
        status="OPEN",
        due_at=utcnow() + timedelta(days=3),
        title="Later",
    )
    draft = _assignment(id="asg-draft", status="DRAFT")

    monkeypatch.setattr(
        "app.services.centers.assignments.memberships_repository.for_student",
        AsyncMock(return_value=[_membership()]),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.centers_repository.by_id",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.users_repository.by_id",
        AsyncMock(return_value=tutor),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.for_center",
        AsyncMock(return_value=[later, draft, early]),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.student_is_targeted",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.attempts_repository.list_where",
        AsyncMock(return_value=[]),
    )

    result = await ListAssignedInboxService(session, "student-1").process()
    ids = [row["id"] for row in result["assignments"]]
    assert ids == ["asg-early", "asg-later"]
    assert result["assignments"][0]["sharedWith"]["centerName"] == "Lagos Prep"


@pytest.mark.asyncio
async def test_resolve_center_targets_active_only(monkeypatch):
    session = AsyncMock()
    active = _membership(user_id="s1", status="ACTIVE")
    left = _membership(id="mem-2", user_id="s2", status="LEFT")
    user = SimpleNamespace(id="s1")
    monkeypatch.setattr(
        "app.services.centers.assignments.memberships_repository.roster_rows",
        AsyncMock(return_value=[(active, user), (left, user)]),
    )
    ids = await resolve_target_user_ids(session, "center-1", [_target(kind="CENTER")])
    assert ids == ["s1"]


@pytest.mark.asyncio
async def test_create_assignment_draft(monkeypatch):
    session = AsyncMock()
    added = []
    monkeypatch.setattr(
        "app.services.centers.access.load_staff_center",
        AsyncMock(
            return_value=(
                _center(),
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )

    async def fake_add(session, entity, flush=False):
        added.append(entity)
        return entity

    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.add", fake_add
    )
    body = SimpleNamespace(
        title="Week 3 quiz",
        taskType="TOPIC_QUIZ",
        settings=None,
        filters={"subjectId": "subj-1", "count": 10},
        targets=[],
        opensAt=None,
        dueAt=None,
        generate=False,
    )
    result = await CreateAssignmentService(
        session, "tutor-1", "center-1", body
    ).process()
    assert result["assignment"]["status"] == "draft"
    assert result["assignment"]["title"] == "Week 3 quiz"
    assert len(added) == 1
    assert added[0].status == "DRAFT"


@pytest.mark.asyncio
async def test_privacy_still_holds_for_assignment_routes():
    """Tutor JWT cannot hit Assigned inbox; student JWT cannot hit tutor CRUD."""
    from fastapi.testclient import TestClient

    from app.core.security import student_token, tutor_token
    from app.database.db import db_session
    from main import app

    session = AsyncMock()

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        tutor = tutor_token(
            {"sub": "tutor-1", "realm": "tutor", "role": "TUTOR", "deviceId": "d1"}
        )
        student = student_token(
            {
                "sub": "student-1",
                "realm": "student",
                "role": "STUDENT",
                "deviceId": "d1",
                "profile": {"role": "STUDENT", "tier": "FREEMIUM"},
            }
        )
        # Wrong-realm tokens must not unlock the other surface.
        assert (
            client.get(
                "/api/centers/assigned",
                headers={"Authorization": f"Bearer {tutor}"},
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/center/api/centers/c1/assignments",
                headers={"Authorization": f"Bearer {student}"},
            ).status_code
            == 401
        )
    finally:
        app.dependency_overrides.pop(db_session, None)
