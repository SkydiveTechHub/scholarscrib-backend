"""Editable approval-card fields for admin Astra. Identity ids stay frozen."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.errors import ApiError
from app.services.admin_astra.tools.registry import admin_tool_spec

# key, label, type, required, max_length
_Field = tuple[str, str, str, bool, int | None]

_EDITABLE: dict[str, tuple[_Field, ...]] = {
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
    if not isinstance(args, dict):
        return None
    rows = _EDITABLE.get(tool)
    if not rows:
        return None
    fields: list[dict] = []
    for key, label, kind, required, max_length in rows:
        if key not in args:
            continue
        field: dict[str, Any] = {
            "key": key,
            "label": label,
            "type": kind,
            "required": required,
        }
        if max_length is not None:
            field["maxLength"] = max_length
        fields.append(field)
    return fields or None


def public_proposal(pending: dict[str, Any], checkpoint_id: str | None) -> dict:
    raw_args = pending.get("args")
    args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
    tool = str(pending.get("tool") or "")
    fields = pending.get("fields")
    if not isinstance(fields, list):
        fields = fields_for(tool, args)
    payload: dict[str, Any] = {
        "actionId": pending.get("actionId"),
        "checkpointId": checkpoint_id,
        "tool": pending.get("tool"),
        "args": args,
        "summary": pending.get("summary") or "Please confirm this change.",
        "destructive": bool(pending.get("destructive")),
        "kind": "actionProposal",
        "status": "pending",
    }
    if fields:
        payload["fields"] = fields
    return payload


def _check_field(field: dict, value: Any) -> None:
    kind = field.get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise ApiError(400, "Invalid action payload")
        max_length = field.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            raise ApiError(400, "Invalid action payload")
        if field.get("required") and not value.strip():
            raise ApiError(400, "Invalid action payload")
        return
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ApiError(400, "Invalid action payload")
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ApiError(400, "Invalid action payload")
        return
    raise ApiError(400, "Invalid action payload")


def resolve_resume_args(pending: dict[str, Any], args: dict | None) -> dict:
    """Merge an edit onto the stored payload, or return the stored payload."""
    raw_stored = pending.get("args")
    stored: dict[str, Any] = raw_stored if isinstance(raw_stored, dict) else {}
    if args is None:
        merged = dict(stored)
    else:
        if not isinstance(args, dict):
            raise ApiError(400, "Invalid action payload")
        if set(args) - set(stored):
            raise ApiError(400, "Invalid action payload")
        fields = pending.get("fields")
        if not isinstance(fields, list):
            fields = fields_for(str(pending.get("tool") or ""), stored) or []
        editable: dict[str, dict] = {}
        for field in fields:
            if isinstance(field, dict) and isinstance(field.get("key"), str):
                editable[field["key"]] = field
        merged = dict(stored)
        for key, value in args.items():
            field = editable.get(key)
            if field is None:
                if value != stored.get(key):
                    raise ApiError(400, "Invalid action payload")
                continue
            _check_field(field, value)
            merged[key] = value
        for field in editable.values():
            if field.get("required") and merged.get(field["key"]) in (None, ""):
                raise ApiError(400, "Invalid action payload")
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
    if not approved:
        return {"approved": False}
    if args is None:
        return {"approved": True, "args": resolve_resume_args(pending, None)}
    return {"approved": True, "args": resolve_resume_args(pending, args)}
