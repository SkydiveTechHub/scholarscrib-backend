"""Editable approval-card fields for admin Astra. Identity ids stay frozen."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai.admin.tools.registry import admin_tool_spec
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
    "update_student": (
        ("firstName", "First name", "string", True, 80),
        ("lastName", "Last name", "string", True, 80),
        ("email", "Email", "string", False, 120),
        ("phone", "Phone", "string", False, 32),
        ("classLevel", "Class", "string", False, 8),
        ("track", "Track", "string", False, 16),
        ("state", "State", "string", False, 40),
    ),
    "set_student_status": (
        ("isActive", "Active", "boolean", True, None),
        ("reason", "Reason", "string", False, 240),
    ),
    "create_question": (
        ("questionText", "Question", "string", True, 4000),
        ("explanation", "Explanation", "string", True, 4000),
        ("correctAnswer", "Answer", "string", True, 40),
        ("difficulty", "Difficulty", "string", False, 20),
        ("marks", "Marks", "number", False, None),
        ("examYear", "Year", "number", False, None),
        ("questionNumber", "Number", "number", False, None),
    ),
    "update_question": (
        ("questionText", "Question", "string", False, 4000),
        ("explanation", "Explanation", "string", False, 4000),
        ("correctAnswer", "Answer", "string", False, 40),
        ("difficulty", "Difficulty", "string", False, 20),
        ("marks", "Marks", "number", False, None),
        ("examYear", "Year", "number", False, None),
        ("questionNumber", "Number", "number", False, None),
    ),
    "create_material": (
        ("title", "Title", "string", True, 200),
        ("description", "Description", "string", False, 500),
        ("url", "URL", "string", True, 500),
        ("author", "Author", "string", False, 120),
        ("isFree", "Free", "boolean", False, None),
    ),
    "update_material": (
        ("title", "Title", "string", False, 200),
        ("description", "Description", "string", False, 500),
        ("url", "URL", "string", False, 500),
        ("author", "Author", "string", False, 120),
        ("isFree", "Free", "boolean", False, None),
    ),
    "create_academic_term": (
        ("session", "Session", "string", True, 16),
        ("term", "Term", "string", True, 16),
        ("startsOn", "Starts", "string", True, 16),
        ("endsOn", "Ends", "string", True, 16),
    ),
    "update_academic_term": (
        ("session", "Session", "string", True, 16),
        ("term", "Term", "string", True, 16),
        ("startsOn", "Starts", "string", True, 16),
        ("endsOn", "Ends", "string", True, 16),
    ),
}


def fields_for(tool: str, args: dict) -> list[dict] | None:
    spec = admin_tool_spec(tool)
    if spec is None or spec.destructive or spec.owner_only:
        return None
    return fields_from_map(_EDITABLE, tool, args)


def public_proposal(pending: dict[str, Any], checkpoint_id: str | None) -> dict:
    return _public_proposal(pending, checkpoint_id, fields_for=fields_for)


def resolve_resume_args(pending: dict[str, Any], args: dict | None) -> dict:
    """Merge an edit onto the stored payload, or return the stored payload."""
    # Admin always re-validates through the tool args model (even bare approve).
    if args is None:
        merged = merge_resume_args(pending, None, fields_for=fields_for)
    else:
        merged = merge_resume_args(pending, args, fields_for=fields_for)
    tool = pending.get("tool")
    if not isinstance(tool, str) or not tool:
        raise ApiError(400, "Invalid action payload")
    spec = admin_tool_spec(tool)
    if spec is None:
        raise ApiError(400, "Invalid action payload")
    try:
        parsed = spec.args_model.model_validate(merged)
    except ValidationError as exc:
        raise ApiError(400, "Invalid action payload") from exc
    return parsed.model_dump(mode="json")


def resume_command(pending: dict[str, Any], approved: bool, args: dict | None) -> dict:
    """Command passed to the interrupted graph. Rejection drops ``args``."""
    return _resume_command(
        pending,
        approved,
        args,
        resolve_args=resolve_resume_args,
        require_args_on_approve=True,
    )
