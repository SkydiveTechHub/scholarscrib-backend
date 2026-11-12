"""Tutor realm: assignment CRUD, publish, close, extend, duplicate."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import TutorPrincipal, require_tutor
from app.database.db import AnSession
from app.services.centers.assignments import (
    CloseAssignmentService,
    CreateAssignmentService,
    DuplicateAssignmentService,
    ExtendAssignmentService,
    GenerateAssignmentPaperService,
    GetAssignmentService,
    ListAssignmentsService,
    PublishAssignmentService,
    UpdateAssignmentService,
)

router = APIRouter(tags=["Center / Assignments"])

TaskType = Literal["TOPIC_QUIZ", "PAST_PAPER", "MOCK_EXAM", "LESSON"]
TargetKind = Literal["STUDENT", "GROUP", "CENTER"]
RevealAnswers = Literal["immediate", "after_due", "never"]
LatePolicy = Literal["blocked", "accepted_flagged"]
AttemptScoring = Literal["best", "latest", "first"]


class ScopePointIn(BaseModel):
    classLevel: Literal["SS1", "SS2", "SS3"]
    term: Literal["FIRST", "SECOND", "THIRD"]


class AssignmentFiltersIn(BaseModel):
    subjectId: str | None = None
    subjectSlug: str | None = None
    topicIds: list[str] | None = None
    difficulty: str | None = None
    examType: str | None = None
    examYear: int | None = Field(default=None, ge=1980, le=2100)
    count: int | None = Field(default=None, ge=5, le=80)
    questionIds: list[str] | None = None
    from_: ScopePointIn | None = Field(default=None, alias="from")
    to: ScopePointIn | None = None
    lessonId: str | None = None

    model_config = {"populate_by_name": True}


class AssignmentSettingsIn(BaseModel):
    instructions: str | None = Field(default=None, max_length=4000)
    timeLimitMinutes: int | None = Field(default=None, ge=1, le=240)
    attemptsAllowed: int | None = Field(default=None, ge=1, le=20)
    attemptScoring: AttemptScoring | None = None
    revealAnswers: RevealAnswers | None = None
    latePolicy: LatePolicy | None = None
    filters: dict[str, Any] | None = None


class AssignmentTargetIn(BaseModel):
    kind: TargetKind
    targetId: str | None = None


class AssignmentCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    taskType: TaskType
    settings: AssignmentSettingsIn | None = None
    filters: AssignmentFiltersIn | None = None
    targets: list[AssignmentTargetIn] = Field(default_factory=list, max_length=200)
    opensAt: str | None = None
    dueAt: str | None = None
    generate: bool = False


class AssignmentUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    instructions: str | None = Field(default=None, max_length=4000)
    settings: AssignmentSettingsIn | None = None
    filters: AssignmentFiltersIn | None = None
    targets: list[AssignmentTargetIn] | None = Field(default=None, max_length=200)
    opensAt: str | None = None
    dueAt: str | None = None
    generate: bool = False


class AssignmentGenerateIn(BaseModel):
    filters: AssignmentFiltersIn | None = None


class AssignmentExtendIn(BaseModel):
    dueAt: str


@router.post("/centers/{center_id}/assignments", status_code=201)
async def create_assignment(
    center_id: str,
    body: AssignmentCreateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await CreateAssignmentService(session, tutor.id, center_id, body).process()


@router.get("/centers/{center_id}/assignments")
async def list_assignments(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ListAssignmentsService(session, tutor.id, center_id).process()


@router.get("/centers/{center_id}/assignments/{assignment_id}")
async def get_assignment(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await GetAssignmentService(
        session, tutor.id, center_id, assignment_id
    ).process()


@router.patch("/centers/{center_id}/assignments/{assignment_id}")
async def update_assignment(
    center_id: str,
    assignment_id: str,
    body: AssignmentUpdateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await UpdateAssignmentService(
        session, tutor.id, center_id, assignment_id, body
    ).process()


@router.post("/centers/{center_id}/assignments/{assignment_id}/generate")
async def generate_assignment_paper(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
    body: AssignmentGenerateIn | None = None,
):
    return await GenerateAssignmentPaperService(
        session, tutor.id, center_id, assignment_id, body
    ).process()


@router.post("/centers/{center_id}/assignments/{assignment_id}/publish")
async def publish_assignment(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await PublishAssignmentService(
        session, tutor.id, center_id, assignment_id
    ).process()


@router.post("/centers/{center_id}/assignments/{assignment_id}/close")
async def close_assignment(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await CloseAssignmentService(
        session, tutor.id, center_id, assignment_id
    ).process()


@router.post("/centers/{center_id}/assignments/{assignment_id}/extend")
async def extend_assignment(
    center_id: str,
    assignment_id: str,
    body: AssignmentExtendIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ExtendAssignmentService(
        session, tutor.id, center_id, assignment_id, body
    ).process()


@router.post(
    "/centers/{center_id}/assignments/{assignment_id}/duplicate", status_code=201
)
async def duplicate_assignment(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await DuplicateAssignmentService(
        session, tutor.id, center_id, assignment_id
    ).process()
