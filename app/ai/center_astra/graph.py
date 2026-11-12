"""Center Tutor Astra graph. Tools stay out of the student tutor registry."""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.ai.center_astra.context import get_center_astra_context
from app.ai.center_astra.execute import execute_center_astra_action
from app.ai.center_astra.prompts import (
    ACTION_SYSTEM,
    CHITCHAT_SYSTEM,
    CLASSIFY_PROMPT,
    DOMAIN_PROMPT,
)
from app.ai.center_astra.tools import ensure_center_astra_tools, tools_for
from app.ai.center_astra.tools.registry import DOMAINS
from app.ai.core.action import (
    action_history,
    await_action_approval,
    inspect_action_tools,
    route_after_action_agent,
    route_after_approval,
    route_after_inspect,
)
from app.ai.core.guardrails import (
    apply_output_guard,
    guard_output,
    run_guarded_tools,
)
from app.ai.core.llm import get_chat_model, get_grader_model
from app.ai.core.message_text import visible_assistant_text
from app.core.logger import logger

RECURSION_LIMIT = 40

_GREETINGS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "good morning",
        "good afternoon",
        "good evening",
    }
)

_DOMAIN_WORDS: dict[str, tuple[str, ...]] = {
    "results": (
        "result",
        "score",
        "average",
        "overview",
        "item analysis",
        "hardest",
        "overdue",
        "not started",
        "completion",
        "who struggled",
        "performance",
    ),
    "assignments": (
        "assignment",
        "quiz",
        "draft",
        "publish",
        "retry",
        "revision",
        "past paper",
        "mock",
    ),
    "recommendations": (
        "recommend",
        "recommendation",
        "message student",
        "follow up",
        "nudge",
    ),
    "roster": ("roster", "group", "students in", "members"),
}


class CenterAstraState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    route: str | None
    domains: list[str]
    decision_meta: dict[str, Any]
    pending_action: dict[str, Any] | None
    action_decision: dict[str, Any] | None
    action_result: dict[str, Any] | None


class RouteDecision(BaseModel):
    route: Literal["chitchat", "work"]


class DomainDecision(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=2)


def _normalise(text: str) -> str:
    cleaned = text.lower()
    for mark in "?!.,":
        cleaned = cleaned.replace(mark, " ")
    return " ".join(cleaned.split())


def pick_domains(question: str) -> list[str]:
    text = _normalise(question)
    scored: list[tuple[int, str]] = []
    for domain, words in _DOMAIN_WORDS.items():
        score = sum(1 for word in words if word in text)
        if score:
            scored.append((score, domain))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [domain for _, domain in scored[:2]]


def classify_route(question: str) -> str | None:
    text = _normalise(question)
    if text in _GREETINGS or text.startswith("hi ") or text.startswith("hello "):
        return "chitchat"
    if pick_domains(question):
        return "work"
    return None


def route_after_classify(state: CenterAstraState) -> str:
    if state.get("route") == "chitchat":
        return "chitchat"
    return "pick_domain"


def _known_domains(names: list[str]) -> list[str]:
    picked = [name for name in names if name in DOMAINS]
    return picked[:2]


def _llm_route(question: str) -> str:
    try:
        prompt = CLASSIFY_PROMPT.format(question=question)
        result = (
            get_grader_model()
            .with_structured_output(RouteDecision)
            .invoke([{"role": "user", "content": prompt}])
        )
        if isinstance(result, RouteDecision):
            route = result.route
        else:
            route = getattr(result, "route", "work")
    except Exception:
        logger.exception("center astra classify failed")
        return "work"
    return route if route in {"chitchat", "work"} else "work"


def _llm_domains(question: str) -> list[str]:
    prompt = DOMAIN_PROMPT.format(domains=", ".join(DOMAINS), question=question)
    try:
        result = (
            get_grader_model()
            .with_structured_output(DomainDecision)
            .invoke([{"role": "user", "content": prompt}])
        )
        if isinstance(result, DomainDecision):
            raw = result.domains
        else:
            raw = getattr(result, "domains", [])
    except Exception:
        logger.exception("center astra domain pick failed")
        return []
    if not isinstance(raw, list):
        return []
    return _known_domains([str(item) for item in raw])


async def classify_intent_node(state: CenterAstraState) -> dict[str, Any]:
    question = state.get("question") or ""
    route = classify_route(question)
    source = "heuristic"
    if route is None:
        route = _llm_route(question)
        source = "model"
    meta = {
        "node": "classify_intent",
        "route": route,
        "source": source,
        "confidence": 1.0 if source == "heuristic" else 0.6,
    }
    return {"question": question, "route": route, "decision_meta": meta}


async def pick_domain_node(state: CenterAstraState) -> dict[str, Any]:
    question = state.get("question") or ""
    domains = pick_domains(question)
    source = "heuristic"
    if not domains:
        domains = _llm_domains(question)
        source = "model"
    if not domains:
        domains = ["results"]
        source = "fallback"
    meta = {
        "node": "pick_domain",
        "route": ",".join(domains),
        "source": source,
        "confidence": 1.0 if source == "heuristic" else 0.6,
    }
    return {"domains": domains, "decision_meta": meta}


async def chitchat_node(state: CenterAstraState) -> dict[str, Any]:
    history = action_history(state.get("messages") or [])
    response = get_chat_model().invoke(
        [SystemMessage(content=CHITCHAT_SYSTEM), *history]
    )
    text = visible_assistant_text(getattr(response, "content", ""))
    decision = await guard_output(text, audience="admin")
    if decision.action != "pass":
        response = AIMessage(
            content=apply_output_guard(text, decision, audience="admin")
        )
    return {"messages": [response]}


def action_agent_node(state: CenterAstraState) -> dict[str, Any]:
    ensure_center_astra_tools()
    ctx = get_center_astra_context()
    bound = tools_for(list(state.get("domains") or []), is_owner=ctx.is_owner)
    if not bound:
        return {
            "messages": [
                AIMessage(content="I don't have tools for that area right now.")
            ]
        }
    domains = ", ".join(state.get("domains") or [])
    model = get_chat_model().bind_tools(bound)
    history = action_history(state.get("messages") or [])
    response = model.invoke(
        [SystemMessage(content=ACTION_SYSTEM.format(domains=domains)), *history]
    )
    return {"messages": [response]}


async def action_tools_node(state: CenterAstraState) -> dict[str, Any]:
    ensure_center_astra_tools()
    ctx = get_center_astra_context()
    bound = tools_for(list(state.get("domains") or []), is_owner=ctx.is_owner)
    if not bound:
        return {}
    return await run_guarded_tools(cast(dict[str, Any], state), bound, audience="admin")


async def execute_action_node(state: CenterAstraState) -> dict[str, Any]:
    pending = state.get("pending_action") or {}
    decision = state.get("action_decision") or {}
    name = pending.get("tool")
    chosen = decision.get("args")
    raw_args = chosen if chosen is not None else pending.get("args")
    args = raw_args if isinstance(raw_args, dict) else {}
    ctx = get_center_astra_context()
    if not isinstance(name, str) or not name:
        result: dict[str, Any] = {"ok": False, "error": "Missing action", "tool": name}
    else:
        result = await execute_center_astra_action(ctx, name, args)
    return {"action_result": result, "pending_action": None}


async def action_outcome_node(state: CenterAstraState) -> dict[str, Any]:
    result = state.get("action_result")
    decision = state.get("action_decision") or {}
    text: str | None = None
    if isinstance(result, dict):
        if result.get("ok"):
            text = str(result.get("message") or "Done.")
        else:
            text = str(result.get("error") or "I couldn't complete that.")
    elif decision.get("approved") is False:
        text = "Okay — I won't make that change."
    if text is None:
        return {}
    guard = await guard_output(text, audience="admin")
    text = apply_output_guard(text, guard, audience="admin")
    return {
        "messages": [AIMessage(content=text)],
        "pending_action": None,
        "action_decision": None,
    }


def build_center_astra_graph(checkpointer=None):
    ensure_center_astra_tools()
    graph = StateGraph(CenterAstraState)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("chitchat", chitchat_node)
    graph.add_node("pick_domain", pick_domain_node)
    graph.add_node("action_agent", action_agent_node)
    graph.add_node("action_tools", action_tools_node)
    graph.add_node("inspect_action_tools", cast(Any, inspect_action_tools))
    graph.add_node("await_action_approval", cast(Any, await_action_approval))
    graph.add_node("execute_action", execute_action_node)
    graph.add_node("action_outcome", action_outcome_node)

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges("classify_intent", route_after_classify)
    graph.add_edge("chitchat", END)
    graph.add_edge("pick_domain", "action_agent")
    graph.add_conditional_edges("action_agent", route_after_action_agent)
    graph.add_edge("action_tools", "inspect_action_tools")
    graph.add_conditional_edges("inspect_action_tools", route_after_inspect)
    graph.add_conditional_edges("await_action_approval", route_after_approval)
    graph.add_edge("execute_action", "action_outcome")
    graph.add_edge("action_outcome", END)
    return graph.compile(checkpointer=checkpointer)


def get_center_astra_graph(checkpointer=None):
    return build_center_astra_graph(checkpointer=checkpointer)
