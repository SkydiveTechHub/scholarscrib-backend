"""Admin Astra: separate graph, owner gate, and frozen action targets."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.core.errors import ApiError
from app.core.security import student_token
from app.services.admin_astra.context import (
    AdminToolContext,
    reset_admin_tool_context,
    set_admin_tool_context,
)
from app.services.admin_astra.execute import execute_admin_action
from app.services.admin_astra.graph import (
    build_admin_graph,
    classify_route,
    pick_domains,
    route_after_classify,
)
from app.services.admin_astra.proposals import resolve_resume_args
from app.services.admin_astra.tools import (
    admin_tool_names,
    ensure_admin_tools,
    tools_for,
)
from app.services.admin_astra.tools.registry import admin_tool_spec
from app.services.rag.tools import tutor_action_tools
from app.services.rag.tools.hitl import decode_proposal


def _state(question: str) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "question": question,
        "route": None,
        "domains": [],
        "decision_meta": {},
        "pending_action": None,
        "action_decision": None,
        "action_result": None,
    }


def test_admin_tools_stay_out_of_the_student_registry():
    ensure_admin_tools()
    student = {tool.name for tool in tutor_action_tools()}
    assert student.isdisjoint(admin_tool_names())
    assert "delete_student" not in student
    assert "search_students" not in student


def test_domain_pack_hides_other_domains_and_owner_tools():
    staff = {tool.name for tool in tools_for(["students"], is_owner=False)}
    assert "search_students" in staff
    assert "set_student_tier" in staff
    assert "delete_student" not in staff
    assert "force_signout_student" not in staff
    assert "list_questions" not in staff
    assert "create_admin" not in staff
    owner = {tool.name for tool in tools_for(["students", "team"], is_owner=True)}
    assert "delete_student" in owner
    assert "create_admin" in owner
    assert "list_questions" not in owner


def test_classify_and_domain_picker_skip_the_model_when_the_wording_is_clear():
    assert classify_route("Hello!") == "chitchat"
    assert classify_route("Please delete this student") == "work"
    assert pick_domains("Please delete this student") == ["students"]
    assert route_after_classify({"route": "chitchat"}) == "chitchat"
    assert route_after_classify({"route": "work"}) == "pick_domain"
    assert "questions" in pick_domains("reindex the questions")
    assert "rag" in pick_domains("reindex the questions")


def test_resume_rejects_a_changed_target_id():
    ensure_admin_tools()
    delete = {"tool": "delete_student", "args": {"userId": "user-1"}, "actionId": "a"}
    with pytest.raises(ApiError):
        resolve_resume_args(delete, {"userId": "user-2"})
    assert resolve_resume_args(delete, {"userId": "user-1"})["userId"] == "user-1"

    tier = {
        "tool": "set_student_tier",
        "args": {
            "userId": "user-1",
            "tier": "PREMIUM",
            "period": "MONTHLY",
            "note": None,
        },
    }
    with pytest.raises(ApiError):
        resolve_resume_args(tier, {"userId": "user-2"})

    sign_out = {
        "tool": "force_signout_student",
        "args": {"userId": "user-1"},
    }
    with pytest.raises(ApiError):
        resolve_resume_args(sign_out, {"userId": "user-2"})


async def test_delete_tool_proposes_without_calling_the_service(monkeypatch):
    called = {"n": 0}

    class Boom:
        def __init__(self, *args, **kwargs):
            called["n"] += 1

        async def process(self):
            called["n"] += 1

    monkeypatch.setattr(
        "app.services.admin_astra.tools.students.DeleteStudentService", Boom
    )
    ensure_admin_tools()
    spec = admin_tool_spec("delete_student")
    assert spec is not None
    raw = await spec.tool.ainvoke(
        {"userId": "user-1", "summary": "Delete Ada. This cannot be undone."}
    )
    proposal = decode_proposal(raw)
    assert proposal is not None
    assert proposal["tool"] == "delete_student"
    assert proposal["args"] == {"userId": "user-1"}
    assert called["n"] == 0


class _Scripted:
    def __init__(self, calls: list[dict]):
        self.calls = calls
        self.bound: list[str] = []
        self._n = 0

    def bind_tools(self, tools):
        self.bound = [tool.name for tool in tools]
        return self

    def invoke(self, messages):
        self._n += 1
        if self._n == 1:
            return AIMessage(content="", tool_calls=self.calls)
        return AIMessage(content="Ada is an SS2 student.")


def _ctx(*, owner: bool) -> AdminToolContext:
    return AdminToolContext(session=object(), admin_id="admin-1", is_owner=owner)


async def test_read_tool_runs_and_does_not_interrupt(monkeypatch):
    called = {"n": 0}

    class FakeSearch:
        def __init__(self, *args, **kwargs):
            pass

        async def process(self):
            called["n"] += 1
            return {
                "students": [{"id": "user-1", "firstName": "Ada"}],
                "pagination": {"total": 1},
            }

    monkeypatch.setattr(
        "app.services.admin_astra.tools.students.SearchStudentsService", FakeSearch
    )
    model = _Scripted(
        [
            {
                "name": "search_students",
                "args": {"query": "Ada"},
                "id": "call-1",
                "type": "tool_call",
            }
        ]
    )
    monkeypatch.setattr("app.services.admin_astra.graph.get_chat_model", lambda: model)
    token = set_admin_tool_context(_ctx(owner=False))
    graph = build_admin_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "read-1"}, "recursion_limit": 40}
    try:
        await graph.ainvoke(_state("Find students named Ada"), config=config)
        snapshot = await graph.aget_state(config)
    finally:
        reset_admin_tool_context(token)
    assert called["n"] == 1
    assert not snapshot.interrupts
    assert "search_students" in model.bound
    assert "delete_student" not in model.bound
    assert "list_questions" not in model.bound


async def _delete_turn(monkeypatch, thread_id: str):
    called = {"n": 0, "actor": None, "user": None}

    class FakeDelete:
        def __init__(self, session, actor_id, user_id):
            called["actor"] = actor_id
            called["user"] = user_id

        async def process(self):
            called["n"] += 1
            return {"ok": True}

    monkeypatch.setattr(
        "app.services.admin_astra.tools.students.DeleteStudentService", FakeDelete
    )
    model = _Scripted(
        [
            {
                "name": "delete_student",
                "args": {
                    "userId": "user-1",
                    "summary": "Delete Ada. This cannot be undone.",
                },
                "id": "call-1",
                "type": "tool_call",
            }
        ]
    )
    monkeypatch.setattr("app.services.admin_astra.graph.get_chat_model", lambda: model)
    token = set_admin_tool_context(_ctx(owner=True))
    graph = build_admin_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    try:
        await graph.ainvoke(_state("Please delete this student"), config=config)
        snapshot = await graph.aget_state(config)
        return graph, config, snapshot, called, token
    except Exception:
        reset_admin_tool_context(token)
        raise


async def test_mutation_interrupts_and_reject_writes_nothing(monkeypatch):
    graph, config, snapshot, called, token = await _delete_turn(monkeypatch, "reject-1")
    try:
        assert snapshot.interrupts
        assert snapshot.interrupts[0].value["tool"] == "delete_student"
        assert called["n"] == 0
        await graph.ainvoke(Command(resume={"approved": False}), config=config)
        assert called["n"] == 0
    finally:
        reset_admin_tool_context(token)


async def test_mutation_approve_calls_the_service_once(monkeypatch):
    graph, config, snapshot, called, token = await _delete_turn(
        monkeypatch, "approve-1"
    )
    try:
        assert snapshot.interrupts
        assert called["n"] == 0
        await graph.ainvoke(Command(resume={"approved": True}), config=config)
        assert called["n"] == 1
        assert called["actor"] == "admin-1"
        assert called["user"] == "user-1"
    finally:
        reset_admin_tool_context(token)


async def test_non_owner_execute_does_not_call_the_service(monkeypatch):
    called = {"n": 0}

    class Boom:
        def __init__(self, *args, **kwargs):
            called["n"] += 1

        async def process(self):
            called["n"] += 1

    monkeypatch.setattr(
        "app.services.admin_astra.tools.students.DeleteStudentService", Boom
    )
    ensure_admin_tools()
    result = await execute_admin_action(
        _ctx(owner=False), "delete_student", {"userId": "user-1"}
    )
    assert result["ok"] is False
    assert result["error"] == "Owner access required"
    assert called["n"] == 0


async def test_approved_delete_records_the_existing_audit_action(monkeypatch):
    user = SimpleNamespace(id="user-1", email="ada@example.com")
    monkeypatch.setattr(
        "app.services.admin.users_repository.by_id", AsyncMock(return_value=user)
    )
    monkeypatch.setattr("app.services.admin.users_repository.remove", AsyncMock())
    audits: list[tuple] = []

    class FakeAudit:
        def __init__(self, session, actor_id, action, entity, summary, entity_id=None):
            audits.append((actor_id, action, entity, entity_id))

        async def process(self):
            return None

    monkeypatch.setattr("app.services.admin.RecordAuditService", FakeAudit)
    ensure_admin_tools()
    result = await execute_admin_action(
        _ctx(owner=True), "delete_student", {"userId": "user-1"}
    )
    assert result["ok"] is True
    assert audits == [("admin-1", "student.delete", "User", "user-1")]


def test_student_session_cannot_open_admin_astra():
    from fastapi.testclient import TestClient

    from app.database.db import db_session
    from main import app

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[db_session] = override_session
    try:
        client = TestClient(app)
        token = student_token({"sub": "student-1"})
        response = client.get(
            "/admin/api/astra/threads",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
        missing = client.get("/admin/api/astra/threads")
        assert missing.status_code == 401
    finally:
        app.dependency_overrides.pop(db_session, None)
