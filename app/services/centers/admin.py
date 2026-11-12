"""Admin tutor verify/suspend, reports, and access audit (X-2/X-6/X-7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import CenterAbuseReport, PushSubscription
from app.database.repositories.centers import (
    assignment_targets_repository,
    assignments_repository,
    center_abuse_reports_repository,
    centers_repository,
    memberships_repository,
    tutor_access_audits_repository,
)
from app.database.repositories.identity import users_repository
from app.database.repositories.notification import push_subscriptions_repository
from app.services.admin import RecordAuditService
from app.services.centers.notify import notify_assignment_closed, notify_user
from app.services.product_events import capture


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _tutor_out(user, *, centers: list | None = None) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "phone": user.phone,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "state": user.state,
        "isActive": user.is_active,
        "suspendedAt": _iso(user.suspended_at),
        "suspendedReason": user.suspended_reason,
        "emailVerified": _iso(user.email_verified),
        "tutorVerifiedAt": _iso(user.tutor_verified_at),
        "createdAt": _iso(user.created_at),
        "centers": centers or [],
    }


class SearchTutorsService:
    def __init__(
        self,
        session: AsyncSession,
        query: str | None,
        page: int,
        page_size: int,
        *,
        status: str | None = None,
        state: str | None = None,
        verified: bool | None = None,
    ) -> None:
        self.session = session
        self.query = query
        self.page = page
        self.page_size = page_size
        self.status = status
        self.state = state
        self.verified = verified

    async def process(self) -> dict[str, Any]:
        rows, total = await users_repository.search_tutors(
            self.session,
            self.query,
            self.page,
            self.page_size,
            status=self.status,
            state=self.state,
            verified=self.verified,
        )
        centers = await centers_repository.for_owner_ids(
            self.session, [row.id for row in rows]
        )
        by_owner: dict[str, list] = {}
        for center in centers:
            by_owner.setdefault(center.owner_id, []).append(
                {
                    "id": center.id,
                    "name": center.name,
                    "plan": center.plan,
                    "planPaidUntil": _iso(center.plan_paid_until),
                }
            )
        return {
            "tutors": [
                _tutor_out(user, centers=by_owner.get(user.id, [])) for user in rows
            ],
            "pagination": {
                "page": self.page,
                "pageSize": self.page_size,
                "total": total,
            },
        }


class GetTutorDetailService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict[str, Any]:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None or user.role != "TUTOR":
            raise ApiError(404, "Tutor not found")
        centers = await centers_repository.for_owner(self.session, user.id)
        center_payload = []
        for center in centers:
            active = await memberships_repository.active_student_count(
                self.session, center.id
            )
            center_payload.append(
                {
                    "id": center.id,
                    "name": center.name,
                    "plan": center.plan,
                    "planPaidUntil": _iso(center.plan_paid_until),
                    "state": center.state,
                    "activeStudentCount": active,
                    "archivedAt": _iso(center.archived_at),
                    "createdAt": _iso(center.created_at),
                }
            )
        return {"tutor": _tutor_out(user, centers=center_payload)}


class VerifyTutorService:
    """X-6 admin verification flag (self-serve email remains separate via T-5)."""

    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        user_id: str,
        *,
        verified: bool = True,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.user_id = user_id
        self.verified = verified

    async def process(self) -> dict[str, Any]:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None or user.role != "TUTOR":
            raise ApiError(404, "Tutor not found")
        user.tutor_verified_at = utcnow() if self.verified else None
        await RecordAuditService(
            self.session,
            self.actor_id,
            "tutor.verify" if self.verified else "tutor.unverify",
            "User",
            "Verified tutor" if self.verified else "Cleared tutor verification",
            user.id,
        ).process()
        return {"ok": True, "tutorVerifiedAt": _iso(user.tutor_verified_at)}


class SetTutorStatusService:
    """Mirror SetStudentStatusService; suspend closes open assignments (X-6)."""

    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        user_id: str,
        *,
        is_active: bool,
        reason: str | None = None,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.user_id = user_id
        self.is_active = is_active
        self.reason = reason

    async def process(self) -> dict[str, Any]:
        user = await self._load()
        closed = 0
        facilitators_blocked = 0
        if not self.is_active:
            closed = await self._close_owner_assignments(user.id)
            facilitators_blocked = await self._block_facilitators(user.id)
        self._apply(user)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "tutor.reactivate" if self.is_active else "tutor.suspend",
            "User",
            "Reactivated tutor"
            if self.is_active
            else (
                f"Suspended tutor: {self.reason} "
                f"(closed {closed} assignments, "
                f"blocked {facilitators_blocked} facilitators)"
            ),
            user.id,
        ).process()
        capture(
            user.id,
            "tutor_reactivated" if self.is_active else "tutor_suspended",
            {
                "reason": self.reason,
                "closed_assignments": closed,
                "facilitators_blocked": facilitators_blocked,
                "actor_id": self.actor_id,
            },
        )
        return {
            "ok": True,
            "closedAssignments": closed,
            "facilitatorsBlocked": facilitators_blocked,
        }

    async def _load(self):
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None or user.role != "TUTOR":
            raise ApiError(404, "Tutor not found")
        if not self.is_active and (
            self.reason is None or not 3 <= len(self.reason) <= 500
        ):
            raise ApiError(400, "A suspension reason of 3–500 characters is required")
        return user

    def _apply(self, user) -> None:
        user.is_active = self.is_active
        user.suspended_at = None if self.is_active else utcnow()
        user.suspended_reason = None if self.is_active else self.reason
        # Halt access immediately — revoke live tutor JWT sessions.
        if not self.is_active:
            user.sessions_valid_from = utcnow()

    async def _close_owner_assignments(self, owner_id: str) -> int:
        centers = await centers_repository.for_owner(
            self.session, owner_id, include_archived=True
        )
        center_ids = [center.id for center in centers]
        by_id = {center.id: center for center in centers}
        open_rows = await assignments_repository.open_for_centers(
            self.session, center_ids
        )
        now = utcnow()
        for assignment in open_rows:
            assignment.status = "CLOSED"
            assignment.closed_at = now
            center = by_id.get(assignment.center_id)
            targets = await assignment_targets_repository.for_assignment(
                self.session, assignment.id
            )
            from app.services.centers.assignments import resolve_target_user_ids

            for student_id in await resolve_target_user_ids(
                self.session, assignment.center_id, targets
            ):
                await notify_assignment_closed(
                    self.session,
                    student_id=student_id,
                    center_name=center.name if center else "Center",
                    assignment_id=assignment.id,
                    title=assignment.title,
                )
        await push_subscriptions_repository.delete_where(
            self.session, PushSubscription.user_id == owner_id
        )
        await self.session.flush()
        return len(open_rows)

    async def _block_facilitators(self, owner_id: str) -> int:
        """T-10: owner suspend immediately blocks facilitators on owned centers.

        Memberships stay ACTIVE so reactivation restores access; staff checks
        deny facilitators while the owner is suspended.
        """
        centers = await centers_repository.for_owner(
            self.session, owner_id, include_archived=True
        )
        center_ids = [center.id for center in centers]
        by_id = {center.id: center for center in centers}
        rows = await memberships_repository.active_facilitators_for_centers(
            self.session, center_ids
        )
        seen_users: set[str] = set()
        for membership in rows:
            if membership.user_id in seen_users:
                continue
            seen_users.add(membership.user_id)
            center = by_id.get(membership.center_id)
            await notify_user(
                self.session,
                user_id=membership.user_id,
                type="CENTER_OWNER_SUSPENDED",
                title="Center access paused",
                body=(
                    f"Access to {center.name if center else 'a center'} was "
                    "paused because the owner account was suspended."
                ),
                url="/center",
                source_key=f"center-owner-suspended:{owner_id}:{membership.user_id}",
            )
        return len(rows)


class CreateAbuseReportService:
    """Student-facing report stub (X-7 / R-11)."""

    KINDS = frozenset({"TUTOR", "INVITE", "RECOMMENDATION"})

    def __init__(
        self,
        session: AsyncSession,
        reporter_id: str,
        *,
        kind: str,
        target_id: str,
        reason: str,
        center_id: str | None = None,
    ) -> None:
        self.session = session
        self.reporter_id = reporter_id
        self.kind = (kind or "").upper()
        self.target_id = target_id
        self.reason = (reason or "").strip()
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        if self.kind not in self.KINDS:
            raise ApiError(400, "Invalid report kind")
        if not self.target_id:
            raise ApiError(400, "targetId is required")
        if not 3 <= len(self.reason) <= 1000:
            raise ApiError(400, "Reason must be 3–1000 characters")
        if self.center_id:
            center = await centers_repository.by_id(self.session, self.center_id)
            if center is None:
                raise ApiError(404, "Center not found")
        row = CenterAbuseReport(
            id=cuid(),
            reporter_id=self.reporter_id,
            center_id=self.center_id,
            kind=self.kind,
            target_id=self.target_id,
            reason=self.reason,
            status="OPEN",
        )
        await center_abuse_reports_repository.add(self.session, row, flush=True)
        return {
            "report": {
                "id": row.id,
                "kind": row.kind,
                "status": row.status,
                "createdAt": _iso(row.created_at),
            }
        }


class ListAbuseReportsService:
    def __init__(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        *,
        status: str | None = None,
        kind: str | None = None,
    ) -> None:
        self.session = session
        self.page = page
        self.page_size = page_size
        self.status = status.upper() if status else None
        self.kind = kind.upper() if kind else None

    async def process(self) -> dict[str, Any]:
        rows, total = await center_abuse_reports_repository.queue(
            self.session,
            page=self.page,
            page_size=self.page_size,
            status=self.status,
            kind=self.kind,
        )
        return {
            "reports": [
                {
                    "id": row.id,
                    "reporterId": row.reporter_id,
                    "centerId": row.center_id,
                    "kind": row.kind,
                    "targetId": row.target_id,
                    "reason": row.reason,
                    "status": row.status,
                    "resolvedAt": _iso(row.resolved_at),
                    "resolvedById": row.resolved_by_id,
                    "resolutionNote": row.resolution_note,
                    "createdAt": _iso(row.created_at),
                }
                for row in rows
            ],
            "pagination": {
                "page": self.page,
                "pageSize": self.page_size,
                "total": total,
            },
        }


class UpdateAbuseReportService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        report_id: str,
        *,
        status: str,
        note: str | None = None,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.report_id = report_id
        self.status = (status or "").upper()
        self.note = note

    async def process(self) -> dict[str, Any]:
        if self.status not in {"OPEN", "REVIEWING", "RESOLVED", "DISMISSED"}:
            raise ApiError(400, "Invalid report status")
        row = await center_abuse_reports_repository.by_id(self.session, self.report_id)
        if row is None:
            raise ApiError(404, "Report not found")
        row.status = self.status
        if self.status in {"RESOLVED", "DISMISSED"}:
            row.resolved_at = utcnow()
            row.resolved_by_id = self.actor_id
            row.resolution_note = self.note
        await RecordAuditService(
            self.session,
            self.actor_id,
            "tutor.report.update",
            "CenterAbuseReport",
            f"Report {row.id} → {self.status}",
            row.id,
        ).process()
        return {"ok": True}


class ListTutorAccessAuditService:
    """X-2 admin summary of tutor access to student results."""

    def __init__(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        *,
        center_id: str | None = None,
        actor_id: str | None = None,
    ) -> None:
        self.session = session
        self.page = page
        self.page_size = page_size
        self.center_id = center_id
        self.actor_id = actor_id

    async def process(self) -> dict[str, Any]:
        rows, total = await tutor_access_audits_repository.recent(
            self.session,
            page=self.page,
            page_size=self.page_size,
            center_id=self.center_id,
            actor_id=self.actor_id,
        )
        return {
            "audits": [
                {
                    "id": row.id,
                    "actorId": row.actor_id,
                    "centerId": row.center_id,
                    "studentId": row.student_id,
                    "action": row.action,
                    "resource": row.resource,
                    "meta": row.meta,
                    "createdAt": _iso(row.created_at),
                }
                for row in rows
            ],
            "pagination": {
                "page": self.page,
                "pageSize": self.page_size,
                "total": total,
            },
        }
