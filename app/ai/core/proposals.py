"""Shared action-proposal payload and resume helpers.

Product modules supply editable-field maps and args validators; this module
owns the public SSE shape and merge/validate of client edits.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.errors import ApiError

# key, label, type, required, max_length
EditableField = tuple[str, str, str, bool, int | None]
EditableMap = dict[str, tuple[EditableField, ...]]


def fields_from_map(
    editable: EditableMap,
    tool: str,
    args: dict,
    *,
    skip: bool = False,
) -> list[dict] | None:
    """Build approval-card field descriptors from an editable map."""
    if skip or not isinstance(args, dict):
        return None
    spec = editable.get(tool)
    if not spec:
        return None
    fields: list[dict] = []
    for key, label, kind, required, max_length in spec:
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


def public_proposal(
    pending: dict[str, Any],
    checkpoint_id: str | None,
    *,
    fields_for: Callable[[str, dict], list[dict] | None] | None = None,
) -> dict:
    """SSE / API shape for an approval card. ``status`` starts as pending."""
    raw_args = pending.get("args")
    args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
    tool = str(pending.get("tool") or "")
    fields = pending.get("fields")
    if not isinstance(fields, list):
        fields = fields_for(tool, args) if fields_for else None
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


def check_field(field: dict, value: Any) -> None:
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


def merge_resume_args(
    pending: dict[str, Any],
    args: dict | None,
    *,
    fields_for: Callable[[str, dict], list[dict] | None],
) -> dict[str, Any]:
    """Merge client edits onto stored args; identity keys stay frozen."""
    raw_stored = pending.get("args")
    stored: dict[str, Any] = raw_stored if isinstance(raw_stored, dict) else {}
    if args is None:
        return dict(stored)
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
        check_field(field, value)
        merged[key] = value
    for field in editable.values():
        if field.get("required") and merged.get(field["key"]) in (None, ""):
            raise ApiError(400, "Invalid action payload")
    return merged


def resume_command(
    pending: dict[str, Any],
    approved: bool,
    args: dict | None,
    *,
    resolve_args: Callable[[dict[str, Any], dict | None], dict],
    require_args_on_approve: bool = False,
) -> dict:
    """Command passed to the interrupted graph. Rejection drops ``args``."""
    if not approved:
        return {"approved": False}
    if args is None and not require_args_on_approve:
        return {"approved": True}
    return {"approved": True, "args": resolve_args(pending, args)}
