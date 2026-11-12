"""Run an approved center Astra action through existing center services."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai.center_astra.compact import scrub
from app.ai.center_astra.context import CenterAstraContext
from app.ai.center_astra.tools.registry import center_astra_tool_spec
from app.core.errors import ApiError
from app.core.logger import logger

_OUTCOME = {
    "draft_assignment": "Created that assignment as a draft.",
    "publish_assignment": "Published that assignment to students.",
    "draft_recommendation": "Sent that recommendation.",
}


def outcome_message(name: str, args: dict[str, Any], data: Any) -> str:
    if name == "draft_recommendation" and isinstance(data, dict):
        delivered = data.get("delivered")
        if isinstance(delivered, int) and delivered > 1:
            return f"Sent that recommendation to {delivered} students."
    return _OUTCOME.get(name, "Done.")


async def execute_center_astra_action(
    ctx: CenterAstraContext, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    spec = center_astra_tool_spec(name)
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
        logger.exception("center astra action %s failed", name)
        return {"ok": False, "error": "That action failed.", "tool": name}
    return {
        "ok": True,
        "tool": name,
        "message": outcome_message(name, args, data),
        "result": scrub(data, list_limit=10, string_limit=240),
    }
