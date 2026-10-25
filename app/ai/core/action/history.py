"""Trim and repair tool-call windows for action-agent LLM turns.

Anthropic (and similar) reject a ``tool_result`` whose matching
``tool_use`` is missing or not adjacent. After ``trim_messages`` cuts the
middle of history, we drop orphaned results so the next model call stays valid.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import trim_messages

_ACTION_HISTORY_LIMIT = 12


def _tool_ids(message: AIMessage) -> set[str]:
    ids: set[str] = set()
    for call in message.tool_calls or []:
        call_id = call.get("id")
        if call_id:
            ids.add(str(call_id))
    content = message.content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                block_id = block.get("id")
                if block_id:
                    ids.add(str(block_id))
    return ids


def _without_open_tool_call(messages: list) -> list:
    """Drop a trailing assistant tool call that never received its results."""
    while messages and (
        isinstance(messages[-1], ToolMessage)
        or (isinstance(messages[-1], AIMessage) and _tool_ids(messages[-1]))
    ):
        messages.pop()
    return messages


def _repair_tool_pairs(window: list) -> list:
    """Drop tool results whose tool_use was trimmed out of the window."""
    repaired: list = []
    pending: set[str] | None = None
    for message in window:
        if isinstance(message, ToolMessage):
            tool_id = str(message.tool_call_id or "")
            if pending and tool_id in pending:
                repaired.append(message)
                pending.discard(tool_id)
                if not pending:
                    pending = None
            continue
        if pending:
            _without_open_tool_call(repaired)
            pending = None
        if isinstance(message, AIMessage) and _tool_ids(message):
            pending = set(_tool_ids(message))
        repaired.append(message)
    if pending:
        _without_open_tool_call(repaired)
    return repaired


def action_history(messages: list, *, limit: int = _ACTION_HISTORY_LIMIT) -> list:
    """Trim like LangGraph's ``trim_messages`` example, then drop broken pairs.

    ``start_on="human"`` and ``end_on=("human", "tool")`` keep the window valid
    at the ends. A tool result whose assistant tool call was cut out of the
    middle is removed afterward, because ``trim_messages`` leaves that result
    in place.
    """
    typed = [
        message
        for message in messages
        if isinstance(message, HumanMessage | AIMessage | ToolMessage)
    ]
    if not typed:
        return []
    trimmed = trim_messages(
        typed,
        max_tokens=limit,
        strategy="last",
        token_counter=len,
        start_on="human",
        end_on=("human", "tool"),
    )
    repaired = _repair_tool_pairs(list(trimmed))
    if repaired:
        return repaired
    return [message for message in typed if isinstance(message, HumanMessage)][-1:]


# Back-compat alias used by older tests / call sites during the move.
_action_history = action_history
