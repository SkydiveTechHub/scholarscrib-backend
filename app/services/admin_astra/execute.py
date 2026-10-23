"""Run an approved admin action through the service the console already uses."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.errors import ApiError
from app.core.logger import logger
from app.services.admin_astra.compact import scrub
from app.services.admin_astra.context import AdminToolContext
from app.services.admin_astra.tools.registry import admin_tool_spec

# Spoken after approval. The card keeps its summary; this is a separate sentence.
_OUTCOME = {
    "update_student": "Updated that student's profile.",
    "set_student_tier": "Updated that student's tier.",
    "delete_student": "Deleted that student.",
    "force_signout_student": "Signed that student out of every device.",
    "create_question": "Created that question.",
    "update_question": "Updated that question.",
    "delete_questions": "Deleted those questions.",
    "import_questions": "Imported those questions.",
    "import_lesson": "Imported that lesson.",
    "create_material": "Created that material.",
    "update_material": "Updated that material.",
    "delete_material": "Deleted that material.",
    "send_announcement": "Sent that announcement.",
    "test_announcement": "Sent that test push.",
    "cancel_announcement": "Cancelled that announcement.",
    "create_academic_term": "Created that academic term.",
    "update_academic_term": "Updated that academic term.",
    "delete_academic_term": "Deleted that academic term.",
    "create_subject": "Created that subject.",
    "update_subject": "Updated that subject.",
    "delete_subject": "Deleted that subject.",
    "create_curriculum": "Created that curriculum level.",
    "update_curriculum": "Updated that curriculum level.",
    "delete_curriculum": "Deleted that curriculum level.",
    "create_topic": "Created that topic.",
    "update_topic": "Updated that topic.",
    "delete_topic": "Deleted that topic.",
    "provider_backfill": "Ran that provider backfill.",
    "reindex_content": "Reindexed that content.",
}


def outcome_message(name: str, args: dict[str, Any], data: Any) -> str:
    """Short result line for the follow-up bubble. Not the approval-card summary."""
    if name == "set_student_status":
        if args.get("isActive"):
            return "Reactivated that student."
        return "Suspended that student."
    if name == "set_admin_status":
        if args.get("isActive"):
            return "Reactivated that admin."
        return "Deactivated that admin."
    if name == "create_admin":
        sent = data.get("resetEmailSent") if isinstance(data, dict) else None
        if sent is False:
            return "Created that admin. The reset email did not send."
        return "Created that admin and sent a reset link."
    return _OUTCOME.get(name, "Done.")


async def execute_admin_action(
    ctx: AdminToolContext, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    spec = admin_tool_spec(name)
    if spec is None or spec.execute is None or not spec.mutating:
        return {"ok": False, "error": "Unknown action", "tool": name}
    if spec.owner_only and not ctx.is_owner:
        return {"ok": False, "error": "Owner access required", "tool": name}
    try:
        parsed = spec.args_model.model_validate(args)
        data = await spec.execute(ctx, parsed)
    except ValidationError:
        return {"ok": False, "error": "Invalid action payload", "tool": name}
    except ApiError as exc:
        return {"ok": False, "error": str(exc), "tool": name}
    except Exception:
        logger.exception("admin astra action %s failed", name)
        return {"ok": False, "error": "That action failed.", "tool": name}
    return {
        "ok": True,
        "tool": name,
        "message": outcome_message(name, args, data),
        "result": scrub(data, list_limit=10, string_limit=240),
    }
