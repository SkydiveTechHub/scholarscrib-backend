"""The tutor and the timetable: propose, approve, edit, reject, and report back.

The tutor never builds a schedule. It proposes settings (now including a
timetable), the student approves, and the same service the REST API uses runs
the deterministic planner.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import domain
from app.api.schemas import StudyPlanIn
from app.core.errors import ApiError
from app.services.rag.graph import build_tutor_graph
from app.services.rag.prompts import ACTION_SYSTEM
from app.services.rag.proposals import resolve_resume_args, resume_command
from app.services.rag.tools import ensure_tools
from app.services.rag.tools import study_plan as tool_module
from app.services.rag.tools.context import (
    TutorToolContext,
    reset_tool_context,
    set_tool_context,
)
from app.services.rag.tools.hitl import decode_proposal
from app.services.rag.tools.reads import _compact_plan
from app.services.rag.tools.study_plan import (
    CreateStudyPlanToolArgs,
    _create_study_plan,
    _update_study_plan,
    execute_study_plan_action,
    parse_validated_args,
    unmet_sentence,
)

CARD = "Plan: Maths on Monday\n\nNothing changes until you approve."


def settings(**extra) -> dict:
    return {
        "classLevel": "SS1",
        "term": "FIRST",
        "subjectIds": ["maths", "eng"],
        "studyDays": [1, 2, 3, 4, 6],
        "weekdayMinutes": 60,
        "weekendMinutes": 120,
        **extra,
    }


def custom(*rows: dict) -> dict:
    return {"mode": "CUSTOM", "allocations": list(rows)}


def row(day=1, subject="maths", **extra) -> dict:
    return {"day": day, "subjectId": subject, **extra}


def ctx(tier="PREMIUM") -> TutorToolContext:
    return TutorToolContext(
        session=object(),  # type: ignore[arg-type]
        user_id="student-1",
        class_level="SS1",
        track="SCIENCE",
        tier=tier,
    )


@pytest.fixture(autouse=True)
def tools():
    ensure_tools()


@contextmanager
def in_context(tier="PREMIUM"):
    """Sets the tutor's tool context for the body of an async test, then clears it."""
    token = set_tool_context(ctx(tier))
    try:
        yield
    finally:
        reset_tool_context(token)


# --- invalid proposals never become cards ----


@pytest.mark.parametrize(
    ("timetable", "fragment"),
    [
        (custom(row(day=5)), "rest day"),  # Friday is not a study day
        (custom(row(subject="chem")), "not part of this plan"),
        (custom(row(minutes=60), row(subject="eng")), "Monday"),  # 90 min in 60
        (custom(row(), row()), "more than once"),
        ({"mode": "CUSTOM"}, "at least one subject"),
        ({"mode": "SMART", "allocations": [row()]}, "custom subjects"),
        ({"mode": "SMART", "dayMinutes": [{"day": 5, "minutes": 60}]}, "rest day"),
    ],
)
async def test_an_invalid_timetable_is_refused_with_the_reason(timetable, fragment):
    raw = await _create_study_plan(**settings(timetable=timetable), summary=CARD)
    assert decode_proposal(raw) is None
    assert json.loads(raw)["ok"] is False
    assert fragment in json.loads(raw)["error"]


@pytest.mark.parametrize(
    "timetable",
    [
        custom(row(day=9)),  # no such weekday
        custom(row(minutes=45)),  # not a multiple of 30 minutes... and under the step
        custom(row(strength="MAYBE")),
        {"mode": "MANUAL"},
    ],
)
async def test_a_malformed_timetable_is_refused_by_the_schema(timetable):
    raw = await _create_study_plan(**settings(timetable=timetable), summary=CARD)
    assert decode_proposal(raw) is None
    assert json.loads(raw)["ok"] is False


# --- a valid proposal changes nothing ----


async def test_a_valid_timetable_is_proposed_and_nothing_runs(monkeypatch):
    calls: list[str] = []

    class Boom:
        def __init__(self, *args, **kwargs):
            calls.append("built")

        async def process(self):
            calls.append("ran")

    monkeypatch.setattr(tool_module, "CreateStudyPlanService", Boom)
    monkeypatch.setattr(tool_module, "UpdateStudyPlanSettingsService", Boom)
    timetable = custom(row(minutes=30, strength="PINNED"), row(day=3, subject="eng"))
    raw = await _create_study_plan(**settings(timetable=timetable), summary=CARD)
    proposal = decode_proposal(raw)
    assert proposal is not None
    assert proposal["summary"] == CARD
    assert proposal["args"]["timetable"]["allocations"][0]["strength"] == "PINNED"
    assert "summary" not in proposal["args"]
    assert calls == []


# --- the tutor cannot wipe a timetable by accident ----


async def test_a_null_timetable_from_the_model_is_not_a_reset(monkeypatch):
    raw = await _update_study_plan(
        planId="p1", timetable=None, summary="Change minutes", **settings()
    )
    proposal = decode_proposal(raw)
    assert proposal is not None
    assert "timetable" not in proposal["args"]
    assert "timetable" not in parse_validated_args(
        "update_study_plan", proposal["args"]
    )
    body = StudyPlanIn.model_validate(
        {k: v for k, v in proposal["args"].items() if k != "planId"}
    )
    assert (
        "timetable" not in body.model_fields_set
    )  # the service will keep the saved one


async def test_the_smart_mode_object_is_how_the_tutor_returns_to_smart():
    raw = await _update_study_plan(
        planId="p1", timetable={"mode": "SMART"}, summary="Back to smart", **settings()
    )
    proposal = decode_proposal(raw)
    assert proposal is not None
    assert proposal["args"]["timetable"]["mode"] == "SMART"
    body = StudyPlanIn.model_validate(
        {k: v for k, v in proposal["args"].items() if k != "planId"}
    )
    assert "timetable" in body.model_fields_set  # explicit, so the service resets it


# --- a saved timetable must still fit new settings ----


def saved_plan(owner="student-1", allocations=None):
    return NS(
        id="p1",
        student_id=owner,
        timetable={
            "version": 1,
            "mode": "CUSTOM",
            "dayMinutes": [],
            "allocations": allocations
            or [
                {"day": 3, "subjectId": "maths", "minutes": None, "strength": "PINNED"}
            ],
        },
    )


async def test_an_update_that_breaks_the_saved_timetable_is_refused_before_a_card(
    monkeypatch,
):
    async def by_id(_session, _plan_id):
        return saved_plan()

    monkeypatch.setattr(tool_module.plans_repository, "by_id", by_id)
    with in_context():
        # Wednesday (3) is dropped from the study days, but its subject is kept.
        raw = await _update_study_plan(
            planId="p1", summary="Fewer days", **settings(studyDays=[1, 2, 4, 6])
        )
        assert decode_proposal(raw) is None
        error = json.loads(raw)["error"]
        assert "saved timetable would no longer fit" in error
        assert "Wednesday is a rest day" in error


async def test_an_update_that_brings_a_new_timetable_does_not_check_the_old_one(
    monkeypatch,
):
    async def by_id(_session, _plan_id):
        raise AssertionError("the saved timetable is being replaced; it is not read")

    monkeypatch.setattr(tool_module.plans_repository, "by_id", by_id)
    with in_context():
        raw = await _update_study_plan(
            planId="p1",
            summary="New days",
            timetable=custom(row(day=2)),
            **settings(studyDays=[1, 2, 4, 6]),
        )
        assert decode_proposal(raw) is not None


async def test_an_update_that_keeps_the_saved_timetable_working_is_proposed(
    monkeypatch,
):
    async def by_id(_session, _plan_id):
        return saved_plan()

    monkeypatch.setattr(tool_module.plans_repository, "by_id", by_id)
    with in_context():
        raw = await _update_study_plan(
            planId="p1", summary="More time", **settings(weekdayMinutes=90)
        )
        assert decode_proposal(raw) is not None


async def test_another_students_plan_is_not_read_into_the_proposal(
    monkeypatch,
):
    async def by_id(_session, _plan_id):
        return saved_plan(
            owner="someone-else",
            allocations=[
                {"day": 5, "subjectId": "maths", "minutes": None, "strength": "PINNED"}
            ],
        )

    monkeypatch.setattr(tool_module.plans_repository, "by_id", by_id)
    with in_context():
        raw = await _update_study_plan(planId="p1", summary="Anything", **settings())
        # No leak of the other plan's contents; the service answers 404 on approval.
        assert decode_proposal(raw) is not None


# --- edited proposals are revalidated ----


def pending(timetable=None, tool="create_study_plan", **extra):
    args = settings(**extra)
    if timetable is not None:
        args["timetable"] = timetable
    return {"tool": tool, "args": args, "actionId": "a1", "summary": CARD}


def test_an_edited_value_that_breaks_the_timetable_is_refused():
    two_on_monday = custom(row(), row(subject="eng"))
    with pytest.raises(ApiError) as raised:
        resolve_resume_args(pending(two_on_monday), {"weekdayMinutes": 30})
    assert raised.value.status_code == 400
    assert "Monday" in str(raised.value)


def test_an_edit_that_keeps_the_timetable_valid_is_accepted_and_keeps_it():
    merged = resolve_resume_args(pending(custom(row())), {"weekendMinutes": 90})
    assert merged["weekendMinutes"] == 90
    assert merged["timetable"]["allocations"][0]["subjectId"] == "maths"


def test_the_timetable_itself_cannot_be_rewritten_from_the_card():
    stored = pending(custom(row()))
    with pytest.raises(ApiError):
        resolve_resume_args(stored, {"timetable": custom(row(day=2))})
    # Sending back exactly what was stored is allowed (nothing changes).
    assert resolve_resume_args(stored, {"timetable": stored["args"]["timetable"]})[
        "timetable"
    ]


def test_a_timetable_cannot_be_smuggled_into_a_proposal_that_had_none():
    with pytest.raises(ApiError):
        resolve_resume_args(pending(), {"timetable": custom(row())})


def test_an_edit_cannot_turn_an_unset_timetable_into_a_reset():
    merged = resolve_resume_args(pending(), {"weekdayMinutes": 45})
    assert "timetable" not in merged


def test_a_rejection_drops_any_edit():
    assert resume_command(pending(custom(row())), False, {"weekdayMinutes": 30}) == {
        "approved": False
    }


# --- entitlement ----


async def test_without_the_entitlement_nothing_runs(monkeypatch):
    monkeypatch.setitem(domain.ENTITLEMENTS, "studyPlanner", "STANDARD")
    ran: list[str] = []

    class Boom:
        def __init__(self, *args, **kwargs):
            ran.append("built")

    monkeypatch.setattr(tool_module, "CreateStudyPlanService", Boom)
    args = settings(timetable=custom(row()))
    result = await execute_study_plan_action(ctx("FREEMIUM"), "create_study_plan", args)
    assert result["ok"] is False
    assert result["feature"] == "studyPlanner"
    assert result["requiredTier"] == "STANDARD"
    assert ran == []


# --- approve and execute: the same service as the API ----


class Scripted:
    """A model that proposes one study-plan tool call, then stops."""

    def __init__(self, name: str, args: dict):
        self.name, self.args = name, args

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": self.name,
                    "args": self.args,
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        )


def initial(question: str) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "question": question,
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
    }


async def propose(monkeypatch, name: str, args: dict, tier="PREMIUM"):
    monkeypatch.setattr(
        "app.services.rag.tools.agent.get_chat_model", lambda: Scripted(name, args)
    )
    token = set_tool_context(ctx(tier))
    graph = build_tutor_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    await graph.ainvoke(initial("Please change my study plan"), config=config)
    snapshot = await graph.aget_state(config)
    return graph, config, snapshot, token


class RecordingCreate:
    seen: list = []

    def __init__(self, session, student_id, class_level, body, track=None):
        RecordingCreate.seen.append(body)

    async def process(self):
        return {"planId": "plan-9"}


async def approve(monkeypatch, decision, args=None, tier="PREMIUM", timetable=None):
    RecordingCreate.seen = []
    monkeypatch.setattr(tool_module, "CreateStudyPlanService", RecordingCreate)
    payload = {**settings(), "summary": CARD}
    if timetable is not None:
        payload["timetable"] = timetable
    graph, config, snapshot, token = await propose(
        monkeypatch, "create_study_plan", payload, tier
    )
    try:
        assert snapshot.interrupts, "the tutor must pause for approval"
        assert RecordingCreate.seen == [], "nothing runs before approval"
        result = await graph.ainvoke(Command(resume=decision), config=config)
    finally:
        reset_tool_context(token)
    return result


async def test_approval_runs_the_service_with_the_timetable_and_reports_back(
    monkeypatch,
):
    async def active(_session, _student):
        return NS(unmet=None)

    monkeypatch.setattr(tool_module.plans_repository, "active", active)
    timetable = custom(row(strength="PINNED"), row(day=3, subject="eng", minutes=60))
    result = await approve(monkeypatch, {"approved": True}, timetable=timetable)
    [body] = RecordingCreate.seen
    assert body.timetable is not None
    assert [(a.day, a.subjectId, a.strength) for a in body.timetable.allocations] == [
        (1, "maths", "PINNED"),
        (3, "eng", "PREFERRED"),
    ]
    assert result["action_result"]["ok"] is True
    assert result["action_result"]["message"] == "Created your study plan."
    assert "unmet" not in result["action_result"]


async def test_the_reply_after_approval_says_what_could_not_be_followed(monkeypatch):
    async def active(_session, _student):
        return NS(
            unmet=[
                {"date": "2026-09-15", "subjectId": "eng", "strength": "PINNED"},
                {"date": "2026-09-22", "subjectId": "eng", "strength": "PINNED"},
                {"date": "2026-09-16", "subjectId": "maths", "strength": "PREFERRED"},
            ]
        )

    async def by_id(_session, subject_id):
        return NS(name={"eng": "English", "maths": "Mathematics"}[subject_id])

    monkeypatch.setattr(tool_module.plans_repository, "active", active)
    monkeypatch.setattr(tool_module.subjects_repository, "by_id", by_id)
    result = await approve(monkeypatch, {"approved": True}, timetable=custom(row()))
    outcome = result["action_result"]
    assert outcome["ok"] is True
    assert (
        "Created your study plan. Some of your timetable could not be followed"
        in outcome["message"]
    )
    assert "English (pinned) on Tue 15 Sep, Tue 22 Sep" in outcome["message"]
    assert "Mathematics on Wed 16 Sep" in outcome["message"]
    assert len(outcome["unmet"]) == 3
    assert any("English (pinned)" in m.content for m in result["messages"][-1:])


async def test_failing_to_describe_the_outcome_never_fails_an_approved_change(
    monkeypatch,
):
    async def active(_session, _student):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(tool_module.plans_repository, "active", active)
    result = await approve(monkeypatch, {"approved": True}, timetable=custom(row()))
    assert result["action_result"]["ok"] is True
    assert result["action_result"]["message"] == "Created your study plan."
    assert len(RecordingCreate.seen) == 1


async def test_a_rejection_runs_nothing_and_says_so(monkeypatch):
    result = await approve(monkeypatch, {"approved": False}, timetable=custom(row()))
    assert RecordingCreate.seen == []
    assert result["messages"][-1].content == "Okay — I won't make that change."


async def test_an_entitlement_failure_after_approval_is_reported_and_runs_nothing(
    monkeypatch,
):
    monkeypatch.setitem(domain.ENTITLEMENTS, "studyPlanner", "STANDARD")
    result = await approve(
        monkeypatch, {"approved": True}, tier="FREEMIUM", timetable=custom(row())
    )
    assert RecordingCreate.seen == []
    assert result["action_result"]["ok"] is False
    assert "Standard" in result["messages"][-1].content


async def test_an_error_from_the_service_is_shown_to_the_student(monkeypatch):
    class Refuses:
        def __init__(self, *args, **kwargs):
            pass

        async def process(self):
            raise ApiError(400, "Your saved timetable no longer fits these settings.")

    RecordingCreate.seen = []
    monkeypatch.setattr(tool_module, "CreateStudyPlanService", Refuses)
    graph, config, snapshot, token = await propose(
        monkeypatch,
        "create_study_plan",
        {**settings(), "summary": CARD, "timetable": custom(row())},
    )
    try:
        result = await graph.ainvoke(Command(resume={"approved": True}), config=config)
    finally:
        reset_tool_context(token)
    assert result["action_result"]["ok"] is False
    assert "no longer fits" in result["messages"][-1].content


async def test_an_approved_edit_is_what_runs(monkeypatch):
    # The card allows changing minutes. The edit is revalidated and merged first.
    pending_action = pending(custom(row()))
    args = resolve_resume_args(pending_action, {"weekendMinutes": 90})
    result = await approve(
        monkeypatch, {"approved": True, "args": args}, timetable=custom(row())
    )
    [body] = RecordingCreate.seen
    assert body.weekendMinutes == 90
    assert result["action_result"]["ok"] is True


# --- what the tutor can read ----


def test_the_tutor_sees_the_saved_timetable_and_what_could_not_be_followed():
    page = {
        "today": "2026-09-14",
        "plan": {
            "id": "p1",
            "subjectIds": ["maths"],
            "studyDays": [1],
            "weekdayMinutes": 60,
            "weekendMinutes": 120,
            "timetable": {
                "version": 1,
                "mode": "CUSTOM",
                "dayMinutes": [],
                "allocations": [],
            },
            "unmet": [
                {
                    "date": f"2026-09-{day:02d}",
                    "subjectId": "eng",
                    "strength": "PINNED",
                    "minutes": 30,
                    "reason": "NO_ELIGIBLE_WORK",
                }
                for day in range(15, 30)
            ],
            "items": [],
        },
        "subjects": [],
    }
    plan = _compact_plan(page)["plan"]
    assert plan["timetable"]["mode"] == "CUSTOM"
    assert len(plan["unmetTimetableSessions"]) == 10
    assert set(plan["unmetTimetableSessions"][0]) == {"date", "subjectId", "strength"}


def test_a_smart_or_legacy_plan_reads_as_no_timetable():
    plan = _compact_plan({"today": "2026-09-14", "plan": {"id": "p1", "items": []}})[
        "plan"
    ]
    assert plan["timetable"] is None
    assert plan["unmetTimetableSessions"] == []


# --- wording the student sees ----


def test_unmet_sentence_groups_dedupes_and_marks_pinned():
    text = unmet_sentence(
        [
            {"date": "2026-09-22", "subjectId": "eng", "strength": "PINNED"},
            {"date": "2026-09-15", "subjectId": "eng", "strength": "PINNED"},
            {"date": "2026-09-15", "subjectId": "eng", "strength": "PINNED"},
            {"date": "2026-09-16", "subjectId": "ghost", "strength": "PREFERRED"},
        ],
        {"eng": "English"},
    )
    assert "English (pinned) on Tue 22 Sep, Tue 15 Sep" in text
    assert text.count("Tue 15 Sep") == 1
    assert "A subject on Wed 16 Sep" in text
    assert text.endswith("timetable on the Study Plan page.")


def test_unmet_sentence_keeps_a_long_list_short():
    rows = [
        {"date": "2026-09-15", "subjectId": f"s{n}", "strength": "PREFERRED"}
        for n in range(9)
    ]
    text = unmet_sentence(rows, {f"s{n}": f"Subject {n}" for n in range(9)})
    assert text.count(" on Tue 15 Sep") == 5


# --- instructions the model works from ----


def test_the_prompt_teaches_the_rules_the_tools_enforce():
    for phrase in (
        "Weekdays are numbered 1 (Monday) to 7 (Sunday)",
        "steps of 30 minutes",
        "PREFERRED",
        "PINNED",
        "Never put a subject or time on a rest day",
        "Leave `timetable` out of an update to keep the saved one",
        'send {"mode": "SMART"}',
        "Never send null",
        "Never promise a topic",
        "finished sessions stay",
    ):
        assert phrase in ACTION_SYSTEM, phrase
    # The prompt is sent as-is, so literal braces are fine; confirm it builds a message.
    assert SystemMessage(content=ACTION_SYSTEM).content == ACTION_SYSTEM


def test_the_tool_schema_describes_each_timetable_field_to_the_model():
    schema = CreateStudyPlanToolArgs.model_json_schema()
    defs = schema["$defs"]
    assert "timetable" in schema["properties"]
    assert "1 is Monday" in defs["AllocationIn"]["properties"]["day"]["description"]
    assert "PINNED" in defs["AllocationIn"]["properties"]["strength"]["description"]
    assert "CUSTOM" in defs["TimetableIn"]["properties"]["mode"]["description"]
