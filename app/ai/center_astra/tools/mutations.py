"""HITL draft tools: approve creates assignment drafts or sends recommendations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field

from app.ai.center_astra.context import CenterAstraContext
from app.ai.center_astra.tools.factory import register_mutation
from app.services.centers.assignments import (
    CreateAssignmentService,
    PublishAssignmentService,
)
from app.services.centers.recommendations import CreateRecommendationsService


class DraftAssignmentArgs(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    taskType: str = Field(min_length=2, max_length=32)
    opensAt: str | None = None
    dueAt: str | None = None
    generate: bool = False
    targets: list[dict[str, Any]] = Field(default_factory=list)
    settings: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None


class PublishAssignmentArgs(BaseModel):
    assignmentId: str = Field(min_length=1, max_length=64)


class DraftRecommendationArgs(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    studentId: str | None = Field(default=None, max_length=64)
    groupId: str | None = Field(default=None, max_length=64)
    toCenter: bool = False
    assignmentId: str | None = Field(default=None, max_length=64)
    attachments: dict[str, Any] | None = None


class _AssignmentBody:
    """Attribute bag matching AssignmentCreateIn for CreateAssignmentService."""

    def __init__(self, data: DraftAssignmentArgs) -> None:
        self.title = data.title
        self.taskType = data.taskType
        self.opensAt = data.opensAt
        self.dueAt = data.dueAt
        self.generate = data.generate
        self.targets = data.targets
        self.settings = data.settings
        self.filters = data.filters


def register_mutation_tools() -> None:
    async def draft_assignment(ctx: CenterAstraContext, args: DraftAssignmentArgs):
        return await CreateAssignmentService(
            ctx.session, ctx.tutor_id, ctx.center_id, _AssignmentBody(args)
        ).process()

    async def publish_assignment(ctx: CenterAstraContext, args: PublishAssignmentArgs):
        return await PublishAssignmentService(
            ctx.session, ctx.tutor_id, ctx.center_id, args.assignmentId
        ).process()

    async def draft_recommendation(
        ctx: CenterAstraContext, args: DraftRecommendationArgs
    ):
        body = SimpleNamespace(
            body=args.body,
            studentId=args.studentId,
            groupId=args.groupId,
            toCenter=args.toCenter,
            center=args.toCenter,
            assignmentId=args.assignmentId,
            attachments=args.attachments,
        )
        return await CreateRecommendationsService(
            ctx.session, ctx.tutor_id, ctx.center_id, body
        ).process()

    register_mutation(
        "draft_assignment",
        "assignments",
        "Propose a new assignment draft for this center. Creates DRAFT on "
        "approval — does not publish to students until publish_assignment.",
        DraftAssignmentArgs,
        draft_assignment,
    )
    register_mutation(
        "publish_assignment",
        "assignments",
        "Propose publishing an existing DRAFT/SCHEDULED assignment to students. "
        "Requires tutor approval before students see it.",
        PublishAssignmentArgs,
        publish_assignment,
    )
    register_mutation(
        "draft_recommendation",
        "recommendations",
        "Propose a recommendation to send to a student, group, or the whole "
        "center. Sends only after tutor approval.",
        DraftRecommendationArgs,
        draft_recommendation,
    )
