"""Editable action-proposal fields and resume validation for the student tutor."""

from __future__ import annotations

from typing import Any

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

# Scalar fields a student may change on the approval card. Keys absent from
# ``args`` are omitted. Destructive tools omit the list entirely.
_EDITABLE: EditableMap = {
    "create_study_plan": (
        ("classLevel", "Class", "string", True, 8),
        ("term", "Term", "string", True, 16),
        ("weekdayMinutes", "Weekday minutes", "number", True, None),
        ("weekendMinutes", "Weekend minutes", "number", True, None),
        ("targetExam", "Exam", "string", False, 16),
        ("targetDate", "Exam date", "string", False, 40),
    ),
    "update_study_plan": (
        ("classLevel", "Class", "string", True, 8),
        ("term", "Term", "string", True, 16),
        ("weekdayMinutes", "Weekday minutes", "number", True, None),
        ("weekendMinutes", "Weekend minutes", "number", True, None),
        ("targetExam", "Exam", "string", False, 16),
        ("targetDate", "Exam date", "string", False, 40),
    ),
    "set_study_plan_item_status": (("status", "Status", "string", True, 20),),
}


def fields_for(tool: str, args: dict) -> list[dict] | None:
    if tool == "delete_study_plan":
        return None
    return fields_from_map(_EDITABLE, tool, args)


def public_proposal(pending: dict[str, Any], checkpoint_id: str | None) -> dict:
    return _public_proposal(pending, checkpoint_id, fields_for=fields_for)


def resolve_resume_args(pending: dict[str, Any], args: dict | None) -> dict:
    """Merge an edit onto the stored payload, or return the stored payload."""
    merged = merge_resume_args(pending, args, fields_for=fields_for)
    tool = pending.get("tool")
    if not isinstance(tool, str) or not tool:
        raise ApiError(400, "Invalid action payload")
    from app.ai.tutor.tools.study_plan import parse_validated_args

    return parse_validated_args(tool, merged)


def resume_command(pending: dict[str, Any], approved: bool, args: dict | None) -> dict:
    """Command passed to the interrupted graph. Rejection drops ``args``."""
    return _resume_command(
        pending,
        approved,
        args,
        resolve_args=resolve_resume_args,
        require_args_on_approve=False,
    )
