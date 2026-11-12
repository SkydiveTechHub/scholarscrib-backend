"""Tutor Centers: create/list, invites, roster, groups, student join/leave."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.rate_limit import hit
from app.core.timeutil import as_utc, utcnow
from app.database.models import (
    Center,
    CenterGroup,
    CenterGroupMember,
    CenterInvite,
    CenterMembership,
    User,
)
from app.database.repositories.centers import (
    centers_repository,
    group_members_repository,
    groups_repository,
    invites_repository,
    memberships_repository,
)
from app.database.repositories.identity import users_repository
from app.domain import CENTER_FREE_ACTIVE_STUDENT_LIMIT, CENTER_LEVELS
from app.services.centers.access import load_owned_center, load_staff_center, staff_role
from app.services.centers.codes import invite_token, unique_invite_code
from app.services.centers.notify import notify_invite_accepted, notify_student_removed

CONSENT_VERSION = "v1"
INVITE_CREATE_LIMIT = 20
INVITE_CREATE_WINDOW = 3600
INVITE_ACCEPT_LIMIT = 30
INVITE_ACCEPT_WINDOW = 3600


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _display_name(user: User | None, *, fallback: str = "Student") -> str:
    if user is None:
        return fallback
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(part for part in parts if part).strip()
    return name or user.email or fallback


def _center_out(center: Center) -> dict[str, Any]:
    return {
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


def _invite_link(code: str) -> str:
    base = settings.frontend_url.rstrip("/")
    return f"{base}/join/{code}"


def _invite_out(invite: CenterInvite) -> dict[str, Any]:
    return {
        "id": invite.id,
        "centerId": invite.center_id,
        "kind": getattr(invite, "kind", None) or "STUDENT",
        "code": invite.code,
        "token": invite.token,
        "link": _invite_link(invite.code),
        "boundEmail": getattr(invite, "bound_email", None),
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
        payload["classLevel"] = user.class_level
        payload["track"] = user.track
        payload["image"] = user.image
    return payload


def _group_out(
    group: CenterGroup, membership_ids: list[str] | None = None
) -> dict[str, Any]:
    return {
        "id": group.id,
        "centerId": group.center_id,
        "name": group.name,
        "membershipIds": membership_ids if membership_ids is not None else [],
        "createdAt": _iso(group.created_at),
        "updatedAt": _iso(group.updated_at),
    }


def free_roster_blocks_accept(active_count: int) -> bool:
    """R-10: Free allows fewer than CENTER_FREE_ACTIVE_STUDENT_LIMIT actives."""
    return active_count + 1 >= CENTER_FREE_ACTIVE_STUDENT_LIMIT


class _StaffCenterMixin:
    """Roster / groups / student invites: owner or facilitator."""

    session: AsyncSession
    tutor_id: str
    center_id: str

    async def _owned_center(self) -> Center:
        """Back-compat name; staff (owner or facilitator) may access."""
        center, _membership = await load_staff_center(
            self.session, self.center_id, self.tutor_id
        )
        return center

    async def _staff_center(self) -> tuple[Center, CenterMembership]:
        return await load_staff_center(self.session, self.center_id, self.tutor_id)

    async def _require_owner_center(self) -> Center:
        return await load_owned_center(self.session, self.center_id, self.tutor_id)


# Alias kept for existing imports/tests.
_OwnedCenterMixin = _StaffCenterMixin


class CreateCenterService:
    def __init__(self, session: AsyncSession, tutor_id: str, body: Any) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        name = (self.body.name or "").strip()
        if not 2 <= len(name) <= 80:
            raise ApiError(400, "Center name must be 2–80 characters")
        subjects = list(self.body.subjects or [])
        levels = list(self.body.levels or [])
        for level in levels:
            if level not in CENTER_LEVELS:
                raise ApiError(400, f"Invalid center level: {level}")
        center = Center(
            id=cuid(),
            owner_id=self.tutor_id,
            name=name,
            state=self.body.state,
            lga=(self.body.lga or None),
            subjects=subjects,
            levels=levels,
            plan="FREE",
        )
        await centers_repository.add(self.session, center, flush=True)
        owner = CenterMembership(
            id=cuid(),
            center_id=center.id,
            user_id=self.tutor_id,
            kind="OWNER",
            status="ACTIVE",
            joined_at=utcnow(),
        )
        await memberships_repository.add(self.session, owner, flush=True)
        from app.services.centers.tokens import ensure_token_pool

        await ensure_token_pool(self.session, center.id, actor_id=self.tutor_id)
        return {"center": _center_out(center)}


class ListCentersService:
    def __init__(self, session: AsyncSession, tutor_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id

    async def process(self) -> dict[str, Any]:
        rows = await centers_repository.for_staff_user(self.session, self.tutor_id)
        centers = []
        for center, membership in rows:
            payload = _center_out(center)
            payload["staffRole"] = staff_role(membership)
            centers.append(payload)
        return {"centers": centers}


class UpdateCenterService(_StaffCenterMixin):
    """Owner-only profile edit: name, location, subjects, levels."""

    def __init__(
        self, session: AsyncSession, tutor_id: str, center_id: str, body: Any
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        center = await self._require_owner_center()
        if center.archived_at is not None:
            raise ApiError(400, "Archived centers cannot be edited.")

        fields = getattr(self.body, "model_fields_set", None) or set()
        if not fields:
            raise ApiError(400, "No changes provided.")

        if "name" in fields:
            name = (self.body.name or "").strip()
            if not 2 <= len(name) <= 80:
                raise ApiError(400, "Center name must be 2–80 characters")
            center.name = name

        if "state" in fields:
            center.state = self.body.state

        if "lga" in fields:
            lga = (self.body.lga or "").strip() or None
            if lga is not None and len(lga) > 80:
                raise ApiError(400, "LGA must be at most 80 characters")
            center.lga = lga

        if "subjects" in fields:
            subjects = list(self.body.subjects or [])
            if len(subjects) > 40:
                raise ApiError(400, "Too many subjects")
            center.subjects = subjects

        if "levels" in fields:
            levels = list(self.body.levels or [])
            if len(levels) > 10:
                raise ApiError(400, "Too many levels")
            for level in levels:
                if level not in CENTER_LEVELS:
                    raise ApiError(400, f"Invalid center level: {level}")
            center.levels = levels

        # Set in Python so flush does not expire updated_at and force a
        # sync lazy-load (MissingGreenlet under asyncpg).
        center.updated_at = utcnow()
        await self.session.flush()
        payload = _center_out(center)
        payload["staffRole"] = "owner"
        return {"center": payload}


class GetCenterService(_StaffCenterMixin):
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        center, membership = await self._staff_center()
        active = await memberships_repository.active_student_count(
            self.session, center.id
        )
        payload = _center_out(center)
        payload["staffRole"] = staff_role(membership)
        payload["activeStudentCount"] = active
        payload["freeStudentLimit"] = CENTER_FREE_ACTIVE_STUDENT_LIMIT
        return {"center": payload}


class CreateInviteService(_StaffCenterMixin):
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
        hit(f"center-invite:{self.tutor_id}", INVITE_CREATE_LIMIT, INVITE_CREATE_WINDOW)
        if self.tutor.email_verified is None:
            raise ApiError(
                403,
                "Verify your email before inviting students.",
                code="TUTOR_EMAIL_UNVERIFIED",
            )
        center = await self._owned_center()
        bound_email = (getattr(self.body, "email", None) or "").strip().lower() or None
        use_limit = self.body.useLimit
        expires_days = self.body.expiresInDays
        # Email-bound invites are single-use with a 14-day default (R-2).
        if bound_email:
            if use_limit is None:
                use_limit = 1
            if expires_days is None:
                expires_days = 14
        expires_at = None
        if expires_days is not None:
            expires_at = utcnow() + timedelta(days=int(expires_days))
        invite = CenterInvite(
            id=cuid(),
            center_id=center.id,
            created_by_id=self.tutor_id,
            kind="STUDENT",
            code=await unique_invite_code(self.session),
            token=invite_token(),
            bound_email=bound_email,
            use_limit=use_limit,
            use_count=0,
            expires_at=expires_at,
        )
        await invites_repository.add(self.session, invite, flush=True)

        email_sent: bool | None = None
        if bound_email:
            from app.services.email import EmailService

            tutor_name = _display_name(self.tutor, fallback="Your tutor")
            email_sent = await EmailService.send_email(
                bound_email,
                f"You're invited to join {center.name} on ScholarsCrib",
                "center_student_invite.html",
                {
                    "email": bound_email,
                    "center_name": center.name,
                    "tutor_name": tutor_name,
                    "invite_url": _invite_link(invite.code),
                    "invite_code": invite.code,
                    "expires_days": int(expires_days or 14),
                },
            )
        return {"invite": _invite_out(invite), "emailSent": email_sent}


class ListInvitesService(_OwnedCenterMixin):
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        rows = await invites_repository.for_center(
            self.session, self.center_id, kind="STUDENT"
        )
        return {"invites": [_invite_out(row) for row in rows]}


class RevokeInviteService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        invite_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.invite_id = invite_id

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        invite = await self._invite()
        if invite.revoked_at is None:
            invite.revoked_at = utcnow()
            await self.session.flush()
        return {"invite": _invite_out(invite)}

    async def _invite(self) -> CenterInvite:
        invite = await invites_repository.by_id(self.session, self.invite_id)
        if (
            invite is None
            or invite.center_id != self.center_id
            or (getattr(invite, "kind", "STUDENT") or "STUDENT") != "STUDENT"
        ):
            raise ApiError(404, "Invite not found")
        return invite


class RegenerateInviteService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor: Any,
        center_id: str,
        invite_id: str,
    ) -> None:
        self.session = session
        self.tutor = tutor
        self.tutor_id = tutor.id
        self.center_id = center_id
        self.invite_id = invite_id

    async def process(self) -> dict[str, Any]:
        hit(f"center-invite:{self.tutor_id}", INVITE_CREATE_LIMIT, INVITE_CREATE_WINDOW)
        if self.tutor.email_verified is None:
            raise ApiError(
                403,
                "Verify your email before inviting students.",
                code="TUTOR_EMAIL_UNVERIFIED",
            )
        await self._owned_center()
        invite = await invites_repository.by_id(self.session, self.invite_id)
        if (
            invite is None
            or invite.center_id != self.center_id
            or (getattr(invite, "kind", "STUDENT") or "STUDENT") != "STUDENT"
        ):
            raise ApiError(404, "Invite not found")
        invite.revoked_at = utcnow()
        replacement = CenterInvite(
            id=cuid(),
            center_id=invite.center_id,
            created_by_id=self.tutor_id,
            kind="STUDENT",
            code=await unique_invite_code(self.session),
            token=invite_token(),
            bound_email=getattr(invite, "bound_email", None),
            use_limit=invite.use_limit,
            use_count=0,
            expires_at=invite.expires_at,
        )
        await invites_repository.add(self.session, replacement, flush=True)
        return {
            "invite": _invite_out(replacement),
            "revokedInviteId": invite.id,
        }


class RosterService(_OwnedCenterMixin):
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        center = await self._owned_center()
        rows = await memberships_repository.roster_rows(self.session, self.center_id)
        members = [_membership_out(membership, user) for membership, user in rows]
        active_count = sum(
            1 for membership, _user in rows if membership.status == "ACTIVE"
        )
        active_limit = (
            CENTER_FREE_ACTIVE_STUDENT_LIMIT if center.plan == "FREE" else None
        )
        return {
            # FE contract uses `members` (not legacy `roster`).
            "members": members,
            "activeCount": active_count,
            "activeLimit": active_limit,
            "plan": center.plan,
        }


class RemoveStudentService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        membership_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.membership_id = membership_id

    async def process(self) -> dict[str, Any]:
        center = await self._owned_center()
        membership = await memberships_repository.by_id(
            self.session, self.membership_id
        )
        if (
            membership is None
            or membership.center_id != self.center_id
            or membership.kind != "STUDENT"
        ):
            raise ApiError(404, "Student membership not found")
        if membership.status == "REMOVED":
            return {"membership": _membership_out(membership)}
        membership.status = "REMOVED"
        membership.left_at = utcnow()
        await self.session.flush()
        student = await users_repository.by_id(self.session, membership.user_id)
        await notify_student_removed(
            self.session,
            student_id=membership.user_id,
            center_name=center.name,
            center_id=center.id,
            membership_id=membership.id,
        )
        return {
            "membership": _membership_out(membership, student),
        }


class ListGroupsService(_OwnedCenterMixin):
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        groups = await groups_repository.for_center(self.session, self.center_id)
        members = await group_members_repository.membership_ids_for_groups(
            self.session, [group.id for group in groups]
        )
        by_group: dict[str, list[str]] = {group.id: [] for group in groups}
        for row in members:
            by_group.setdefault(row.group_id, []).append(row.membership_id)
        return {
            "groups": [
                _group_out(group, by_group.get(group.id, [])) for group in groups
            ]
        }


class CreateGroupService(_OwnedCenterMixin):
    def __init__(
        self, session: AsyncSession, tutor_id: str, center_id: str, body: Any
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        name = (self.body.name or "").strip()
        if not 1 <= len(name) <= 60:
            raise ApiError(400, "Group name must be 1–60 characters")
        existing = await groups_repository.by_center_name(
            self.session, self.center_id, name
        )
        if existing is not None:
            raise ApiError(409, "A group with that name already exists")
        group = CenterGroup(id=cuid(), center_id=self.center_id, name=name)
        try:
            await groups_repository.add(self.session, group, flush=True)
        except IntegrityError as exc:
            raise ApiError(409, "A group with that name already exists") from exc
        return {"group": _group_out(group, [])}


class UpdateGroupService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        group_id: str,
        body: Any,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.group_id = group_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        group = await self._group()
        name = (self.body.name or "").strip()
        if not 1 <= len(name) <= 60:
            raise ApiError(400, "Group name must be 1–60 characters")
        clash = await groups_repository.by_center_name(
            self.session, self.center_id, name
        )
        if clash is not None and clash.id != group.id:
            raise ApiError(409, "A group with that name already exists")
        group.name = name
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ApiError(409, "A group with that name already exists") from exc
        members = await group_members_repository.for_group(self.session, group.id)
        return {
            "group": _group_out(group, [row.membership_id for row in members]),
        }

    async def _group(self) -> CenterGroup:
        group = await groups_repository.by_id(self.session, self.group_id)
        if group is None or group.center_id != self.center_id:
            raise ApiError(404, "Group not found")
        return group


class DeleteGroupService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        group_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.group_id = group_id

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        group = await groups_repository.by_id(self.session, self.group_id)
        if group is None or group.center_id != self.center_id:
            raise ApiError(404, "Group not found")
        await groups_repository.remove(self.session, group)
        return {"ok": True}


class AddGroupMembersService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        group_id: str,
        body: Any,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.group_id = group_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        group = await groups_repository.by_id(self.session, self.group_id)
        if group is None or group.center_id != self.center_id:
            raise ApiError(404, "Group not found")
        membership_ids = list(dict.fromkeys(self.body.membershipIds or []))
        if not membership_ids:
            raise ApiError(400, "membershipIds is required")
        added: list[str] = []
        for membership_id in membership_ids:
            membership = await memberships_repository.by_id(self.session, membership_id)
            if (
                membership is None
                or membership.center_id != self.center_id
                or membership.kind != "STUDENT"
                or membership.status != "ACTIVE"
            ):
                raise ApiError(400, f"Invalid membership: {membership_id}")
            existing = await group_members_repository.by_group_membership(
                self.session, group.id, membership_id
            )
            if existing is not None:
                continue
            await group_members_repository.add(
                self.session,
                CenterGroupMember(
                    id=cuid(),
                    group_id=group.id,
                    membership_id=membership_id,
                ),
                flush=True,
            )
            added.append(membership_id)
        members = await group_members_repository.for_group(self.session, group.id)
        return {
            "group": _group_out(group, [row.membership_id for row in members]),
            "added": added,
        }


class RemoveGroupMemberService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        group_id: str,
        membership_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.group_id = group_id
        self.membership_id = membership_id

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        group = await groups_repository.by_id(self.session, self.group_id)
        if group is None or group.center_id != self.center_id:
            raise ApiError(404, "Group not found")
        row = await group_members_repository.by_group_membership(
            self.session, group.id, self.membership_id
        )
        if row is None:
            raise ApiError(404, "Group member not found")
        await group_members_repository.remove(self.session, row)
        members = await group_members_repository.for_group(self.session, group.id)
        return {
            "group": _group_out(group, [m.membership_id for m in members]),
        }


# --- Student realm ---------------------------------------------------------


class PreviewInviteService:
    def __init__(self, session: AsyncSession, code: str) -> None:
        self.session = session
        self.code = code.strip()

    async def process(self) -> dict[str, Any]:
        invite, center, owner = await _load_open_invite(self.session, self.code)
        return {
            "invite": {
                "code": invite.code,
                "expiresAt": _iso(invite.expires_at),
                "useLimit": invite.use_limit,
                "useCount": invite.use_count,
            },
            "center": {
                "id": center.id,
                "name": center.name,
                "state": center.state,
            },
            "tutor": {
                "firstName": owner.first_name if owner else None,
                "lastName": owner.last_name if owner else None,
                "state": owner.state if owner else None,
            },
            "consent": {
                "version": CONSENT_VERSION,
                "tutorWillSee": [
                    "Results of tasks this tutor assigns",
                    "Whether you started and finished those tasks",
                    "Per-question answers on those tasks",
                ],
                "tutorWillNotSee": [
                    "Your self-started practice",
                    "Mastery, study plan, and Astra chats",
                    "Results from other tutors",
                    "Your contact details beyond name and class",
                ],
                "tutorCanDo": [
                    "Assign tasks",
                    "Send recommendations",
                ],
                "control": "You can leave this center at any time.",
            },
        }


class AcceptInviteService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        code: str,
        body: Any,
        *,
        ip: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.code = code.strip()
        self.body = body
        self.ip = ip or "unknown"

    async def process(self) -> dict[str, Any]:
        hit(
            f"center-accept:{self.student_id}",
            INVITE_ACCEPT_LIMIT,
            INVITE_ACCEPT_WINDOW,
        )
        hit(f"center-accept-ip:{self.ip}", INVITE_ACCEPT_LIMIT, INVITE_ACCEPT_WINDOW)
        invite, center, owner = await _load_open_invite(self.session, self.code)
        consent = _validate_consent(self.body)
        student = await users_repository.by_id(self.session, self.student_id)
        if student is None or student.role != "STUDENT":
            raise ApiError(403, "Only students can join a center")

        bound = (getattr(invite, "bound_email", None) or "").strip().lower()
        if bound and (student.email or "").lower() != bound:
            raise ApiError(
                403,
                "This invite was sent to a different email address. "
                "Sign in with that account, or ask your tutor for a new invite.",
                code="INVITE_EMAIL_MISMATCH",
            )

        membership = await memberships_repository.by_center_user(
            self.session, center.id, self.student_id
        )
        if membership is not None and membership.status == "ACTIVE":
            return {
                "membership": _membership_out(membership, student),
                "center": _center_out(center),
                "alreadyMember": True,
            }

        active = await memberships_repository.active_student_count(
            self.session, center.id
        )
        if center.plan == "FREE" and free_roster_blocks_accept(active):
            raise ApiError(
                403,
                "This center is on the Free plan and cannot accept more students. "
                "Ask the tutor to upgrade to Pro.",
                code="CENTER_FREE_CAP",
                upgradePlan="PRO",
                activeStudentCount=active,
                freeStudentLimit=CENTER_FREE_ACTIVE_STUDENT_LIMIT,
            )

        now = utcnow()
        if membership is None:
            membership = CenterMembership(
                id=cuid(),
                center_id=center.id,
                user_id=self.student_id,
                kind="STUDENT",
                status="ACTIVE",
                consent=consent,
                joined_at=now,
            )
            await memberships_repository.add(self.session, membership, flush=True)
        else:
            membership.status = "ACTIVE"
            membership.consent = consent
            membership.joined_at = now
            membership.left_at = None
            await self.session.flush()

        invite.use_count = int(invite.use_count or 0) + 1
        await self.session.flush()

        await notify_invite_accepted(
            self.session,
            owner_id=center.owner_id,
            student_name=_display_name(student),
            center_name=center.name,
            center_id=center.id,
            membership_id=membership.id,
        )
        return {
            "membership": _membership_out(membership, student),
            "center": _center_out(center),
            "tutor": {
                "firstName": owner.first_name if owner else None,
                "lastName": owner.last_name if owner else None,
                "state": owner.state if owner else None,
            },
            "alreadyMember": False,
        }


class LeaveCenterService:
    def __init__(self, session: AsyncSession, student_id: str, center_id: str) -> None:
        self.session = session
        self.student_id = student_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        membership = await memberships_repository.by_center_user(
            self.session, self.center_id, self.student_id
        )
        if (
            membership is None
            or membership.kind != "STUDENT"
            or membership.status not in {"ACTIVE", "INVITED"}
        ):
            raise ApiError(404, "You are not an active member of this center")
        membership.status = "LEFT"
        membership.left_at = utcnow()
        await self.session.flush()
        return {"membership": _membership_out(membership)}


class ListMyCentersService:
    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id

    async def process(self) -> dict[str, Any]:
        memberships = await memberships_repository.for_student(
            self.session, self.student_id
        )
        centers: list[dict[str, Any]] = []
        for membership in memberships:
            if membership.status not in {"ACTIVE", "LEFT", "REMOVED"}:
                continue
            center = await centers_repository.by_id(self.session, membership.center_id)
            if center is None or center.archived_at is not None:
                continue
            owner = await users_repository.by_id(self.session, center.owner_id)
            centers.append(
                {
                    "center": {
                        "id": center.id,
                        "name": center.name,
                        "state": center.state,
                    },
                    "tutor": {
                        "firstName": owner.first_name if owner else None,
                        "lastName": owner.last_name if owner else None,
                        "state": owner.state if owner else None,
                    },
                    "membership": _membership_out(membership),
                    "whatTutorCanSee": (
                        "Results of tasks this tutor assigns, whether you started "
                        "and finished them, and per-question answers on those tasks."
                    ),
                }
            )
        return {"centers": centers}


async def _load_open_invite(
    session: AsyncSession, code: str
) -> tuple[CenterInvite, Center, User | None]:
    if not code:
        raise ApiError(404, "Invite not found")
    invite = await invites_repository.by_code_or_token(session, code)
    if invite is None or (getattr(invite, "kind", "STUDENT") or "STUDENT") != "STUDENT":
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


def _validate_consent(body: Any) -> dict[str, Any]:
    accepted = bool(getattr(body, "accepted", False))
    if not accepted:
        raise ApiError(400, "You must accept the consent terms to join")
    version = getattr(body, "version", None) or CONSENT_VERSION
    if version != CONSENT_VERSION:
        raise ApiError(400, "Unsupported consent version")
    return {
        "version": version,
        "accepted": True,
        "acceptedAt": utcnow().isoformat(),
        "acknowledgedVisibility": bool(getattr(body, "acknowledgedVisibility", True)),
        "acknowledgedLeave": bool(getattr(body, "acknowledgedLeave", True)),
    }
