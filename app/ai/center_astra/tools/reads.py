"""Center-scoped read tools. Assignment-linked evidence only (AI-2 / AI-3 / X-1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.center_astra.context import CenterAstraContext
from app.ai.center_astra.tools.factory import EmptyArgs, register_read
from app.core.errors import ApiError
from app.database.repositories.centers import (
    assignments_repository,
    memberships_repository,
)
from app.services.centers.assignments import ListAssignmentsService
from app.services.centers.results import (
    AssignmentItemAnalysisService,
    AssignmentOverviewService,
    AssignmentStudentRowsService,
    StudentScopedDetailService,
)
from app.services.centers.service import ListGroupsService, RosterService


class AssignmentIdArgs(BaseModel):
    assignmentId: str = Field(min_length=1, max_length=64)


class StudentScopedArgs(BaseModel):
    studentId: str = Field(min_length=1, max_length=64)


UNSCOPED_FORBIDDEN = frozenset(
    {
        "topic_mastery",
        "topicMastery",
        "mastery",
        "study_plan",
        "studyPlan",
        "student_astra",
        "studentAstra",
        "self_practice",
        "selfPractice",
        "unscoped_attempts",
        "unscopedAttempts",
    }
)


def refuse_unscoped(resource: str) -> None:
    """Hard fail if a tool is asked for data outside section 4 scope."""
    key = (resource or "").strip()
    if key in UNSCOPED_FORBIDDEN or key.lower() in UNSCOPED_FORBIDDEN:
        raise ApiError(
            403,
            "Tutor Astra can only use assignment-scoped evidence for this center.",
            code="CENTER_PRIVACY_X1",
            resource=key,
        )


async def _require_center_assignment(
    ctx: CenterAstraContext, assignment_id: str
) -> None:
    row = await assignments_repository.by_center(
        ctx.session, ctx.center_id, assignment_id
    )
    if row is None:
        raise ApiError(404, "Assignment not found in this center")


async def _require_active_student(ctx: CenterAstraContext, student_id: str) -> None:
    membership = await memberships_repository.by_center_user(
        ctx.session, ctx.center_id, student_id
    )
    if (
        membership is None
        or membership.kind != "STUDENT"
        or membership.status != "ACTIVE"
    ):
        raise ApiError(404, "Student not found in this center")


def register_read_tools() -> None:
    async def list_assignments(ctx: CenterAstraContext, _args: EmptyArgs):
        return await ListAssignmentsService(
            ctx.session, ctx.tutor_id, ctx.center_id
        ).process()

    async def assignment_overview(ctx: CenterAstraContext, args: AssignmentIdArgs):
        await _require_center_assignment(ctx, args.assignmentId)
        return await AssignmentOverviewService(
            ctx.session, ctx.tutor_id, ctx.center_id, args.assignmentId
        ).process()

    async def assignment_students(ctx: CenterAstraContext, args: AssignmentIdArgs):
        await _require_center_assignment(ctx, args.assignmentId)
        return await AssignmentStudentRowsService(
            ctx.session, ctx.tutor_id, ctx.center_id, args.assignmentId
        ).process()

    async def assignment_item_analysis(ctx: CenterAstraContext, args: AssignmentIdArgs):
        await _require_center_assignment(ctx, args.assignmentId)
        return await AssignmentItemAnalysisService(
            ctx.session, ctx.tutor_id, ctx.center_id, args.assignmentId
        ).process()

    async def student_assignment_detail(
        ctx: CenterAstraContext, args: StudentScopedArgs
    ):
        await _require_active_student(ctx, args.studentId)
        return await StudentScopedDetailService(
            ctx.session, ctx.tutor_id, ctx.center_id, args.studentId
        ).process()

    async def list_roster(ctx: CenterAstraContext, _args: EmptyArgs):
        return await RosterService(ctx.session, ctx.tutor_id, ctx.center_id).process()

    async def list_groups(ctx: CenterAstraContext, _args: EmptyArgs):
        return await ListGroupsService(
            ctx.session, ctx.tutor_id, ctx.center_id
        ).process()

    async def refuse_unscoped_student_data(
        ctx: CenterAstraContext, args: StudentScopedArgs
    ):
        """AI-3 / X-1: any attempt to fetch mastery or student Astra must fail."""
        del ctx  # center context is intentionally unused — always refuse
        refuse_unscoped("mastery")
        return {"studentId": args.studentId}

    register_read(
        "list_assignments",
        "assignments",
        "List this center's assignments (draft/open/closed) with targets.",
        EmptyArgs,
        list_assignments,
    )
    register_read(
        "assignment_overview",
        "results",
        "Assignment overview: completion, averages, distribution (P-1).",
        AssignmentIdArgs,
        assignment_overview,
    )
    register_read(
        "assignment_student_rows",
        "results",
        "Per-student rows for an assignment, including not-started and overdue (P-2).",
        AssignmentIdArgs,
        assignment_students,
    )
    register_read(
        "assignment_item_analysis",
        "results",
        "Item analysis for an assignment — hardest questions (P-3).",
        AssignmentIdArgs,
        assignment_item_analysis,
    )
    register_read(
        "student_scoped_detail",
        "results",
        "Student detail based only on tasks this center assigned (P-4). "
        "Never returns mastery or self-started practice.",
        StudentScopedArgs,
        student_assignment_detail,
    )
    register_read(
        "list_roster",
        "roster",
        "Active students in this center roster (names/ids only).",
        EmptyArgs,
        list_roster,
    )
    register_read(
        "list_groups",
        "roster",
        "Groups in this center.",
        EmptyArgs,
        list_groups,
    )
    register_read(
        "get_student_mastery",
        "results",
        "DO NOT USE for ordinary answers. Attempts to read unscoped mastery "
        "and must fail closed (privacy).",
        StudentScopedArgs,
        refuse_unscoped_student_data,
    )
