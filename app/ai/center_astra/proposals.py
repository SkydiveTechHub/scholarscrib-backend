"""Editable approval-card fields for center Astra."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai.center_astra.tools.registry import center_astra_tool_spec
from app.ai.core.proposals import (
    EditableMap,
    fields_from_map,
    merge_resume_args,
)
from app.ai.core.proposals import (
    public_proposal as _public_proposal,
)
from app.ai.core.proposals import (
    resume_command as _resume_command,
)
from app.core.errors import ApiError

_EDITABLE: EditableMap = {
    "draft_assignment": (
        ("title", "Title", "string", True, 120),
        ("taskType", "Task type", "string", True, 32),
        ("dueAt", "Due at", "string", False, 40),
        ("opensAt", "Opens at", "string", False, 40),
    ),
    "draft_recommendation": (("body", "Message", "string", True, 2000),),
}


def fields_for(tool: str, args: dict) -> list[dict] | None:
    spec = center_astra_tool_spec(tool)
    if spec is None or spec.destructive or spec.owner_only:
        return None
    return fields_from_map(_EDITABLE, tool, args)


def public_proposal(pending: dict[str, Any], checkpoint_id: str | None) -> dict:
    return _public_proposal(pending, checkpoint_id, fields_for=fields_for)


def resolve_resume_args(pending: dict[str, Any], args: dict | None) -> dict:
    if args is None:
        merged = merge_resume_args(pending, None, fields_for=fields_for)
    else:
        merged = merge_resume_args(pending, args, fields_for=fields_for)
    tool = pending.get("tool")
    if not isinstance(tool, str) or not tool:
        raise ApiError(400, "Invalid action payload")
    spec = center_astra_tool_spec(tool)
    if spec is None:
        raise ApiError(400, "Invalid action payload")
    try:
        parsed = spec.args_model.model_validate(merged)
    except ValidationError as exc:
        raise ApiError(400, "Invalid action payload") from exc
    return parsed.model_dump(mode="json")


def resume_command(pending: dict[str, Any], approved: bool, args: dict | None) -> dict:
    return _resume_command(
        pending,
        approved,
        args,
        resolve_args=resolve_resume_args,
        require_args_on_approve=True,
    )
