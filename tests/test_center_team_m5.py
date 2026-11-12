"""M5: Team plan checkout, facilitator invite limits, permission denials."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.domain import (
    CENTER_PRO_MAX_FACILITATORS,
    CENTER_TEAM_MAX_FACILITATORS,
    CENTER_TEAM_YEARLY_KOBO,
)
from app.services.centers.access import load_staff_center
from app.services.centers.admin import SetTutorStatusService
from app.services.centers.billing import (
    ApplyCenterChargeSuccessService,
    GetCenterBillingService,
    activate_center_plan,
)
from app.services.centers.facilitators import (
    AcceptFacilitatorInviteService,
    ArchiveCenterService,
    InviteFacilitatorService,
    RevokeFacilitatorService,
)


def _tutor(uid="tutor-1", **extra):
    base = dict(
        id=uid,
        email=f"{uid}@example.com",
        role="TUTOR",
        is_active=True,
        suspended_at=None,
        suspended_reason=None,
        sessions_valid_from=None,
        tutor_verified_at=utcnow(),
        email_verified=utcnow(),
        first_name="Tolu",
        last_name="Ade",
        state="Lagos",
        phone=None,
        image=None,
        created_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _center(**extra):
    base = dict(
        id="center-1",
        owner_id="tutor-1",
        name="Lagos Prep",
        plan="TEAM",
        plan_paid_until=utcnow() + timedelta(days=365),
        archived_at=None,
        state="Lagos",
        lga="Ikeja",
        subjects=[],
        levels=[],
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _membership(**extra):
    base = dict(
        id="mem-1",
        center_id="center-1",
        user_id="fac-1",
        kind="FACILITATOR",
        status="ACTIVE",
        joined_at=utcnow(),
        left_at=None,
        created_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _invite(**extra):
    base = dict(
        id="inv-1",
        center_id="center-1",
        created_by_id="tutor-1",
        kind="FACILITATOR",
        code="TEAMCODE",
        token="tok-fac",
        bound_email=None,
        use_limit=1,
        use_count=0,
        expires_at=utcnow() + timedelta(days=14),
        revoked_at=None,
        created_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_team_checkout_allows_yearly(monkeypatch):
    from app.services.billing.service import CheckoutService

    user = SimpleNamespace(
        id="tutor-1",
        email="tutor@example.com",
        paystack_subscription_code=None,
        paystack_email_token=None,
        tier="FREEMIUM",
        billing_status="freemium",
    )
    session = AsyncMock()
    center = _center(plan="FREE", plan_paid_until=None)
    added = {}

    async def fake_add(_session, row, flush=False):
        added["row"] = row
        return row

    monkeypatch.setattr(
        "app.services.billing.service.settings",
        SimpleNamespace(billing_enabled=True, frontend_url="https://app.test"),
    )
    monkeypatch.setattr(
        "app.services.billing.service.plan_by_code",
        AsyncMock(
            return_value=SimpleNamespace(
                is_purchasable=True,
                amount_kobo=CENTER_TEAM_YEARLY_KOBO,
                code="center_team",
            )
        ),
    )
    monkeypatch.setattr(
        "app.database.repositories.centers.centers_repository.owned_by",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.database.repositories.centers.center_plan_checkouts_repository.abandon_pending",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.database.repositories.centers.center_plan_checkouts_repository.add",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.billing.service.new_reference", lambda: "ctr_team_1"
    )
    monkeypatch.setattr("app.services.billing.service.capture", lambda *_a, **_k: None)

    svc = CheckoutService(session, user, "TEAM", "YEARLY", center_id="center-1")
    assert svc.period == "YEARLY"
    assert svc.plan_code == "center_team"
    await svc._validate()
    amount = await svc._amount()
    assert amount == CENTER_TEAM_YEARLY_KOBO
    await svc._store_pending("ctr_team_1", CENTER_TEAM_YEARLY_KOBO)
    assert added["row"].plan == "TEAM"
    assert added["row"].amount_kobo == CENTER_TEAM_YEARLY_KOBO


@pytest.mark.asyncio
async def test_pro_can_upgrade_to_team(monkeypatch):
    from app.services.billing.service import CheckoutService

    user = SimpleNamespace(id="tutor-1", email="tutor@example.com")
    session = AsyncMock()
    center = _center(plan="PRO", plan_paid_until=utcnow() + timedelta(days=100))
    monkeypatch.setattr(
        "app.services.billing.service.settings",
        SimpleNamespace(billing_enabled=True),
    )
    monkeypatch.setattr(
        "app.services.billing.service.plan_by_code",
        AsyncMock(
            return_value=SimpleNamespace(
                is_purchasable=True,
                amount_kobo=CENTER_TEAM_YEARLY_KOBO,
                code="center_team",
            )
        ),
    )
    monkeypatch.setattr(
        "app.database.repositories.centers.centers_repository.owned_by",
        AsyncMock(return_value=center),
    )
    svc = CheckoutService(session, user, "TEAM", "YEARLY", center_id="center-1")
    await svc._validate()


@pytest.mark.asyncio
async def test_apply_center_charge_sets_team(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    now = utcnow()
    end = now + timedelta(days=365)
    row = SimpleNamespace(
        id="cko-1",
        center_id="center-1",
        user_id="tutor-1",
        plan="TEAM",
        period="YEARLY",
        status="PENDING",
        amount_kobo=CENTER_TEAM_YEARLY_KOBO,
        currency="NGN",
        paid_at=None,
        starts_at=None,
        ends_at=None,
        channel=None,
    )
    center = _center(plan="PRO")
    monkeypatch.setattr(
        "app.services.centers.billing.center_plan_checkouts_repository.by_reference_for_update",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        "app.services.centers.billing.settlement_outcome",
        lambda **_k: "activate",
    )
    monkeypatch.setattr(
        "app.services.centers.billing.centers_repository.by_id",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr("app.services.centers.billing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.centers.billing.term_end", lambda *_a, **_k: end)
    monkeypatch.setattr("app.services.centers.billing.capture", lambda *_a, **_k: None)
    outcome = await ApplyCenterChargeSuccessService(
        session,
        "ref-team",
        {"status": "success", "amount": CENTER_TEAM_YEARLY_KOBO, "currency": "NGN"},
    ).process()
    assert outcome == "activate"
    assert center.plan == "TEAM"
    assert center.plan_paid_until == end


@pytest.mark.asyncio
async def test_invite_facilitator_allowed_on_pro(monkeypatch):
    """Pro can invite facilitators (cap 3)."""
    session = AsyncMock()
    session.flush = AsyncMock()
    center = _center(plan="PRO")
    added = {}

    async def fake_add(_s, row, flush=False):
        added["invite"] = row
        return row

    monkeypatch.setattr(
        "app.services.centers.facilitators.load_owned_center",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr("app.services.centers.facilitators.hit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.centers.facilitators.memberships_repository.active_facilitator_count",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invites_repository.open_facilitator_invite_count",
        AsyncMock(side_effect=[0, 1]),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.unique_invite_code",
        AsyncMock(return_value="NORCODE1"),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invite_token", lambda: "tok-n"
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invites_repository.add",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.settings",
        SimpleNamespace(frontend_url="https://app.test"),
    )
    result = await InviteFacilitatorService(
        session,
        _tutor(),
        "center-1",
        SimpleNamespace(email="fac@example.com", expiresInDays=7),
    ).process()
    assert result["invite"]["kind"] == "FACILITATOR"
    assert result["seats"]["maxFacilitators"] == CENTER_PRO_MAX_FACILITATORS
    assert added["invite"].kind == "FACILITATOR"


@pytest.mark.asyncio
async def test_invite_facilitator_blocked_on_free(monkeypatch):
    session = AsyncMock()
    center = _center(plan="FREE", plan_paid_until=None)
    monkeypatch.setattr(
        "app.services.centers.facilitators.load_owned_center",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr("app.services.centers.facilitators.hit", lambda *_a, **_k: None)
    with pytest.raises(ApiError) as exc:
        await InviteFacilitatorService(
            session,
            _tutor(),
            "center-1",
            SimpleNamespace(email=None, expiresInDays=14),
        ).process()
    assert exc.value.payload["code"] == "CENTER_TEAM_REQUIRED"
    assert exc.value.payload["upgradePlan"] == "PRO"


@pytest.mark.asyncio
async def test_invite_facilitator_cap_at_team_five(monkeypatch):
    session = AsyncMock()
    center = _center(plan="TEAM")
    monkeypatch.setattr(
        "app.services.centers.facilitators.load_owned_center",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr("app.services.centers.facilitators.hit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.centers.facilitators.memberships_repository.active_facilitator_count",
        AsyncMock(return_value=CENTER_TEAM_MAX_FACILITATORS),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invites_repository.open_facilitator_invite_count",
        AsyncMock(return_value=0),
    )
    with pytest.raises(ApiError) as exc:
        await InviteFacilitatorService(
            session,
            _tutor(),
            "center-1",
            SimpleNamespace(email=None, expiresInDays=14),
        ).process()
    assert exc.value.payload["code"] == "CENTER_FACILITATOR_CAP"
    assert exc.value.payload["maxFacilitators"] == CENTER_TEAM_MAX_FACILITATORS
    assert exc.value.payload["maxStaff"] == 1 + CENTER_TEAM_MAX_FACILITATORS


@pytest.mark.asyncio
async def test_invite_facilitator_succeeds_on_team(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    center = _center(plan="TEAM")
    added = {}

    async def fake_add(_s, row, flush=False):
        added["invite"] = row
        return row

    monkeypatch.setattr(
        "app.services.centers.facilitators.load_owned_center",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr("app.services.centers.facilitators.hit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.centers.facilitators.memberships_repository.active_facilitator_count",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invites_repository.open_facilitator_invite_count",
        AsyncMock(side_effect=[0, 1]),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.unique_invite_code",
        AsyncMock(return_value="FACCODE1"),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invite_token", lambda: "tok-1"
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invites_repository.add",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.settings",
        SimpleNamespace(frontend_url="https://app.test"),
    )
    result = await InviteFacilitatorService(
        session,
        _tutor(),
        "center-1",
        SimpleNamespace(email="fac@example.com", expiresInDays=7),
    ).process()
    assert result["invite"]["kind"] == "FACILITATOR"
    assert result["invite"]["code"] == "FACCODE1"
    assert added["invite"].kind == "FACILITATOR"
    assert added["invite"].use_limit == 1
    assert result["seats"]["maxFacilitators"] == CENTER_TEAM_MAX_FACILITATORS


@pytest.mark.asyncio
async def test_facilitator_cannot_invite_facilitators(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.facilitators.load_owned_center",
        AsyncMock(side_effect=ApiError(404, "Center not found")),
    )
    monkeypatch.setattr("app.services.centers.facilitators.hit", lambda *_a, **_k: None)
    with pytest.raises(ApiError) as exc:
        await InviteFacilitatorService(
            session,
            _tutor("fac-1"),
            "center-1",
            SimpleNamespace(email=None, expiresInDays=14),
        ).process()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_facilitator_cannot_billing(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.billing.centers_repository.owned_by",
        AsyncMock(return_value=None),
    )
    with pytest.raises(ApiError) as exc:
        await GetCenterBillingService(session, "fac-1", "center-1").process()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_facilitator_cannot_archive(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(
        "app.services.centers.facilitators.load_owned_center",
        AsyncMock(side_effect=ApiError(404, "Center not found")),
    )
    with pytest.raises(ApiError) as exc:
        await ArchiveCenterService(session, "fac-1", "center-1").process()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_accept_facilitator_into_tutor_identity(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    center = _center(plan="TEAM")
    invite = _invite()
    fac = _tutor("fac-1", email="fac@example.com")
    owner = _tutor("tutor-1")
    added = {}

    async def fake_add(_s, row, flush=False):
        added["membership"] = row
        return row

    monkeypatch.setattr("app.services.centers.facilitators.hit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.centers.facilitators.invites_repository.by_code_or_token",
        AsyncMock(return_value=invite),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.centers_repository.by_id",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.users_repository.by_id",
        AsyncMock(return_value=owner),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.memberships_repository.by_center_user",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.memberships_repository.active_facilitator_count",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.invites_repository.open_facilitator_invite_count",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.memberships_repository.add",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.notify_user",
        AsyncMock(),
    )
    result = await AcceptFacilitatorInviteService(session, fac, "TEAMCODE").process()
    assert result["alreadyMember"] is False
    assert result["membership"]["kind"] == "FACILITATOR"
    assert result["center"]["staffRole"] == "facilitator"
    assert added["membership"].kind == "FACILITATOR"
    assert added["membership"].status == "ACTIVE"
    assert invite.use_count == 1


@pytest.mark.asyncio
async def test_owner_can_revoke_facilitator(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    center = _center(plan="TEAM")
    membership = _membership()
    monkeypatch.setattr(
        "app.services.centers.facilitators.load_owned_center",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.memberships_repository.by_id",
        AsyncMock(return_value=membership),
    )
    monkeypatch.setattr(
        "app.services.centers.facilitators.notify_user",
        AsyncMock(),
    )
    result = await RevokeFacilitatorService(
        session, "tutor-1", "center-1", membership_id="mem-1"
    ).process()
    assert result["ok"] is True
    assert membership.status == "REMOVED"
    assert membership.left_at is not None


@pytest.mark.asyncio
async def test_staff_access_allows_facilitator(monkeypatch):
    session = AsyncMock()
    center = _center(plan="TEAM")
    membership = _membership(user_id="fac-1")
    owner = _tutor("tutor-1", is_active=True, suspended_at=None)
    monkeypatch.setattr(
        "app.services.centers.access.centers_repository.by_id",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.services.centers.access.memberships_repository.by_center_user",
        AsyncMock(return_value=membership),
    )
    monkeypatch.setattr(
        "app.services.centers.access.users_repository.by_id",
        AsyncMock(return_value=owner),
    )
    loaded, mem = await load_staff_center(session, "center-1", "fac-1")
    assert loaded.id == "center-1"
    assert mem.kind == "FACILITATOR"


@pytest.mark.asyncio
async def test_owner_suspend_blocks_facilitator_access(monkeypatch):
    session = AsyncMock()
    center = _center(plan="TEAM")
    membership = _membership(user_id="fac-1")
    owner = _tutor("tutor-1", is_active=False, suspended_at=utcnow())
    monkeypatch.setattr(
        "app.services.centers.access.centers_repository.by_id",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.services.centers.access.memberships_repository.by_center_user",
        AsyncMock(return_value=membership),
    )
    monkeypatch.setattr(
        "app.services.centers.access.users_repository.by_id",
        AsyncMock(return_value=owner),
    )
    with pytest.raises(ApiError) as exc:
        await load_staff_center(session, "center-1", "fac-1")
    assert exc.value.status_code == 403
    assert exc.value.payload["code"] == "CENTER_OWNER_SUSPENDED"


@pytest.mark.asyncio
async def test_suspend_owner_notifies_facilitators(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    owner = _tutor("tutor-1")
    center = _center()
    fac_mem = _membership(user_id="fac-1")
    notified = []

    monkeypatch.setattr(
        "app.services.centers.admin.users_repository.by_id",
        AsyncMock(return_value=owner),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.centers_repository.for_owner",
        AsyncMock(return_value=[center]),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.assignments_repository.open_for_centers",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.push_subscriptions_repository.delete_where",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.memberships_repository.active_facilitators_for_centers",
        AsyncMock(return_value=[fac_mem]),
    )

    async def fake_notify(_session, **kwargs):
        notified.append(kwargs)

    monkeypatch.setattr(
        "app.services.centers.admin.notify_user",
        fake_notify,
    )
    monkeypatch.setattr(
        "app.services.centers.admin.RecordAuditService",
        lambda *a, **k: SimpleNamespace(process=AsyncMock()),
    )
    monkeypatch.setattr("app.services.centers.admin.capture", lambda *_a, **_k: None)

    result = await SetTutorStatusService(
        session,
        "admin-1",
        "tutor-1",
        is_active=False,
        reason="Abuse reports confirmed",
    ).process()
    assert result["facilitatorsBlocked"] == 1
    assert notified[0]["user_id"] == "fac-1"
    assert notified[0]["type"] == "CENTER_OWNER_SUSPENDED"


@pytest.mark.asyncio
async def test_audit_actor_is_facilitator_id(monkeypatch):
    from app.services.centers import audit as audit_mod

    session = AsyncMock()
    added = {}

    async def fake_add(_s, row, flush=False):
        added["row"] = row
        return row

    monkeypatch.setattr(
        "app.services.centers.audit.tutor_access_audits_repository.add",
        fake_add,
    )
    row = await audit_mod.record_tutor_access(
        session,
        actor_id="fac-1",
        center_id="center-1",
        action="view_student_results",
        resource="assignment:asg-1",
        student_id="student-1",
    )
    assert added["row"].actor_id == "fac-1"
    assert row.actor_id == "fac-1"


@pytest.mark.asyncio
async def test_billing_payload_includes_team(monkeypatch):
    session = AsyncMock()
    center = _center(plan="PRO")
    monkeypatch.setattr(
        "app.services.centers.billing.centers_repository.owned_by",
        AsyncMock(return_value=center),
    )
    monkeypatch.setattr(
        "app.services.centers.billing.memberships_repository.active_student_count",
        AsyncMock(return_value=3),
    )
    monkeypatch.setattr(
        "app.services.centers.billing.memberships_repository.active_facilitator_count",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.services.centers.billing.ensure_center_catalog",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.centers.billing.plan_by_code",
        AsyncMock(
            side_effect=lambda _s, code: SimpleNamespace(
                amount_kobo=(
                    CENTER_TEAM_YEARLY_KOBO if code == "center_team" else 1_000_000
                ),
                code=code,
            )
        ),
    )
    result = await GetCenterBillingService(session, "tutor-1", "center-1").process()
    assert result["billing"]["team"]["code"] == "center_team"
    assert result["billing"]["team"]["amountKobo"] == CENTER_TEAM_YEARLY_KOBO
    assert result["billing"]["team"]["purchasable"] is True
    assert result["billing"]["pro"]["purchasable"] is False
    assert result["billing"]["maxStaff"] == 1 + CENTER_PRO_MAX_FACILITATORS
    assert result["billing"]["pro"]["maxFacilitators"] == CENTER_PRO_MAX_FACILITATORS
    assert result["billing"]["team"]["maxFacilitators"] == CENTER_TEAM_MAX_FACILITATORS


@pytest.mark.asyncio
async def test_activate_team_plan_helper():
    session = AsyncMock()
    session.flush = AsyncMock()
    center = _center(plan="FREE", plan_paid_until=None)
    end = utcnow() + timedelta(days=365)
    await activate_center_plan(session, center, "TEAM", end)
    assert center.plan == "TEAM"
    assert center.plan_paid_until == end
