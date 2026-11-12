"""X-1 privacy harness: tutor JWT must not read unscoped student data."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TutorPrincipal, require_tutor
from app.core.security import student_token, tutor_token
from app.database.db import db_session
from app.services.auth.service import issue_tutor_token
from main import app

STUDENT_ID = "student-private-1"


def _tutor_user(**extra):
    return SimpleNamespace(
        id="tutor-1",
        email="tutor@example.com",
        role="TUTOR",
        first_name="Tolu",
        last_name="Ade",
        state="Lagos",
        image=None,
        is_active=True,
        sessions_valid_from=None,
        password_hash="hashed",
        email_verified=None,
        **extra,
    )


def _tutor_principal() -> TutorPrincipal:
    return TutorPrincipal(
        id="tutor-1",
        email="tutor@example.com",
        device_id="dev-1",
        state="Lagos",
        first_name="Tolu",
        last_name="Ade",
        image=None,
        has_password=True,
        issued_at=None,
        email_verified=None,
    )


@pytest.fixture
def client():
    session = AsyncMock()

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    try:
        test_client = TestClient(app)
        yield test_client
    finally:
        app.dependency_overrides.pop(db_session, None)
        app.dependency_overrides.pop(require_tutor, None)


def test_center_health_is_mounted(client):
    response = client.get("/center/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "realm": "tutor"}


def test_tutor_token_cannot_read_student_performance(client, monkeypatch):
    """Student realm must reject a tutor-signed JWT (wrong secret / realm)."""
    token = tutor_token(
        {"sub": "tutor-1", "realm": "tutor", "role": "TUTOR", "deviceId": "dev-1"}
    )
    monkeypatch.setattr(
        "app.api.deps.users_repository.by_id",
        AsyncMock(return_value=_tutor_user()),
    )
    response = client.get(
        "/api/performance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_tutor_token_cannot_read_student_astra_threads(client, monkeypatch):
    token = tutor_token(
        {"sub": "tutor-1", "realm": "tutor", "role": "TUTOR", "deviceId": "dev-1"}
    )
    monkeypatch.setattr(
        "app.api.deps.users_repository.by_id",
        AsyncMock(return_value=_tutor_user()),
    )
    response = client.get(
        "/api/tutor/threads",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Wrong realm → unauthorized before feature gates / data access.
    assert response.status_code == 401


def test_student_token_cannot_open_center_session(client, monkeypatch):
    token = student_token(
        {
            "sub": "student-1",
            "profile": {"role": "STUDENT", "tier": "FREEMIUM"},
            "deviceId": "dev-s",
        }
    )
    monkeypatch.setattr(
        "app.api.deps.users_repository.by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                id="student-1",
                email="s@example.com",
                role="STUDENT",
                is_active=True,
                sessions_valid_from=None,
                password_hash="x",
                first_name="Ada",
                last_name="O",
                state="Lagos",
                image=None,
                email_verified=None,
                tier="FREEMIUM",
                class_level="SS1",
                track="SCIENCE",
            )
        ),
    )
    response = client.get(
        "/center/api/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        f"/center/api/privacy/students/{STUDENT_ID}/attempts",
        f"/center/api/privacy/students/{STUDENT_ID}/mastery",
        f"/center/api/privacy/students/{STUDENT_ID}/astra",
    ],
)
def test_unscoped_center_privacy_stubs_are_forbidden(client, path):
    app.dependency_overrides[require_tutor] = lambda: _tutor_principal()
    try:
        response = client.get(path)
    finally:
        app.dependency_overrides.pop(require_tutor, None)
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "CENTER_PRIVACY_X1"
    assert "assignment-scoped" in body["error"]


def test_center_astra_mastery_tool_cannot_return_unscoped_data():
    """AI-3: Astra tool surface refuses mastery (same X-1 bar as privacy stubs)."""
    from app.ai.center_astra.tools.reads import refuse_unscoped

    with pytest.raises(Exception) as exc:
        refuse_unscoped("topicMastery")
    err = exc.value
    assert getattr(err, "status_code", None) == 403
    assert getattr(err, "payload", {}).get("code") == "CENTER_PRIVACY_X1"


def test_issue_tutor_token_uses_tutor_realm(monkeypatch):
    user = _tutor_user()
    token = issue_tutor_token(user, "device-9")
    from app.core.config import settings
    from app.core.security import decode_token

    claims = decode_token(token, settings.tutor_auth_secret)
    assert claims is not None
    assert claims["sub"] == "tutor-1"
    assert claims["realm"] == "tutor"
    assert claims["role"] == "TUTOR"
    assert claims["deviceId"] == "device-9"
    assert decode_token(token, settings.auth_secret) is None
    assert decode_token(token, settings.admin_auth_secret) is None
