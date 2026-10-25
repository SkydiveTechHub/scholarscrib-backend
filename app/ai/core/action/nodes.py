"""Shared HITL action-subgraph nodes and routers.

Product graphs own ``action_agent`` / ``execute_action`` / ``action_outcome``
(different tools and execute paths). Both wire these inspect/await/route
helpers so interrupt + resume behave the same.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import tools_condition
from langgraph.types import interrupt

from app.ai.core.hitl import decode_proposal
from app.core.ids import cuid


def inspect_action_tools(state: dict[str, Any]) -> dict[str, Any]:
    """Scan recent tool messages for an encoded proposal and park it as pending."""
    pending: dict[str, Any] | None = None
    for message in reversed(state.get("messages") or []):
        if isinstance(message, AIMessage):
            break
        if not isinstance(message, ToolMessage):
            continue
        content = message.content
        text = content if isinstance(content, str) else str(content)
        proposal = decode_proposal(text)
        if proposal is None:
            continue
        pending = {**proposal, "actionId": proposal.get("actionId") or cuid()}
        break
    return {"pending_action": pending}


def route_after_action_agent(state: dict[str, Any]) -> str:
    if tools_condition(state) == "tools":
        return "action_tools"
    return "action_outcome"


def route_after_inspect(state: dict[str, Any]) -> str:
    if state.get("pending_action"):
        return "await_action_approval"
    return "action_agent"


def await_action_approval(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph interrupt: client must resume with approve/reject (+ optional args)."""
    pending = state.get("pending_action")
    if not pending:
        return {}
    decision = interrupt(pending)
    if not isinstance(decision, dict):
        decision = {"approved": False}
    return {"action_decision": decision}


def route_after_approval(state: dict[str, Any]) -> str:
    decision = state.get("action_decision") or {}
    if decision.get("approved"):
        return "execute_action"
    return "action_outcome"
