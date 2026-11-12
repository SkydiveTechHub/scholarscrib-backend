"""M1 Tutor Centers: invites, join, roster, Free cap."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.domain import CENTER_FREE_ACTIVE_STUDENT_LIMIT
from app.services.centers.service import (
    AcceptInviteService,
    CreateCenterService,
    CreateInviteService,
    LeaveCenterService,
    PreviewInviteService,
    RemoveStudentService,
    RosterService,
    UpdateCenterService,
    free_roster_blocks_accept,
)


def _tutor(*, verified: bool = True):
    return SimpleNamespace(
        id="tutor-1",
        email="tutor@example.com",
        email_verified=utcnow() if verified else None,
        first_name="Tolu",
        last_name="Ade",
        state="Lagos",
    )


def _student(**extra):
    base = dict(
        id="student-1",
        email="ada@example.com",
        role="STUDENT",
        first_name="Ada",
        last_name="Obi",
        class_level="SS3",
        track="SCIENCE",
        image=None,
        state="Lagos",
    )
    base.update(extra)
    return SimpleNamespace(**base)


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


def _invite(**extra):
    base = dict(
        id="invite-1",
        center_id="center-1",
        created_by_id="tutor-1",
        code="ABCD2345",
        token="tok-long",
        bound_email=None,
        bound_phone=None,
        use_limit=None,
        use_count=0,
        expires_at=None,
        revoked_at=None,
        created_at=utcnow(),
        updated_at=utcnow(),
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


def test_free_roster_blocks_fifth_accept():
    assert CENTER_FREE_ACTIVE_STUDENT_LIMIT == 5
    assert not free_roster_blocks_accept(0)
    assert not free_roster_blocks_accept(3)
    assert free_roster_blocks_accept(4)
    assert free_roster_blocks_accept(5)


def test_m1_routes_are_mounted(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database.db import db_session
    from main import app

    session = AsyncMock()

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=None),
    )
    try:
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/center/api/centers").status_code == 401
        assert client.get("/center/api/centers/c1/roster").status_code == 401
        assert client.get("/center/api/centers/c1/invites").status_code == 401
        assert client.get("/center/api/centers/c1/groups").status_code == 401
        assert client.get("/api/centers").status_code == 401
        assert client.post("/api/centers/c1/leave").status_code == 401
        assert client.get("/api/centers/invites/MISSING").status_code == 404
        assert (
            client.post(
                "/api/centers/invites/MISSING/accept", json={"accepted": True}
            ).status_code
            == 401
        )
    finally:
        app.dependency_overrides.pop(db_session, None)


@pytest.mark.asyncio
async def test_create_center_adds_owner_membership(monkeypatch):
    session = AsyncMock()
    added = []

    async def fake_add(_session, entity, flush=True):
        added.append(entity)
        return entity

    monkeypatch.setattr("app.services.centers.service.centers_repository.add", fake_add)
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.add", fake_add
    )
    body = SimpleNamespace(
        name="Ikeja JAMB",
        state="Lagos",
        lga="Ikeja",
        subjects=["Physics"],
        levels=["SS3"],
    )
    result = await CreateCenterService(session, "tutor-1", body).process()
    assert result["center"]["name"] == "Ikeja JAMB"
    assert result["center"]["plan"] == "FREE"
    assert len(added) == 2
    assert added[1].kind == "OWNER"
    assert added[1].status == "ACTIVE"


@pytest.mark.asyncio
async def test_update_center_owner_can_edit_profile(monkeypatch):
    session = AsyncMock()
    center = _center(name="Old Name", subjects=["Physics"], levels=["SS2"])
    monkeypatch.setattr(
        "app.services.centers.service.load_owned_center",
        AsyncMock(return_value=center),
    )
    body = SimpleNamespace(
        name="New Prep",
        state="Ogun",
        lga="Abeokuta",
        subjects=["Mathematics", "English"],
        levels=["SS3", "POST_SS3"],
        model_fields_set={"name", "state", "lga", "subjects", "levels"},
    )
    result = await UpdateCenterService(session, "tutor-1", "center-1", body).process()
    assert result["center"]["name"] == "New Prep"
    assert result["center"]["state"] == "Ogun"
    assert result["center"]["lga"] == "Abeokuta"
    assert result["center"]["subjects"] == ["Mathematics", "English"]
    assert result["center"]["levels"] == ["SS3", "POST_SS3"]
    assert result["center"]["staffRole"] == "owner"
    assert center.name == "New Prep"
    assert center.updated_at is not None
    assert result["center"]["updatedAt"] is not None
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_update_center_rejects_invalid_level(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.load_owned_center",
        AsyncMock(return_value=_center()),
    )
    body = SimpleNamespace(
        levels=["SS9"],
        model_fields_set={"levels"},
    )
    with pytest.raises(ApiError) as exc:
        await UpdateCenterService(session, "tutor-1", "center-1", body).process()
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_update_center_rejects_non_owner(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.load_owned_center",
        AsyncMock(side_effect=ApiError(404, "Center not found")),
    )
    body = SimpleNamespace(
        name="Hijack",
        model_fields_set={"name"},
    )
    with pytest.raises(ApiError) as exc:
        await UpdateCenterService(session, "facilitator-1", "center-1", body).process()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_invite_requires_verified_email(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.load_staff_center",
        AsyncMock(
            return_value=(
                _center(),
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    with pytest.raises(ApiError) as exc:
        await CreateInviteService(
            session,
            _tutor(verified=False),
            "center-1",
            SimpleNamespace(email=None, expiresInDays=None, useLimit=None),
        ).process()
    assert exc.value.status_code == 403
    assert exc.value.payload["code"] == "TUTOR_EMAIL_UNVERIFIED"


@pytest.mark.asyncio
async def test_create_invite_returns_link_and_code(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.load_staff_center",
        AsyncMock(
            return_value=(
                _center(),
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.service.unique_invite_code",
        AsyncMock(return_value="JOIN9XYZ"),
    )
    monkeypatch.setattr(
        "app.services.centers.service.invite_token",
        lambda: "secret-token",
    )
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.add",
        AsyncMock(side_effect=lambda *_a, **_k: _a[1]),
    )
    result = await CreateInviteService(
        session,
        _tutor(),
        "center-1",
        SimpleNamespace(email=None, expiresInDays=7, useLimit=50),
    ).process()
    invite = result["invite"]
    assert invite["code"] == "JOIN9XYZ"
    assert invite["token"] == "secret-token"
    assert invite["link"].endswith("/join/JOIN9XYZ")
    assert invite["useLimit"] == 50
    assert invite["boundEmail"] is None
    assert result["emailSent"] is None


@pytest.mark.asyncio
async def test_create_invite_with_email_binds_and_sends(monkeypatch):
    session = AsyncMock()
    added = []
    monkeypatch.setattr(
        "app.services.centers.service.load_staff_center",
        AsyncMock(
            return_value=(
                _center(),
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.service.unique_invite_code",
        AsyncMock(return_value="MAIL9XYZ"),
    )
    monkeypatch.setattr(
        "app.services.centers.service.invite_token",
        lambda: "secret-token",
    )

    async def fake_add(_session, entity, flush=True):
        added.append(entity)
        return entity

    monkeypatch.setattr("app.services.centers.service.invites_repository.add", fake_add)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.email.EmailService.send_email",
        send,
    )
    result = await CreateInviteService(
        session,
        _tutor(),
        "center-1",
        SimpleNamespace(email="Ada@Example.com", expiresInDays=None, useLimit=None),
    ).process()
    invite = result["invite"]
    assert invite["boundEmail"] == "ada@example.com"
    assert invite["useLimit"] == 1
    assert invite["expiresAt"] is not None
    assert result["emailSent"] is True
    assert added[0].bound_email == "ada@example.com"
    assert added[0].use_limit == 1
    send.assert_awaited_once()
    assert send.await_args.args[0] == "ada@example.com"


@pytest.mark.asyncio
async def test_accept_invite_rejects_email_mismatch(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    invite = _invite(bound_email="ada@example.com", use_limit=1)
    student = _student(email="other@example.com")
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=invite),
    )
    monkeypatch.setattr(
        "app.services.centers.service.centers_repository.by_id",
        AsyncMock(return_value=_center()),
    )
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(
            side_effect=lambda _s, uid: _tutor() if uid == "tutor-1" else student
        ),
    )
    with pytest.raises(ApiError) as exc:
        await AcceptInviteService(
            session,
            "student-1",
            "ABCD2345",
            SimpleNamespace(
                accepted=True,
                version="v1",
                acknowledgedVisibility=True,
                acknowledgedLeave=True,
            ),
            ip="127.0.0.1",
        ).process()
    assert exc.value.status_code == 403
    assert exc.value.payload["code"] == "INVITE_EMAIL_MISMATCH"


@pytest.mark.asyncio
async def test_preview_invite_returns_consent_copy(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=_invite()),
    )
    monkeypatch.setattr(
        "app.services.centers.service.centers_repository.by_id",
        AsyncMock(return_value=_center()),
    )
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(return_value=_tutor()),
    )
    result = await PreviewInviteService(session, "ABCD2345").process()
    assert result["center"]["name"] == "Lagos Prep"
    assert result["tutor"]["firstName"] == "Tolu"
    assert result["consent"]["version"] == "v1"
    assert "Results of tasks this tutor assigns" in result["consent"]["tutorWillSee"]


@pytest.mark.asyncio
async def test_accept_invite_creates_active_membership(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    invite = _invite()
    center = _center()
    student = _student()
    added = []

    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=invite),
    )
    monkeypatch.setattr(
        "app.services.centers.service.centers_repository.by_id",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(side_effect=[_tutor(), student]),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.by_center_user",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.active_student_count",
        AsyncMock(return_value=1),
    )

    async def fake_add(_session, entity, flush=True):
        added.append(entity)
        return entity

    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.add", fake_add
    )
    notify = AsyncMock()
    monkeypatch.setattr("app.services.centers.service.notify_invite_accepted", notify)

    body = SimpleNamespace(
        accepted=True,
        version="v1",
        acknowledgedVisibility=True,
        acknowledgedLeave=True,
    )
    # AcceptInviteService loads owner then student via by_id — reset side_effect
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(
            side_effect=lambda _s, uid: _tutor() if uid == "tutor-1" else student
        ),
    )
    result = await AcceptInviteService(
        session, "student-1", "ABCD2345", body, ip="127.0.0.1"
    ).process()
    assert result["alreadyMember"] is False
    assert result["membership"]["status"] == "active"
    assert added[0].status == "ACTIVE"
    assert added[0].consent["accepted"] is True
    assert invite.use_count == 1
    notify.assert_awaited()


@pytest.mark.asyncio
async def test_accept_invite_blocks_free_fifth(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=_invite()),
    )
    monkeypatch.setattr(
        "app.services.centers.service.centers_repository.by_id",
        AsyncMock(return_value=_center(plan="FREE")),
    )
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(
            side_effect=lambda _s, uid: _tutor() if uid == "tutor-1" else _student()
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.by_center_user",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.active_student_count",
        AsyncMock(return_value=4),
    )
    body = SimpleNamespace(
        accepted=True,
        version="v1",
        acknowledgedVisibility=True,
        acknowledgedLeave=True,
    )
    with pytest.raises(ApiError) as exc:
        await AcceptInviteService(
            session, "student-1", "ABCD2345", body, ip="127.0.0.1"
        ).process()
    assert exc.value.status_code == 403
    assert exc.value.payload["code"] == "CENTER_FREE_CAP"
    assert exc.value.payload["upgradePlan"] == "PRO"


@pytest.mark.asyncio
async def test_accept_allows_fifth_on_pro(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    invite = _invite()
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=invite),
    )
    monkeypatch.setattr(
        "app.services.centers.service.centers_repository.by_id",
        AsyncMock(return_value=_center(plan="PRO")),
    )
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(
            side_effect=lambda _s, uid: _tutor() if uid == "tutor-1" else _student()
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.by_center_user",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.active_student_count",
        AsyncMock(return_value=4),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.add",
        AsyncMock(side_effect=lambda *_a, **_k: _a[1]),
    )
    monkeypatch.setattr(
        "app.services.centers.service.notify_invite_accepted",
        AsyncMock(),
    )
    body = SimpleNamespace(
        accepted=True,
        version="v1",
        acknowledgedVisibility=True,
        acknowledgedLeave=True,
    )
    result = await AcceptInviteService(
        session, "student-1", "ABCD2345", body, ip="127.0.0.1"
    ).process()
    assert result["membership"]["status"] == "active"


@pytest.mark.asyncio
async def test_accept_rejects_without_consent(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=_invite()),
    )
    monkeypatch.setattr(
        "app.services.centers.service.centers_repository.by_id",
        AsyncMock(return_value=_center()),
    )
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(return_value=_tutor()),
    )
    with pytest.raises(ApiError) as exc:
        await AcceptInviteService(
            session,
            "student-1",
            "ABCD2345",
            SimpleNamespace(accepted=False, version="v1"),
            ip="127.0.0.1",
        ).process()
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_accept_rejects_revoked_invite(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=_invite(revoked_at=utcnow())),
    )
    with pytest.raises(ApiError) as exc:
        await PreviewInviteService(session, "ABCD2345").process()
    assert exc.value.status_code == 410
    assert exc.value.payload["code"] == "INVITE_REVOKED"


@pytest.mark.asyncio
async def test_roster_lists_student_statuses(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.service.load_staff_center",
        AsyncMock(
            return_value=(
                _center(),
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.roster_rows",
        AsyncMock(
            return_value=[
                (_membership(status="ACTIVE"), _student()),
                (
                    _membership(
                        id="mem-2",
                        user_id="student-2",
                        status="REMOVED",
                        left_at=utcnow(),
                    ),
                    _student(id="student-2", first_name="Bisi"),
                ),
            ]
        ),
    )
    result = await RosterService(session, "tutor-1", "center-1").process()
    assert len(result["members"]) == 2
    assert result["members"][0]["status"] == "active"
    assert result["members"][0]["firstName"] == "Ada"
    assert result["members"][1]["status"] == "removed"
    assert result["activeCount"] == 1
    assert result["activeLimit"] == CENTER_FREE_ACTIVE_STUDENT_LIMIT
    assert result["plan"] == "FREE"


@pytest.mark.asyncio
async def test_remove_student_notifies(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    membership = _membership(status="ACTIVE")
    monkeypatch.setattr(
        "app.services.centers.service.load_staff_center",
        AsyncMock(
            return_value=(
                _center(),
                SimpleNamespace(kind="OWNER", status="ACTIVE", user_id="tutor-1"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.by_id",
        AsyncMock(return_value=membership),
    )
    monkeypatch.setattr(
        "app.services.centers.service.users_repository.by_id",
        AsyncMock(return_value=_student()),
    )
    notify = AsyncMock()
    monkeypatch.setattr("app.services.centers.service.notify_student_removed", notify)
    result = await RemoveStudentService(
        session, "tutor-1", "center-1", "mem-1"
    ).process()
    assert result["membership"]["status"] == "removed"
    assert membership.status == "REMOVED"
    notify.assert_awaited()


@pytest.mark.asyncio
async def test_student_leave_sets_left(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    membership = _membership(status="ACTIVE")
    monkeypatch.setattr(
        "app.services.centers.service.memberships_repository.by_center_user",
        AsyncMock(return_value=membership),
    )
    result = await LeaveCenterService(session, "student-1", "center-1").process()
    assert result["membership"]["status"] == "left"
    assert membership.status == "LEFT"
    assert membership.left_at is not None


@pytest.mark.asyncio
async def test_preview_rejects_expired_invite(monkeypatch):
    session = AsyncMock()
    expired = utcnow() - timedelta(days=1)
    monkeypatch.setattr(
        "app.services.centers.service.invites_repository.by_code_or_token",
        AsyncMock(return_value=_invite(expires_at=expired)),
    )
    with pytest.raises(ApiError) as exc:
        await PreviewInviteService(session, "ABCD2345").process()
    assert exc.value.status_code == 410
    assert exc.value.payload["code"] == "INVITE_EXPIRED"
