"""M0 Tutor Centers foundations: registration, plans, Role.TUTOR."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.schemas import RegisterIn
from app.core.errors import ApiError
from app.domain import (
    CENTER_FREE_ACTIVE_STUDENT_LIMIT,
    CENTER_PRO_YEARLY_KOBO,
    CENTER_TEAM_YEARLY_KOBO,
)
from app.services.auth.service import LoginStudentService, RegisterStudentService
from app.services.centers.plans import (
    CENTER_PLAN_CODES,
    SEED_CENTER_PLANS,
    is_center_plan_code,
)

VALID_STUDENT = {
    "firstName": "Ada",
    "lastName": "Obi",
    "email": "ada@example.com",
    "password": "secret1",
    "classLevel": "SS1",
    "track": "SCIENCE",
    "state": "Lagos",
}


def test_register_accepts_tutor_without_class_level():
    body = RegisterIn(
        role="TUTOR",
        firstName="Tolu",
        lastName="Ade",
        email="tutor@example.com",
        password="secret1",
        state="Lagos",
    )
    assert body.role == "TUTOR"
    assert body.classLevel is None
    assert body.track is None


def test_register_rejects_teacher_role():
    with pytest.raises(ValidationError):
        RegisterIn(**{**VALID_STUDENT, "role": "TEACHER"})


def test_register_student_still_requires_class_and_track():
    with pytest.raises(ValidationError):
        RegisterIn(
            role="STUDENT",
            firstName="Ada",
            lastName="Obi",
            email="ada@example.com",
            password="secret1",
            state="Lagos",
        )


def test_center_plan_constants_match_prd():
    from app.domain import (
        CENTER_PRO_MAX_FACILITATORS,
        CENTER_TEAM_MAX_FACILITATORS,
    )

    assert CENTER_FREE_ACTIVE_STUDENT_LIMIT == 5
    assert CENTER_PRO_YEARLY_KOBO == 1_000_000
    assert CENTER_TEAM_YEARLY_KOBO == 1_500_000
    assert CENTER_PRO_MAX_FACILITATORS == 3
    assert CENTER_TEAM_MAX_FACILITATORS == 5
    codes = {row["code"] for row in SEED_CENTER_PLANS}
    assert codes == CENTER_PLAN_CODES
    free = next(row for row in SEED_CENTER_PLANS if row["code"] == "center_free")
    assert "Solo owner" in free["blurb"]
    pro = next(row for row in SEED_CENTER_PLANS if row["code"] == "center_pro")
    assert pro["interval"] == "yearly"
    assert pro["amount_kobo"] == CENTER_PRO_YEARLY_KOBO
    assert pro["display_name"] == "Center Pro"
    assert "3" in pro["blurb"]
    team = next(row for row in SEED_CENTER_PLANS if row["code"] == "center_team")
    assert team["display_name"] == "Center Team"
    assert "5" in team["blurb"]
    assert is_center_plan_code("center_pro")
    assert not is_center_plan_code("standard")


@pytest.mark.asyncio
async def test_register_tutor_skips_student_billing(monkeypatch):
    session = AsyncMock()
    created = {}

    async def fake_add(_session, user, flush=True):
        created["user"] = user

    provision = AsyncMock()
    otp = AsyncMock()
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.add",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.auth.service.StudentOTPService.send_otp",
        otp,
    )
    monkeypatch.setattr(
        "app.services.billing.provisioning.provision_signup_billing",
        provision,
    )
    monkeypatch.setattr("app.services.auth.service.hit", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.auth.service.capture", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.auth.service.hash_password", lambda _p: "hashed")

    body = RegisterIn(
        role="TUTOR",
        firstName="Tolu",
        lastName="Ade",
        email="tutor@example.com",
        password="secret1",
        state="Lagos",
    )
    result = await RegisterStudentService(session, body, "127.0.0.1").process()
    assert created["user"].role == "TUTOR"
    assert created["user"].class_level is None
    assert result["user"]["role"] == "TUTOR"
    provision.assert_not_awaited()
    otp.assert_awaited()


@pytest.mark.asyncio
async def test_student_login_rejects_tutor_accounts(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr("app.services.auth.service.hit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(
            return_value=SimpleNamespace(
                id="t1",
                email="tutor@example.com",
                password_hash="hashed",
                is_active=True,
                role="TUTOR",
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.auth.service.verify_password",
        lambda *_a, **_k: True,
    )
    result = await LoginStudentService(
        session, "tutor@example.com", "secret1", "127.0.0.1", "Browser"
    ).process()
    assert result is None


@pytest.mark.asyncio
async def test_register_guard_rejects_teacher_even_if_forced(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr("app.services.auth.service.hit", lambda *_a, **_k: None)
    body = SimpleNamespace(
        role="TEACHER",
        email="t@example.com",
        firstName="A",
        lastName="B",
        password="secret1",
        classLevel="SS1",
        track="SCIENCE",
        state="Lagos",
    )
    with pytest.raises(ApiError) as exc:
        await RegisterStudentService(session, body, "127.0.0.1").process()
    assert exc.value.status_code == 400
