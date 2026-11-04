from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ApiError
from app.core.security import hash_password
from app.services.auth import DeleteAccountService, GoogleSignInService
from app.services.email import EmailService

PASSWORD = "correct-horse-battery"


@pytest.fixture
def session():
    session = AsyncMock()
    session.flush = AsyncMock()
    return session


def _user(**overrides):
    values = {
        "id": "u1",
        "email": "student@example.com",
        "role": "STUDENT",
        "tier": "FREEMIUM",
        "password_hash": hash_password(PASSWORD),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def patched(monkeypatch):
    """Patch the repositories DeleteAccountService touches; returns the mocks."""
    mocks = SimpleNamespace(
        by_id=AsyncMock(),
        remove=AsyncMock(),
        threads=AsyncMock(return_value=[SimpleNamespace(id="t1")]),
    )
    monkeypatch.setattr("app.services.auth.service.users_repository.by_id", mocks.by_id)
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.remove", mocks.remove
    )
    monkeypatch.setattr(
        "app.services.auth.service.chat_threads_repository.for_user", mocks.threads
    )
    return mocks


async def test_delete_account_unknown_user(session, patched):
    patched.by_id.return_value = None
    with pytest.raises(ApiError) as exc:
        await DeleteAccountService(session, "u1", PASSWORD, None).process()
    assert exc.value.status_code == 404
    patched.remove.assert_not_awaited()


async def test_delete_account_rejects_non_student(session, patched):
    patched.by_id.return_value = _user(role="ADMIN")
    with pytest.raises(ApiError) as exc:
        await DeleteAccountService(session, "u1", PASSWORD, None).process()
    assert exc.value.status_code == 403
    patched.remove.assert_not_awaited()


@pytest.mark.parametrize("password", [None, "", "wrong-password"])
async def test_delete_account_wrong_or_missing_password(session, patched, password):
    patched.by_id.return_value = _user()
    with pytest.raises(ApiError) as exc:
        await DeleteAccountService(session, "u1", password, None).process()
    assert exc.value.status_code == 400
    assert "Incorrect password" in str(exc.value)
    patched.remove.assert_not_awaited()


async def test_delete_account_with_password(session, patched):
    user = _user()
    patched.by_id.return_value = user
    result = await DeleteAccountService(session, "u1", PASSWORD, None).process()
    assert result == {"ok": True}
    patched.remove.assert_awaited_once_with(session, user)


async def test_delete_account_google_user_needs_matching_email(session, patched):
    user = _user(password_hash=None)
    patched.by_id.return_value = user

    with pytest.raises(ApiError) as exc:
        await DeleteAccountService(session, "u1", None, "other@example.com").process()
    assert exc.value.status_code == 400
    patched.remove.assert_not_awaited()

    result = await DeleteAccountService(
        session, "u1", None, "  Student@Example.com "
    ).process()
    assert result == {"ok": True}
    patched.remove.assert_awaited_once_with(session, user)


async def test_delete_account_purges_checkpoints(session, patched, monkeypatch):
    patched.by_id.return_value = _user()
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    monkeypatch.setattr("app.ai.core.checkpointer._checkpointer", checkpointer)

    await DeleteAccountService(session, "u1", PASSWORD, None).process()
    checkpointer.adelete_thread.assert_awaited_once_with("t1")


async def test_delete_account_survives_cleanup_failure(session, patched, monkeypatch):
    patched.by_id.return_value = _user()
    checkpointer = SimpleNamespace(
        adelete_thread=AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr("app.ai.core.checkpointer._checkpointer", checkpointer)

    result = await DeleteAccountService(session, "u1", PASSWORD, None).process()
    assert result == {"ok": True}


async def test_google_signup_sends_welcome_only_when_created(session, monkeypatch):
    sent = []

    async def fake_send_email(to_email, subject, template_name, context, text=None):
        sent.append(template_name)
        return True

    monkeypatch.setattr(EmailService, "send_email", fake_send_email)
    monkeypatch.setattr(
        "app.services.auth.service.accounts_repository.by_provider",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.auth.service.accounts_repository.add", AsyncMock()
    )

    async def fake_add(_session, user, flush=False):
        user.is_active = True  # column default, applied by the DB on insert

    monkeypatch.setattr("app.services.auth.service.users_repository.add", fake_add)
    monkeypatch.setattr(
        "app.services.auth.service.RefreshUserTierService.process", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.auth.service.RegisterDeviceService.process",
        AsyncMock(return_value=SimpleNamespace(id="d1")),
    )
    monkeypatch.setattr(
        "app.services.auth.service.issue_student_token", lambda *_: "token"
    )
    profile = {"sub": "g1", "email": "New@Example.com", "given_name": "Ada"}

    # New user: created, so welcome email goes out.
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(return_value=None),
    )
    await GoogleSignInService(session, profile, "Chrome").process()
    assert sent == ["student_welcome.html"]

    # Existing user: no second welcome.
    sent.clear()
    existing = SimpleNamespace(
        id="u9",
        email="new@example.com",
        is_active=True,
        sessions_valid_from=None,
        role="STUDENT",
        class_level=None,
        track=None,
        state=None,
        first_name="Ada",
        last_name=None,
        image=None,
        tier="FREEMIUM",
    )
    monkeypatch.setattr(
        "app.services.auth.service.users_repository.by_email",
        AsyncMock(return_value=existing),
    )
    await GoogleSignInService(session, profile, "Chrome").process()
    assert sent == []
