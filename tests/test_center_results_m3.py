"""M3 Tutor Centers: scoped results, recommendations, audit, privacy."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import require_tutor
from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.database.db import db_session
from app.services.centers.recommendations import (
    sanitize_recommendation_body,
)
from app.services.centers.results import (
    AssignmentOverviewService,
    StudentScopedDetailService,
    _focus_loss_flag,
    _pick_attempt,
    _student_row_status,
    _topic_evidence_row,
)
from main import app


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
        status="OPEN",
        settings={"attemptScoring": "latest", "attemptsAllowed": 1},
        opens_at=None,
        due_at=utcnow() + timedelta(days=1),
        closed_at=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _attempt(**extra):
    base = dict(
        id="att-1",
        student_id="student-1",
        assessment_id="assess-1",
        assignment_id="asg-1",
        status="COMPLETED",
        score=8,
        total_marks=10,
        percentage=80.0,
        grade="B2",
        time_spent_seconds=400,
        away_events=2,
        started_at=utcnow() - timedelta(hours=1),
        completed_at=utcnow() - timedelta(minutes=30),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_focus_loss_flag_neutral():
    assert _focus_loss_flag(0) == {"flagged": False, "awayEvents": 0}
    assert _focus_loss_flag(3)["flagged"] is True


def test_topic_evidence_floor_thin_copy():
    row = _topic_evidence_row(
        topic_id="t1", topic_title="Simultaneous Equations", correct=1, total=2
    )
    assert row["evidenceLevel"] == "THIN"
    assert row["accuracy"] is None
    assert "assign more" in row["message"]


def test_topic_evidence_enough_shows_accuracy():
    row = _topic_evidence_row(
        topic_id="t1", topic_title="Simultaneous Equations", correct=6, total=8
    )
    assert row["evidenceLevel"] == "ENOUGH"
    assert row["accuracy"] == 75.0
    assert row["status"] == "competent"


def test_pick_attempt_best_scoring():
    low = _attempt(id="a", percentage=40.0, completed_at=utcnow() - timedelta(days=1))
    high = _attempt(id="b", percentage=90.0, completed_at=utcnow())
    assert _pick_attempt([low, high], "best").id == "b"
    assert _pick_attempt([low, high], "first").id == "a"


def test_student_row_status_overdue_and_late():
    due = utcnow() - timedelta(hours=2)
    now = utcnow()
    assert _student_row_status(attempt=None, due_at=due, now=now) == "overdue"
    late = _attempt(completed_at=utcnow())
    assert _student_row_status(attempt=late, due_at=due, now=now) == "late"


def test_sanitize_strips_links_and_limits():
    cleaned = sanitize_recommendation_body(
        "Revise https://evil.example.com/path and www.spam.com please"
    )
    assert "evil" not in cleaned
    assert "[link removed]" in cleaned
    with pytest.raises(ApiError) as exc:
        sanitize_recommendation_body("x" * 2001)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_overview_never_queries_topic_mastery(monkeypatch):
    session = AsyncMock()
    assignment = _assignment()
    center = _center()

    async def owned(*_a, **_k):
        return center, SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1")

    async def by_center(*_a, **_k):
        return assignment

    async def targets(*_a, **_k):
        return [SimpleNamespace(kind="CENTER", target_id=None)]

    async def resolve(*_a, **_k):
        return ["student-1"]

    async def attempts(*_a, **_k):
        return [_attempt()]

    audit_calls = []

    async def audit(*_args, **kwargs):
        audit_calls.append(kwargs)
        return SimpleNamespace(id="audit-1")

    # Ownership helpers live on the assignments mixin module.
    monkeypatch.setattr("app.services.centers.access.load_staff_center", owned)
    monkeypatch.setattr(
        "app.services.centers.assignments.assignments_repository.by_center",
        by_center,
    )
    monkeypatch.setattr(
        "app.services.centers.results.assignment_targets_repository.for_assignment",
        targets,
    )
    monkeypatch.setattr("app.services.centers.results.resolve_target_user_ids", resolve)
    monkeypatch.setattr(
        "app.services.centers.results.attempts_repository.list_where", attempts
    )
    monkeypatch.setattr("app.services.centers.results.record_tutor_access", audit)

    result = await AssignmentOverviewService(
        session, "tutor-1", "center-1", "asg-1"
    ).process()
    assert result["counts"]["submitted"] == 1
    assert result["scores"]["average"] == 80.0
    assert result["framing"]["basedOnTasks"] == 1
    assert audit_calls and audit_calls[0]["action"] == "view_assignment_overview"
    # Guard: no TopicMastery import (docstring may mention the ban).
    import inspect

    import app.services.centers.results as results_mod

    source = inspect.getsource(results_mod)
    assert "from app.database.models" in source
    assert "import TopicMastery" not in source
    assert "TopicMastery," not in source


@pytest.mark.asyncio
async def test_student_detail_rejects_left_member_and_scopes_attempts(monkeypatch):
    session = AsyncMock()
    center = _center()

    async def owned(*_a, **_k):
        return center, SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1")

    async def left_member(*_a, **_k):
        return SimpleNamespace(
            id="mem-1",
            center_id="center-1",
            user_id="student-1",
            kind="STUDENT",
            status="LEFT",
        )

    monkeypatch.setattr("app.services.centers.access.load_staff_center", owned)
    monkeypatch.setattr(
        "app.services.centers.results.memberships_repository.by_center_user",
        left_member,
    )

    with pytest.raises(ApiError) as exc:
        await StudentScopedDetailService(
            session, "tutor-1", "center-1", "student-1"
        ).process()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_student_detail_filters_by_assignment_ids_only(monkeypatch):
    session = AsyncMock()
    center = _center()
    assignment = _assignment()
    attempt = _attempt()
    list_where = AsyncMock(return_value=[attempt])

    async def owned(*_a, **_k):
        return center, SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1")

    async def active_member(*_a, **_k):
        return SimpleNamespace(
            id="mem-1",
            kind="STUDENT",
            status="ACTIVE",
            user_id="student-1",
            center_id="center-1",
        )

    async def for_center(*_a, **_k):
        return [assignment]

    async def student(*_a, **_k):
        return SimpleNamespace(
            id="student-1",
            first_name="Ada",
            last_name="Obi",
            class_level="SS3",
            track="SCIENCE",
            email="ada@example.com",
        )

    async def audit(*_args, **kwargs):
        return SimpleNamespace(id="audit-1")

    async def empty_topics(*_a, **_k):
        return []

    monkeypatch.setattr("app.services.centers.access.load_staff_center", owned)
    monkeypatch.setattr(
        "app.services.centers.results.memberships_repository.by_center_user",
        active_member,
    )
    monkeypatch.setattr(
        "app.services.centers.results.assignments_repository.for_center", for_center
    )
    monkeypatch.setattr(
        "app.services.centers.results.attempts_repository.list_where", list_where
    )
    monkeypatch.setattr("app.services.centers.results.users_repository.by_id", student)
    monkeypatch.setattr("app.services.centers.results.record_tutor_access", audit)
    monkeypatch.setattr(
        StudentScopedDetailService,
        "_topic_breakdown",
        empty_topics,
    )

    result = await StudentScopedDetailService(
        session, "tutor-1", "center-1", "student-1"
    ).process()
    assert result["framing"]["basedOnTasks"] == 1
    assert "Based on 1 task" in result["framing"]["label"]
    assert result["history"][0]["focusLoss"]["flagged"] is True
    # Critically: attempts query must include assignment_id filter, not bare student.
    args, kwargs = list_where.await_args
    # criteria are positional after session
    criteria_text = " ".join(str(item) for item in args[1:])
    assert "assignmentId" in criteria_text or "assignment_id" in criteria_text


def test_m3_routes_mounted():
    session = AsyncMock()

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        paths = [
            "/center/api/centers/c1/assignments/a1/overview",
            "/center/api/centers/c1/assignments/a1/students",
            "/center/api/centers/c1/assignments/a1/items",
            "/center/api/centers/c1/students/s1/results",
            "/center/api/centers/c1/recommendations",
            "/api/centers/recommendations",
        ]
        for path in paths:
            # Unauthenticated → 401 means the route is mounted.
            assert client.get(path).status_code == 401, path
    finally:
        app.dependency_overrides.pop(db_session, None)
        app.dependency_overrides.pop(require_tutor, None)


@pytest.mark.asyncio
async def test_create_recommendation_notifies_and_sanitizes(monkeypatch):
    from app.services.centers.recommendations import CreateRecommendationsService

    session = AsyncMock()
    center = _center()
    added = []
    notified = []

    async def owned(*_a, **_k):
        return center, SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1")

    async def member(*_a, **_k):
        return SimpleNamespace(
            id="mem-1",
            user_id="student-1",
            kind="STUDENT",
            status="ACTIVE",
            center_id="center-1",
        )

    async def add(_session, row, flush=False):
        added.append(row)

    async def notify(*_args, **kwargs):
        notified.append(kwargs)

    async def tutor(*_a, **_k):
        return SimpleNamespace(
            id="tutor-1", first_name="Tolu", last_name="Ade", email="t@x.com"
        )

    monkeypatch.setattr("app.services.centers.access.load_staff_center", owned)
    monkeypatch.setattr(
        "app.services.centers.recommendations.memberships_repository.by_center_user",
        member,
    )
    monkeypatch.setattr(
        "app.services.centers.recommendations.recommendations_repository.add", add
    )
    monkeypatch.setattr(
        "app.services.centers.recommendations.notify_recommendation", notify
    )
    monkeypatch.setattr(
        "app.services.centers.recommendations.users_repository.by_id", tutor
    )

    body = SimpleNamespace(
        model_dump=lambda by_alias=True: {
            "body": "Revise topic X https://bad.example",
            "studentId": "student-1",
            "attachments": {"topicIds": ["topic-1"]},
        }
    )
    result = await CreateRecommendationsService(
        session, "tutor-1", "center-1", body
    ).process()
    assert result["delivered"] == 1
    assert "bad.example" not in added[0].body
    assert added[0].status == "DELIVERED"
    assert added[0].attachments == {"topicIds": ["topic-1"]}
    assert notified and notified[0]["student_id"] == "student-1"
