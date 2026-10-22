"""Run an approved admin action through the service the console already uses."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.errors import ApiError
from app.core.logger import logger
from app.services.admin_astra.compact import scrub
from app.services.admin_astra.context import AdminToolContext
from app.services.admin_astra.tools.registry import admin_tool_spec


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
        "result": scrub(data, list_limit=10, string_limit=240),
    }
