"""M6: Tutor Astra tools stay scoped; token pool refuses at zero (AI-3/AI-5)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.ai.center_astra.context import (
    CenterAstraContext,
    reset_center_astra_context,
    set_center_astra_context,
)
from app.ai.center_astra.execute import execute_center_astra_action, outcome_message
from app.ai.center_astra.graph import (
    action_outcome_node,
    classify_route,
    pick_domains,
)
from app.ai.center_astra.proposals import resolve_resume_args
from app.ai.center_astra.tools import (
    center_astra_tool_names,
    ensure_center_astra_tools,
    tools_for,
)
from app.ai.center_astra.tools.reads import refuse_unscoped
from app.ai.core.hitl import decode_proposal
from app.ai.tutor.tools import tutor_action_tools
from app.api.deps import TutorPrincipal, require_tutor
from app.core.errors import ApiError
from app.core.security import student_token, tutor_token
from app.database.db import db_session
from app.domain import CENTER_TOKEN_PACKS
from app.services.centers.tokens import (
    deduct_turn_tokens,
    list_token_packs,
    token_pack_by_code,
)
from main import app


def _tutor_principal() -> TutorPrincipal:
    return TutorPrincipal(
        id="tutor-1",
        email="tutor@example.com",
        device_id="dev-1",
        state="Lagos",
        first_name="Tolu",
        last_name="Ade",
        image=None,
        has_password=True,
        issued_at=None,
        email_verified=None,
    )


@pytest.fixture
def client():
    session = AsyncMock()

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(db_session, None)
        app.dependency_overrides.pop(require_tutor, None)


def test_center_tools_stay_out_of_student_and_admin_registries():
    ensure_center_astra_tools()
    student = {tool.name for tool in tutor_action_tools()}
    center = center_astra_tool_names()
    assert student.isdisjoint(center)
    assert "draft_assignment" in center
    assert "draft_recommendation" in center
    assert "assignment_overview" in center
    assert "get_student_mastery" in center
    assert "delete_student" not in center
    assert "search_students" not in center


def test_domain_pack_surfaces_results_and_drafts():
    names = {tool.name for tool in tools_for(["results", "assignments"], is_owner=True)}
    assert "assignment_overview" in names
    assert "draft_assignment" in names
    assert "draft_recommendation" not in names
    recs = {tool.name for tool in tools_for(["recommendations"], is_owner=False)}
    assert "draft_recommendation" in recs


def test_pick_domains_for_results_and_assignments():
    assert "results" in pick_domains("Who struggled on the assignment overview?")
    assert "assignments" in pick_domains("Draft a quiz assignment for SS2")
    assert classify_route("hello") == "chitchat"


def test_outcome_messages():
    assert outcome_message("draft_assignment", {}, {}) == (
        "Created that assignment as a draft."
    )
    assert (
        outcome_message("draft_recommendation", {}, {"delivered": 3})
        == "Sent that recommendation to 3 students."
    )


def test_refuse_unscoped_mastery_raises_privacy_code():
    with pytest.raises(ApiError) as exc:
        refuse_unscoped("mastery")
    assert exc.value.status_code == 403
    assert exc.value.payload["code"] == "CENTER_PRIVACY_X1"


@pytest.mark.asyncio
async def test_get_student_mastery_tool_fails_closed():
    ensure_center_astra_tools()
    from app.ai.center_astra.tools.registry import center_astra_tool_spec

    spec = center_astra_tool_spec("get_student_mastery")
    assert spec is not None
    ctx = CenterAstraContext(
        session=AsyncMock(),
        center_id="center-1",
        tutor_id="tutor-1",
        is_owner=True,
        staff_role="OWNER",
    )
    token = set_center_astra_context(ctx)
    try:
        raw = await spec.tool.ainvoke({"studentId": "student-1"})
    finally:
        reset_center_astra_context(token)
    assert "CENTER_PRIVACY_X1" in raw or "assignment-scoped" in raw


@pytest.mark.asyncio
async def test_draft_assignment_tool_only_proposes():
    ensure_center_astra_tools()
    from app.ai.center_astra.tools.registry import center_astra_tool_spec

    spec = center_astra_tool_spec("draft_assignment")
    assert spec is not None and spec.mutating
    ctx = CenterAstraContext(
        session=AsyncMock(),
        center_id="center-1",
        tutor_id="tutor-1",
        is_owner=True,
        staff_role="OWNER",
    )
    token = set_center_astra_context(ctx)
    try:
        raw = await spec.tool.ainvoke(
            {
                "title": "Retry quiz",
                "taskType": "TOPIC_QUIZ",
                "targets": [{"kind": "CENTER"}],
                "summary": "Create a draft retry quiz for the whole center.",
            }
        )
    finally:
        reset_center_astra_context(token)
    pending = decode_proposal(raw)
    assert pending is not None
    assert pending["tool"] == "draft_assignment"
    assert pending["args"]["title"] == "Retry quiz"


def test_resolve_resume_keeps_assignment_shape():
    pending = {
        "tool": "draft_assignment",
        "args": {
            "title": "Retry quiz",
            "taskType": "TOPIC_QUIZ",
            "targets": [{"kind": "CENTER"}],
            "generate": False,
            "opensAt": None,
            "dueAt": None,
            "settings": None,
            "filters": None,
        },
    }
    merged = resolve_resume_args(pending, {"title": "Retry quiz v2"})
    assert merged["title"] == "Retry quiz v2"
    assert merged["taskType"] == "TOPIC_QUIZ"


@pytest.mark.asyncio
async def test_outcome_speaks_only_after_approval(monkeypatch):
    async def pass_guard(text, *, audience="admin"):
        from app.ai.core.guardrails import GuardDecision

        return GuardDecision(action="pass", side="output", source="test")

    monkeypatch.setattr("app.ai.center_astra.graph.guard_output", pass_guard)
    assert await action_outcome_node({}) == {}
    rejected = await action_outcome_node({"action_decision": {"approved": False}})
    assert rejected["messages"][0].content == "Okay — I won't make that change."


@pytest.mark.asyncio
async def test_token_deduct_refuses_at_zero(monkeypatch):
    session = AsyncMock()
    pool = SimpleNamespace(balance=0, center_id="c1", updated_at=None)

    async def for_update(_s, _cid):
        return pool

    monkeypatch.setattr(
        "app.services.centers.tokens.center_token_pools_repository.for_center_for_update",
        for_update,
    )
    monkeypatch.setattr(
        "app.services.centers.tokens.ensure_token_pool",
        AsyncMock(return_value=pool),
    )
    with pytest.raises(ApiError) as exc:
        await deduct_turn_tokens(session, "c1", actor_id="tutor-1")
    assert exc.value.status_code == 402
    assert exc.value.payload["code"] == "CENTER_ASTRA_TOKENS_EMPTY"
    assert "topUpPath" in exc.value.payload


def test_token_pack_catalog_is_reasonable():
    packs = list_token_packs()
    assert len(packs) >= 2
    assert token_pack_by_code("astra_pack_100") is not None
    assert all(int(p["tokens"]) > 0 for p in CENTER_TOKEN_PACKS)
    assert all(int(p["amount_kobo"]) > 0 for p in CENTER_TOKEN_PACKS)


def test_astra_routes_mounted_under_center_api():
    from app.api.center.astra import router as astra_router

    paths = {getattr(r, "path", "") for r in astra_router.routes}
    assert "/centers/{center_id}/astra/threads" in paths
    assert "/centers/{center_id}/astra/threads/{thread_id}/messages/stream" in paths
    assert "/centers/{center_id}/astra/threads/{thread_id}/actions/resume" in paths
    # Must not collide with student /api/tutor
    assert all(not p.startswith("/tutor") for p in paths)


def test_tutor_token_still_cannot_use_student_astra(client, monkeypatch):
    token = tutor_token(
        {"sub": "tutor-1", "realm": "tutor", "role": "TUTOR", "deviceId": "dev-1"}
    )
    monkeypatch.setattr(
        "app.api.deps.users_repository.by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                id="tutor-1",
                email="tutor@example.com",
                role="TUTOR",
                first_name="Tolu",
                last_name="Ade",
                state="Lagos",
                image=None,
                is_active=True,
                sessions_valid_from=None,
                password_hash="hashed",
                email_verified=None,
            )
        ),
    )
    response = client.get(
        "/api/tutor/threads",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_student_token_cannot_open_center_astra(client, monkeypatch):
    token = student_token(
        {
            "sub": "student-1",
            "profile": {"role": "STUDENT", "tier": "FREEMIUM"},
            "deviceId": "dev-s",
        }
    )
    monkeypatch.setattr(
        "app.api.deps.users_repository.by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                id="student-1",
                email="s@example.com",
                role="STUDENT",
                is_active=True,
                sessions_valid_from=None,
                password_hash="x",
                first_name="Ada",
                last_name="O",
                state="Lagos",
                image=None,
                email_verified=None,
                tier="FREEMIUM",
                class_level="SS1",
                track="SCIENCE",
            )
        ),
    )
    response = client.get(
        "/center/api/centers/center-1/astra/threads",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_execute_unknown_action():
    ctx = CenterAstraContext(
        session=AsyncMock(),
        center_id="c1",
        tutor_id="t1",
        is_owner=True,
        staff_role="OWNER",
    )
    result = await execute_center_astra_action(ctx, "nope", {})
    assert result["ok"] is False
