"""M4: Pro yearly checkout unlocks Free cap; admin suspend closes assignments."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.domain import CENTER_FREE_ACTIVE_STUDENT_LIMIT, CENTER_PRO_YEARLY_KOBO
from app.services.centers.admin import SetTutorStatusService
from app.services.centers.billing import (
    ApplyCenterChargeSuccessService,
    activate_center_plan,
)
from app.services.centers.service import AcceptInviteService, free_roster_blocks_accept


def _tutor(**extra):
    base = dict(
        id="tutor-1",
        email="tutor@example.com",
        role="TUTOR",
        is_active=True,
        suspended_at=None,
        suspended_reason=None,
        sessions_valid_from=None,
        tutor_verified_at=None,
        email_verified=utcnow(),
        first_name="Tolu",
        last_name="Ade",
        state="Lagos",
        phone=None,
        created_at=utcnow(),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _center(**extra):
    base = dict(
        id="center-1",
        owner_id="tutor-1",
        name="Lagos Prep",
        plan="FREE",
        plan_paid_until=None,
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
    )
    base.update(extra)
    return SimpleNamespace(**base)


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


def test_free_roster_blocks_fifth_seat():
    assert free_roster_blocks_accept(CENTER_FREE_ACTIVE_STUDENT_LIMIT - 1) is True
    assert free_roster_blocks_accept(CENTER_FREE_ACTIVE_STUDENT_LIMIT - 2) is False


@pytest.mark.asyncio
async def test_checkout_service_allows_yearly_for_center_pro(monkeypatch):
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
    center = _center(plan="FREE")
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
                amount_kobo=CENTER_PRO_YEARLY_KOBO,
                code="center_pro",
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
        "app.services.billing.service.new_reference", lambda: "ctr_ref_1"
    )
    monkeypatch.setattr("app.services.billing.service.capture", lambda *_a, **_k: None)

    # Avoid Paystack initialize — stop after store_pending via patched process pieces.
    svc = CheckoutService(session, user, "PRO", "YEARLY", center_id="center-1")
    assert svc.period == "YEARLY"
    assert svc.plan_code == "center_pro"
    await svc._validate()
    await svc._store_pending("ctr_ref_1", CENTER_PRO_YEARLY_KOBO)
    assert added["row"].plan == "PRO"
    assert added["row"].period == "YEARLY"
    assert added["row"].amount_kobo == CENTER_PRO_YEARLY_KOBO
    assert added["row"].status == "PENDING"


@pytest.mark.asyncio
async def test_checkout_rejects_student_yearly_still_forced_monthly():
    from app.services.billing.service import CheckoutService

    user = SimpleNamespace(id="u1", email="a@b.com")
    session = AsyncMock()
    svc = CheckoutService(session, user, "PREMIUM", "YEARLY")
    assert svc.period == "MONTHLY"
    assert svc.plan_code == "premium"


@pytest.mark.asyncio
async def test_pro_activation_unlocks_fifth_accept(monkeypatch):
    """Paid Pro lifts Free cap — fifth student can join after activation."""
    session = AsyncMock()
    session.flush = AsyncMock()
    center = _center(plan="FREE")
    end = utcnow() + timedelta(days=365)

    await activate_center_plan(session, center, "PRO", end)
    assert center.plan == "PRO"
    assert center.plan_paid_until == end

    invite = _invite()
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
async def test_apply_center_charge_success_sets_pro(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    now = utcnow()
    end = now + timedelta(days=365)
    row = SimpleNamespace(
        id="cko-1",
        center_id="center-1",
        user_id="tutor-1",
        plan="PRO",
        period="YEARLY",
        status="PENDING",
        amount_kobo=CENTER_PRO_YEARLY_KOBO,
        currency="NGN",
        paid_at=None,
        starts_at=None,
        ends_at=None,
        channel=None,
    )
    center = _center(plan="FREE")
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
        "ref-1",
        {"status": "success", "amount": CENTER_PRO_YEARLY_KOBO, "currency": "NGN"},
    ).process()
    assert outcome == "activate"
    assert center.plan == "PRO"
    assert center.plan_paid_until == end
    assert row.status == "ACTIVE"


@pytest.mark.asyncio
async def test_suspend_tutor_closes_open_assignments(monkeypatch):
    session = AsyncMock()
    session.flush = AsyncMock()
    tutor = _tutor()
    center = _center()
    open_a = SimpleNamespace(
        id="asg-1",
        center_id="center-1",
        title="Quiz 1",
        status="OPEN",
        closed_at=None,
    )
    scheduled = SimpleNamespace(
        id="asg-2",
        center_id="center-1",
        title="Quiz 2",
        status="SCHEDULED",
        closed_at=None,
    )
    notified = []

    monkeypatch.setattr(
        "app.services.centers.admin.users_repository.by_id",
        AsyncMock(return_value=tutor),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.centers_repository.for_owner",
        AsyncMock(return_value=[center]),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.assignments_repository.open_for_centers",
        AsyncMock(return_value=[open_a, scheduled]),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.assignment_targets_repository.for_assignment",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.centers.assignments.resolve_target_user_ids",
        AsyncMock(return_value=["student-1"]),
    )

    async def fake_notify(_session, **kwargs):
        notified.append(kwargs)

    monkeypatch.setattr(
        "app.services.centers.admin.notify_assignment_closed",
        fake_notify,
    )
    monkeypatch.setattr(
        "app.services.centers.admin.push_subscriptions_repository.delete_where",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.RecordAuditService",
        lambda *a, **k: SimpleNamespace(process=AsyncMock()),
    )
    monkeypatch.setattr("app.services.centers.admin.capture", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.centers.admin.memberships_repository.active_facilitators_for_centers",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.notify_user",
        AsyncMock(),
    )

    result = await SetTutorStatusService(
        session,
        "admin-1",
        "tutor-1",
        is_active=False,
        reason="Abuse reports confirmed",
    ).process()

    assert result["ok"] is True
    assert result["closedAssignments"] == 2
    assert result["facilitatorsBlocked"] == 0
    assert open_a.status == "CLOSED"
    assert scheduled.status == "CLOSED"
    assert open_a.closed_at is not None
    assert tutor.is_active is False
    assert tutor.suspended_reason == "Abuse reports confirmed"
    assert tutor.sessions_valid_from is not None
    assert len(notified) == 2


@pytest.mark.asyncio
async def test_reactivate_tutor_does_not_require_reason(monkeypatch):
    session = AsyncMock()
    tutor = _tutor(is_active=False, suspended_reason="old")
    monkeypatch.setattr(
        "app.services.centers.admin.users_repository.by_id",
        AsyncMock(return_value=tutor),
    )
    monkeypatch.setattr(
        "app.services.centers.admin.RecordAuditService",
        lambda *a, **k: SimpleNamespace(process=AsyncMock()),
    )
    monkeypatch.setattr("app.services.centers.admin.capture", lambda *_a, **_k: None)
    result = await SetTutorStatusService(
        session, "admin-1", "tutor-1", is_active=True
    ).process()
    assert result["ok"] is True
    assert result["closedAssignments"] == 0
    assert tutor.is_active is True
    assert tutor.suspended_at is None
    assert tutor.suspended_reason is None


@pytest.mark.asyncio
async def test_suspend_requires_reason():
    session = AsyncMock()
    with (
        patch(
            "app.services.centers.admin.users_repository.by_id",
            AsyncMock(return_value=_tutor()),
        ),
        pytest.raises(ApiError) as exc,
    ):
        await SetTutorStatusService(
            session, "admin-1", "tutor-1", is_active=False, reason="no"
        ).process()
    assert exc.value.status_code == 400
