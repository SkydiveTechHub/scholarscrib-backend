from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ApiError
from app.core.security import hash_password, verify_password
from app.services.admin import (
    AdminForgotPasswordService,
    AdminResetPasswordService,
)
from app.services.auth import (
    DirectGoogleSignInService,
    StudentForgotPasswordService,
    StudentOTPService,
    StudentResetPasswordService,
)
from app.services.email import EmailService


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_email(monkeypatch):
    sent = []

    async def fake_send_email(to_email, subject, template_name, context, text=None):
        sent.append(
            {
                "email": to_email,
                "subject": subject,
                "template": template_name,
                "context": context,
            }
        )
        return True

    monkeypatch.setattr(EmailService, "send_email", fake_send_email)
    return sent


async def test_student_forgot_password_unknown_email(
    mock_session, mock_email, monkeypatch
):
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(return_value=None),
    )
    service = StudentForgotPasswordService(
        mock_session, "unknown@example.com", "127.0.0.1"
    )
    res = await service.process()

    assert "instructions to reset your password have been sent" in res["message"]
    assert len(mock_email) == 0


async def test_student_forgot_password_success(mock_session, mock_email, monkeypatch):
    user = SimpleNamespace(id="u1", email="student@example.com", is_active=True)
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(return_value=user),
    )
    tokens_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.auth.service.verification_tokens_repository.create_token",
        tokens_mock,
    )

    service = StudentForgotPasswordService(
        mock_session, "student@example.com", "127.0.0.1"
    )
    res = await service.process()

    assert "instructions to reset your password have been sent" in res["message"]
    assert tokens_mock.await_count == 1
    call_args = tokens_mock.call_args[1]
    assert call_args["identifier"] == "student:reset:student@example.com"
    assert len(mock_email) == 1
    assert mock_email[0]["email"] == "student@example.com"
    assert mock_email[0]["template"] == "student_password_reset.html"
    assert "token=" in mock_email[0]["context"]["reset_url"]


async def test_student_reset_password_invalid_token(mock_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.auth.service.verification_tokens_repository.get_valid_token",
        AsyncMock(return_value=None),
    )
    service = StudentResetPasswordService(
        mock_session, "student@example.com", "bad-token", "newSecretPassword123"
    )

    with pytest.raises(ApiError) as exc_info:
        await service.process()
    assert exc_info.value.status_code == 400
    assert "Invalid or expired" in str(exc_info.value)


async def test_student_reset_password_same_password(mock_session, monkeypatch):
    old_pw = "existingPassword123"
    user = SimpleNamespace(
        id="u1",
        email="student@example.com",
        is_active=True,
        password_hash=hash_password(old_pw),
        sessions_valid_from=None,
    )
    token_row = SimpleNamespace(
        identifier="student:reset:student@example.com", token="valid-token"
    )

    monkeypatch.setattr(
        "app.services.auth.service.verification_tokens_repository.get_valid_token",
        AsyncMock(return_value=token_row),
    )
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(return_value=user),
    )

    service = StudentResetPasswordService(
        mock_session, "student@example.com", "valid-token", old_pw
    )

    with pytest.raises(ApiError) as exc_info:
        await service.process()
    assert exc_info.value.status_code == 400
    assert "cannot be the same" in str(exc_info.value)


async def test_student_reset_password_success(mock_session, monkeypatch):
    old_pw = "existingPassword123"
    user = SimpleNamespace(
        id="u1",
        email="student@example.com",
        is_active=True,
        password_hash=hash_password(old_pw),
        sessions_valid_from=None,
    )
    token_row = SimpleNamespace(
        identifier="student:reset:student@example.com", token="valid-token"
    )

    monkeypatch.setattr(
        "app.services.auth.service.verification_tokens_repository.get_valid_token",
        AsyncMock(return_value=token_row),
    )
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(return_value=user),
    )
    delete_token_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.auth.service.verification_tokens_repository.delete_by_identifier",
        delete_token_mock,
    )
    revoke_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.auth.service.RevokeOtherDevicesService.process",
        revoke_mock,
    )

    service = StudentResetPasswordService(
        mock_session, "student@example.com", "valid-token", "completelyNewPassword456"
    )
    res = await service.process()

    assert "Password reset successfully" in res["message"]
    assert verify_password("completelyNewPassword456", user.password_hash)
    assert user.sessions_valid_from is not None
    assert delete_token_mock.await_count == 1


async def test_student_otp_flow(mock_session, mock_email, monkeypatch):
    user = SimpleNamespace(id="u1", email="student@example.com", email_verified=None)
    monkeypatch.setattr(
        "app.services.auth.otp.users_repository.by_email",
        AsyncMock(return_value=user),
    )

    created_token = {}

    async def fake_create(_session, identifier, token, expires):
        created_token["id"] = identifier
        created_token["token"] = token
        return SimpleNamespace(identifier=identifier, token=token, expires=expires)

    async def fake_get_valid(_session, identifier, token):
        if (
            created_token.get("id") == identifier
            and created_token.get("token") == token
        ):
            return SimpleNamespace(identifier=identifier, token=token)
        return None

    monkeypatch.setattr(
        "app.services.auth.otp.verification_tokens_repository.create_token",
        fake_create,
    )
    monkeypatch.setattr(
        "app.services.auth.otp.verification_tokens_repository.get_valid_token",
        fake_get_valid,
    )
    monkeypatch.setattr(
        "app.services.auth.otp.verification_tokens_repository.delete_by_identifier",
        AsyncMock(),
    )

    otp_service = StudentOTPService(mock_session)

    # 1. Send OTP
    await otp_service.send_otp("student@example.com")
    assert len(mock_email) == 1
    assert mock_email[0]["template"] == "student_otp.html"
    sent_code = mock_email[0]["context"]["otp_code"]
    assert len(sent_code) == 6

    # 2. Verify with invalid OTP
    ok, err = await otp_service.verify_otp("student@example.com", "000000")
    assert not ok
    assert "Invalid or expired" in err
    assert user.email_verified is None

    # 3. Verify with correct OTP
    ok, msg = await otp_service.verify_otp("student@example.com", sent_code)
    assert ok
    assert "Email verified successfully" in msg
    assert user.email_verified is not None


async def test_admin_forgot_and_reset_password(mock_session, mock_email, monkeypatch):
    old_pw = "adminInitialPassword"
    admin = SimpleNamespace(
        id="adm1",
        email="admin@example.com",
        username="superadmin",
        is_active=True,
        password_hash=hash_password(old_pw),
        sessions_valid_from=None,
    )

    monkeypatch.setattr(
        "app.services.admin.admins_repository.by_email",
        AsyncMock(return_value=admin),
    )
    monkeypatch.setattr(
        "app.services.admin.admins_repository.by_username",
        AsyncMock(return_value=admin),
    )

    admin_token_stored = {}

    async def fake_create(_session, identifier, token, expires):
        admin_token_stored["id"] = identifier
        admin_token_stored["token"] = token
        return SimpleNamespace(identifier=identifier, token=token)

    async def fake_get_valid(_session, identifier, token):
        if (
            admin_token_stored.get("id") == identifier
            and admin_token_stored.get("token") == token
        ):
            return SimpleNamespace(identifier=identifier, token=token)
        return None

    monkeypatch.setattr(
        "app.services.admin.verification_tokens_repository.create_token",
        fake_create,
    )
    monkeypatch.setattr(
        "app.services.admin.verification_tokens_repository.get_valid_token",
        fake_get_valid,
    )
    monkeypatch.setattr(
        "app.services.admin.verification_tokens_repository.delete_by_identifier",
        AsyncMock(),
    )

    # 1. Admin forgot password
    forgot_service = AdminForgotPasswordService(mock_session, "superadmin", "127.0.0.1")
    res = await forgot_service.process()
    assert "instructions to reset your password have been sent" in res["message"]
    assert len(mock_email) == 1
    assert mock_email[0]["template"] == "admin_password_reset.html"
    assert mock_email[0]["email"] == "admin@example.com"
    assert "token=" in mock_email[0]["context"]["reset_url"]

    token = admin_token_stored["token"]

    # 2. Reset password
    reset_service = AdminResetPasswordService(
        mock_session, "admin@example.com", token, "newAdminPass999"
    )
    res_reset = await reset_service.process()
    assert "Password reset successfully" in res_reset["message"]
    assert verify_password("newAdminPass999", admin.password_hash)
    assert admin.sessions_valid_from is not None


async def test_direct_google_sign_in(mock_session, monkeypatch):
    fake_profile = {
        "sub": "google-123",
        "email": "user@gmail.com",
        "given_name": "Test",
        "family_name": "User",
        "picture": "https://img.example.com/pic.jpg",
    }
    monkeypatch.setattr(
        "app.services.auth.service.exchange_google_code",
        AsyncMock(return_value=fake_profile),
    )

    fake_login_res = {
        "accessToken": "jwt-token-123",
        "user": {"id": "u1", "email": "user@gmail.com"},
    }
    monkeypatch.setattr(
        "app.services.auth.service.GoogleSignInService.process",
        AsyncMock(return_value=fake_login_res),
    )

    service = DirectGoogleSignInService(mock_session, "auth-code-xyz", None, "Chrome")
    result = await service.process()

    assert result["accessToken"] == "jwt-token-123"
    assert result["user"]["email"] == "user@gmail.com"


def test_student_and_admin_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database.db import get_session
    from main import app

    async def override_get_session():
        yield AsyncMock()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    # 1. Student forgot password endpoint
    monkeypatch.setattr(
        "app.api.student.auth.StudentForgotPasswordService.process",
        AsyncMock(return_value={"message": "Sent"}),
    )
    res = client.post(
        "/api/auth/forgot-password", json={"email": "student@example.com"}
    )
    assert res.status_code == 200
    assert res.json() == {"message": "Sent"}

    # 2. Student reset password endpoint
    monkeypatch.setattr(
        "app.api.student.auth.StudentResetPasswordService.process",
        AsyncMock(return_value={"message": "Reset ok"}),
    )
    res = client.post(
        "/api/auth/reset-password",
        json={
            "email": "student@example.com",
            "token": "tok123",
            "newPassword": "newPass1234",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"message": "Reset ok"}

    # 3. Student OTP send endpoint
    monkeypatch.setattr(
        "app.api.student.auth.StudentOTPService.send_otp",
        AsyncMock(),
    )
    res = client.post("/api/auth/otp/send", json={"email": "student@example.com"})
    assert res.status_code == 200
    assert "Verification code sent" in res.json()["message"]

    # 4. Student OTP verify endpoint
    monkeypatch.setattr(
        "app.api.student.auth.StudentOTPService.verify_otp",
        AsyncMock(return_value=(True, "Verified")),
    )
    res = client.post(
        "/api/auth/otp/verify",
        json={"email": "student@example.com", "otp": "123456"},
    )
    assert res.status_code == 200
    assert res.json() == {"detail": "Verified", "status": True}

    # 5. Direct Google tokens endpoint
    monkeypatch.setattr(
        "app.api.student.auth.DirectGoogleSignInService.process",
        AsyncMock(
            return_value={
                "accessToken": "jwt-tok",
                "user": {"id": "u1", "email": "g@example.com"},
            }
        ),
    )
    res = client.post("/api/auth/google/tokens", json={"code": "gcode-123"})
    assert res.status_code == 200
    assert res.json()["accessToken"] == "jwt-tok"

    # 5b. Google start forwards a valid `state` and rejects a malformed one
    monkeypatch.setattr(
        "app.api.student.auth.settings.auth_google_id", "real-id", raising=False
    )
    monkeypatch.setattr(
        "app.api.student.auth.settings.auth_google_secret", "real-secret", raising=False
    )
    state = "a" * 32
    res = client.get(f"/api/auth/google?state={state}", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert f"state={state}" in res.headers["location"]
    res = client.get("/api/auth/google", follow_redirects=False)
    assert "state=" not in res.headers["location"]
    res = client.get("/api/auth/google?state=bad%20state", follow_redirects=False)
    assert res.status_code == 400

    # 6. Admin forgot password endpoint
    monkeypatch.setattr(
        "app.api.admin.auth.AdminForgotPasswordService.process",
        AsyncMock(return_value={"message": "Admin sent"}),
    )
    res = client.post(
        "/admin/api/auth/forgot-password", json={"identifier": "superadmin"}
    )
    assert res.status_code == 200
    assert res.json() == {"message": "Admin sent"}

    # 7. Admin reset password endpoint
    monkeypatch.setattr(
        "app.api.admin.auth.AdminResetPasswordService.process",
        AsyncMock(return_value={"message": "Admin reset ok"}),
    )
    res = client.post(
        "/admin/api/auth/reset-password",
        json={
            "identifier": "superadmin",
            "token": "tok",
            "newPassword": "newAdminPass123",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"message": "Admin reset ok"}

    app.dependency_overrides.clear()


async def test_email_service_jinja2_rendering():
    # 1. Student reset template
    rendered_student = await EmailService.render_template(
        "student_password_reset.html",
        {
            "email": "student@example.com",
            "reset_url": "https://example.com/reset?t=123",
        },
    )
    assert "student@example.com" in rendered_student
    assert "https://example.com/reset?t=123" in rendered_student
    assert "Reset Your Password" in rendered_student

    # 2. Admin reset template
    rendered_admin = await EmailService.render_template(
        "admin_password_reset.html",
        {
            "email": "admin@example.com",
            "reset_url": "https://example.com/admin/reset?t=456",
        },
    )
    assert "admin@example.com" in rendered_admin
    assert "https://example.com/admin/reset?t=456" in rendered_admin
    assert "Admin Password Reset" in rendered_admin

    # 3. OTP template
    rendered_otp = await EmailService.render_template(
        "student_otp.html",
        {"email": "student@example.com", "otp_code": "654321"},
    )
    assert "student@example.com" in rendered_otp
    assert "654321" in rendered_otp
    assert "Verify Your Email Address" in rendered_otp
