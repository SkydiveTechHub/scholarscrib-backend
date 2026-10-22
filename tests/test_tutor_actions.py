"""Study-plan tutor tools propose first and execute only after approval."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.services.rag.graph import _heuristic_route, build_tutor_graph
from app.services.rag.service import _settled_grounding
from app.services.rag.tools import ensure_tools
from app.services.rag.tools.agent import _action_history
from app.services.rag.tools.context import (
    TutorToolContext,
    reset_tool_context,
    set_tool_context,
)
from app.services.rag.tools.hitl import PROPOSAL_KEY, decode_proposal
from app.services.rag.tools.study_plan import _create_study_plan


def _plan_args() -> dict:
    return {
        "classLevel": "SS2",
        "term": "FIRST",
        "subjectIds": ["sub-1"],
        "studyDays": [1, 2, 3],
        "weekdayMinutes": 60,
        "weekendMinutes": 0,
    }


def test_settled_grounding_drops_pending_proposal_kind():
    grounded = [
        {
            "kind": "actionProposal",
            "status": "pending",
            "actionId": "act-1",
            "summary": "Create a study plan",
            "tool": "create_study_plan",
        }
    ]
    executed = _settled_grounding(grounded, "act-1", "executed")
    assert executed is not None
    assert executed[0]["status"] == "executed"
    assert "kind" not in executed[0]
    assert executed[0]["summary"] == "Create a study plan"
    assert _settled_grounding(executed, "act-1", "rejected") is None
    other = _settled_grounding(grounded, "act-2", "rejected")
    assert other is None


def test_action_history_drops_tool_results_whose_call_was_cut_off():
    orphan = ToolMessage(content="{}", tool_call_id="toolu_01141AmXc9Tcv3UQjR4kEFZa")
    question = HumanMessage(content="Make me a study plan")
    history = _action_history([orphan, question])
    assert history == [question]

    call = AIMessage(
        content="",
        tool_calls=[{"id": "toolu_cut", "name": "get_study_plan", "args": {}}],
    )
    result = ToolMessage(content="plan", tool_call_id="toolu_cut")
    earlier = HumanMessage(content="What is on my plan?")
    follow_up = HumanMessage(content="Update it")
    kept = _action_history([earlier, call, result, follow_up])
    assert kept == [earlier, call, result, follow_up]
    # A window that would open on the tool result starts on the human instead.
    assert _action_history([call, result, follow_up], limit=2) == [follow_up]

    mismatched = ToolMessage(content="x", tool_call_id="toolu_other")
    repaired = _action_history([earlier, mismatched, follow_up])
    assert repaired == [earlier, follow_up]


_PLAN = (
    "Plan: Create a study plan\n\n"
    "I'll build an SS2 First term plan for Mathematics.\n\n"
    "Nothing changes until you approve."
)


def test_heuristic_routes_study_plan_to_action():
    assert _heuristic_route("Help me create a study plan") == "action"
    assert _heuristic_route("What subjects do you have?") == "sql"


async def test_create_tool_proposes_without_executing(monkeypatch):
    called = {"n": 0}

    class Boom:
        def __init__(self, *args, **kwargs):
            called["n"] += 1

        async def process(self):
            called["n"] += 1

    monkeypatch.setattr(
        "app.services.rag.tools.study_plan.CreateStudyPlanService", Boom
    )
    ensure_tools()
    raw = await _create_study_plan(**_plan_args(), summary=_PLAN)
    proposal = decode_proposal(raw)
    assert proposal is not None
    assert proposal["tool"] == "create_study_plan"
    assert proposal["summary"] == _PLAN
    assert "summary" not in proposal["args"]
    assert proposal["args"]["subjectIds"] == ["sub-1"]
    assert called["n"] == 0
    assert PROPOSAL_KEY in raw


async def test_create_tool_rejects_incomplete_settings():
    raw = await _create_study_plan(
        **{
            **_plan_args(),
            "weekdayMinutes": 0,
            "weekendMinutes": 0,
            "summary": _PLAN,
        }
    )
    assert decode_proposal(raw) is None
    assert "study day" in raw.lower() or "ok" in raw.lower()


class _ScriptedModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_study_plan",
                    "args": {**_plan_args(), "summary": _PLAN},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        )


async def test_interrupt_then_approve_creates_plan(monkeypatch):
    monkeypatch.setattr(
        "app.services.rag.tools.agent.get_chat_model", lambda: _ScriptedModel()
    )
    created: dict = {}

    class FakeCreate:
        def __init__(self, session, student_id, class_level, body, track=None):
            created["student_id"] = student_id
            created["subjects"] = list(body.subjectIds)

        async def process(self):
            return {"planId": "plan-1"}

    monkeypatch.setattr(
        "app.services.rag.tools.study_plan.CreateStudyPlanService", FakeCreate
    )
    ctx = TutorToolContext(
        session=object(),  # type: ignore[arg-type]
        user_id="student-1",
        class_level="SS2",
        track="SCIENCE",
        tier="PREMIUM",
    )
    token = set_tool_context(ctx)
    graph = build_tutor_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "thread-1"}}
    try:
        await graph.ainvoke(
            {
                "messages": [HumanMessage(content="Please create a study plan")],
                "question": "Please create a study plan",
                "documents": [],
                "citations": [],
                "rewrite_count": 0,
                "user_id": "student-1",
                "route": None,
                "summary": "",
                "sql_result": "",
                "long_term_context": "",
                "decision_trace": [],
                "decision_meta": {},
                "skip_rewrite": False,
                "document_scores": [],
                "pending_action": None,
                "action_decision": None,
                "action_result": None,
            },
            config=config,
        )
        snapshot = await graph.aget_state(config)
        assert snapshot.interrupts
        pending = snapshot.interrupts[0].value
        assert pending["tool"] == "create_study_plan"
        assert pending["summary"] == _PLAN
        assert created == {}

        result = await graph.ainvoke(Command(resume={"approved": True}), config=config)
    finally:
        reset_tool_context(token)

    assert created["student_id"] == "student-1"
    assert created["subjects"] == ["sub-1"]
    assert result["action_result"]["planId"] == "plan-1"
    assert result["action_result"]["ok"] is True
