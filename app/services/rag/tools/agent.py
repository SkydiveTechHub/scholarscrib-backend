"""Action-agent nodes: read tools, then interrupt before study-plan writes."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import tools_condition
from langgraph.types import interrupt

from app.core.errors import ApiError
from app.core.ids import cuid
from app.services.rag.llm import get_chat_model
from app.services.rag.prompts import ACTION_SYSTEM
from app.services.rag.tools import tutor_action_tools
from app.services.rag.tools.context import get_tool_context
from app.services.rag.tools.hitl import decode_proposal
from app.services.rag.tools.study_plan import execute_study_plan_action


def action_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    tools = tutor_action_tools()
    model = get_chat_model().bind_tools(tools)
    history = [
        message
        for message in state.get("messages") or []
        if isinstance(message, HumanMessage | AIMessage | ToolMessage)
    ][-12:]
    response = model.invoke([SystemMessage(content=ACTION_SYSTEM), *history])
    return {"messages": [response]}


def inspect_action_tools(state: dict[str, Any]) -> dict[str, Any]:
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


def action_outcome_node(state: dict[str, Any]) -> dict[str, Any]:
    result = state.get("action_result")
    decision = state.get("action_decision") or {}
    if isinstance(result, dict):
        if result.get("ok"):
            text = str(result.get("message") or "Done.")
            plan_id = result.get("planId")
            if plan_id:
                text = f"{text} Plan id: {plan_id}."
        else:
            text = str(result.get("error") or "I couldn't complete that.")
        return {
            "messages": [AIMessage(content=text)],
            "pending_action": None,
            "action_decision": None,
        }
    if decision.get("approved") is False:
        text = "Okay — I won't make that change."
        return {
            "messages": [AIMessage(content=text)],
            "pending_action": None,
            "action_decision": None,
        }
    return {}
