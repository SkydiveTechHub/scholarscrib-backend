from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.timeutil import lagos_day_start
from app.services.learning import session_store
from app.services.learning.mastery import (
    TopicState,
    evidence_level,
    mean_retrievability,
    topic_retention,
)
from app.services.learning.session import (
    DayEvent,
    SessionInputs,
    compose_session,
    drop_inactive,
    exam_weight,
    item_is_done,
    mark_done,
    reconcile_plan,
    retrieval_priority,
    summarise,
)

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def make_state(
    topic_id,
    mastery=0,
    confidence=0.0,
    observations=0,
    retention=None,
    last_days=None,
    available=True,
    acc=None,
):
    return TopicState(
        topic_id=topic_id,
        mastery=mastery,
        confidence=confidence,
        retention=retention,
        observations=observations,
        last_effort_at=None if last_days is None else NOW - timedelta(days=last_days),
        available=available,
        acc_observations=observations if acc is None else acc,
    )


def test_lagos_day_start_is_previous_utc_evening():
    # Lagos is UTC+1 with no DST: its midnight is 23:00 UTC the day before.
    assert lagos_day_start("2026-10-12") == datetime(2026, 10, 11, 23, 0, tzinfo=UTC)


def test_evidence_level_bands():
    assert evidence_level(make_state("a")) == "NONE"
    assert evidence_level(make_state("a", observations=2, confidence=0.2)) == "THIN"
    assert evidence_level(make_state("a", observations=8, confidence=0.6)) == "ENOUGH"


def test_topic_retention_prefers_cards_with_enough_reviewed_cards():
    state = make_state("a", observations=5, confidence=0.5, retention=0.7)
    assert topic_retention(state, (0.5, 3)) == (0.5, "CARDS")
    assert topic_retention(state, (0.5, 2)) == (0.7, "ESTIMATE")
    assert topic_retention(state, None) == (0.7, "ESTIMATE")
    assert topic_retention(make_state("b"), None) == (None, "ESTIMATE")


def test_mean_retrievability_uses_scheduler_curve_and_skips_unreviewed():
    rows = [
        ("t1", 10.0, NOW - timedelta(days=10)),  # (1 + 19/81)^-0.5 == 0.9
        ("t1", 0.0, NOW - timedelta(days=1)),  # zero stability: skipped
        ("t1", 5.0, None),  # never reviewed: skipped
        (None, 5.0, NOW),  # no topic: skipped
    ]
    result = mean_retrievability(rows, NOW)
    assert set(result) == {"t1"}
    mean, count = result["t1"]
    assert count == 1
    assert abs(mean - 0.9) < 1e-9


def make_topic(
    topic_id,
    subject="s1",
    order=0,
    waec=0.0,
    jamb=0.0,
    minutes=15,
    prereq=None,
    title=None,
):
    return SimpleNamespace(
        id=topic_id,
        subject_id=subject,
        title=title or topic_id.title(),
        slug=topic_id,
        order_index=order,
        estimated_minutes=minutes,
        waec_weight=waec,
        jamb_weight=jamb,
        prerequisite_topic_id=prereq,
    )


def make_inputs(topics, states, **overrides):
    base = dict(
        topics=topics,
        states={state.topic_id: state for state in states},
        leverage={},
        dependents={},
        due_cards={},
        card_retention={},
        cadence_due=set(),
        plan_items=[],
        continue_lesson=None,
        recent={},
        now=NOW,
    )
    base.update(overrides)
    return SessionInputs(**base)


def plan_item(
    item_id="p1", topic_id="a", activity="LESSON", minutes=25, status="PENDING"
):
    return {
        "id": item_id,
        "subjectId": "s1",
        "topicId": topic_id,
        "activityType": activity,
        "durationMinutes": minutes,
        "status": status,
    }


def test_exam_weight_adds_both_boards_and_tolerates_none():
    assert exam_weight(make_topic("a", waec=2.0, jamb=3.0)) == 5.0
    assert exam_weight(SimpleNamespace(waec_weight=None, jamb_weight=None)) == 0


def test_a_due_topic_never_has_zero_priority():
    # Due only for flashcards, retention still above the decay line: decay is 0.
    assert retrieval_priority(0.0, 0.0, 0, 0) > 0
    assert retrieval_priority(0.35, 0.0, 0, 0) > retrieval_priority(0.0, 0.0, 0, 0)


def test_session_order_is_retrieval_then_practice_then_new():
    topics = [
        make_topic("a", order=1),
        make_topic("b", order=2),
        make_topic("c", order=3),
    ]
    states = [
        make_state(
            "a",
            mastery=80,
            confidence=0.6,
            observations=10,
            retention=0.6,
            last_days=20,
        ),
        make_state(
            "b", mastery=30, confidence=0.6, observations=8, retention=0.95, last_days=3
        ),
        make_state("c"),
    ]
    items = compose_session(
        make_inputs(
            topics,
            states,
            due_cards={"a": 4},
            recent={"b": [False, False, True, False]},
        )
    )
    assert [item["kind"] for item in items] == ["RETRIEVE", "PRACTICE", "LEARN"]
    assert [item["topicId"] for item in items] == ["a", "b", "c"]
    assert [item["minutes"] for item in items] == [3, 8, 9]
    assert items[1]["reason"] == "1 of your last 4 answers here were right"
    assert all(item["source"] == "ENGINE" and item["doneAt"] is None for item in items)


def test_card_only_due_topic_is_still_recommended():
    topics = [make_topic("d")]
    states = [
        make_state(
            "d", mastery=75, confidence=0.6, observations=10, retention=0.9, last_days=5
        )
    ]
    items = compose_session(make_inputs(topics, states, due_cards={"d": 2}))
    assert len(items) == 1
    assert items[0]["kind"] == "RETRIEVE"
    assert items[0]["reason"] == "2 flashcards due"
    assert items[0]["dueCards"] == 2
    assert items[0]["retentionSource"] == "ESTIMATE"


def test_retrieval_reason_names_card_retention_only_when_it_is_card_based():
    topics = [make_topic("a")]
    states = [
        make_state(
            "a",
            mastery=75,
            confidence=0.6,
            observations=10,
            retention=0.9,
            last_days=20,
        )
    ]
    cards = compose_session(make_inputs(topics, states, card_retention={"a": (0.6, 4)}))
    assert cards[0]["reason"] == "Retention about 60% — last studied 3 weeks ago"
    assert cards[0]["retentionSource"] == "CARDS"
    estimate = compose_session(
        make_inputs(
            topics,
            [
                make_state(
                    "a",
                    mastery=75,
                    confidence=0.6,
                    observations=10,
                    retention=0.6,
                    last_days=20,
                )
            ],
        )
    )
    assert estimate[0]["reason"] == "Probably fading — last studied 3 weeks ago"
    assert estimate[0]["retentionSource"] == "ESTIMATE"


def test_thin_evidence_reason_has_no_percentage():
    topics = [make_topic("b")]
    states = [make_state("b", mastery=20, confidence=0.2, observations=2, last_days=1)]
    items = compose_session(make_inputs(topics, states))
    assert items[0]["kind"] == "PRACTICE"
    assert items[0]["evidenceLevel"] == "THIN"
    assert items[0]["reason"] == "2 questions answered so far"
    assert "%" not in items[0]["reason"]


def test_practice_reason_adds_unlocks_and_exam_weight():
    topics = [make_topic("b", waec=2.0, jamb=3.0)]
    states = [make_state("b", mastery=30, confidence=0.6, observations=8, last_days=1)]
    items = compose_session(
        make_inputs(
            topics,
            states,
            leverage={"b": 0.5},
            dependents={"b": 3},
            recent={"b": [True, False, False]},
        )
    )
    assert items[0]["reason"] == (
        "1 of your last 3 answers here were right · unlocks 3 topics · ~5 exam weight"
    )


def test_weak_topic_beats_developing_topic_at_similar_value():
    topics = [make_topic("dev", order=1), make_topic("weak", order=2)]
    states = [
        make_state("dev", mastery=55, confidence=0.6, observations=8, last_days=1),
        make_state("weak", mastery=45, confidence=0.6, observations=8, last_days=1),
    ]
    items = compose_session(make_inputs(topics, states))
    assert items[0]["topicId"] == "weak"


def test_continue_lesson_fills_the_new_slot():
    topics = [make_topic("c", minutes=12), make_topic("z", order=5)]
    states = [make_state("c", observations=1, confidence=0.1, acc=0), make_state("z")]
    items = compose_session(
        make_inputs(topics, states, continue_lesson=("c", "lesson-9"))
    )
    new = [item for item in items if item["kind"] in {"CONTINUE", "LEARN"}]
    assert [item["kind"] for item in new] == ["CONTINUE"]
    assert new[0]["lessonId"] == "lesson-9"
    assert new[0]["reason"] == "Continue where you left off"


def test_learn_reason_cites_a_solid_prerequisite():
    topics = [
        make_topic("p", order=1, title="Fractions"),
        make_topic("n", order=2, prereq="p", title="Ratios"),
    ]
    states = [
        make_state(
            "p",
            mastery=78,
            confidence=0.7,
            observations=12,
            retention=0.95,
            last_days=2,
        ),
        make_state("n"),
    ]
    items = compose_session(make_inputs(topics, states))
    assert items[0]["kind"] == "LEARN"
    assert items[0]["topicId"] == "n"
    assert items[0]["reason"] == "Fractions is solid (78%) — this is the next step"


def test_learn_prefers_subjects_the_student_already_studies():
    topics = [
        make_topic("x1", subject="s-new", order=0),
        make_topic("y1", subject="s-old", order=5),
        make_topic("y0", subject="s-old", order=1),
    ]
    states = [
        make_state("x1"),
        make_state(
            "y0",
            mastery=90,
            confidence=0.8,
            observations=15,
            retention=0.95,
            last_days=2,
        ),
        make_state("y1"),
    ]
    items = compose_session(make_inputs(topics, states))
    assert items[0]["topicId"] == "y1"


def test_locked_topics_are_never_offered():
    topics = [make_topic("locked")]
    states = [make_state("locked", available=False)]
    assert compose_session(make_inputs(topics, states)) == []


def test_empty_when_nothing_to_do():
    topics = [make_topic("a")]
    states = [
        make_state(
            "a",
            mastery=90,
            confidence=0.8,
            observations=15,
            retention=0.95,
            last_days=2,
        )
    ]
    assert compose_session(make_inputs(topics, states)) == []


def test_a_topic_appears_once_even_when_it_qualifies_for_two_slots():
    topics = [make_topic("x")]
    states = [
        make_state(
            "x", mastery=30, confidence=0.6, observations=8, retention=0.5, last_days=10
        )
    ]
    items = compose_session(make_inputs(topics, states))
    assert [item["kind"] for item in items] == ["RETRIEVE"]


def test_pending_plan_item_that_fills_the_budget_suppresses_engine_items():
    topics = [make_topic("a"), make_topic("b", order=2)]
    states = [
        make_state("a", observations=1, confidence=0.1),
        make_state("b", mastery=30, confidence=0.6, observations=8, last_days=1),
    ]
    items = compose_session(
        make_inputs(topics, states, plan_items=[plan_item(minutes=25)])
    )
    assert len(items) == 1
    assert items[0]["id"] == "plan:p1"
    assert items[0]["source"] == "PLAN"
    assert items[0]["kind"] == "LEARN"
    assert items[0]["planItemId"] == "p1"
    assert items[0]["reason"] == "From your study plan"


def test_completed_plan_items_do_not_consume_budget():
    topics = [make_topic("a"), make_topic("b", order=2)]
    states = [
        make_state("a", observations=1, confidence=0.1),
        make_state("b", mastery=30, confidence=0.6, observations=8, last_days=1),
    ]
    items = compose_session(
        make_inputs(
            topics, states, plan_items=[plan_item(minutes=25, status="COMPLETED")]
        )
    )
    assert [item["source"] for item in items] == ["PLAN", "ENGINE"]
    assert items[1]["topicId"] == "b"


def test_topicless_plan_items_get_a_readable_title():
    items = compose_session(
        make_inputs(
            [],
            [],
            plan_items=[
                {
                    "id": "m1",
                    "subjectId": "s1",
                    "topicId": None,
                    "activityType": "MOCK_EXAM",
                    "durationMinutes": 60,
                    "status": "PENDING",
                }
            ],
        )
    )
    assert items[0]["kind"] == "MOCK"
    assert items[0]["title"] == "Mock exam"
    assert items[0]["topicId"] is None
    assert items[0]["subjectId"] == "s1"


def test_session_never_exceeds_four_items():
    plans = [plan_item(item_id=f"p{n}", minutes=5) for n in range(6)]
    topics = [make_topic("a")]
    items = compose_session(make_inputs(topics, [make_state("a")], plan_items=plans))
    assert len(items) == 4


def day_events(**counts):
    return [DayEvent("b", kind) for kind, count in counts.items() for _ in range(count)]


def engine_item(kind, due_cards=0):
    return {
        "id": f"{kind.lower()}:b",
        "kind": kind,
        "source": "ENGINE",
        "topicId": "b",
        "subjectId": "s1",
        "title": "B",
        "slug": "b",
        "minutes": 8,
        "reason": "r",
        "doneAt": None,
        "lessonId": None,
        "dueCards": due_cards,
        "planItemId": None,
        "activityType": None,
    }


def test_practice_is_done_after_five_questions():
    item = engine_item("PRACTICE")
    assert item_is_done(item, day_events(QUESTION_ANSWERED=4), {}) is False
    assert item_is_done(item, day_events(QUESTION_ANSWERED=5), {}) is True


def test_events_for_other_topics_do_not_count():
    other = [DayEvent("zzz", "QUESTION_ANSWERED")] * 9
    assert item_is_done(engine_item("PRACTICE"), other, {}) is False


def test_retrieve_counts_cards_and_questions_and_respects_due_cards():
    item = engine_item("RETRIEVE", due_cards=3)
    assert (
        item_is_done(item, day_events(QUESTION_ANSWERED=2, CARD_REVIEWED=1), {}) is True
    )
    assert item_is_done(item, day_events(CARD_REVIEWED=2), {}) is False
    no_cards = engine_item("RETRIEVE", due_cards=0)
    assert item_is_done(no_cards, day_events(QUESTION_ANSWERED=4), {}) is False
    assert item_is_done(no_cards, day_events(QUESTION_ANSWERED=5), {}) is True


def test_lessons_are_done_after_one_checkpointed_block():
    for kind in ("LEARN", "CONTINUE"):
        item = engine_item(kind)
        assert item_is_done(item, [], {}) is False
        assert item_is_done(item, day_events(LESSON_BLOCK_COMPLETED=1), {}) is True


def test_plan_items_follow_the_plan_status():
    item = {**engine_item("LEARN"), "source": "PLAN", "planItemId": "p1"}
    assert item_is_done(item, [], {"p1": "PENDING"}) is False
    assert item_is_done(item, [], {"p1": "COMPLETED"}) is True


def test_mark_done_stamps_once_and_never_clears():
    items = [engine_item("PRACTICE")]
    events = day_events(QUESTION_ANSWERED=5)
    assert mark_done(items, events, {}, NOW) is True
    stamp = items[0]["doneAt"]
    assert stamp == NOW.isoformat()
    later = NOW + timedelta(hours=2)
    assert mark_done(items, events, {}, later) is False
    assert items[0]["doneAt"] == stamp
    # Even if the evidence vanishes, a done item stays done.
    assert mark_done(items, [], {}, later) is False
    assert items[0]["doneAt"] == stamp


def test_drop_inactive_removes_stored_items():
    keep = engine_item("PRACTICE")
    gone = {**engine_item("LEARN"), "id": "learn:q", "subjectId": "s-off"}
    mock = {**engine_item("MOCK"), "id": "plan:m", "subjectId": None, "topicId": None}
    assert drop_inactive([keep, gone, mock], {"s1"}) == [keep, mock]


def test_summarise_counts_done_and_minutes():
    done = {**engine_item("PRACTICE"), "doneAt": NOW.isoformat()}
    open_item = {**engine_item("LEARN"), "minutes": 7}
    summary = summarise([done, open_item])
    assert summary["total"] == 2
    assert summary["done"] == 1
    assert summary["minutes"] == 15
    assert summary["items"] == [done, open_item]
    assert summarise([]) == {"minutes": 0, "done": 0, "total": 0, "items": []}


class FakeSession:
    def __init__(self):
        self.flushed = 0

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def flush(self):
        self.flushed += 1


@pytest.fixture
def store(monkeypatch):
    state = SimpleNamespace(row=None, events=[], added=[], fail_add=False)

    async def for_day(_session, _student, _day):
        return state.row

    async def add(_session, entity, flush=False):
        if state.fail_add:
            raise RuntimeError("unique violation")
        state.added.append(entity)
        return entity

    async def since(_session, _student, _moment):
        return state.events

    monkeypatch.setattr(session_store.daily_sessions_repository, "for_day", for_day)
    monkeypatch.setattr(session_store.daily_sessions_repository, "add", add)
    monkeypatch.setattr(session_store.learning_events_repository, "since", since)
    return state


def lesson_event(topic_id, kind):
    return SimpleNamespace(topic_id=topic_id, kind=kind)


async def never_compose():
    raise AssertionError("a stored session must not be recomposed")


async def test_first_load_composes_and_stores(store):
    async def compose():
        return [engine_item("PRACTICE")]

    result = await session_store.todays_session(
        FakeSession(), "stu", "2026-10-12", compose=compose, plan_status={}, now=NOW
    )
    assert result["total"] == 1 and result["done"] == 0
    assert len(store.added) == 1
    assert store.added[0].day == "2026-10-12"
    assert store.added[0].student_id == "stu"
    assert store.added[0].items[0]["id"] == "practice:b"


async def test_second_load_returns_the_stored_list_unchanged(store):
    stored = [engine_item("PRACTICE"), {**engine_item("LEARN"), "id": "learn:z"}]
    store.row = SimpleNamespace(items=stored)
    result = await session_store.todays_session(
        FakeSession(),
        "stu",
        "2026-10-12",
        compose=never_compose,
        plan_status={},
        now=NOW,
    )
    assert [item["id"] for item in result["items"]] == ["practice:b", "learn:z"]
    assert store.added == []


async def test_empty_composition_is_not_stored(store):
    async def compose():
        return []

    result = await session_store.todays_session(
        FakeSession(), "stu", "2026-10-12", compose=compose, plan_status={}, now=NOW
    )
    assert result == {"minutes": 0, "done": 0, "total": 0, "items": []}
    assert store.added == []


async def test_done_is_persisted_back_onto_the_stored_row(store):
    store.row = SimpleNamespace(items=[engine_item("PRACTICE")])
    store.events = [lesson_event("b", "QUESTION_ANSWERED")] * 5
    result = await session_store.todays_session(
        FakeSession(),
        "stu",
        "2026-10-12",
        compose=never_compose,
        plan_status={},
        now=NOW,
    )
    assert result["done"] == 1
    assert store.row.items[0]["doneAt"] == NOW.isoformat()


async def test_save_failure_still_returns_session(store):
    store.fail_add = True

    async def compose():
        return [engine_item("PRACTICE")]

    result = await session_store.todays_session(
        FakeSession(), "stu", "2026-10-12", compose=compose, plan_status={}, now=NOW
    )
    assert result["total"] == 1


def plan_session_item(plan_item_id, topic_id="b", activity="LESSON"):
    return {
        **engine_item("LEARN"),
        "id": f"plan:{plan_item_id}",
        "source": "PLAN",
        "topicId": topic_id,
        "planItemId": plan_item_id,
        "activityType": activity,
    }


def live_plan_item(plan_item_id, topic_id="b", activity="LESSON", status="PENDING"):
    return {
        "id": plan_item_id,
        "subjectId": "s1",
        "topicId": topic_id,
        "activityType": activity,
        "durationMinutes": 30,
        "status": status,
    }


def test_reconcile_repoints_a_replanned_item_to_its_new_id():
    items = [plan_session_item("old")]
    assert reconcile_plan(items, [live_plan_item("new", status="COMPLETED")]) is True
    assert items[0]["planItemId"] == "new"
    assert items[0]["id"] == "plan:new"
    assert item_is_done(items[0], [], {"new": "COMPLETED"}) is True


def test_reconcile_pairs_duplicate_topic_items_one_to_one():
    items = [plan_session_item("old1"), plan_session_item("old2")]
    live = [live_plan_item("n1"), live_plan_item("n2")]
    reconcile_plan(items, live)
    assert [item["planItemId"] for item in items] == ["n1", "n2"]


def test_reconcile_drops_a_plan_item_that_left_the_plan():
    gone = plan_session_item("old", topic_id="zzz")
    kept = plan_session_item("p2")
    engine = engine_item("PRACTICE")
    items = [gone, kept, engine]
    assert reconcile_plan(items, [live_plan_item("p2")]) is True
    assert items == [kept, engine]


def test_reconcile_keeps_finished_items_and_is_a_noop_when_in_sync():
    done = {**plan_session_item("old", topic_id="zzz"), "doneAt": NOW.isoformat()}
    items = [done]
    assert reconcile_plan(items, []) is False
    assert items == [done]
    synced = [plan_session_item("p1")]
    assert reconcile_plan(synced, [live_plan_item("p1")]) is False


async def test_stale_plan_item_is_ticked_and_saved(store):
    store.row = SimpleNamespace(items=[plan_session_item("old")])
    live = [live_plan_item("new", status="COMPLETED")]
    result = await session_store.todays_session(
        FakeSession(),
        "stu",
        "2026-10-12",
        compose=never_compose,
        plan_status={"new": "COMPLETED"},
        plan_items=live,
        now=NOW,
    )
    assert result["done"] == 1
    assert store.row.items[0]["planItemId"] == "new"
    assert store.row.items[0]["doneAt"] == NOW.isoformat()


def test_dashboard_out_serves_session_not_old_rails():
    from app.api.responses import DashboardOut

    out = DashboardOut(streak=1, tier="FREE", achievements={}, session={"total": 0})
    assert out.session == {"total": 0}
    for removed in (
        "keepLearning",
        "learningPicks",
        "gaps",
        "revision",
        "revisionTotal",
    ):
        assert removed not in DashboardOut.model_fields


SESSION_ITEM_KEYS = {
    "id", "kind", "source", "topicId", "subjectId", "title", "slug", "minutes",
    "reason", "doneAt", "lessonId", "dueCards", "planItemId", "activityType",
    "mastery", "retention", "retentionSource", "evidenceLevel", "confidence",
    "accObservations", "lessonObservations", "srsObservations", "lastStudy",
}  # fmt: skip


def test_every_session_item_has_exactly_the_contract_keys():
    engine = compose_session(
        make_inputs(
            [make_topic("a")],
            [make_state("a", mastery=30, confidence=0.6, observations=8, last_days=1)],
        )
    )
    mock = compose_session(
        make_inputs(
            [],
            [],
            plan_items=[
                plan_item(item_id="m1", topic_id=None, activity="MOCK_EXAM", minutes=60)
            ],
        )
    )
    assert engine[0]["source"] == "ENGINE"
    assert mock[0]["kind"] == "MOCK"
    assert set(engine[0]) == SESSION_ITEM_KEYS
    assert set(mock[0]) == SESSION_ITEM_KEYS
