"""Study-plan mutations: validate and propose now, execute only after approval."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from app.api.schemas import PositionIn, StudyPlanIn
from app.core.errors import ApiError
from app.core.timeutil import lagos_day_key
from app.domain import can, entitlement_denial
from app.services.planner.mode import PlanSettingsCheck, plan_settings_problem
from app.services.planner.service import (
    CreateStudyPlanService,
    DeleteStudyPlanService,
    SetStudyPlanItemStatusService,
    SetStudyPlanPositionsService,
    UpdateStudyPlanSettingsService,
)
from app.services.planner.timetable import day_minutes_overrides, timetable_problem
from app.services.rag.tools.context import TutorToolContext
from app.services.rag.tools.hitl import encode_proposal
from app.services.rag.tools.registry import (
    ToolSpec,
    dump_model,
    proposal_from_args,
    register_tool,
)

_FEATURE = "studyPlanner"
_SUMMARY = Field(
    min_length=1,
    description=(
        "The approval card. Follow the plan example in your instructions: "
        "what will change, the details in names (not ids), and what happens "
        "if the student approves."
    ),
)


class CreateStudyPlanArgs(StudyPlanIn):
    """New study plan settings. Subject ids must come from a read tool."""


class UpdateStudyPlanArgs(StudyPlanIn):
    planId: str = Field(min_length=1)


class DeleteStudyPlanArgs(BaseModel):
    planId: str = Field(min_length=1)


class PlanPositionArg(BaseModel):
    subjectId: str
    topicId: str | None = None


class SetPositionsArgs(BaseModel):
    positions: list[PlanPositionArg] = Field(min_length=1, max_length=20)


class SetItemStatusArgs(BaseModel):
    itemId: str = Field(min_length=1)
    status: str = Field(description="COMPLETED, SKIPPED, or PENDING")


class CreateStudyPlanToolArgs(CreateStudyPlanArgs):
    summary: str = _SUMMARY


class UpdateStudyPlanToolArgs(UpdateStudyPlanArgs):
    summary: str = _SUMMARY


class DeleteStudyPlanToolArgs(DeleteStudyPlanArgs):
    summary: str = _SUMMARY


class SetPositionsToolArgs(SetPositionsArgs):
    summary: str = _SUMMARY


class SetItemStatusToolArgs(SetItemStatusArgs):
    summary: str = _SUMMARY


def _settings_error(body: StudyPlanIn) -> str | None:
    target = body.targetDate.isoformat() if body.targetDate else None
    problem = plan_settings_problem(
        PlanSettingsCheck(
            class_level=body.classLevel,
            target_date=target,
            force_exam_mode=body.forceExamMode,
            study_days=body.studyDays,
            weekday_minutes=body.weekdayMinutes,
            weekend_minutes=body.weekendMinutes,
            today=lagos_day_key(),
            day_minutes=day_minutes_overrides(body.timetable),
        )
    )
    if problem:
        return problem
    problem = timetable_problem(
        body.timetable,
        study_days=body.studyDays,
        weekday_minutes=body.weekdayMinutes,
        weekend_minutes=body.weekendMinutes,
        subject_ids=body.subjectIds,
    )
    if problem:
        return problem
    if bool(body.targetExam) != bool(body.targetDate):
        return "An exam target needs both an exam and a date"
    return None


def _fail(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _propose(name: str, args: dict[str, Any], summary: str | None = None) -> str:
    return encode_proposal(proposal_from_args(name, args, summary=summary))


def _dump(model: BaseModel) -> dict[str, Any]:
    """`dump_model`, but an unset timetable stays unset. Dumping it as null would
    tell the service to reset the plan to Smart and wipe the student's choices."""
    payload = dump_model(model)
    if "timetable" in payload and "timetable" not in model.model_fields_set:
        payload.pop("timetable")
    return payload


def _split_summary(model: BaseModel) -> tuple[dict[str, Any], str]:
    payload = _dump(model)
    summary = str(payload.pop("summary", "")).strip()
    return payload, summary


async def _create_study_plan(**kwargs: Any) -> str:
    try:
        parsed = CreateStudyPlanToolArgs.model_validate(kwargs)
    except ValidationError as exc:
        return _fail(exc.errors()[0].get("msg", "Invalid study plan"))
    problem = _settings_error(parsed)
    if problem:
        return _fail(problem)
    payload, summary = _split_summary(parsed)
    return _propose("create_study_plan", payload, summary)


async def _update_study_plan(**kwargs: Any) -> str:
    try:
        parsed = UpdateStudyPlanToolArgs.model_validate(kwargs)
    except ValidationError as exc:
        return _fail(exc.errors()[0].get("msg", "Invalid study plan"))
    problem = _settings_error(parsed)
    if problem:
        return _fail(problem)
    payload, summary = _split_summary(parsed)
    return _propose("update_study_plan", payload, summary)


async def _delete_study_plan(**kwargs: Any) -> str:
    try:
        parsed = DeleteStudyPlanToolArgs.model_validate(kwargs)
    except ValidationError as exc:
        return _fail(exc.errors()[0].get("msg", "Invalid study plan"))
    payload, summary = _split_summary(parsed)
    return _propose("delete_study_plan", payload, summary)


async def _set_positions(**kwargs: Any) -> str:
    try:
        parsed = SetPositionsToolArgs.model_validate(kwargs)
    except ValidationError as exc:
        return _fail(exc.errors()[0].get("msg", "Invalid positions"))
    payload, summary = _split_summary(parsed)
    return _propose("set_study_plan_positions", payload, summary)


async def _set_item_status(**kwargs: Any) -> str:
    try:
        parsed = SetItemStatusToolArgs.model_validate(kwargs)
    except ValidationError as exc:
        return _fail(exc.errors()[0].get("msg", "Invalid status"))
    normalized = parsed.status.strip().upper()
    if normalized not in {"COMPLETED", "SKIPPED", "PENDING"}:
        return _fail("status must be COMPLETED, SKIPPED, or PENDING")
    payload, summary = _split_summary(parsed)
    payload["status"] = normalized
    return _propose("set_study_plan_item_status", payload, summary)


def _fallback_summary(_: dict[str, Any]) -> str:
    return "Please confirm this change."


def register_study_plan_tools() -> None:
    entries: list[tuple[str, str, Any, type[BaseModel], bool]] = [
        (
            "create_study_plan",
            "Propose a new study plan. It is not created until the student "
            "approves. subjectIds must be real ids from a read tool. "
            "summary is the plan on the approval card.",
            _create_study_plan,
            CreateStudyPlanToolArgs,
            False,
        ),
        (
            "update_study_plan",
            "Propose new settings for an existing plan. planId comes from "
            "get_study_plan. summary is the plan on the approval card.",
            _update_study_plan,
            UpdateStudyPlanToolArgs,
            False,
        ),
        (
            "delete_study_plan",
            "Propose archiving a study plan. Destructive; waits for approval. "
            "summary is the plan on the approval card.",
            _delete_study_plan,
            DeleteStudyPlanToolArgs,
            True,
        ),
        (
            "set_study_plan_positions",
            "Propose where the class is (topic per subject) and replan. "
            "summary is the plan on the approval card.",
            _set_positions,
            SetPositionsToolArgs,
            False,
        ),
        (
            "set_study_plan_item_status",
            "Propose marking one session COMPLETED, SKIPPED, or PENDING. "
            "summary is the plan on the approval card.",
            _set_item_status,
            SetItemStatusToolArgs,
            False,
        ),
    ]
    for name, description, fn, schema, destructive in entries:
        tool = StructuredTool.from_function(
            coroutine=fn,
            name=name,
            description=description,
            args_schema=schema,
        )
        register_tool(
            ToolSpec(
                name=name,
                mutating=True,
                feature=_FEATURE,
                tool=tool,
                summary_fn=_fallback_summary,
                destructive=destructive,
            )
        )


def parse_validated_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Re-validate resume edits before execution."""
    try:
        if name == "create_study_plan":
            body = CreateStudyPlanArgs.model_validate(args)
            problem = _settings_error(body)
            if problem:
                raise ApiError(400, problem)
            return _dump(body)
        if name == "update_study_plan":
            body = UpdateStudyPlanArgs.model_validate(args)
            problem = _settings_error(body)
            if problem:
                raise ApiError(400, problem)
            return _dump(body)
        if name == "delete_study_plan":
            return dump_model(DeleteStudyPlanArgs.model_validate(args))
        if name == "set_study_plan_positions":
            return dump_model(SetPositionsArgs.model_validate(args))
        if name == "set_study_plan_item_status":
            parsed = SetItemStatusArgs.model_validate(args)
            parsed.status = parsed.status.strip().upper()
            if parsed.status not in {"COMPLETED", "SKIPPED", "PENDING"}:
                raise ApiError(400, "status must be COMPLETED, SKIPPED, or PENDING")
            return dump_model(parsed)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "Invalid action payload")
        raise ApiError(400, str(message)) from exc
    raise ApiError(400, "Unknown study plan action")


def _deny_feature(tier: str) -> dict[str, Any] | None:
    if can(tier, _FEATURE):
        return None
    denial = entitlement_denial(_FEATURE)
    return {
        "ok": False,
        "error": str(denial["error"]),
        "requiredTier": denial["requiredTier"],
        "feature": _FEATURE,
    }


async def execute_study_plan_action(
    ctx: TutorToolContext, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    denied = _deny_feature(ctx.tier)
    if denied:
        return denied
    validated = parse_validated_args(name, args)
    if name == "create_study_plan":
        created = await CreateStudyPlanService(
            ctx.session,
            ctx.user_id,
            ctx.class_level,
            StudyPlanIn.model_validate(validated),
            ctx.track,
        ).process()
        return {
            "ok": True,
            "message": "Created your study plan.",
            "planId": created.get("planId"),
            "tool": name,
        }
    if name == "update_study_plan":
        plan_id = validated.pop("planId")
        await UpdateStudyPlanSettingsService(
            ctx.session,
            ctx.user_id,
            ctx.class_level,
            plan_id,
            StudyPlanIn.model_validate(validated),
            ctx.track,
        ).process()
        return {
            "ok": True,
            "message": "Updated your study plan.",
            "planId": plan_id,
            "tool": name,
        }
    if name == "delete_study_plan":
        await DeleteStudyPlanService(
            ctx.session, ctx.user_id, validated["planId"]
        ).process()
        return {
            "ok": True,
            "message": "Archived that study plan.",
            "planId": validated["planId"],
            "tool": name,
        }
    if name == "set_study_plan_positions":
        positions = [PositionIn.model_validate(row) for row in validated["positions"]]
        await SetStudyPlanPositionsService(
            ctx.session, ctx.user_id, ctx.class_level, positions
        ).process()
        return {
            "ok": True,
            "message": "Updated where your class is and rebuilt the plan.",
            "tool": name,
        }
    if name == "set_study_plan_item_status":
        await SetStudyPlanItemStatusService(
            ctx.session, ctx.user_id, validated["itemId"], validated["status"]
        ).process()
        return {
            "ok": True,
            "message": f"Marked that session {validated['status'].lower()}.",
            "tool": name,
        }
    return {"ok": False, "error": "Unknown study plan action", "tool": name}
