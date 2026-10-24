"""Astra stream contract: rejoin, tools, editable proposals, study cards."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.core.sse import format_sse
from app.ai.core.stream_events import (
    ToolTracker,
    cursor_after,
    interpret_graph_event,
    is_stale_run,
    partial_from_events,
)
from app.ai.tutor.proposals import fields_for, public_proposal, resume_command
from app.ai.tutor.runs import hub
from app.ai.tutor.ui_blocks import UiStreamFilter, split_ui, validate_ui_block
from app.core.errors import ApiError


def _plan_args() -> dict:
    return {
        "classLevel": "SS2",
        "term": "FIRST",
        "subjectIds": ["sub-1"],
        "studyDays": [1, 2, 3],
        "weekdayMinutes": 60,
        "weekendMinutes": 0,
    }


def test_sse_frame_id_is_monotonic_metadata():
    frame = format_sse("token", {"text": "Hi"}, "3")
    assert frame.startswith("id: 3\n")
    assert "event: token\n" in frame
    assert json.loads(frame.split("data: ", 1)[1]) == {"text": "Hi"}


def test_missing_or_stale_cursor_replays_from_the_start():
    frames = [{"id": "1"}, {"id": "2"}, {"id": "4"}]
    assert cursor_after(None, frames) == 0
    assert cursor_after("nope", frames) == 0
    assert cursor_after("3", frames) == 0
    assert cursor_after("9", frames) == 0
    assert cursor_after("2", frames) == 2


def test_stale_running_row_is_not_treated_as_live():
    fresh = {"status": "running", "beat": datetime.now(UTC).isoformat()}
    assert is_stale_run(fresh) is False
    old = {
        "status": "running",
        "beat": (datetime.now(UTC) - timedelta(seconds=90)).isoformat(),
    }
    assert is_stale_run(old) is True
    assert is_stale_run({"status": "paused"}) is False
    assert is_stale_run({"status": "running"}) is True


def test_tool_events_hide_sql_and_always_close():
    tools = ToolTracker()
    start = interpret_graph_event(
        {
            "event": "on_tool_start",
            "name": "run_readonly_sql",
            "run_id": "r1",
            "data": {"input": {"query": "SELECT secret FROM User"}},
        },
        tools,
    )
    assert start == [
        (
            "tool",
            {
                "id": "tool_1",
                "name": "run_readonly_sql",
                "phase": "running",
                "summary": "Looking up the catalogue…",
            },
        )
    ]
    assert "SELECT" not in json.dumps(start)
    assert "secret" not in json.dumps(start)
    grade = interpret_graph_event(
        {"event": "on_chain_start", "name": "grade_documents", "run_id": "n1"},
        tools,
    )
    assert grade[0] == ("status", {"node": "grade_documents"})
    assert grade[1][1]["summary"] == "Checking lesson notes…"
    assert grade[1][1]["phase"] == "running"
    done = interpret_graph_event(
        {
            "event": "on_tool_end",
            "name": "run_readonly_sql",
            "run_id": "r1",
            "data": {},
        },
        tools,
    )
    assert done[0][1]["phase"] == "done"
    assert done[0][1]["id"] == "tool_1"
    interpret_graph_event(
        {
            "event": "on_chain_end",
            "name": "grade_documents",
            "run_id": "n1",
            "data": {"output": {}},
        },
        tools,
    )
    assert tools.close_remaining("failed") == []


def test_open_tool_is_closed_before_the_turn_ends():
    tools = ToolTracker()
    tools.start("r1", "get_study_plan")
    closed = tools.close_remaining("done")
    assert closed[0]["phase"] == "done"
    assert closed[0]["summary"] == "Looking at your study plan…"
    assert "SELECT" not in closed[0]["summary"]


def test_partial_log_keeps_student_text_only():
    content, ui, tools = partial_from_events(
        [
            {"event": "token", "data": {"text": "Hello"}},
            {
                "event": "tool",
                "data": {
                    "id": "tool_1",
                    "name": "run_readonly_sql",
                    "phase": "running",
                    "summary": "Looking up the catalogue…",
                    "query": "SELECT secret",
                },
            },
            {
                "event": "tool",
                "data": {
                    "id": "tool_1",
                    "name": "run_readonly_sql",
                    "phase": "done",
                    "summary": "Looking up the catalogue…",
                },
            },
            {
                "event": "ui",
                "data": {
                    "id": "ui_1",
                    "component": "worked_example",
                    "props": {"problem": "2+2", "steps": ["4"]},
                },
            },
        ]
    )
    assert content == "Hello"
    assert tools == [
        {
            "id": "tool_1",
            "name": "run_readonly_sql",
            "phase": "done",
            "summary": "Looking up the catalogue…",
        }
    ]
    assert "secret" not in json.dumps(tools)
    assert ui[0]["component"] == "worked_example"


def test_proposal_fields_are_editable_keys_only():
    fields = fields_for("create_study_plan", _plan_args())
    assert fields is not None
    keys = {field["key"] for field in fields}
    assert "weekdayMinutes" in keys
    assert "subjectIds" not in keys
    assert "studyDays" not in keys
    for field in fields:
        assert field["key"] in _plan_args()
    proposal = public_proposal(
        {
            "actionId": "act_1",
            "tool": "delete_study_plan",
            "args": {"planId": "plan-1"},
            "summary": "Archive it",
            "destructive": True,
        },
        "ckpt_1",
    )
    assert "fields" not in proposal
    assert proposal["status"] == "pending"


def test_resume_merges_edits_and_rejects_a_bad_payload():
    pending = {
        "tool": "create_study_plan",
        "args": _plan_args(),
        "fields": fields_for("create_study_plan", _plan_args()),
    }
    edited = resume_command(pending, True, {"weekdayMinutes": 45})
    assert edited["approved"] is True
    assert edited["args"]["weekdayMinutes"] == 45
    assert edited["args"]["subjectIds"] == ["sub-1"]
    assert resume_command(pending, True, None) == {"approved": True}
    assert resume_command(pending, False, {"subjectIds": ["other"]}) == {
        "approved": False
    }
    with pytest.raises(ApiError) as extra:
        resume_command(pending, True, {"planId": "nope"})
    assert extra.value.status_code == 400
    assert extra.value.payload == {"error": "Invalid action payload"}
    with pytest.raises(ApiError):
        resume_command(pending, True, {"subjectIds": ["other"]})
    with pytest.raises(ApiError):
        resume_command(pending, True, {"weekdayMinutes": "45"})
    with pytest.raises(ApiError):
        resume_command(pending, True, {"classLevel": "SS2-EXTRA"})


def test_study_cards_drop_answers_html_and_unknown_components():
    visible, blocks = split_ui(
        "See this.\n\n```ui\n"
        '{"component":"worked_example","props":{"problem":"Find <b>x</b>",'
        '"steps":["Add 2","Divide"]}}\n'
        "```\n\n```ui\n"
        '{"component":"practice_question","props":{"prompt":"2+2",'
        '"choices":["3","4"],"answer":"4"}}\n'
        "```\n\n```ui\n"
        '{"component":"html_blob","props":{"html":"<p>no</p>"}}\n'
        "```"
    )
    assert "```ui" not in visible
    assert visible.startswith("See this.")
    assert len(blocks) == 1
    assert blocks[0]["id"] == "ui_1"
    assert blocks[0]["props"]["problem"] == "Find x"
    assert len(blocks[0]["props"]["steps"]) == 2
    too_many = validate_ui_block(
        {
            "component": "worked_example",
            "props": {"problem": "x", "steps": [str(i) for i in range(9)]},
        }
    )
    assert too_many is None
    question = validate_ui_block(
        {
            "component": "practice_question",
            "props": {"prompt": "Capital of Mali?", "choices": ["Bamako", "Dakar"]},
        }
    )
    assert question is not None
    assert "answer" not in json.dumps(question)
    assert set(question["props"]) <= {"prompt", "choices"}


def test_token_filter_holds_back_the_ui_fence():
    filt = UiStreamFilter()
    assert filt.push("Hello ") == "Hello "
    assert filt.push("```ui\n") == ""
    assert filt.push('{"component":"worked_example"}') == ""
    assert filt.flush_tail() == ""


async def test_run_keeps_going_after_the_subscriber_disconnects():
    hub.reset()
    run = hub.create("student-1", "thread-1", "run-1")

    async def produce() -> None:
        await run.publish("token", {"text": "a"})
        await asyncio.sleep(0.05)
        await run.publish("token", {"text": "b"})
        await run.seal()

    task = asyncio.create_task(produce())
    agen = run.iter_after(0)
    first = await agen.__anext__()
    assert first["data"]["text"] == "a"
    await agen.aclose()
    await task
    assert task.cancelled() is False
    assert [frame["data"]["text"] for frame in run.events] == ["a", "b"]
    replayed = [frame async for frame in run.iter_after(1)]
    assert [frame["id"] for frame in replayed] == ["2"]
    hub.reset()
