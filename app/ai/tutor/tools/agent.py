"""Tutor action-agent nodes: study-plan tools with HITL before writes.

Shared inspect/await/route helpers live in ``app.ai.core.action``; this
module owns the student-specific agent, execute, and outcome nodes.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, SystemMessage

from app.ai.core.action import (
    action_history,
    await_action_approval,
    inspect_action_tools,
    route_after_action_agent,
    route_after_approval,
    route_after_inspect,
)
from app.ai.core.guardrails import apply_output_guard, guard_output
from app.ai.core.llm import get_chat_model
from app.ai.tutor.prompts import ACTION_SYSTEM
from app.ai.tutor.tools import tutor_action_tools
from app.ai.tutor.tools.context import get_tool_context
from app.ai.tutor.tools.study_plan import execute_study_plan_action
from app.core.errors import ApiError

# Re-export for graph wiring and tests.
__all__ = [
    "action_agent_node",
    "action_outcome_node",
    "await_action_approval",
    "execute_action_node",
    "inspect_action_tools",
    "route_after_action_agent",
    "route_after_approval",
    "route_after_inspect",
    "action_history",
    "_action_history",
]

_action_history = action_history


def action_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    tools = tutor_action_tools()
    model = get_chat_model().bind_tools(tools)
    history = action_history(state.get("messages") or [])
    response = model.invoke([SystemMessage(content=ACTION_SYSTEM), *history])
    return {"messages": [response]}


async def execute_action_node(state: dict[str, Any]) -> dict[str, Any]:
    pending = state.get("pending_action") or {}
    decision = state.get("action_decision") or {}
    name = pending.get("tool")
    chosen = decision.get("args")
    raw_args = chosen if chosen is not None else pending.get("args")
    args = raw_args if isinstance(raw_args, dict) else {}
    ctx = get_tool_context()
    try:
        if not isinstance(name, str) or not name:
            result: dict[str, Any] = {"ok": False, "error": "Missing action"}
        else:
            result = await execute_study_plan_action(ctx, name, args)
    except ApiError as exc:
        result = {"ok": False, "error": str(exc), "tool": name}
    return {"action_result": result, "pending_action": None}


async def action_outcome_node(state: dict[str, Any]) -> dict[str, Any]:
    result = state.get("action_result")
    decision = state.get("action_decision") or {}
    text: str | None = None
    if isinstance(result, dict):
        if result.get("ok"):
            text = str(result.get("message") or "Done.")
            plan_id = result.get("planId")
            if plan_id:
                text = f"{text} Plan id: {plan_id}."
        else:
            text = str(result.get("error") or "I couldn't complete that.")
    elif decision.get("approved") is False:
        text = "Okay — I won't make that change."
    if text is None:
        return {}
    guard = await guard_output(text, audience="student")
    text = apply_output_guard(text, guard, audience="student")
    out: dict[str, Any] = {
        "messages": [AIMessage(content=text)],
        "pending_action": None,
        "action_decision": None,
    }
    if guard.action != "pass":
        out["decision_meta"] = guard.as_meta()
        out["decision_trace"] = [guard.as_meta()]
    return out
