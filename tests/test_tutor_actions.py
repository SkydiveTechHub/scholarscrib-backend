"""Study-plan tutor tools propose first and execute only after approval."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.services.rag.graph import _heuristic_route, build_tutor_graph
from app.services.rag.tools import ensure_tools
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
    raw = await _create_study_plan(**_plan_args())
    proposal = decode_proposal(raw)
    assert proposal is not None
    assert proposal["tool"] == "create_study_plan"
    assert proposal["args"]["subjectIds"] == ["sub-1"]
    assert called["n"] == 0
    assert PROPOSAL_KEY in raw


async def test_create_tool_rejects_incomplete_settings():
    raw = await _create_study_plan(
        **{
            **_plan_args(),
            "weekdayMinutes": 0,
            "weekendMinutes": 0,
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
                    "args": _plan_args(),
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
        assert created == {}

        result = await graph.ainvoke(Command(resume={"approved": True}), config=config)
    finally:
        reset_tool_context(token)

    assert created["student_id"] == "student-1"
    assert created["subjects"] == ["sub-1"]
    assert result["action_result"]["planId"] == "plan-1"
    assert result["action_result"]["ok"] is True
