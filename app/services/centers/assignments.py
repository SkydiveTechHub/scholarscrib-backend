"""Assignment lifecycle: draft → scheduled → open → closed (M2)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import as_utc, utcnow
from app.database.models import (
    AssessmentAttempt,
    Assignment,
    AssignmentTarget,
    Center,
    User,
)
from app.database.repositories.assessment import (
    assessments_repository,
    attempts_repository,
)
from app.database.repositories.centers import (
    assignment_targets_repository,
    assignments_repository,
    centers_repository,
    group_members_repository,
    groups_repository,
    memberships_repository,
)
from app.database.repositories.identity import users_repository
from app.services.assessments.service import _paper_questions, _payload, reap_stale
from app.services.centers.notify import (
    notify_assignment_closed,
    notify_new_assignment,
)
from app.services.centers.paper import (
    build_assignment_assessment,
    clone_assessment_questions,
)
from app.services.product_events import capture

TASK_TYPES = frozenset({"TOPIC_QUIZ", "PAST_PAPER", "MOCK_EXAM", "LESSON"})
TARGET_KINDS = frozenset({"STUDENT", "GROUP", "CENTER"})
ATTEMPT_SCORING = frozenset({"best", "latest", "first"})
REVEAL_ANSWERS = frozenset({"immediate", "after_due", "never"})
LATE_POLICIES = frozenset({"blocked", "accepted_flagged"})

DEFAULT_SETTINGS: dict[str, Any] = {
    "instructions": None,
    "timeLimitMinutes": None,
    "attemptsAllowed": 1,
    "attemptScoring": "latest",
    "revealAnswers": "immediate",
    "latePolicy": "accepted_flagged",
    "filters": {},
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _settings(raw: dict | None) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_SETTINGS)
    if raw:
        merged.update({key: value for key, value in raw.items() if value is not None})
    if not isinstance(merged.get("filters"), dict):
        merged["filters"] = {}
    return merged


def _body_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return {
            key: item
            for key, item in value.model_dump(by_alias=True).items()
            if item is not None
        }
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if item is not None}
    return {}


def _normalize_settings(raw: dict | None) -> dict[str, Any]:
    settings = _settings(raw)
    scoring = str(settings.get("attemptScoring") or "latest").lower()
    if scoring not in ATTEMPT_SCORING:
        raise ApiError(400, f"Invalid attemptScoring: {scoring}")
    settings["attemptScoring"] = scoring
    reveal = str(settings.get("revealAnswers") or "immediate").lower()
    if reveal not in REVEAL_ANSWERS:
        raise ApiError(400, f"Invalid revealAnswers: {reveal}")
    settings["revealAnswers"] = reveal
    late = str(settings.get("latePolicy") or "accepted_flagged").lower()
    if late not in LATE_POLICIES:
        raise ApiError(400, f"Invalid latePolicy: {late}")
    settings["latePolicy"] = late
    attempts = int(settings.get("attemptsAllowed") or 1)
    if attempts < 1 or attempts > 20:
        raise ApiError(400, "attemptsAllowed must be 1–20")
    settings["attemptsAllowed"] = attempts
    limit = settings.get("timeLimitMinutes")
    if limit is not None:
        limit = int(limit)
        if limit < 1 or limit > 240:
            raise ApiError(400, "timeLimitMinutes must be 1–240")
        settings["timeLimitMinutes"] = limit
    instructions = settings.get("instructions")
    if instructions is not None:
        instructions = str(instructions).strip()
        if len(instructions) > 4000:
            raise ApiError(400, "instructions too long")
        settings["instructions"] = instructions or None
    return settings


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return as_utc(datetime.fromisoformat(text))


def _assignment_out(
    assignment: Assignment,
    *,
    targets: list[AssignmentTarget] | None = None,
    center: Center | None = None,
    tutor: User | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": assignment.id,
        "centerId": assignment.center_id,
        "createdById": assignment.created_by_id,
        "assessmentId": assignment.assessment_id,
        "title": assignment.title,
        "taskType": assignment.task_type,
        "status": assignment.status.lower(),
        "settings": _settings(assignment.settings),
        "opensAt": _iso(assignment.opens_at),
        "dueAt": _iso(assignment.due_at),
        "closedAt": _iso(assignment.closed_at),
        "createdAt": _iso(assignment.created_at),
        "updatedAt": _iso(assignment.updated_at),
    }
    if targets is not None:
        payload["targets"] = [
            {"kind": row.kind, "targetId": row.target_id} for row in targets
        ]
    if center is not None:
        payload["center"] = {
            "id": center.id,
            "name": center.name,
            "state": center.state,
        }
    if tutor is not None:
        name = " ".join(
            part for part in (tutor.first_name or "", tutor.last_name or "") if part
        ).strip()
        payload["tutor"] = {
            "id": tutor.id,
            "name": name or tutor.email or "Tutor",
        }
    if extra:
        payload.update(extra)
    return payload


class _OwnedCenterMixin:
    """Staff access (owner or facilitator) for assignments / results / recs."""

    session: AsyncSession
    tutor_id: str
    center_id: str

    async def _owned_center(self) -> Center:
        from app.services.centers.access import load_staff_center

        center, _membership = await load_staff_center(
            self.session, self.center_id, self.tutor_id
        )
        return center

    async def _load_assignment(self, assignment_id: str) -> Assignment:
        await self._owned_center()
        row = await assignments_repository.by_center(
            self.session, self.center_id, assignment_id
        )
        if row is None:
            raise ApiError(404, "Assignment not found")
        return row


async def maybe_open_scheduled(session: AsyncSession, assignment: Assignment) -> None:
    if assignment.status != "SCHEDULED":
        return
    if assignment.opens_at is None or as_utc(assignment.opens_at) > utcnow():
        return
    assignment.status = "OPEN"
    await session.flush()


async def resolve_target_user_ids(
    session: AsyncSession, center_id: str, targets: list[AssignmentTarget]
) -> list[str]:
    if not targets:
        return []
    user_ids: set[str] = set()
    for target in targets:
        if target.kind == "CENTER":
            roster = await memberships_repository.roster_rows(session, center_id)
            for membership, _user in roster:
                if membership.status == "ACTIVE":
                    user_ids.add(membership.user_id)
        elif target.kind == "GROUP":
            if not target.target_id:
                continue
            members = await group_members_repository.for_group(
                session, target.target_id
            )
            for member in members:
                membership = await memberships_repository.by_id(
                    session, member.membership_id
                )
                if (
                    membership is not None
                    and membership.center_id == center_id
                    and membership.kind == "STUDENT"
                    and membership.status == "ACTIVE"
                ):
                    user_ids.add(membership.user_id)
        elif target.kind == "STUDENT":
            if not target.target_id:
                continue
            membership = await memberships_repository.by_id(session, target.target_id)
            if (
                membership is not None
                and membership.center_id == center_id
                and membership.kind == "STUDENT"
                and membership.status == "ACTIVE"
            ):
                user_ids.add(membership.user_id)
    return sorted(user_ids)


async def student_is_targeted(
    session: AsyncSession, assignment: Assignment, student_id: str
) -> bool:
    targets = await assignment_targets_repository.for_assignment(session, assignment.id)
    if not targets:
        return False
    user_ids = await resolve_target_user_ids(session, assignment.center_id, targets)
    return student_id in user_ids


async def _replace_targets(
    session: AsyncSession,
    assignment: Assignment,
    targets_in: list[Any],
) -> list[AssignmentTarget]:
    if not targets_in:
        raise ApiError(400, "At least one target is required")
    await assignment_targets_repository.delete_for_assignment(session, assignment.id)
    rows: list[AssignmentTarget] = []
    seen: set[tuple[str, str | None]] = set()
    for item in targets_in:
        kind = getattr(item, "kind", None) or item.get("kind")
        kind = str(kind or "").upper()
        target_id = getattr(item, "targetId", None)
        if target_id is None and isinstance(item, dict):
            target_id = item.get("targetId")
        if kind not in TARGET_KINDS:
            raise ApiError(400, f"Invalid target kind: {kind}")
        if kind == "CENTER":
            target_id = None
        elif not target_id:
            raise ApiError(400, f"{kind} targets require targetId")
        if kind == "STUDENT":
            membership = await memberships_repository.by_id(session, str(target_id))
            if (
                membership is None
                or membership.center_id != assignment.center_id
                or membership.kind != "STUDENT"
            ):
                raise ApiError(400, "Target student is not on this center roster")
        if kind == "GROUP":
            group = await groups_repository.by_id(session, str(target_id))
            if group is None or group.center_id != assignment.center_id:
                raise ApiError(400, "Target group not found on this center")
        key = (kind, target_id)
        if key in seen:
            continue
        seen.add(key)
        row = AssignmentTarget(
            id=cuid(),
            assignment_id=assignment.id,
            kind=kind,
            target_id=target_id,
        )
        await assignment_targets_repository.add(session, row)
        rows.append(row)
    await session.flush()
    return rows


def _has_submissions(count: int) -> bool:
    return count > 0


class CreateAssignmentService(_OwnedCenterMixin):
    def __init__(
        self, session: AsyncSession, tutor_id: str, center_id: str, body: Any
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        title = (self.body.title or "").strip()
        if not 2 <= len(title) <= 120:
            raise ApiError(400, "Title must be 2–120 characters")
        task_type = str(self.body.taskType or "").upper()
        if task_type not in TASK_TYPES:
            raise ApiError(400, f"Invalid taskType: {task_type}")
        settings = _normalize_settings(_body_dict(getattr(self.body, "settings", None)))
        if getattr(self.body, "filters", None):
            filters = _body_dict(self.body.filters)
            settings["filters"] = {**settings.get("filters", {}), **filters}
        opens_at = _parse_dt(getattr(self.body, "opensAt", None))
        due_at = _parse_dt(getattr(self.body, "dueAt", None))
        if due_at and opens_at and due_at < opens_at:
            raise ApiError(400, "dueAt must be after opensAt")
        assignment = Assignment(
            id=cuid(),
            center_id=self.center_id,
            created_by_id=self.tutor_id,
            title=title,
            task_type=task_type,
            status="DRAFT",
            settings=settings,
            opens_at=opens_at,
            due_at=due_at,
        )
        await assignments_repository.add(self.session, assignment, flush=True)
        targets_in = list(getattr(self.body, "targets", None) or [])
        targets: list[AssignmentTarget] = []
        if targets_in:
            targets = await _replace_targets(self.session, assignment, targets_in)
        if getattr(self.body, "generate", False) and task_type != "LESSON":
            await self._generate(assignment, settings)
        return {
            "assignment": _assignment_out(assignment, targets=targets),
        }

    async def _generate(self, assignment: Assignment, settings: dict[str, Any]) -> None:
        assessment, _questions = await build_assignment_assessment(
            self.session,
            tutor_id=self.tutor_id,
            title=assignment.title,
            task_type=assignment.task_type,
            filters=settings.get("filters") or {},
            time_limit_minutes_override=settings.get("timeLimitMinutes"),
            untimed=settings.get("timeLimitMinutes") is None
            and assignment.task_type == "TOPIC_QUIZ",
        )
        assignment.assessment_id = assessment.id
        await self.session.flush()


class ListAssignmentsService(_OwnedCenterMixin):
    def __init__(self, session: AsyncSession, tutor_id: str, center_id: str) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        rows = await assignments_repository.for_center(self.session, self.center_id)
        for row in rows:
            await maybe_open_scheduled(self.session, row)
        return {
            "assignments": [
                _assignment_out(
                    row,
                    targets=await assignment_targets_repository.for_assignment(
                        self.session, row.id
                    ),
                )
                for row in rows
            ]
        }


class GetAssignmentService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        assignment = await self._load_assignment(self.assignment_id)
        await maybe_open_scheduled(self.session, assignment)
        targets = await assignment_targets_repository.for_assignment(
            self.session, assignment.id
        )
        return {"assignment": _assignment_out(assignment, targets=targets)}


class UpdateAssignmentService(_OwnedCenterMixin):
    """A-7: open assignments only allow non-destructive edits."""

    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
        body: Any,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        assignment = await self._load_assignment(self.assignment_id)
        await maybe_open_scheduled(self.session, assignment)
        status = assignment.status
        if status == "CLOSED":
            raise ApiError(409, "Closed assignments cannot be edited")
        submission_count = await attempts_repository.count(
            self.session,
            AssessmentAttempt.assignment_id == assignment.id,
            AssessmentAttempt.status == "COMPLETED",
        )
        if status in {"OPEN", "SCHEDULED"}:
            await self._patch_open(assignment, submission_count)
        else:
            await self._patch_draft(assignment, submission_count)
        await self.session.flush()
        targets = await assignment_targets_repository.for_assignment(
            self.session, assignment.id
        )
        return {"assignment": _assignment_out(assignment, targets=targets)}

    async def _patch_open(self, assignment: Assignment, submission_count: int) -> None:
        if getattr(self.body, "title", None) is not None:
            title = self.body.title.strip()
            if not 2 <= len(title) <= 120:
                raise ApiError(400, "Title must be 2–120 characters")
            assignment.title = title
        if getattr(self.body, "dueAt", None) is not None:
            assignment.due_at = _parse_dt(self.body.dueAt)
        settings = _settings(assignment.settings)
        if getattr(self.body, "instructions", None) is not None:
            text = str(self.body.instructions).strip()
            if len(text) > 4000:
                raise ApiError(400, "instructions too long")
            settings["instructions"] = text or None
            assignment.settings = settings
        # Block question / filter changes once open (and always after submissions).
        destructive = any(
            getattr(self.body, field, None) is not None
            for field in ("targets", "filters", "settings", "generate", "taskType")
        )
        if destructive:
            raise ApiError(
                409,
                "Open assignments only allow title, instructions, and due date changes",
                code="ASSIGNMENT_NON_DESTRUCTIVE",
            )
        if (
            _has_submissions(submission_count)
            and getattr(self.body, "questionIds", None) is not None
        ):
            raise ApiError(
                409,
                "Cannot change questions after submissions; duplicate instead",
                code="ASSIGNMENT_QUESTIONS_LOCKED",
            )

    async def _patch_draft(self, assignment: Assignment, submission_count: int) -> None:
        if getattr(self.body, "title", None) is not None:
            title = self.body.title.strip()
            if not 2 <= len(title) <= 120:
                raise ApiError(400, "Title must be 2–120 characters")
            assignment.title = title
        if getattr(self.body, "opensAt", None) is not None:
            assignment.opens_at = _parse_dt(self.body.opensAt)
        if getattr(self.body, "dueAt", None) is not None:
            assignment.due_at = _parse_dt(self.body.dueAt)
        if getattr(self.body, "settings", None) is not None:
            current = _settings(assignment.settings)
            current.update(_body_dict(self.body.settings))
            assignment.settings = _normalize_settings(current)
        if getattr(self.body, "filters", None) is not None:
            settings = _settings(assignment.settings)
            settings["filters"] = _body_dict(self.body.filters)
            assignment.settings = _normalize_settings(settings)
        if getattr(self.body, "targets", None) is not None:
            await _replace_targets(self.session, assignment, list(self.body.targets))
        if getattr(self.body, "generate", False):
            if _has_submissions(submission_count):
                raise ApiError(409, "Cannot regenerate after submissions")
            settings = _settings(assignment.settings)
            assessment, _ = await build_assignment_assessment(
                self.session,
                tutor_id=self.tutor_id,
                title=assignment.title,
                task_type=assignment.task_type,
                filters=settings.get("filters") or {},
                time_limit_minutes_override=settings.get("timeLimitMinutes"),
            )
            assignment.assessment_id = assessment.id


class GenerateAssignmentPaperService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
        body: Any | None = None,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        assignment = await self._load_assignment(self.assignment_id)
        if assignment.status != "DRAFT":
            raise ApiError(409, "Only draft assignments can regenerate papers")
        if assignment.task_type == "LESSON":
            raise ApiError(400, "Lesson assignments do not use the assessment engine")
        settings = _settings(assignment.settings)
        if self.body is not None and getattr(self.body, "filters", None) is not None:
            settings["filters"] = _body_dict(self.body.filters)
            assignment.settings = _normalize_settings(settings)
        assessment, questions = await build_assignment_assessment(
            self.session,
            tutor_id=self.tutor_id,
            title=assignment.title,
            task_type=assignment.task_type,
            filters=settings.get("filters") or {},
            time_limit_minutes_override=settings.get("timeLimitMinutes"),
        )
        assignment.assessment_id = assessment.id
        await self.session.flush()
        from app.services.catalogue import public_question

        return {
            "assignment": _assignment_out(
                assignment,
                targets=await assignment_targets_repository.for_assignment(
                    self.session, assignment.id
                ),
            ),
            "preview": {
                "assessmentId": assessment.id,
                "totalQuestions": len(questions),
                "timeLimitMinutes": assessment.time_limit_minutes,
                "questions": [
                    public_question(question, include_answers=True)
                    for question in questions
                ],
            },
        }


class PublishAssignmentService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        center = await self._owned_center()
        assignment = await self._load_assignment(self.assignment_id)
        if assignment.status not in {"DRAFT", "SCHEDULED"}:
            raise ApiError(409, "Only draft or scheduled assignments can be published")
        targets = await assignment_targets_repository.for_assignment(
            self.session, assignment.id
        )
        if not targets:
            raise ApiError(400, "Add at least one target before publishing")
        if assignment.task_type != "LESSON" and not assignment.assessment_id:
            settings = _settings(assignment.settings)
            assessment, _ = await build_assignment_assessment(
                self.session,
                tutor_id=self.tutor_id,
                title=assignment.title,
                task_type=assignment.task_type,
                filters=settings.get("filters") or {},
                time_limit_minutes_override=settings.get("timeLimitMinutes"),
            )
            assignment.assessment_id = assessment.id
        now = utcnow()
        if assignment.opens_at and as_utc(assignment.opens_at) > now:
            assignment.status = "SCHEDULED"
        else:
            assignment.status = "OPEN"
            if assignment.opens_at is None:
                assignment.opens_at = now
        await self.session.flush()
        if assignment.status == "OPEN":
            await self._notify_targets(center, assignment, targets)
        return {
            "assignment": _assignment_out(assignment, targets=targets, center=center)
        }

    async def _notify_targets(
        self,
        center: Center,
        assignment: Assignment,
        targets: list[AssignmentTarget],
    ) -> None:
        user_ids = await resolve_target_user_ids(
            self.session, assignment.center_id, targets
        )
        for user_id in user_ids:
            await notify_new_assignment(
                self.session,
                student_id=user_id,
                center_name=center.name,
                assignment_id=assignment.id,
                title=assignment.title,
                due_at=assignment.due_at,
            )


class CloseAssignmentService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        center = await self._owned_center()
        assignment = await self._load_assignment(self.assignment_id)
        if assignment.status == "CLOSED":
            return {
                "assignment": _assignment_out(
                    assignment,
                    targets=await assignment_targets_repository.for_assignment(
                        self.session, assignment.id
                    ),
                )
            }
        if assignment.status == "DRAFT":
            raise ApiError(409, "Draft assignments cannot be closed; delete instead")
        assignment.status = "CLOSED"
        assignment.closed_at = utcnow()
        await self.session.flush()
        targets = await assignment_targets_repository.for_assignment(
            self.session, assignment.id
        )
        for user_id in await resolve_target_user_ids(
            self.session, assignment.center_id, targets
        ):
            await notify_assignment_closed(
                self.session,
                student_id=user_id,
                center_name=center.name,
                assignment_id=assignment.id,
                title=assignment.title,
            )
        return {"assignment": _assignment_out(assignment, targets=targets)}


class ExtendAssignmentService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
        body: Any,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id
        self.body = body

    async def process(self) -> dict[str, Any]:
        assignment = await self._load_assignment(self.assignment_id)
        if assignment.status not in {"OPEN", "SCHEDULED"}:
            raise ApiError(409, "Only open or scheduled assignments can be extended")
        due_at = _parse_dt(self.body.dueAt)
        if due_at is None:
            raise ApiError(400, "dueAt is required")
        if assignment.due_at and due_at < as_utc(assignment.due_at):
            raise ApiError(400, "New dueAt must be later than the current due date")
        assignment.due_at = due_at
        await self.session.flush()
        return {
            "assignment": _assignment_out(
                assignment,
                targets=await assignment_targets_repository.for_assignment(
                    self.session, assignment.id
                ),
            )
        }


class DuplicateAssignmentService(_OwnedCenterMixin):
    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        source = await self._load_assignment(self.assignment_id)
        settings = deepcopy(_settings(source.settings))
        copy = Assignment(
            id=cuid(),
            center_id=self.center_id,
            created_by_id=self.tutor_id,
            title=f"Copy of {source.title}"[:120],
            task_type=source.task_type,
            status="DRAFT",
            settings=settings,
            opens_at=None,
            due_at=None,
        )
        if source.assessment_id:
            cloned = await clone_assessment_questions(
                self.session,
                source_assessment_id=source.assessment_id,
                tutor_id=self.tutor_id,
                title=copy.title,
            )
            copy.assessment_id = cloned.id
        await assignments_repository.add(self.session, copy, flush=True)
        source_targets = await assignment_targets_repository.for_assignment(
            self.session, source.id
        )
        targets = []
        if source_targets:
            targets = await _replace_targets(
                self.session,
                copy,
                [
                    {"kind": row.kind, "targetId": row.target_id}
                    for row in source_targets
                ],
            )
        return {"assignment": _assignment_out(copy, targets=targets)}


# --- Student realm -----------------------------------------------------------------


class ListAssignedInboxService:
    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id

    async def process(self) -> dict[str, Any]:
        memberships = await memberships_repository.for_student(
            self.session, self.student_id
        )
        active_center_ids = [
            row.center_id for row in memberships if row.status == "ACTIVE"
        ]
        items: list[dict[str, Any]] = []
        for center_id in active_center_ids:
            center = await centers_repository.by_id(self.session, center_id)
            if center is None or center.archived_at is not None:
                continue
            tutor = await users_repository.by_id(self.session, center.owner_id)
            rows = await assignments_repository.for_center(self.session, center_id)
            for assignment in rows:
                await maybe_open_scheduled(self.session, assignment)
                if assignment.status != "OPEN":
                    continue
                if not await student_is_targeted(
                    self.session, assignment, self.student_id
                ):
                    continue
                attempt_status = await self._student_status(assignment.id)
                items.append(
                    _assignment_out(
                        assignment,
                        center=center,
                        tutor=tutor,
                        extra={
                            "studentStatus": attempt_status,
                            "sharedWith": {
                                "tutorName": (
                                    " ".join(
                                        part
                                        for part in (
                                            (tutor.first_name if tutor else None) or "",
                                            (tutor.last_name if tutor else None) or "",
                                        )
                                        if part
                                    ).strip()
                                    or (tutor.email if tutor else "Tutor")
                                ),
                                "centerName": center.name,
                            },
                        },
                    )
                )
        items.sort(
            key=lambda row: (
                row.get("dueAt") or "9999",
                row.get("createdAt") or "",
            )
        )
        return {"assignments": items}

    async def _student_status(self, assignment_id: str) -> str:
        attempts = await attempts_repository.list_where(
            self.session,
            AssessmentAttempt.assignment_id == assignment_id,
            AssessmentAttempt.student_id == self.student_id,
            order_by=(AssessmentAttempt.started_at.desc(),),
            limit=20,
        )
        if not attempts:
            return "not_started"
        if any(row.status == "IN_PROGRESS" for row in attempts):
            return "in_progress"
        if any(row.status == "COMPLETED" for row in attempts):
            return "submitted"
        return "not_started"


class GetStudentAssignmentService:
    def __init__(
        self, session: AsyncSession, student_id: str, assignment_id: str
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        assignment = await assignments_repository.by_id(
            self.session, self.assignment_id
        )
        if assignment is None:
            raise ApiError(404, "Assignment not found")
        await maybe_open_scheduled(self.session, assignment)
        membership = await memberships_repository.by_center_user(
            self.session, assignment.center_id, self.student_id
        )
        if membership is None or membership.status != "ACTIVE":
            raise ApiError(404, "Assignment not found")
        if not await student_is_targeted(self.session, assignment, self.student_id):
            raise ApiError(404, "Assignment not found")
        center = await centers_repository.by_id(self.session, assignment.center_id)
        tutor = (
            await users_repository.by_id(self.session, center.owner_id)
            if center
            else None
        )
        return {
            "assignment": _assignment_out(
                assignment,
                targets=await assignment_targets_repository.for_assignment(
                    self.session, assignment.id
                ),
                center=center,
                tutor=tutor,
            )
        }


class StartAssignedAttemptService:
    """Start (or resume) an attempt for an open assignment. D6: no tier gates."""

    def __init__(
        self, session: AsyncSession, student_id: str, assignment_id: str
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        assignment = await assignments_repository.by_id(
            self.session, self.assignment_id
        )
        if assignment is None:
            raise ApiError(404, "Assignment not found")
        await maybe_open_scheduled(self.session, assignment)
        membership = await memberships_repository.by_center_user(
            self.session, assignment.center_id, self.student_id
        )
        if membership is None or membership.status != "ACTIVE":
            raise ApiError(403, "You are not an active member of this center")
        if not await student_is_targeted(self.session, assignment, self.student_id):
            raise ApiError(403, "This assignment is not shared with you")
        if assignment.status == "CLOSED":
            raise ApiError(409, "This assignment is closed")
        if assignment.status != "OPEN":
            raise ApiError(409, "This assignment is not open yet")
        settings = _settings(assignment.settings)
        now = utcnow()
        late = bool(assignment.due_at and now > as_utc(assignment.due_at))
        if late and settings.get("latePolicy") == "blocked":
            raise ApiError(
                409,
                "Late submissions are blocked for this assignment",
                code="ASSIGNMENT_LATE_BLOCKED",
            )
        if assignment.task_type == "LESSON":
            return {
                "assignmentId": assignment.id,
                "taskType": "LESSON",
                "title": assignment.title,
                "settings": settings,
                "late": late,
                "tierBypass": True,
                "sharedWith": await self._shared_with(assignment),
            }
        if not assignment.assessment_id:
            raise ApiError(409, "Assignment has no paper yet")
        await reap_stale(self.session, self.student_id)
        existing = await attempts_repository.list_where(
            self.session,
            AssessmentAttempt.assignment_id == assignment.id,
            AssessmentAttempt.student_id == self.student_id,
            order_by=(AssessmentAttempt.started_at.desc(),),
            limit=20,
        )
        in_progress = next(
            (row for row in existing if row.status == "IN_PROGRESS"), None
        )
        if in_progress is not None:
            assessment = await assessments_repository.by_id(
                self.session, in_progress.assessment_id
            )
            if assessment is None:
                raise ApiError(404, "Assignment paper not found")
            paper = await _paper_questions(self.session, assessment.id)
            payload = _payload(assessment, in_progress, paper, resumed=True)
            return await self._decorate(payload, assignment, late=late)

        completed = [row for row in existing if row.status == "COMPLETED"]
        allowed = int(settings.get("attemptsAllowed") or 1)
        if len(completed) >= allowed:
            raise ApiError(
                409,
                "No attempts remaining for this assignment",
                code="ASSIGNMENT_ATTEMPTS_EXHAUSTED",
            )
        assessment = await assessments_repository.by_id(
            self.session, assignment.assessment_id
        )
        if assessment is None:
            raise ApiError(404, "Assignment paper not found")
        paper = await _paper_questions(self.session, assessment.id)
        attempt = AssessmentAttempt(
            id=cuid(),
            student_id=self.student_id,
            assessment_id=assessment.id,
            assignment_id=assignment.id,
            status="IN_PROGRESS",
            started_at=now,
        )
        await attempts_repository.add(self.session, attempt, flush=True)
        capture(
            self.student_id,
            "assignment_attempt_started",
            {
                "assignment_id": assignment.id,
                "assessment_type": assessment.assessment_type,
                "late": late,
                "tier_bypass": True,
            },
        )
        payload = _payload(assessment, attempt, paper)
        return await self._decorate(payload, assignment, late=late)

    async def _decorate(
        self, payload: dict[str, Any], assignment: Assignment, *, late: bool
    ) -> dict[str, Any]:
        settings = _settings(assignment.settings)
        payload["assignmentId"] = assignment.id
        payload["late"] = late
        payload["tierBypass"] = True  # D6
        payload["sharedWith"] = await self._shared_with(assignment)  # S-2
        payload["settings"] = {
            "instructions": settings.get("instructions"),
            "revealAnswers": settings.get("revealAnswers"),
            "attemptsAllowed": settings.get("attemptsAllowed"),
            "latePolicy": settings.get("latePolicy"),
            "focusLossDisclosed": True,  # D7
        }
        return payload

    async def _shared_with(self, assignment: Assignment) -> dict[str, str]:
        center = await centers_repository.by_id(self.session, assignment.center_id)
        tutor = (
            await users_repository.by_id(self.session, center.owner_id)
            if center
            else None
        )
        name = ""
        if tutor:
            name = " ".join(
                part for part in (tutor.first_name or "", tutor.last_name or "") if part
            ).strip() or (tutor.email or "Tutor")
        return {
            "tutorName": name or "Tutor",
            "centerName": center.name if center else "",
        }


async def reveal_answers_allowed(assignment: Assignment | None) -> bool:
    if assignment is None:
        return True
    settings = _settings(assignment.settings)
    reveal = settings.get("revealAnswers") or "immediate"
    if reveal == "never":
        return False
    if reveal == "after_due":
        if assignment.due_at is None:
            return True
        return utcnow() >= as_utc(assignment.due_at)
    return True


async def assert_assignment_submit_allowed(
    session: AsyncSession, attempt: AssessmentAttempt
) -> None:
    """Late policy gate for submits of assignment-linked attempts.

    Blocked late policy refuses starts after due (see StartAssignedAttemptService).
    In-progress attempts that began on time may still submit after due.
    """
    if not attempt.assignment_id:
        return
    assignment = await assignments_repository.by_id(session, attempt.assignment_id)
    if assignment is None:
        return
    if assignment.status == "CLOSED":
        raise ApiError(409, "This assignment is closed")
    settings = _settings(assignment.settings)
    if settings.get("latePolicy") != "blocked" or assignment.due_at is None:
        return
    due = as_utc(assignment.due_at)
    started = as_utc(attempt.started_at)
    if started > due:
        raise ApiError(
            409,
            "Late submissions are blocked for this assignment",
            code="ASSIGNMENT_LATE_BLOCKED",
        )


async def send_due_reminders(
    session: AsyncSession, *, now: datetime | None = None, budget_rows: int = 200
) -> dict[str, int]:
    """Cheap due-soon / due-today reminders (N-1). Deduped via source_key."""
    from app.services.centers.notify import notify_assignment_due

    clock = now or utcnow()
    # Africa/Lagos civil day boundaries are approximated via utcnow callers in cron.
    due_soon_from = clock
    due_soon_to = clock + timedelta(hours=36)
    rows = await assignments_repository.due_between(
        session,
        status="OPEN",
        due_from=due_soon_from,
        due_to=due_soon_to,
        limit=budget_rows,
    )
    sent = 0
    scanned = 0
    for assignment in rows:
        scanned += 1
        await maybe_open_scheduled(session, assignment)
        if assignment.status != "OPEN" or assignment.due_at is None:
            continue
        due = as_utc(assignment.due_at)
        hours = (due - clock).total_seconds() / 3600
        if hours < 0:
            kind = "due_today"
        elif hours <= 24:
            kind = "due_today" if due.date() == clock.date() else "due_soon"
        else:
            kind = "due_soon"
        center = await centers_repository.by_id(session, assignment.center_id)
        if center is None:
            continue
        targets = await assignment_targets_repository.for_assignment(
            session, assignment.id
        )
        for user_id in await resolve_target_user_ids(
            session, assignment.center_id, targets
        ):
            if await notify_assignment_due(
                session,
                student_id=user_id,
                center_name=center.name,
                assignment_id=assignment.id,
                title=assignment.title,
                due_at=due,
                kind=kind,
            ):
                sent += 1
    return {"scanned": scanned, "sent": sent}
