"""Tutor → student recommendations (C-1, C-2, C-3, C-7)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import (
    Center,
    Recommendation,
    User,
)
from app.database.repositories.centers import (
    assignments_repository,
    centers_repository,
    group_members_repository,
    groups_repository,
    memberships_repository,
    recommendations_repository,
)
from app.database.repositories.identity import users_repository
from app.services.centers.assignments import _OwnedCenterMixin
from app.services.centers.notify import notify_recommendation

# C-7 safety limits
MAX_BODY_LENGTH = 2000
MAX_RECIPIENTS = 150
URL_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"']+|\b[a-z0-9.-]+\.(?:com|ng|org|net|edu|gov)\b[^\s<>\"']*",
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _display_name(user: User | None, *, fallback: str = "Tutor") -> str:
    if user is None:
        return fallback
    name = " ".join(
        part for part in (user.first_name or "", user.last_name or "") if part
    ).strip()
    return name or user.email or fallback


def sanitize_recommendation_body(raw: str) -> str:
    """C-7: length limit + strip bare URLs / link-like tokens."""
    text = (raw or "").strip()
    if not text:
        raise ApiError(400, "Recommendation body is required")
    cleaned = URL_RE.sub("[link removed]", text)
    cleaned = re.sub(r"\s{3,}", "  ", cleaned).strip()
    if len(cleaned) > MAX_BODY_LENGTH:
        raise ApiError(
            400,
            f"Recommendation body must be at most {MAX_BODY_LENGTH} characters",
            code="RECOMMENDATION_TOO_LONG",
        )
    if not cleaned.replace("[link removed]", "").strip():
        raise ApiError(400, "Recommendation body cannot be only links")
    return cleaned


def _normalize_attachments(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(raw, dict):
        raise ApiError(400, "attachments must be an object")
    out: dict[str, Any] = {}
    topic_ids = raw.get("topicIds") or raw.get("topic_ids")
    if topic_ids is not None:
        if not isinstance(topic_ids, list) or len(topic_ids) > 20:
            raise ApiError(400, "topicIds must be a list of at most 20 ids")
        out["topicIds"] = [str(item) for item in topic_ids][:20]
    for key, alias in (
        ("lessonId", "lessonId"),
        ("practiceSetId", "practiceSetId"),
        ("assignmentId", "assignmentId"),
    ):
        value = raw.get(key) or raw.get(alias)
        if value is not None:
            out[key] = str(value)[:64]
    return out or None


def _recommendation_out(
    row: Recommendation,
    *,
    center: Center | None = None,
    author: User | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "centerId": row.center_id,
        "assignmentId": row.assignment_id,
        "authorId": row.author_id,
        "studentId": row.student_id,
        "body": row.body,
        "attachments": row.attachments,
        "status": row.status.lower(),
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }
    if center is not None:
        payload["center"] = {"id": center.id, "name": center.name}
    if author is not None:
        payload["author"] = {
            "id": author.id,
            "name": _display_name(author),
        }
    return payload


class CreateRecommendationsService(_OwnedCenterMixin):
    """Create one recommendation per recipient (student / group / center)."""

    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        body: Any,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        center = await self._owned_center()
        data = (
            self.body.model_dump(by_alias=True)
            if hasattr(self.body, "model_dump")
            else dict(self.body)
        )
        text = sanitize_recommendation_body(str(data.get("body") or ""))
        attachments = _normalize_attachments(data.get("attachments"))
        assignment_id = data.get("assignmentId")
        if assignment_id:
            assignment = await assignments_repository.by_center(
                self.session, self.center_id, str(assignment_id)
            )
            if assignment is None:
                raise ApiError(404, "Assignment not found")
            assignment_id = assignment.id
        else:
            assignment_id = None

        student_ids = await self._resolve_recipients(data)
        if not student_ids:
            raise ApiError(400, "At least one recipient is required")
        if len(student_ids) > MAX_RECIPIENTS:
            raise ApiError(
                400,
                f"At most {MAX_RECIPIENTS} recipients per recommendation",
                code="RECOMMENDATION_TOO_MANY",
            )

        created: list[Recommendation] = []
        for student_id in student_ids:
            row = Recommendation(
                id=cuid(),
                center_id=self.center_id,
                assignment_id=assignment_id,
                author_id=self.tutor_id,
                student_id=student_id,
                body=text,
                attachments=attachments,
                status="DELIVERED",
            )
            await recommendations_repository.add(self.session, row, flush=True)
            created.append(row)
            await notify_recommendation(
                self.session,
                student_id=student_id,
                center_name=center.name,
                recommendation_id=row.id,
                preview=text[:120],
            )

        author = await users_repository.by_id(self.session, self.tutor_id)
        return {
            "recommendations": [
                _recommendation_out(row, center=center, author=author)
                for row in created
            ],
            "delivered": len(created),
        }

    async def _resolve_recipients(self, data: dict[str, Any]) -> list[str]:
        student_id = data.get("studentId")
        group_id = data.get("groupId")
        whole_center = bool(data.get("center") or data.get("toCenter"))
        user_ids: set[str] = set()

        if student_id:
            membership = await memberships_repository.by_center_user(
                self.session, self.center_id, str(student_id)
            )
            if (
                membership is None
                or membership.kind != "STUDENT"
                or membership.status != "ACTIVE"
            ):
                raise ApiError(404, "Student not found in this center")
            user_ids.add(membership.user_id)

        if group_id:
            group = await groups_repository.by_id(self.session, str(group_id))
            if group is None or group.center_id != self.center_id:
                raise ApiError(404, "Group not found")
            members = await group_members_repository.for_group(self.session, group.id)
            for member in members:
                membership = await memberships_repository.by_id(
                    self.session, member.membership_id
                )
                if (
                    membership is not None
                    and membership.kind == "STUDENT"
                    and membership.status == "ACTIVE"
                ):
                    user_ids.add(membership.user_id)

        if whole_center:
            roster = await memberships_repository.roster_rows(
                self.session, self.center_id
            )
            for membership, _user in roster:
                if membership.status == "ACTIVE" and membership.kind == "STUDENT":
                    user_ids.add(membership.user_id)

        return sorted(user_ids)


class ListCenterRecommendationsService(_OwnedCenterMixin):
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        center = await self._owned_center()
        rows = await recommendations_repository.for_center(
            self.session, self.center_id, limit=200
        )
        author = await users_repository.by_id(self.session, self.tutor_id)
        return {
            "recommendations": [
                _recommendation_out(row, center=center, author=author) for row in rows
            ]
        }


class GetCenterRecommendationService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        recommendation_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.recommendation_id = recommendation_id

    async def process(self) -> dict[str, Any]:
        center = await self._owned_center()
        row = await recommendations_repository.by_center(
            self.session, self.center_id, self.recommendation_id
        )
        if row is None:
            raise ApiError(404, "Recommendation not found")
        author = await users_repository.by_id(self.session, row.author_id)
        return {
            "recommendation": _recommendation_out(row, center=center, author=author)
        }


class ListStudentRecommendationsService:
    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id

    async def process(self) -> dict[str, Any]:
        rows = await recommendations_repository.for_student(
            self.session, self.student_id, limit=100
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            center = await centers_repository.by_id(self.session, row.center_id)
            author = await users_repository.by_id(self.session, row.author_id)
            items.append(_recommendation_out(row, center=center, author=author))
        return {"recommendations": items}


class UpdateStudentRecommendationStatusService:
    """Student marks seen / acted (done) / dismiss as acted."""

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        recommendation_id: str,
        status: str,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.recommendation_id = recommendation_id
        self.status = status.upper()

    async def process(self) -> dict[str, Any]:
        if self.status not in {"SEEN", "ACTED"}:
            raise ApiError(400, "status must be seen or acted")
        row = await recommendations_repository.by_id(
            self.session, self.recommendation_id
        )
        if row is None or row.student_id != self.student_id:
            raise ApiError(404, "Recommendation not found")
        # Monotonic: DELIVERED → SEEN → ACTED
        order = {"DELIVERED": 0, "SEEN": 1, "ACTED": 2}
        current = order.get(row.status, 0)
        desired = order[self.status]
        if desired > current:
            row.status = self.status
            row.updated_at = utcnow()
            await self.session.flush()
        center = await centers_repository.by_id(self.session, row.center_id)
        author = await users_repository.by_id(self.session, row.author_id)
        return {
            "recommendation": _recommendation_out(row, center=center, author=author)
        }
