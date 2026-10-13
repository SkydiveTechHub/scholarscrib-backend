# Today's Session (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's three topic rails with one stored, time-boxed daily session (retrieval → practice → new material) with evidence-based reasons and per-item completion tracking.

**Architecture:** A pure composer (`app/services/learning/session.py`) turns topic states, due cards, plan items and recent answers into at most 4 items. A thin store (`session_store.py`) persists the day's list in a new `DailySession` table, marks items done from today's learning events, and returns the summary. `GetDashboardService` gathers inputs and exposes `session`; the Next.js dashboard renders one `TodaysSession` component.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, pytest (`asyncio_mode = "auto"`), Next.js (App Router), TypeScript, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-10-12-todays-session-design.md` (identical copies in `scholarscrib-backend` and `scholarscrib-frontend`). Read it before starting.

## Global Constraints

- Mastery scoring is unchanged. Do **not** touch `SCORING_VERSION`, `CHANNEL_WEIGHTS` or `DIFFICULTY_OUTCOMES`.
- Flashcards inform retention only, never mastery.
- Session defaults: `SESSION_MINUTES = 20`, `SESSION_MAX_ITEMS = 4`, retrieval ≤ 8 min (5 when no cards are due), practice 8 min, new-material slot needs ≥ 5 min remaining.
- Day key is `lagos_day_key()` (Africa/Lagos). "Today's events" are events with `occurred_at >= lagos_day_start(day)`.
- Done thresholds: `DONE_QUESTIONS = 5`. `LEARN`/`CONTINUE` are done after ≥ 1 `LESSON_BLOCK_COMPLETED` or `LESSON_COMPLETED` event on the topic (no service emits `LESSON_COMPLETED` today).
- Reasons quote percentages only when `evidenceLevel == "ENOUGH"`. Practice reasons use counts only.
- Items from deactivated subjects never reach the student, including items already stored in today's session.
- The existing project has no `git` repository (`Is a git repository: false`). Where a normal plan would commit, this plan says **Checkpoint** and runs the test/lint commands instead. If the executor is working in a git checkout, commit at each Checkpoint with a `feat:`/`refactor:` message.
- Frontend: `scholarscrib-frontend/AGENTS.md` says this Next.js has breaking changes. This plan only adds plain server components and a pure helper, but read the relevant guide in `node_modules/next/dist/docs/` before touching routing or data-fetching code.
- Run backend commands from `scholarscrib-backend` (`uv run pytest ...`, `uv run ruff check`, `uv run ruff format`). Run frontend commands from `scholarscrib-frontend`.

## Review Focus

1. **Nothing to recommend:** a student with activity but every topic mastered, nothing due and no new topics must get `items == []` and a visible empty-state card, not a crash or a blank gap. Pinned in Task 2 (`test_empty_when_nothing_to_do`) and Task 5 (empty state).
2. **Plan fully completed:** finished plan items must not eat the budget; the rest of the session still appears. Pinned in Task 2 (`test_completed_plan_items_do_not_consume_budget`).
3. **Subject deactivated after the session was stored:** stored items from inactive subjects must disappear on the next load. Pinned in Task 2 (`test_drop_inactive_removes_stored_items`) and applied in Task 4.
4. **Two first loads race on the unique `(studentId, day)` row (or the insert fails):** the page must still render the composed session. Pinned in Task 3 (`test_save_failure_still_returns_session`).
5. **Lagos midnight vs UTC:** a session generated at 23:30 Lagos must belong to that Lagos day, and "today's events" must start at 23:00 UTC the previous day. Pinned in Task 1 (`test_lagos_day_start_is_previous_utc_evening`).

---

## File Structure

**Backend (`scholarscrib-backend`)**

| File | Action | Responsibility |
|---|---|---|
| `app/services/learning/evidence.py` | modify | Session constants |
| `app/core/timeutil.py` | modify | `lagos_day_start` |
| `app/services/learning/mastery.py` | modify | `evidence_level`, `topic_retention`, `mean_retrievability`; delete `recommend`, `recommendation_score` |
| `app/services/learning/session.py` | create | Pure composer, reasons, done evaluation, `drop_inactive`, `summarise` |
| `app/services/learning/session_store.py` | create | Load/compose/persist the day's session |
| `app/database/models/learning.py` | modify | `DailySession` model |
| `app/database/models/__init__.py` | modify | Export `DailySession` |
| `alembic/versions/f6a3d9b1c470_add_daily_session.py` | create | Table + unique constraint |
| `app/database/repositories/learning.py` | modify | `daily_sessions_repository`; `learning_events_repository.since`, `.recent_outcomes_by_topic` |
| `app/database/repositories/flashcard.py` | modify | `reviews_repository.retrievability_rows` |
| `app/services/pages.py` | modify | Dashboard builds `session`, loses the three rails and their helpers |
| `app/api/responses.py` | modify | `DashboardOut` shape |
| `tests/test_session.py` | create | All new backend tests |

**Frontend (`scholarscrib-frontend`)**

| File | Action | Responsibility |
|---|---|---|
| `src/lib/api/types.ts` | modify | `DashboardSession*` types; drop rail types |
| `src/lib/session-links.ts` | create | `sessionItemHref`, `sessionActionLabel`, `sessionKindLabel` |
| `scripts/test-session-links.mts` | create | Tests for the helpers |
| `src/lib/dashboard.ts` | modify | Map `session` |
| `src/components/path/todays-session.tsx` | create | The dashboard section |
| `src/app/(dashboard)/dashboard/page.tsx` | modify | Use the new section |
| `src/components/path/next-topics.tsx`, `gap-list.tsx`, `revision-queue.tsx` | delete | Replaced |
| `src/types/learning.ts` | modify | Remove now-unused rail types |

---

### Task 1: Engine inputs — constants, Lagos day start, retention helpers

**Files:**
- Modify: `app/services/learning/evidence.py` (append constants after line 33)
- Modify: `app/core/timeutil.py` (append function)
- Modify: `app/services/learning/mastery.py` (imports at lines 5-23; append helpers after `is_mastered`)
- Test: `tests/test_session.py` (create)

**Interfaces:**
- Produces:
  - `evidence.py` constants: `SESSION_MINUTES: int`, `SESSION_MAX_ITEMS: int`, `RETRIEVE_MAX_MINUTES: int`, `RETRIEVE_NO_CARDS_MINUTES: int`, `PRACTICE_MINUTES: int`, `LEARN_MIN_MINUTES: int`, `DUE_FLOOR: float`, `CARD_RETENTION_MIN_CARDS: int`, `DONE_QUESTIONS: int`, `WEAK_PRACTICE_BOOST: float`
  - `timeutil.lagos_day_start(day: str) -> datetime` (UTC-aware)
  - `mastery.evidence_level(state: TopicState) -> str` returning `"NONE" | "THIN" | "ENOUGH"`
  - `mastery.topic_retention(state: TopicState, card_retention: tuple[float, int] | None) -> tuple[float | None, str]` returning `(value, "CARDS" | "ESTIMATE")`
  - `mastery.mean_retrievability(rows, now) -> dict[str, tuple[float, int]]` where `rows` is an iterable of `(topic_id, stability_days, last_reviewed_at)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session.py`:

```python
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.timeutil import lagos_day_start
from app.services.learning.mastery import (
    TopicState,
    evidence_level,
    mean_retrievability,
    topic_retention,
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
    assert (
        evidence_level(make_state("a", observations=2, confidence=0.2)) == "THIN"
    )
    assert (
        evidence_level(make_state("a", observations=8, confidence=0.6)) == "ENOUGH"
    )


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'lagos_day_start'` (and the missing mastery helpers).

- [ ] **Step 3: Add the constants**

Append to `app/services/learning/evidence.py` (after `STALE_RETENTION = 0.9`):

```python

# Today's session (see docs/superpowers/specs/2026-10-12-todays-session-design.md).
SESSION_MINUTES = 20
SESSION_MAX_ITEMS = 4
RETRIEVE_MAX_MINUTES = 8
RETRIEVE_NO_CARDS_MINUTES = 5
PRACTICE_MINUTES = 8
LEARN_MIN_MINUTES = 5
# A topic that is due must outrank one that is not, even when retention has not
# fallen below the decay line (due only for flashcards or a schedule).
DUE_FLOOR = 0.05
# Fewer reviewed cards than this say too little about a topic to trust.
CARD_RETENTION_MIN_CARDS = 3
DONE_QUESTIONS = 5
WEAK_PRACTICE_BOOST = 1.5
```

- [ ] **Step 4: Add `lagos_day_start`**

Append to `app/core/timeutil.py`:

```python


def lagos_day_start(day: str) -> datetime:
    """The UTC instant at which a Lagos civil day (YYYY-MM-DD) begins."""
    local = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=LAGOS)
    return local.astimezone(UTC)
```

- [ ] **Step 5: Add the mastery helpers**

In `app/services/learning/mastery.py`, add `CARD_RETENTION_MIN_CARDS` to the import from `app.services.learning.evidence` (keep the list alphabetical-ish as it is), then append after `is_mastered`:

```python
def evidence_level(state: TopicState) -> str:
    """How much a student's numbers on a topic can be trusted."""
    if state.observations == 0:
        return "NONE"
    if state.confidence < CONFIDENCE_FLOOR:
        return "THIN"
    return "ENOUGH"


def topic_retention(
    state: TopicState, card_retention: tuple[float, int] | None
) -> tuple[float | None, str]:
    """Retention from real flashcard data when there is enough, else the estimate."""
    if card_retention is not None:
        mean, count = card_retention
        if count >= CARD_RETENTION_MIN_CARDS:
            return mean, "CARDS"
    return state.retention, "ESTIMATE"


def mean_retrievability(
    rows, now: datetime
) -> dict[str, tuple[float, int]]:
    """Per topic: mean current retrievability of reviewed cards, and how many.

    `rows` yields (topic_id, stability_days, last_reviewed_at). Uses the same
    curve as the scheduler (`evidence.retention`), so a card's number here
    matches what the flashcard screens would say.
    """
    values: dict[str, list[float]] = {}
    for topic_id, stability, reviewed_at in rows:
        if not topic_id or reviewed_at is None or not stability or stability <= 0:
            continue
        days = max(0.0, (as_utc(now) - as_utc(reviewed_at)).total_seconds() / 86400)
        values.setdefault(topic_id, []).append(retention(days, stability))
    return {
        topic_id: (sum(found) / len(found), len(found))
        for topic_id, found in values.items()
    }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_session.py -v`
Expected: 4 passed.

- [ ] **Step 7: Checkpoint**

Run: `uv run ruff check app tests && uv run pytest -q`
Expected: no lint errors; existing suite still green.

---

### Task 2: The session composer (pure)

**Files:**
- Create: `app/services/learning/session.py`
- Test: `tests/test_session.py` (append)

**Interfaces:**
- Consumes: Task 1 constants; `mastery.evidence_level`, `mastery.topic_retention`, `TopicState`; `evidence.TARGET`, `GATE`? (not needed), `CONFIDENCE_FLOOR`, `WEAK_MASTERY`, `DECAY_RETENTION`.
- Produces (all in `app/services/learning/session.py`):
  - `@dataclass SessionInputs(topics, states, leverage, dependents, due_cards, card_retention, cadence_due, plan_items, continue_lesson, recent, now, budget=SESSION_MINUTES)`
  - `@dataclass(frozen=True) DayEvent(topic_id: str | None, kind: str)`
  - `exam_weight(topic) -> float`
  - `retrieval_priority(decay: float, weight: float, blocked: int, max_blocked: int) -> float`
  - `compose_session(inputs: SessionInputs) -> list[dict]`
  - `item_is_done(item: dict, events: Sequence[DayEvent], plan_status: Mapping[str, str]) -> bool`
  - `mark_done(items: list[dict], events, plan_status, now: datetime) -> bool` (mutates, returns whether anything changed)
  - `drop_inactive(items: list[dict], active_subject_ids: set[str]) -> list[dict]`
  - `summarise(items: list[dict]) -> dict` → `{"minutes": int, "done": int, "total": int, "items": list[dict]}`
- Item dict keys (camelCase, always all present): `id, kind, source, topicId, subjectId, title, slug, minutes, reason, doneAt, lessonId, dueCards, planItemId, activityType, mastery, retention, retentionSource, evidenceLevel, confidence, accObservations, lessonObservations, srsObservations, lastStudy`.
  - `kind` ∈ `RETRIEVE, PRACTICE, LEARN, CONTINUE, MOCK, PAST`; `source` ∈ `PLAN, ENGINE`.
  - Plan items arrive as dicts with keys `id, subjectId, topicId, activityType, durationMinutes, status` (the shape `_today_items` already produces).
  - `topics` are objects with `id, subject_id, title, slug, order_index, estimated_minutes, waec_weight, jamb_weight, prerequisite_topic_id`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session.py`. The `from ... import` lines below belong in the import block at the top of the file (ruff flags imports mid-file): merge them there and append the rest.

```python
from app.services.learning.session import (
    DayEvent,
    SessionInputs,
    compose_session,
    drop_inactive,
    exam_weight,
    item_is_done,
    mark_done,
    retrieval_priority,
    summarise,
)


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


def plan_item(item_id="p1", topic_id="a", activity="LESSON", minutes=25, status="PENDING"):
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
        make_state("a", mastery=80, confidence=0.6, observations=10, retention=0.6, last_days=20),
        make_state("b", mastery=30, confidence=0.6, observations=8, retention=0.95, last_days=3),
        make_state("c"),
    ]
    items = compose_session(
        make_inputs(topics, states, due_cards={"a": 4}, recent={"b": [False, False, True, False]})
    )
    assert [item["kind"] for item in items] == ["RETRIEVE", "PRACTICE", "LEARN"]
    assert [item["topicId"] for item in items] == ["a", "b", "c"]
    assert [item["minutes"] for item in items] == [3, 8, 9]
    assert items[1]["reason"] == "1 of your last 4 answers here were right"
    assert all(item["source"] == "ENGINE" and item["doneAt"] is None for item in items)


def test_card_only_due_topic_is_still_recommended():
    topics = [make_topic("d")]
    states = [make_state("d", mastery=75, confidence=0.6, observations=10, retention=0.9, last_days=5)]
    items = compose_session(make_inputs(topics, states, due_cards={"d": 2}))
    assert len(items) == 1
    assert items[0]["kind"] == "RETRIEVE"
    assert items[0]["reason"] == "2 flashcards due"
    assert items[0]["dueCards"] == 2
    assert items[0]["retentionSource"] == "ESTIMATE"


def test_retrieval_reason_names_card_retention_only_when_it_is_card_based():
    topics = [make_topic("a")]
    states = [make_state("a", mastery=75, confidence=0.6, observations=10, retention=0.9, last_days=20)]
    cards = compose_session(make_inputs(topics, states, card_retention={"a": (0.6, 4)}))
    assert cards[0]["reason"] == "Retention about 60% — last studied 3 weeks ago"
    assert cards[0]["retentionSource"] == "CARDS"
    estimate = compose_session(
        make_inputs(
            topics,
            [make_state("a", mastery=75, confidence=0.6, observations=10, retention=0.6, last_days=20)],
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
    items = compose_session(make_inputs(topics, states, continue_lesson=("c", "lesson-9")))
    new = [item for item in items if item["kind"] in {"CONTINUE", "LEARN"}]
    assert [item["kind"] for item in new] == ["CONTINUE"]
    assert new[0]["lessonId"] == "lesson-9"
    assert new[0]["reason"] == "Continue where you left off"


def test_learn_reason_cites_a_solid_prerequisite():
    topics = [make_topic("p", order=1, title="Fractions"), make_topic("n", order=2, prereq="p", title="Ratios")]
    states = [
        make_state("p", mastery=78, confidence=0.7, observations=12, retention=0.95, last_days=2),
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
        make_state("y0", mastery=90, confidence=0.8, observations=15, retention=0.95, last_days=2),
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
    states = [make_state("a", mastery=90, confidence=0.8, observations=15, retention=0.95, last_days=2)]
    assert compose_session(make_inputs(topics, states)) == []


def test_a_topic_appears_once_even_when_it_qualifies_for_two_slots():
    topics = [make_topic("x")]
    states = [make_state("x", mastery=30, confidence=0.6, observations=8, retention=0.5, last_days=10)]
    items = compose_session(make_inputs(topics, states))
    assert [item["kind"] for item in items] == ["RETRIEVE"]


def test_pending_plan_item_that_fills_the_budget_suppresses_engine_items():
    topics = [make_topic("a"), make_topic("b", order=2)]
    states = [
        make_state("a", observations=1, confidence=0.1),
        make_state("b", mastery=30, confidence=0.6, observations=8, last_days=1),
    ]
    items = compose_session(make_inputs(topics, states, plan_items=[plan_item(minutes=25)]))
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
        make_inputs(topics, states, plan_items=[plan_item(minutes=25, status="COMPLETED")])
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
    return [
        DayEvent("b", kind)
        for kind, count in counts.items()
        for _ in range(count)
    ]


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
    assert item_is_done(item, day_events(QUESTION_ANSWERED=2, CARD_REVIEWED=1), {}) is True
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'app.services.learning.session'`.

- [ ] **Step 3: Write the composer**

Create `app/services/learning/session.py`:

```python
"""Today's session: one short, ordered queue of study actions.

Everything here is pure. The dashboard service gathers the inputs and calls
`compose_session` at most once per Lagos day; `session_store.py` keeps the
result so the list does not reshuffle while the student works.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.timeutil import as_utc
from app.services.learning.evidence import (
    CONFIDENCE_FLOOR,
    DECAY_RETENTION,
    DONE_QUESTIONS,
    DUE_FLOOR,
    LEARN_MIN_MINUTES,
    PRACTICE_MINUTES,
    RETRIEVE_MAX_MINUTES,
    RETRIEVE_NO_CARDS_MINUTES,
    SESSION_MAX_ITEMS,
    SESSION_MINUTES,
    TARGET,
    WEAK_MASTERY,
    WEAK_PRACTICE_BOOST,
)
from app.services.learning.mastery import (
    TopicState,
    evidence_level,
    topic_retention,
)

PLAN_KINDS = {
    "LESSON": "LEARN",
    "PRACTICE": "PRACTICE",
    "REVISION": "RETRIEVE",
    "MOCK_EXAM": "MOCK",
    "PAST_QUESTIONS": "PAST",
}
PLAN_LABELS = {"MOCK_EXAM": "Mock exam", "PAST_QUESTIONS": "Past questions"}


@dataclass
class SessionInputs:
    topics: Sequence
    states: dict[str, TopicState]
    leverage: dict[str, float]
    dependents: dict[str, int]
    due_cards: dict[str, int]
    card_retention: dict[str, tuple[float, int]]
    cadence_due: set[str]
    plan_items: Sequence[dict]
    continue_lesson: tuple[str, str] | None
    recent: dict[str, list[bool]]
    now: datetime
    budget: int = SESSION_MINUTES


@dataclass(frozen=True)
class DayEvent:
    topic_id: str | None
    kind: str


def exam_weight(topic) -> float:
    return (topic.waec_weight or 0) + (topic.jamb_weight or 0)


def retrieval_priority(
    decay: float, weight: float, blocked: int, max_blocked: int
) -> float:
    urgency = max(decay, DUE_FLOOR)
    blocked_factor = 1 + (blocked / max_blocked if max_blocked else 0)
    return urgency * max(weight, 1.0) * blocked_factor


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _ago(days: float) -> str:
    if days < 1:
        return "today"
    if days < 7:
        count = max(1, round(days))
        return f"{count} day{'' if count == 1 else 's'} ago"
    if days < 45:
        count = round(days / 7)
        return f"{count} week{'' if count == 1 else 's'} ago"
    count = max(1, round(days / 30))
    return f"{count} month{'' if count == 1 else 's'} ago"


def _evidence(state: TopicState | None) -> dict:
    if state is None:
        return {
            "confidence": 0.0,
            "accObservations": 0,
            "lessonObservations": 0,
            "srsObservations": 0,
            "lastStudy": None,
        }
    return {
        "confidence": state.confidence,
        "accObservations": state.acc_observations,
        "lessonObservations": state.lesson_observations,
        "srsObservations": state.srs_observations,
        "lastStudy": state.last_effort_at.isoformat() if state.last_effort_at else None,
    }


def _item(
    *,
    item_id: str,
    kind: str,
    source: str,
    topic,
    state: TopicState | None,
    minutes: int,
    reason: str,
    **extra,
) -> dict:
    item = {
        "id": item_id,
        "kind": kind,
        "source": source,
        "topicId": topic.id if topic is not None else None,
        "subjectId": topic.subject_id if topic is not None else None,
        "title": topic.title if topic is not None else "",
        "slug": topic.slug if topic is not None else None,
        "minutes": minutes,
        "reason": reason,
        "doneAt": None,
        "lessonId": None,
        "dueCards": 0,
        "planItemId": None,
        "activityType": None,
        "mastery": state.mastery if state is not None else None,
        "retention": None,
        "retentionSource": None,
        "evidenceLevel": evidence_level(state) if state is not None else "NONE",
        **_evidence(state),
    }
    item.update(extra)
    return item


def _plan_entry(plan_item: dict, topics: dict, states: dict) -> dict:
    topic = topics.get(plan_item["topicId"]) if plan_item.get("topicId") else None
    state = states.get(topic.id) if topic is not None else None
    activity = plan_item["activityType"]
    title = topic.title if topic is not None else PLAN_LABELS.get(activity, "")
    return _item(
        item_id=f"plan:{plan_item['id']}",
        kind=PLAN_KINDS.get(activity, "PRACTICE"),
        source="PLAN",
        topic=topic,
        state=state,
        minutes=plan_item["durationMinutes"],
        reason="From your study plan",
        subjectId=plan_item.get("subjectId"),
        title=title or activity.replace("_", " ").title(),
        planItemId=plan_item["id"],
        activityType=activity,
    )


def _pick_retrieval(inputs: SessionInputs, used: set[str], remaining: int) -> dict | None:
    if remaining < 3:
        return None
    max_blocked = max(inputs.dependents.values(), default=0)
    best = None
    for topic in inputs.topics:
        state = inputs.states.get(topic.id)
        if topic.id in used or state is None or state.last_effort_at is None:
            continue
        value, source = topic_retention(state, inputs.card_retention.get(topic.id))
        faded = value is not None and value < DECAY_RETENTION
        cards = inputs.due_cards.get(topic.id, 0)
        cadence = topic.id in inputs.cadence_due
        if not (faded or cards > 0 or cadence):
            continue
        decay = 0.0 if value is None else max(0.0, DECAY_RETENTION - value)
        priority = retrieval_priority(
            decay,
            exam_weight(topic),
            inputs.dependents.get(topic.id, 0),
            max_blocked,
        )
        key = (-priority, -cards, topic.order_index)
        if best is None or key < best[0]:
            best = (key, topic, state, value, source, faded, cards)
    if best is None:
        return None
    _key, topic, state, value, source, faded, cards = best
    wanted = (
        min(RETRIEVE_MAX_MINUTES, math.ceil(1 + cards * 0.5))
        if cards > 0
        else RETRIEVE_NO_CARDS_MINUTES
    )
    minutes = min(wanted, remaining)
    ago = _ago(
        (as_utc(inputs.now) - as_utc(state.last_effort_at)).total_seconds() / 86400
    )
    if faded and source == "CARDS":
        reason = f"Retention about {round(value * 100)}% — last studied {ago}"
    elif faded:
        reason = f"Probably fading — last studied {ago}"
    elif cards > 0:
        reason = f"{_plural(cards, 'flashcard')} due"
    else:
        reason = "Scheduled revision is due"
    return _item(
        item_id=f"retrieve:{topic.id}",
        kind="RETRIEVE",
        source="ENGINE",
        topic=topic,
        state=state,
        minutes=minutes,
        reason=reason,
        dueCards=cards,
        retention=value,
        retentionSource=source,
    )


def _practice_reason(inputs: SessionInputs, topic, state: TopicState) -> str:
    parts = []
    recent = inputs.recent.get(topic.id, [])
    if len(recent) >= 3:
        parts.append(
            f"{sum(1 for right in recent if right)} of your last "
            f"{len(recent)} answers here were right"
        )
    elif state.acc_observations > 0:
        parts.append(f"{_plural(state.acc_observations, 'question')} answered so far")
    else:
        parts.append("Started — needs more practice")
    unlocks = inputs.dependents.get(topic.id, 0)
    if unlocks >= 1:
        parts.append(f"unlocks {_plural(unlocks, 'topic')}")
    weight = round(exam_weight(topic))
    if weight > 0:
        parts.append(f"~{weight} exam weight")
    return " · ".join(parts)


def _pick_practice(inputs: SessionInputs, used: set[str], remaining: int) -> dict | None:
    if remaining < PRACTICE_MINUTES:
        return None
    best = None
    for topic in inputs.topics:
        state = inputs.states.get(topic.id)
        if topic.id in used or state is None:
            continue
        if not state.available or state.mastery >= TARGET:
            continue
        if evidence_level(state) == "NONE":
            continue
        urgency = (TARGET - state.mastery) / TARGET
        value = (
            urgency
            * (1 + inputs.leverage.get(topic.id, 0.0))
            * max(exam_weight(topic), 1.0)
        )
        if state.confidence >= CONFIDENCE_FLOOR and state.mastery < WEAK_MASTERY:
            value *= WEAK_PRACTICE_BOOST
        key = (-value, topic.order_index)
        if best is None or key < best[0]:
            best = (key, topic, state)
    if best is None:
        return None
    _key, topic, state = best
    return _item(
        item_id=f"practice:{topic.id}",
        kind="PRACTICE",
        source="ENGINE",
        topic=topic,
        state=state,
        minutes=PRACTICE_MINUTES,
        reason=_practice_reason(inputs, topic, state),
    )


def _pick_new(inputs: SessionInputs, used: set[str], remaining: int) -> dict | None:
    if remaining < LEARN_MIN_MINUTES:
        return None
    by_id = {topic.id: topic for topic in inputs.topics}
    if inputs.continue_lesson is not None:
        topic_id, lesson_id = inputs.continue_lesson
        topic = by_id.get(topic_id)
        state = inputs.states.get(topic_id)
        if topic is not None and state is not None and topic_id not in used:
            return _item(
                item_id=f"continue:{topic.id}",
                kind="CONTINUE",
                source="ENGINE",
                topic=topic,
                state=state,
                minutes=min(remaining, max(LEARN_MIN_MINUTES, topic.estimated_minutes or 10)),
                reason="Continue where you left off",
                lessonId=lesson_id,
            )
    studied_subjects = {
        topic.subject_id
        for topic in inputs.topics
        if (state := inputs.states.get(topic.id)) is not None
        and state.observations > 0
    }
    candidates = []
    for topic in inputs.topics:
        state = inputs.states.get(topic.id)
        if topic.id in used or state is None or not state.available:
            continue
        if evidence_level(state) != "NONE":
            continue
        candidates.append(
            (
                0 if topic.subject_id in studied_subjects else 1,
                topic.order_index,
                topic.title,
                topic,
                state,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda found: found[:3])
    _a, _b, _c, topic, state = candidates[0]
    reason = "New topic — you're ready for it"
    prereq = by_id.get(topic.prerequisite_topic_id) if topic.prerequisite_topic_id else None
    prereq_state = inputs.states.get(prereq.id) if prereq is not None else None
    if (
        prereq is not None
        and prereq_state is not None
        and evidence_level(prereq_state) == "ENOUGH"
        and prereq_state.mastery >= TARGET
    ):
        reason = f"{prereq.title} is solid ({prereq_state.mastery}%) — this is the next step"
    return _item(
        item_id=f"learn:{topic.id}",
        kind="LEARN",
        source="ENGINE",
        topic=topic,
        state=state,
        minutes=min(remaining, max(LEARN_MIN_MINUTES, topic.estimated_minutes or 10)),
        reason=reason,
    )


def compose_session(inputs: SessionInputs) -> list[dict]:
    topics = {topic.id: topic for topic in inputs.topics}
    items: list[dict] = []
    used: set[str] = set()
    spent = 0
    for plan_item in inputs.plan_items:
        if len(items) >= SESSION_MAX_ITEMS:
            break
        entry = _plan_entry(plan_item, topics, inputs.states)
        items.append(entry)
        if entry["topicId"]:
            used.add(entry["topicId"])
        if plan_item["status"] != "COMPLETED":
            spent += entry["minutes"]
    # The unfinished lesson's topic is held back from the first two slots so the
    # "Continue" item is not pre-empted by a practice block on the same topic.
    reserved = {inputs.continue_lesson[0]} if inputs.continue_lesson else set()
    for pick in (_pick_retrieval, _pick_practice, _pick_new):
        if len(items) >= SESSION_MAX_ITEMS:
            break
        remaining = inputs.budget - spent
        if remaining <= 0:
            break
        blocked = used if pick is _pick_new else used | reserved
        found = pick(inputs, blocked, remaining)
        if found is None:
            continue
        items.append(found)
        used.add(found["topicId"])
        spent += found["minutes"]
    return items


def item_is_done(
    item: dict, events: Sequence[DayEvent], plan_status: Mapping[str, str]
) -> bool:
    if item["source"] == "PLAN":
        return plan_status.get(item["planItemId"]) == "COMPLETED"
    mine = [event for event in events if event.topic_id == item["topicId"]]
    answered = sum(1 for event in mine if event.kind == "QUESTION_ANSWERED")
    reviewed = sum(1 for event in mine if event.kind == "CARD_REVIEWED")
    lessons = sum(
        1
        for event in mine
        if event.kind in {"LESSON_BLOCK_COMPLETED", "LESSON_COMPLETED"}
    )
    kind = item["kind"]
    if kind == "RETRIEVE":
        due = item.get("dueCards", 0)
        target = min(DONE_QUESTIONS, due) if due > 0 else DONE_QUESTIONS
        return answered + reviewed >= target
    if kind == "PRACTICE":
        return answered >= DONE_QUESTIONS
    if kind in {"LEARN", "CONTINUE"}:
        return lessons >= 1
    return False


def mark_done(
    items: list[dict],
    events: Sequence[DayEvent],
    plan_status: Mapping[str, str],
    now: datetime,
) -> bool:
    """Stamp `doneAt` on newly finished items. Never clears one. True if anything changed."""
    changed = False
    for item in items:
        if item.get("doneAt"):
            continue
        if item_is_done(item, events, plan_status):
            item["doneAt"] = as_utc(now).isoformat()
            changed = True
    return changed


def drop_inactive(items: list[dict], active_subject_ids: set[str]) -> list[dict]:
    return [
        item
        for item in items
        if not item.get("subjectId") or item["subjectId"] in active_subject_ids
    ]


def summarise(items: list[dict]) -> dict:
    return {
        "minutes": sum(item["minutes"] for item in items),
        "done": sum(1 for item in items if item.get("doneAt")),
        "total": len(items),
        "items": items,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session.py -v`
Expected: all tests pass. If `test_session_order_is_retrieval_then_practice_then_new` fails on minutes, re-check: retrieval for 4 due cards is `ceil(1 + 2) = 3`, practice is 8, so learn gets `min(20 - 11, 15) = 9`.

- [ ] **Step 5: Checkpoint**

Run: `uv run ruff check app tests && uv run ruff format app tests && uv run pytest -q`
Expected: clean lint, suite green.

---

### Task 3: Persistence — `DailySession` model, migration, repositories, store

**Files:**
- Modify: `app/database/models/learning.py` (append model after `TopicMastery`)
- Modify: `app/database/models/__init__.py` (import + `__all__`)
- Create: `alembic/versions/f6a3d9b1c470_add_daily_session.py`
- Modify: `app/database/repositories/learning.py` (add repository class + `since`)
- Create: `app/services/learning/session_store.py`
- Test: `tests/test_session.py` (append)

**Interfaces:**
- Consumes: Task 2 (`DayEvent`, `mark_done`, `summarise`), `timeutil.lagos_day_start`.
- Produces:
  - `DailySession` model: `id`, `student_id`, `day: str`, `items: list[dict]` (JSONB), `generated_at`.
  - `daily_sessions_repository.for_day(session, student_id, day) -> DailySession | None`.
  - `learning_events_repository.since(session, student_id, moment) -> list[LearningEvent]`.
  - `session_store.todays_session(session, student_id, day, *, compose, plan_status, now) -> dict` where `compose` is `Callable[[], Awaitable[list[dict]]]` and the return value is a `summarise(...)` dict.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session.py` (move the `session_store` import to the top import block):

```python
from app.services.learning import session_store


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
        FakeSession(), "stu", "2026-10-12", compose=never_compose, plan_status={}, now=NOW
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
        FakeSession(), "stu", "2026-10-12", compose=never_compose, plan_status={}, now=NOW
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session.py -v -k "stored or stores or persisted or save_failure or not_stored"`
Expected: collection ERROR — `cannot import name 'session_store'`.

- [ ] **Step 3: Add the model**

Append to `app/database/models/learning.py` (after the `TopicMastery` class; `UniqueConstraint`, `JSONB`, `DateTime`, `func`, `ForeignKey`, `String` are already imported):

```python
class DailySession(Base):
    """The study session composed for one student on one Lagos day.

    Stored so the list does not reshuffle while the student works through it.
    Each item carries its own `doneAt`, which is also how completion is measured.
    """

    __tablename__ = "DailySession"
    __table_args__ = (
        UniqueConstraint("studentId", "day", name="DailySession_studentId_day_key"),
    )

    id: Mapped[str] = id_column()
    student_id: Mapped[str] = mapped_column(
        "studentId", ForeignKey("User.id", ondelete="CASCADE")
    )
    day: Mapped[str] = mapped_column(String)
    items: Mapped[list] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(
        "generatedAt", DateTime(timezone=True), server_default=func.now()
    )
```

In `app/database/models/__init__.py`, add `DailySession,` to the import from `app.database.models.learning` (next to `StudentProgress`/`TopicMastery`) and add `"DailySession",` to `__all__` in alphabetical position.

- [ ] **Step 4: Add the migration**

Create `alembic/versions/f6a3d9b1c470_add_daily_session.py`:

```python
"""Add DailySession so a student's study session is stable for the day

Revision ID: f6a3d9b1c470
Revises: c52f8a1e0d34
Create Date: 2026-10-12 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f6a3d9b1c470"
down_revision = "c52f8a1e0d34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "DailySession",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("studentId", sa.String(), nullable=False),
        sa.Column("day", sa.String(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "generatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("studentId", "day", name="DailySession_studentId_day_key"),
    )


def downgrade() -> None:
    op.drop_table("DailySession")
```

Before moving on, confirm `c52f8a1e0d34` is still the only head: `uv run alembic heads` should print `c52f8a1e0d34` (if the repair-stage work has already added a revision, set `down_revision` to the real head).

- [ ] **Step 5: Add the repositories**

In `app/database/repositories/learning.py`, add `DailySession` to the `from app.database.models import (...)` block, then add inside `LearningEventRepository` (after `recorded_sources`):

```python
    async def since(
        self, session: AsyncSession, student_id: str, moment: datetime
    ) -> list[LearningEvent]:
        return await self.many(
            session,
            select(LearningEvent)
            .where(
                LearningEvent.student_id == student_id,
                LearningEvent.occurred_at >= moment,
            )
            .order_by(LearningEvent.seq),
        )
```

and add a new repository class next to `TopicMasteryRepository`, plus its instance where the other instances are created at the bottom of the file (follow the existing naming such as `topic_mastery_repository = TopicMasteryRepository()`):

```python
class DailySessionRepository(BaseRepository[DailySession]):
    model = DailySession

    async def for_day(
        self, session: AsyncSession, student_id: str, day: str
    ) -> DailySession | None:
        return await self.first(
            session,
            DailySession.student_id == student_id,
            DailySession.day == day,
        )
```

```python
daily_sessions_repository = DailySessionRepository()
```

- [ ] **Step 6: Write the store**

Create `app/services/learning/session_store.py`:

```python
"""Keep today's session stable.

The first dashboard load of a Lagos day composes the session and stores it;
later loads read it back and only stamp newly finished items. A failed write
must not fail the page: the composed session is still returned and the next
load tries again.
"""

import copy
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.core.timeutil import lagos_day_start
from app.database.models import DailySession
from app.database.repositories.learning import (
    daily_sessions_repository,
    learning_events_repository,
)
from app.services.learning.session import DayEvent, mark_done, summarise


async def todays_session(
    session: AsyncSession,
    student_id: str,
    day: str,
    *,
    compose: Callable[[], Awaitable[list[dict]]],
    plan_status: Mapping[str, str],
    now: datetime,
) -> dict:
    row = await daily_sessions_repository.for_day(session, student_id, day)
    items = copy.deepcopy(row.items) if row is not None else await compose()
    if not items:
        return summarise([])
    events = [
        DayEvent(event.topic_id, event.kind)
        for event in await learning_events_repository.since(
            session, student_id, lagos_day_start(day)
        )
    ]
    changed = mark_done(items, events, plan_status, now)
    if row is None or changed:
        await _save(session, student_id, day, row, items)
    return summarise(items)


async def _save(
    session: AsyncSession,
    student_id: str,
    day: str,
    row: DailySession | None,
    items: list[dict],
) -> None:
    try:
        async with session.begin_nested():
            if row is None:
                await daily_sessions_repository.add(
                    session, DailySession(student_id=student_id, day=day, items=items)
                )
            else:
                # A new list object, so SQLAlchemy sees the JSONB change.
                row.items = items
            await session.flush()
    except Exception:
        logger.exception("Daily session save failed for %s", student_id)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_session.py -v`
Expected: all pass.

- [ ] **Step 8: Verify the model and migration agree**

Run: `uv run python -c "from app.database.models import DailySession; print(DailySession.__table__.c.keys())"`
Expected: `['id', 'studentId', 'day', 'items', 'generatedAt']`

Run: `uv run alembic heads`
Expected: a single head, `f6a3d9b1c470`.

(If a dev database is available: `uv run alembic upgrade head`, then `uv run alembic downgrade -1`, then `upgrade head` again, all without error.)

- [ ] **Step 9: Checkpoint**

Run: `uv run ruff check app tests && uv run ruff format app tests && uv run pytest -q`
Expected: clean and green.

---

### Task 4: Wire the dashboard backend

**Files:**
- Modify: `app/database/repositories/flashcard.py` (add `retrievability_rows`)
- Modify: `app/database/repositories/learning.py` (add `recent_outcomes_by_topic`)
- Modify: `app/services/pages.py` (new `process`, `_compose`; remove rails and dead helpers)
- Modify: `app/api/responses.py:461-482` (`DashboardOut`)
- Modify: `app/services/learning/mastery.py` (delete `recommendation_score`, `recommend`, and the now-unused `STRONG_MASTERY` import if ruff flags it)
- Test: SQL compile check + manual dashboard load

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `GET /api/dashboard` response with `session: {minutes, done, total, items[]} | null` and **without** `keepLearning`, `learningPicks`, `gaps`, `revision`, `revisionTotal`.
  - `reviews_repository.retrievability_rows(session, student_id) -> list[tuple[str, float, datetime]]`
  - `learning_events_repository.recent_outcomes_by_topic(session, student_id, topic_ids, limit=8) -> dict[str, list[bool]]`, newest answer first.

- [ ] **Step 1: Add the flashcard query**

In `app/database/repositories/flashcard.py`, add inside `FlashcardReviewRepository` after `due_counts_by_topic`:

```python
    async def retrievability_rows(
        self, session: AsyncSession, student_id: str
    ) -> list[tuple[str, float, datetime]]:
        """(topic_id, stability_days, last_reviewed_at) for every reviewed card."""
        topic_id = func.coalesce(FlashcardDeck.topic_id, Subtopic.topic_id)
        result = await self.rows(
            session,
            select(topic_id, FlashcardReview.stability, FlashcardReview.last_reviewed_at)
            .select_from(FlashcardReview)
            .join(Flashcard, Flashcard.id == FlashcardReview.flashcard_id)
            .join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)
            .outerjoin(Lesson, Lesson.id == FlashcardDeck.lesson_id)
            .outerjoin(Subtopic, Subtopic.id == Lesson.subtopic_id)
            .where(
                FlashcardReview.student_id == student_id,
                FlashcardReview.last_reviewed_at.is_not(None),
                FlashcardReview.stability > 0,
            ),
        )
        return [(row[0], row[1], row[2]) for row in result.all() if row[0]]
```

`FlashcardReview.stability` is in days: the scheduler passes it straight to `retention(interval_days, stability)` in `app/services/srs/scheduler.py` (`_remember`).

- [ ] **Step 2: Add the recent-outcomes query**

In `app/database/repositories/learning.py`, add inside `LearningEventRepository`:

```python
    async def recent_outcomes_by_topic(
        self,
        session: AsyncSession,
        student_id: str,
        topic_ids: list[str],
        limit: int = 8,
    ) -> dict[str, list[bool]]:
        """Each topic's last `limit` answers, newest first (True = correct)."""
        if not topic_ids:
            return {}
        ranked = (
            select(
                LearningEvent.topic_id.label("topic_id"),
                LearningEvent.correct.label("correct"),
                func.row_number()
                .over(
                    partition_by=LearningEvent.topic_id,
                    order_by=LearningEvent.seq.desc(),
                )
                .label("rank"),
            )
            .where(
                LearningEvent.student_id == student_id,
                LearningEvent.kind == "QUESTION_ANSWERED",
                LearningEvent.correct.is_not(None),
                LearningEvent.topic_id.in_(topic_ids),
            )
            .subquery()
        )
        rows = await self.rows(
            session,
            select(ranked.c.topic_id, ranked.c.correct)
            .where(ranked.c.rank <= limit)
            .order_by(ranked.c.topic_id, ranked.c.rank),
        )
        found: dict[str, list[bool]] = {}
        for topic_id, correct in rows.all():
            found.setdefault(topic_id, []).append(bool(correct))
        return found
```

- [ ] **Step 3: Compile-check both queries against the Postgres dialect**

Run:

```bash
uv run python - <<'EOF'
from sqlalchemy.dialects import postgresql
from sqlalchemy import select, func
from app.database.models import (Flashcard, FlashcardDeck, FlashcardReview, Lesson, Subtopic, LearningEvent)

topic_id = func.coalesce(FlashcardDeck.topic_id, Subtopic.topic_id)
q1 = (select(topic_id, FlashcardReview.stability, FlashcardReview.last_reviewed_at)
    .select_from(FlashcardReview)
    .join(Flashcard, Flashcard.id == FlashcardReview.flashcard_id)
    .join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)
    .outerjoin(Lesson, Lesson.id == FlashcardDeck.lesson_id)
    .outerjoin(Subtopic, Subtopic.id == Lesson.subtopic_id)
    .where(FlashcardReview.student_id == "x", FlashcardReview.stability > 0))
print(str(q1.compile(dialect=postgresql.dialect()))[:200])
ranked = select(LearningEvent.topic_id.label("topic_id"), LearningEvent.correct.label("correct"),
    func.row_number().over(partition_by=LearningEvent.topic_id, order_by=LearningEvent.seq.desc()).label("rank")
).where(LearningEvent.student_id == "x").subquery()
q2 = select(ranked.c.topic_id, ranked.c.correct).where(ranked.c.rank <= 8)
print(str(q2.compile(dialect=postgresql.dialect()))[:200])
EOF
```

Expected: two SQL strings print without exceptions. (Behavior against real data is verified in Task 6.)

- [ ] **Step 4: Rewrite the dashboard service**

In `app/services/pages.py`:

1. Replace the constants block (lines 49-55: `DASHBOARD_ATTEMPTS_PAGE_SIZE`, `DASHBOARD_RAIL_SIZE`, `DASHBOARD_PICKS`, `CONTINUE_REASON`) with only:

```python
DASHBOARD_ATTEMPTS_PAGE_SIZE = 5
```

2. Update imports: remove `DECAY_RETENTION, TARGET` from the `evidence` import (delete the line if nothing else from it is used), remove `recommend` and `recommendation_score` from the `mastery` import, and add:

```python
from app.services.learning.mastery import mean_retrievability
from app.services.learning.session import (
    SessionInputs,
    compose_session,
    drop_inactive,
    summarise,
)
from app.services.learning.session_store import todays_session
```

(merge `mean_retrievability` into the existing `from app.services.learning.mastery import (...)` block rather than adding a second import from the same module.)

3. Replace `GetDashboardService.process` and delete the methods `_learning_picks`, `_revision_queue`, `_keep_learning`. Keep `__init__`, `_today_items` and `_highlights` exactly as they are. New `process` and `_compose`:

```python
    async def process(self) -> dict:
        today = lagos_day_key()
        now = utcnow()
        completed = await attempts_repository.completed_at(
            self.session, self.student_id, limit=400
        )
        days = {lagos_day_key(moment) for moment in completed if moment}
        recent = await _attempts(
            self.session,
            self.student_id,
            DASHBOARD_ATTEMPTS_PAGE_SIZE,
            self.activity_page,
        )
        newest = (
            recent
            if self.activity_page == 1
            else await _attempts(
                self.session, self.student_id, DASHBOARD_ATTEMPTS_PAGE_SIZE
            )
        )
        attempt_total = await attempts_repository.completed_count(
            self.session, self.student_id
        )
        last_week = await attempts_repository.completed_count(
            self.session, self.student_id, since=now - timedelta(days=7)
        )
        plan = await plans_repository.active(self.session, self.student_id)
        active_ids = await subjects_repository.active_ids(self.session)
        today_items = [
            item
            for item in await self._today_items(plan, today)
            if item["subjectId"] in active_ids
        ]
        # A deactivated subject leaves the session too, not just the classroom.
        all_topics = [
            topic
            for topic in await topics_repository.all_ordered(self.session)
            if topic.subject_id in active_ids
        ]
        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [topic.id for topic in all_topics],
        ).process()
        await _paint(self.session, all_topics, states, self.student_id)
        dependents = _unmastered_dependents(all_topics, states)
        answered, correct, topic_count = await responses_repository.answer_totals(
            self.session, self.student_id
        )
        has_activity = (
            attempt_total > 0
            or answered > 0
            or any(state.observations > 0 for state in states.values())
        )
        session_payload = None
        if has_activity:
            stored = await todays_session(
                self.session,
                self.student_id,
                today,
                compose=lambda: self._compose(
                    all_topics, states, dependents, today_items, now
                ),
                plan_status={item["id"]: item["status"] for item in today_items},
                now=now,
            )
            # A subject deactivated after today's session was stored leaves it.
            session_payload = summarise(drop_inactive(stored["items"], active_ids))
        highlights = await self._highlights()
        referenced = {
            item["subjectId"]
            for item in (session_payload["items"] if session_payload else [])
            if item.get("subjectId")
        }
        subjects = {
            subject.id: {
                "slug": subject.slug,
                "name": subject.name,
                "code": subject.code,
            }
            for subject in await subjects_repository.active_ordered(self.session)
            if subject.id in referenced
        }
        return {
            "firstName": self.first_name,
            "streak": current_streak(days, today),
            "tier": self.tier,
            "session": session_payload,
            "subjects": subjects,
            "todayItems": today_items,
            "hasStudyPlan": plan is not None,
            "hasActivity": has_activity,
            "recentAttempts": recent,
            "attemptTotal": attempt_total,
            "bestScore": max((row["percentage"] for row in newest), default=None),
            "lastWeekActivity": last_week,
            "totalResponses": answered,
            "correctResponses": correct,
            "accuracy": round(correct / answered * 100) if answered else None,
            "topicCount": topic_count,
            "achievements": highlights,
        }

    async def _compose(
        self,
        topic_rows: Sequence[Topic],
        states: dict,
        dependents: dict[str, int],
        today_items: list[dict],
        now,
    ) -> list[dict]:
        cadence = await progress_repository.revision_due_by_topic(
            self.session, self.student_id
        )
        due_cards = await reviews_repository.due_counts_by_topic(
            self.session, self.student_id, now
        )
        card_rows = await reviews_repository.retrievability_rows(
            self.session, self.student_id
        )
        in_progress = await progress_repository.latest_in_progress(
            self.session, self.student_id
        )
        studied = [
            topic_id for topic_id, state in states.items() if state.observations > 0
        ]
        recent = await learning_events_repository.recent_outcomes_by_topic(
            self.session, self.student_id, studied
        )
        topic_ids = {topic.id for topic in topic_rows}
        continue_lesson = (
            (in_progress.topic_id, in_progress.lesson_id)
            if in_progress is not None and in_progress.topic_id in topic_ids
            else None
        )
        return compose_session(
            SessionInputs(
                topics=topic_rows,
                states=states,
                leverage=_leverage(topic_rows),
                dependents=dependents,
                due_cards=due_cards,
                card_retention=mean_retrievability(card_rows, now),
                cadence_due={
                    topic_id
                    for topic_id, due_at in cadence.items()
                    if as_utc(due_at) <= now
                },
                plan_items=[
                    item
                    for item in today_items
                    if item["status"] in {"PENDING", "COMPLETED"}
                ],
                continue_lesson=continue_lesson,
                recent=recent,
                now=now,
            )
        )
```

Add `learning_events_repository` to the existing `from app.database.repositories.learning import (...)` block in `pages.py`.

4. Delete the module-level helpers that only the removed code used: `_gap_view`, `_pick_reason`, `_pick_view`, `_evidence`, `_exam_weight`. **Keep** `_gaps`, `_leverage`, `_unmastered_dependents`, `_paint`, `_attempts`, `_lesson_unlocked` and all the other service classes.

5. In `app/services/learning/mastery.py`, delete `recommendation_score` and `recommend` (nothing else calls them) and remove `STRONG_MASTERY` from the import block if ruff reports it unused.

6. In `app/api/responses.py`, replace the head of `DashboardOut` so it reads:

```python
class DashboardOut(ApiOut):
    firstName: str | None = None
    streak: int
    tier: str
    session: dict | None = None
    subjects: dict[str, dict] = {}
    todayItems: list[dict] = []
```

(and delete the `keepLearning`, `learningPicks`, `gaps`, `revision`, `revisionTotal` lines; the remaining fields from `hasStudyPlan` onward are unchanged).

- [ ] **Step 5: Lint and test**

Run: `uv run ruff check app tests --fix && uv run ruff format app tests && uv run pytest -q`
Expected: no remaining unused-import or unused-name errors (fix any ruff reports by deleting the dead import/function); suite green, including `test_engines.py`.

Run: `uv run python -c "import app.services.pages, app.api.student.dashboard; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Checkpoint**

Run: `uv run pytest -q`
Expected: green. (Full end-to-end verification happens in Task 6 once the frontend consumes the payload.)

---

### Task 5: Frontend — types, link helper, component, page

**Files:**
- Modify: `src/lib/api/types.ts` (Dashboard section, lines ~289-386)
- Create: `src/lib/session-links.ts`
- Create: `scripts/test-session-links.mts`
- Modify: `src/lib/dashboard.ts`
- Create: `src/components/path/todays-session.tsx`
- Modify: `src/app/(dashboard)/dashboard/page.tsx`
- Delete: `src/components/path/next-topics.tsx`, `gap-list.tsx`, `revision-queue.tsx`
- Modify: `src/types/learning.ts`

**Interfaces:**
- Consumes: backend payload `session` from Task 4.
- Produces:
  - `DashboardSessionItem`, `DashboardSession` types (in `types.ts`).
  - `sessionItemHref(item, subjects): string | null`
  - `sessionActionLabel(item): string`
  - `sessionKindLabel(item): string`
  - `<TodaysSession session subjects />`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-session-links.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  sessionActionLabel,
  sessionItemHref,
  sessionKindLabel,
} from "../src/lib/session-links";
import type { DashboardSessionItem } from "../src/lib/api/types";

const subjects = { s1: { slug: "physics", name: "Physics", code: "PHY" } };

function item(overrides: Partial<DashboardSessionItem>): DashboardSessionItem {
  return {
    id: "x",
    kind: "PRACTICE",
    source: "ENGINE",
    topicId: "t1",
    subjectId: "s1",
    title: "Gas laws",
    slug: "gas-laws",
    minutes: 8,
    reason: "r",
    doneAt: null,
    lessonId: null,
    dueCards: 0,
    planItemId: null,
    activityType: null,
    mastery: 40,
    retention: null,
    retentionSource: null,
    evidenceLevel: "ENOUGH",
    confidence: 0.6,
    accObservations: 8,
    lessonObservations: 0,
    srsObservations: 0,
    lastStudy: null,
    ...overrides,
  };
}

test("practice links to the topic practice page", () => {
  assert.equal(
    sessionItemHref(item({}), subjects),
    "/classroom/physics/gas-laws/practice",
  );
});

test("retrieve with due cards goes to flashcards, without goes to practice", () => {
  assert.equal(
    sessionItemHref(item({ kind: "RETRIEVE", dueCards: 3 }), subjects),
    "/flashcards",
  );
  assert.equal(
    sessionItemHref(item({ kind: "RETRIEVE", dueCards: 0 }), subjects),
    "/classroom/physics/gas-laws/practice",
  );
});

test("continue resumes in the lesson player; learn opens the topic", () => {
  assert.equal(
    sessionItemHref(item({ kind: "CONTINUE", lessonId: "l1" }), subjects),
    "/classroom/physics/gas-laws/study",
  );
  assert.equal(
    sessionItemHref(item({ kind: "LEARN" }), subjects),
    "/classroom/physics/gas-laws",
  );
});

test("mock exam and past questions link to their practice pages", () => {
  const base = { topicId: null, slug: null, title: "Mock exam" };
  assert.equal(
    sessionItemHref(item({ ...base, kind: "MOCK" }), subjects),
    "/practice/mock-exam",
  );
  assert.equal(
    sessionItemHref(item({ ...base, kind: "PAST" }), subjects),
    "/practice/past-questions/physics",
  );
  assert.equal(
    sessionItemHref(item({ ...base, kind: "PAST", subjectId: null }), subjects),
    "/practice/past-questions",
  );
});

test("a topic item whose subject is unknown has no link", () => {
  assert.equal(sessionItemHref(item({ subjectId: "gone" }), subjects), null);
  assert.equal(sessionItemHref(item({ slug: null }), subjects), null);
});

test("labels", () => {
  assert.equal(sessionActionLabel(item({ kind: "RETRIEVE", dueCards: 2 })), "Review cards");
  assert.equal(sessionActionLabel(item({ kind: "RETRIEVE" })), "Refresh");
  assert.equal(sessionActionLabel(item({ kind: "PRACTICE" })), "Practise");
  assert.equal(sessionActionLabel(item({ kind: "CONTINUE" })), "Continue");
  assert.equal(sessionActionLabel(item({ kind: "LEARN" })), "Start lesson");
  assert.equal(sessionActionLabel(item({ kind: "MOCK" })), "Start");
  assert.equal(sessionKindLabel(item({ kind: "RETRIEVE" })), "Revise");
  assert.equal(sessionKindLabel(item({ kind: "PRACTICE" })), "Practise");
  assert.equal(sessionKindLabel(item({ kind: "CONTINUE" })), "Continue");
  assert.equal(sessionKindLabel(item({ kind: "LEARN" })), "New");
  assert.equal(sessionKindLabel(item({ kind: "MOCK" })), "Mock exam");
  assert.equal(sessionKindLabel(item({ kind: "PAST" })), "Past questions");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-session-links.mts`
Expected: FAIL — `Cannot find module '../src/lib/session-links'`.

- [ ] **Step 3: Replace the dashboard types**

In `src/lib/api/types.ts`, delete `DashboardPick`, `DashboardGap` and `DashboardRevisionItem`. Keep `DashboardEvidence`, `DashboardAttempt` and `DashboardTodayItem`. Add after `DashboardEvidence`:

```ts
export type DashboardSessionKind =
  | "RETRIEVE"
  | "PRACTICE"
  | "LEARN"
  | "CONTINUE"
  | "MOCK"
  | "PAST";

/** One step of today's session. `doneAt` is an ISO string once finished. */
export type DashboardSessionItem = DashboardEvidence & {
  id: string;
  kind: DashboardSessionKind;
  source: "PLAN" | "ENGINE";
  topicId: string | null;
  subjectId: string | null;
  title: string;
  slug: string | null;
  minutes: number;
  reason: string;
  doneAt: string | null;
  lessonId: string | null;
  dueCards: number;
  planItemId: string | null;
  activityType: string | null;
  mastery: number | null;
  retention: number | null;
  retentionSource: "CARDS" | "ESTIMATE" | null;
  evidenceLevel: "NONE" | "THIN" | "ENOUGH";
};

export type DashboardSession = {
  minutes: number;
  done: number;
  total: number;
  items: DashboardSessionItem[];
};
```

In `DashboardOut`, delete `keepLearning`, `learningPicks`, `gaps`, `revision` and `revisionTotal`, and add `session?: DashboardSession | null;` in their place.

- [ ] **Step 4: Write the link helper**

Create `src/lib/session-links.ts`:

```ts
import type { DashboardSessionItem } from "@/lib/api/types";

type SubjectMap = Record<string, { slug: string; name: string; code: string }>;

/**
 * Where a session item's button goes, or `null` when it cannot be resolved
 * (a topic whose subject is no longer in the payload, or a missing slug).
 */
export function sessionItemHref(
  item: DashboardSessionItem,
  subjects: SubjectMap,
): string | null {
  const subject = item.subjectId ? subjects[item.subjectId] : undefined;

  if (item.kind === "MOCK") return "/practice/mock-exam";
  if (item.kind === "PAST") {
    return subject
      ? `/practice/past-questions/${subject.slug}`
      : "/practice/past-questions";
  }

  if (!subject || !item.slug) return null;
  const topic = `/classroom/${subject.slug}/${item.slug}`;

  switch (item.kind) {
    case "RETRIEVE":
      return item.dueCards > 0 ? "/flashcards" : `${topic}/practice`;
    case "PRACTICE":
      return `${topic}/practice`;
    case "CONTINUE":
      return `${topic}/study`;
    case "LEARN":
      return topic;
    default:
      return null;
  }
}

export function sessionActionLabel(item: DashboardSessionItem): string {
  switch (item.kind) {
    case "RETRIEVE":
      return item.dueCards > 0 ? "Review cards" : "Refresh";
    case "PRACTICE":
      return "Practise";
    case "CONTINUE":
      return "Continue";
    case "LEARN":
      return "Start lesson";
    default:
      return "Start";
  }
}

export function sessionKindLabel(item: DashboardSessionItem): string {
  switch (item.kind) {
    case "RETRIEVE":
      return "Revise";
    case "PRACTICE":
      return "Practise";
    case "CONTINUE":
      return "Continue";
    case "LEARN":
      return "New";
    case "MOCK":
      return "Mock exam";
    case "PAST":
      return "Past questions";
  }
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-session-links.mts`
Expected: all tests pass.

- [ ] **Step 6: Update `dashboard.ts`**

In `src/lib/dashboard.ts`: remove the `DashboardGap`, `DashboardPick`, `DashboardRevisionItem` imports and add `DashboardSession`. In `DashboardData` replace the four rail fields (`learningPicks`, `gaps`, `revision`, `revisionTotal`, with their doc comment) with:

```ts
  /** Today's study session, or null until the student has any activity. */
  session: DashboardSession | null;
```

In the returned object replace the four rail lines with `session: dash.session ?? null,`.

- [ ] **Step 7: Create the component**

Create `src/components/path/todays-session.tsx`:

```tsx
import Link from "next/link";
import { LuArrowRight, LuCircleCheck } from "react-icons/lu";
import type { DashboardSession } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { evidenceLabel } from "@/lib/evidence-display";
import {
  sessionActionLabel,
  sessionItemHref,
  sessionKindLabel,
} from "@/lib/session-links";

type Subjects = Record<string, { slug: string; name: string; code: string }>;

const KIND_VARIANT = {
  RETRIEVE: "amber",
  PRACTICE: "blue",
  CONTINUE: "green",
  LEARN: "purple",
  MOCK: "neutral",
  PAST: "neutral",
} as const;

export function TodaysSession({
  session,
  subjects,
}: {
  session: DashboardSession;
  subjects: Subjects;
}) {
  if (session.items.length === 0) {
    return (
      <div className="card p-4 text-sm text-muted">
        Nothing urgent today — pick any subject below and keep your streak going.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold text-muted">
        {session.done} of {session.total} done · about {session.minutes} min
      </p>
      <ol className="space-y-3">
        {session.items.map((item, index) => {
          const subject = item.subjectId ? subjects[item.subjectId] : undefined;
          const href = item.doneAt ? null : sessionItemHref(item, subjects);
          const evidence =
            item.topicId == null
              ? null
              : (evidenceLabel(item) ??
                (item.evidenceLevel === "ENOUGH" && item.mastery != null
                  ? `${item.mastery}% mastery`
                  : null));
          return (
            <li
              key={item.id}
              className={cn("card flex items-center gap-3 p-4", item.doneAt && "opacity-60")}
            >
              <span
                className={cn(
                  "flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl text-sm font-bold",
                  item.doneAt
                    ? "bg-success-soft text-success"
                    : "bg-primary-soft text-primary",
                )}
              >
                {item.doneAt ? <LuCircleCheck className="h-5 w-5" /> : index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-bold text-foreground">{item.title}</p>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                  <Badge variant={KIND_VARIANT[item.kind]}>{sessionKindLabel(item)}</Badge>
                  {subject && (
                    <span className="text-xs font-medium text-muted">
                      {subject.code} · {subject.name}
                    </span>
                  )}
                  <span className="text-xs text-muted">{item.minutes} min</span>
                </div>
                <p className="mt-1 text-xs text-muted">
                  {item.reason}
                  {evidence ? ` · ${evidence}` : ""}
                </p>
              </div>
              {href && (
                <Link
                  href={href}
                  className="inline-flex flex-shrink-0 items-center gap-1 rounded-xl bg-primary px-3 py-2 text-xs font-bold text-white transition-transform hover:scale-[1.02] active:scale-[0.98]"
                >
                  {sessionActionLabel(item)}
                  <LuArrowRight className="h-3.5 w-3.5" />
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
```

- [ ] **Step 8: Use it on the dashboard page**

In `src/app/(dashboard)/dashboard/page.tsx`:

1. In the `react-icons/lu` import, remove `LuCompass`, `LuTriangleAlert`, `LuRotateCcw` (leave the rest).
2. Replace the three imports of `NextTopics`, `GapList`, `RevisionQueue` with `import { TodaysSession } from "@/components/path/todays-session";`.
3. In the `getDashboardData` destructuring, replace `learningPicks, gaps, revision, revisionTotal,` with `session,`.
4. Replace the two sections "Learning path — next for you" and "Learning path — revise today" (the two `{hasActivity && (<section>…</section>)}` blocks) with one:

```tsx
      {/* Today's session (hidden until the student has activity) */}
      {hasActivity && session && (
        <section>
          <h2 className="mb-3 section-label">Today&apos;s session</h2>
          <TodaysSession session={session} subjects={subjects} />
        </section>
      )}
```

5. Update the comment inside the destructuring that mentions "Next for you" and "Revise today" to say the session stays hidden until the student has engaged somewhere.

- [ ] **Step 9: Delete the replaced components and unused types**

Run: `grep -rn "next-topics\|gap-list\|revision-queue\|NextTopics\|GapList\|RevisionQueue" src scripts`
Expected: no matches except the three files themselves. Then delete `src/components/path/next-topics.tsx`, `src/components/path/gap-list.tsx`, `src/components/path/revision-queue.tsx`.

Run: `grep -rn "TopicGap\|NextTopicRecommendation\|RevisionQueueItem\|RecommendationFactors\|CONTINUE_REASON\|GapCategory" src scripts`
Delete from `src/types/learning.ts` every export that no longer has a user (expected: `TopicGap`, `RecommendationFactors`, `NextTopicRecommendation`, `CONTINUE_REASON`, `RevisionQueueItem`, and `GapCategory` if nothing else uses it). Keep `MasteryLevel`, `EvidenceFields` and anything still imported elsewhere.

- [ ] **Step 10: Typecheck, lint, test**

Run: `npx tsc --noEmit && npm run lint && npm test`
Expected: no type errors, no lint errors, all `node:test` suites pass (including `test-session-links.mts` and `test-evidence-display.mts`).

- [ ] **Step 11: Checkpoint**

Fix anything the three commands report, then re-run them until clean.

---

### Task 6: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full automated checks**

Backend: `uv run ruff check app tests && uv run pytest -q`. Frontend: `npx tsc --noEmit && npm run lint && npm test`.
Expected: everything green.

- [ ] **Step 2: Apply the migration to a dev database**

Run: `uv run alembic upgrade head`
Expected: ends at `f6a3d9b1c470`. Then `uv run alembic downgrade -1 && uv run alembic upgrade head` succeeds.

- [ ] **Step 3: Load the dashboard as a student with data**

Start the backend and frontend (see each project's `README.md` for the dev commands), sign in as a student who has answered questions, and open `/dashboard`. Check:

1. One "Today's session" section shows 1–4 numbered items; the old three rails are gone.
2. Each item shows kind badge, minutes, a reason made of real numbers, and an action button that opens the right page (practice, flashcards, lesson).
3. Reload twice: the same items appear in the same order (stored in `DailySession`; confirm one row with `select "day", jsonb_array_length(items) from "DailySession" where "studentId" = '<id>';`).
4. Answer five questions on a PRACTICE item's topic, return to `/dashboard`: that item shows a check mark and "1 of N done", and the list order is unchanged.
5. A brand-new student (no attempts, answers or evidence) sees no session section and no `DailySession` row.
6. Deactivate the subject of one session item in the admin console, reload: that item is gone and the counts adjust.

- [ ] **Step 4: Report**

Write down what passed and anything that did not, with the exact output for any failure, before declaring the work done. Do not claim completion on the strength of the unit tests alone: queries in `retrievability_rows` and `recent_outcomes_by_topic` are only exercised by this step.
