"""Team plan facilitators: invite, accept, revoke, archive (T-6–T-10, D2)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.rate_limit import hit
from app.core.timeutil import as_utc, utcnow
from app.database.models import Center, CenterInvite, CenterMembership, User
from app.database.repositories.centers import (
    assignment_targets_repository,
    assignments_repository,
    centers_repository,
    invites_repository,
    memberships_repository,
)
from app.database.repositories.identity import users_repository
from app.domain import (
    center_allows_facilitators,
    center_max_facilitators,
    center_max_staff,
)
from app.services.centers.access import load_owned_center, load_staff_center, staff_role
from app.services.centers.codes import invite_token, unique_invite_code
from app.services.centers.notify import notify_assignment_closed, notify_user

FACILITATOR_INVITE_LIMIT = 10
FACILITATOR_INVITE_WINDOW = 3600
FACILITATOR_ACCEPT_LIMIT = 20
FACILITATOR_ACCEPT_WINDOW = 3600


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _display_name(user: User | None, *, fallback: str = "Tutor") -> str:
    if user is None:
        return fallback
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(part for part in parts if part).strip()
    return name or user.email or fallback


def _center_out(
    center: Center, *, staff_role_name: str | None = None
) -> dict[str, Any]:
    payload = {
        "id": center.id,
        "name": center.name,
        "state": center.state,
        "lga": center.lga,
        "subjects": center.subjects or [],
        "levels": center.levels or [],
        "plan": center.plan,
        "planPaidUntil": _iso(getattr(center, "plan_paid_until", None)),
        "ownerId": center.owner_id,
        "archivedAt": _iso(center.archived_at),
        "createdAt": _iso(center.created_at),
        "updatedAt": _iso(center.updated_at),
    }
    if staff_role_name is not None:
        payload["staffRole"] = staff_role_name
    return payload


def _facilitator_invite_link(code: str) -> str:
    base = settings.frontend_url.rstrip("/")
    return f"{base}/center/join-team/{code}"


def _invite_out(invite: CenterInvite) -> dict[str, Any]:
    return {
        "id": invite.id,
        "centerId": invite.center_id,
        "kind": invite.kind,
        "code": invite.code,
        "token": invite.token,
        "link": _facilitator_invite_link(invite.code),
        "boundEmail": invite.bound_email,
        "useLimit": invite.use_limit,
        "useCount": invite.use_count,
        "expiresAt": _iso(invite.expires_at),
        "revokedAt": _iso(invite.revoked_at),
        "createdAt": _iso(invite.created_at),
    }


def _membership_out(
    membership: CenterMembership, user: User | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": membership.id,
        "centerId": membership.center_id,
        "userId": membership.user_id,
        "kind": membership.kind,
        "status": membership.status.lower(),
        "joinedAt": _iso(membership.joined_at),
        "leftAt": _iso(membership.left_at),
        "createdAt": _iso(membership.created_at),
    }
    if user is not None:
        payload["firstName"] = user.first_name
        payload["lastName"] = user.last_name
        payload["email"] = getattr(user, "email", None)
        payload["image"] = getattr(user, "image", None)
    return payload


def team_plan_required(center: Center) -> None:
    """Pro or Team required to invite facilitators; Free (solo) is blocked."""
    if not center_allows_facilitators(center.plan):
        raise ApiError(
            403,
            "Facilitators require Pro or Team. Upgrade to invite facilitators.",
            code="CENTER_TEAM_REQUIRED",
            upgradePlan="PRO",
            plan=center.plan,
        )


async def facilitator_seats_used(session: AsyncSession, center_id: str) -> int:
    active = await memberships_repository.active_facilitator_count(session, center_id)
    open_invites = await invites_repository.open_facilitator_invite_count(
        session, center_id
    )
    return active + open_invites


def _seat_payload(center: Center, used: int) -> dict[str, int]:
    max_fac = center_max_facilitators(center.plan)
    return {
        "maxFacilitators": max_fac,
        "maxStaff": center_max_staff(center.plan),
        "used": used,
    }


async def assert_facilitator_seat_available(
    session: AsyncSession, center: Center
) -> None:
    team_plan_required(center)
    used = await facilitator_seats_used(session, center.id)
    max_fac = center_max_facilitators(center.plan)
    if used >= max_fac:
        raise ApiError(
            403,
            f"This center already has the maximum of {max_fac} facilitators.",
            code="CENTER_FACILITATOR_CAP",
            maxFacilitators=max_fac,
            maxStaff=center_max_staff(center.plan),
            seatsUsed=used,
        )


class InviteFacilitatorService:
    """Owner-only. Team plan. Holds a seat until accept or revoke/expiry."""

    def __init__(
        self,
        session: AsyncSession,
        tutor: Any,
        center_id: str,
        body: Any,
    ) -> None:
        self.session = session
        self.tutor = tutor
        self.tutor_id = tutor.id
        self.center_id = center_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        hit(
            f"center-facilitator-invite:{self.tutor_id}",
            FACILITATOR_INVITE_LIMIT,
            FACILITATOR_INVITE_WINDOW,
        )
        if self.tutor.email_verified is None:
            raise ApiError(
                403,
                "Verify your email before inviting facilitators.",
                code="TUTOR_EMAIL_UNVERIFIED",
            )
        center = await load_owned_center(self.session, self.center_id, self.tutor_id)
        team_plan_required(center)
        await assert_facilitator_seat_available(self.session, center)

        bound_email = (getattr(self.body, "email", None) or "").strip().lower() or None
        expires_at = utcnow() + timedelta(
            days=int(getattr(self.body, "expiresInDays", None) or 14)
        )
        invite = CenterInvite(
            id=cuid(),
            center_id=center.id,
            created_by_id=self.tutor_id,
            kind="FACILITATOR",
            code=await unique_invite_code(self.session),
            token=invite_token(),
            bound_email=bound_email,
            use_limit=1,
            use_count=0,
            expires_at=expires_at,
        )
        await invites_repository.add(self.session, invite, flush=True)
        used = await facilitator_seats_used(self.session, center.id)
        return {
            "invite": _invite_out(invite),
            "seats": _seat_payload(center, used),
        }


class ListFacilitatorsService:
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        # Owner manages team; facilitators may view roster of co-staff.
        center, membership = await load_staff_center(
            self.session, self.center_id, self.tutor_id
        )
        rows = await memberships_repository.facilitators_for_center(
            self.session, center.id
        )
        open_invites = await invites_repository.for_center(
            self.session, center.id, active_only=True, kind="FACILITATOR"
        )
        open_invites = [
            inv
            for inv in open_invites
            if (inv.use_limit is None or inv.use_count < inv.use_limit)
            and (inv.expires_at is None or as_utc(inv.expires_at) > utcnow())
        ]
        return {
            "centerId": center.id,
            "plan": center.plan,
            "staffRole": staff_role(membership),
            "canManage": membership.kind == "OWNER",
            "facilitators": [
                _membership_out(row, user)
                for row, user in rows
                if row.status == "ACTIVE"
            ],
            "pendingInvites": [_invite_out(inv) for inv in open_invites],
            "seats": _seat_payload(
                center, await facilitator_seats_used(self.session, center.id)
            ),
        }


class RevokeFacilitatorService:
    """Owner-only: remove an active facilitator or revoke a pending invite."""

    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        *,
        membership_id: str | None = None,
        invite_id: str | None = None,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.membership_id = membership_id
        self.invite_id = invite_id

    async def process(self) -> dict[str, Any]:
        center = await load_owned_center(self.session, self.center_id, self.tutor_id)
        if self.membership_id:
            membership = await memberships_repository.by_id(
                self.session, self.membership_id
            )
            if (
                membership is None
                or membership.center_id != center.id
                or membership.kind != "FACILITATOR"
            ):
                raise ApiError(404, "Facilitator not found")
            if membership.status == "REMOVED":
                return {"ok": True, "alreadyRevoked": True}
            membership.status = "REMOVED"
            membership.left_at = utcnow()
            await self.session.flush()
            await notify_user(
                self.session,
                user_id=membership.user_id,
                type="CENTER_FACILITATOR_REVOKED",
                title="Removed from a center team",
                body=f"You were removed as a facilitator from {center.name}.",
                url="/center",
                source_key=f"center-facilitator-revoked:{membership.id}",
            )
            return {"ok": True, "membership": _membership_out(membership)}

        if self.invite_id:
            invite = await invites_repository.by_id(self.session, self.invite_id)
            if (
                invite is None
                or invite.center_id != center.id
                or invite.kind != "FACILITATOR"
            ):
                raise ApiError(404, "Facilitator invite not found")
            if invite.revoked_at is None:
                invite.revoked_at = utcnow()
                await self.session.flush()
            return {"ok": True, "invite": _invite_out(invite)}

        raise ApiError(400, "membershipId or inviteId is required")


class PreviewFacilitatorInviteService:
    def __init__(self, session: AsyncSession, code: str) -> None:
        self.session = session
        self.code = code.strip()

    async def process(self) -> dict[str, Any]:
        invite, center, owner = await _load_open_facilitator_invite(
            self.session, self.code
        )
        return {
            "invite": {
                "code": invite.code,
                "kind": invite.kind,
                "expiresAt": _iso(invite.expires_at),
                "boundEmail": invite.bound_email,
            },
            "center": {
                "id": center.id,
                "name": center.name,
                "state": center.state,
                "plan": center.plan,
            },
            "owner": {
                "firstName": owner.first_name if owner else None,
                "lastName": owner.last_name if owner else None,
                "state": owner.state if owner else None,
            },
            "permissions": {
                "can": [
                    "Manage roster and groups",
                    "Create and publish assignments",
                    "View assignment-scoped results",
                    "Send recommendations",
                ],
                "cannot": [
                    "Change billing or plan",
                    "Invite or revoke facilitators",
                    "Archive the center",
                ],
            },
        }


class AcceptFacilitatorInviteService:
    """Accept into TUTOR identity for the center (T-8)."""

    def __init__(
        self,
        session: AsyncSession,
        tutor: Any,
        code: str,
    ) -> None:
        self.session = session
        self.tutor = tutor
        self.tutor_id = tutor.id
        self.code = code.strip()

    async def process(self) -> dict[str, Any]:
        hit(
            f"center-facilitator-accept:{self.tutor_id}",
            FACILITATOR_ACCEPT_LIMIT,
            FACILITATOR_ACCEPT_WINDOW,
        )
        if self.tutor.role != "TUTOR":
            raise ApiError(403, "Only tutors can accept facilitator invites")
        invite, center, owner = await _load_open_facilitator_invite(
            self.session, self.code
        )
        team_plan_required(center)

        if (
            invite.bound_email
            and (self.tutor.email or "").lower() != invite.bound_email
        ):
            raise ApiError(
                403,
                "This invite is bound to a different email address.",
                code="INVITE_EMAIL_MISMATCH",
            )
        if self.tutor_id == center.owner_id:
            raise ApiError(400, "You already own this center")

        membership = await memberships_repository.by_center_user(
            self.session, center.id, self.tutor_id
        )
        if membership is not None and membership.status == "ACTIVE":
            if membership.kind == "FACILITATOR":
                return {
                    "membership": _membership_out(membership, self.tutor),
                    "center": _center_out(center, staff_role_name="facilitator"),
                    "alreadyMember": True,
                }
            raise ApiError(400, "You already have a different role on this center")

        # Seat check excludes this invite's remaining use so accept doesn't self-block.
        active = await memberships_repository.active_facilitator_count(
            self.session, center.id
        )
        open_others = await invites_repository.open_facilitator_invite_count(
            self.session, center.id
        )
        # Current invite still counts in open_others; subtract one for this accept.
        held = active + max(0, open_others - 1)
        max_fac = center_max_facilitators(center.plan)
        if held >= max_fac:
            raise ApiError(
                403,
                f"This center already has the maximum of {max_fac} facilitators.",
                code="CENTER_FACILITATOR_CAP",
                maxFacilitators=max_fac,
                maxStaff=center_max_staff(center.plan),
            )

        now = utcnow()
        if membership is None:
            membership = CenterMembership(
                id=cuid(),
                center_id=center.id,
                user_id=self.tutor_id,
                kind="FACILITATOR",
                status="ACTIVE",
                joined_at=now,
            )
            await memberships_repository.add(self.session, membership, flush=True)
        else:
            membership.kind = "FACILITATOR"
            membership.status = "ACTIVE"
            membership.joined_at = now
            membership.left_at = None
            await self.session.flush()

        invite.use_count = int(invite.use_count or 0) + 1
        await self.session.flush()

        await notify_user(
            self.session,
            user_id=center.owner_id,
            type="CENTER_FACILITATOR_ACCEPTED",
            title="Facilitator joined your center",
            body=(
                f"{_display_name(self.tutor)} accepted your facilitator "
                f"invite to {center.name}."
            ),
            url=f"/center/team?centerId={center.id}",
            source_key=f"center-facilitator-accepted:{membership.id}",
        )
        return {
            "membership": _membership_out(membership, self.tutor),
            "center": _center_out(center, staff_role_name="facilitator"),
            "owner": {
                "firstName": owner.first_name if owner else None,
                "lastName": owner.last_name if owner else None,
            },
            "alreadyMember": False,
        }


class ArchiveCenterService:
    """T-6: owner-only archive; closes open assignments."""

    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        center = await load_owned_center(self.session, self.center_id, self.tutor_id)
        if center.archived_at is not None:
            return {"center": _center_out(center), "alreadyArchived": True}
        now = utcnow()
        center.archived_at = now
        open_rows = await assignments_repository.open_for_centers(
            self.session, [center.id]
        )
        for assignment in open_rows:
            assignment.status = "CLOSED"
            assignment.closed_at = now
            targets = await assignment_targets_repository.for_assignment(
                self.session, assignment.id
            )
            from app.services.centers.assignments import resolve_target_user_ids

            for student_id in await resolve_target_user_ids(
                self.session, center.id, targets
            ):
                await notify_assignment_closed(
                    self.session,
                    student_id=student_id,
                    center_name=center.name,
                    assignment_id=assignment.id,
                    title=assignment.title,
                )
        # Detach active facilitators
        facilitators = await memberships_repository.active_facilitators_for_centers(
            self.session, [center.id]
        )
        for membership in facilitators:
            membership.status = "REMOVED"
            membership.left_at = now
        await self.session.flush()
        return {
            "center": _center_out(center),
            "closedAssignments": len(open_rows),
            "alreadyArchived": False,
        }


async def _load_open_facilitator_invite(
    session: AsyncSession, code: str
) -> tuple[CenterInvite, Center, User | None]:
    if not code:
        raise ApiError(404, "Invite not found")
    invite = await invites_repository.by_code_or_token(session, code)
    if invite is None or invite.kind != "FACILITATOR":
        raise ApiError(404, "Invite not found")
    if invite.revoked_at is not None:
        raise ApiError(410, "This invite has been revoked", code="INVITE_REVOKED")
    if invite.expires_at is not None and as_utc(invite.expires_at) <= utcnow():
        raise ApiError(410, "This invite has expired", code="INVITE_EXPIRED")
    if invite.use_limit is not None and invite.use_count >= invite.use_limit:
        raise ApiError(
            410, "This invite has reached its use limit", code="INVITE_EXHAUSTED"
        )
    center = await centers_repository.by_id(session, invite.center_id)
    if center is None or center.archived_at is not None:
        raise ApiError(404, "Center not found")
    owner = await users_repository.by_id(session, center.owner_id)
    return invite, center, owner
