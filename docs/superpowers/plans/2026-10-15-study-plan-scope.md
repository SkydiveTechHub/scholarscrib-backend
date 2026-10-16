# Study Plan Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A study plan has an explicit class and term chosen by the student, the planner only schedules topics from that scope, mastery data reaches the planner, and the classroom opens on the student's own class.

**Architecture:** `StudyPlan` stores `scopeClassLevel` and `scopeTerm`. The pure planner (`app/services/planner/`) filters strictly by scope; gap-fill may only reach back to earlier scopes. The service layer reads the scope from the plan (the profile class is only used for calendar pacing and defaults). The frontend setup form gets class and term chips and a prompt when the chosen class differs from the profile class.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, pytest (`asyncio_mode = auto`), Next.js, React, node:test (`scripts/test-*.mts`).

**Spec:** `docs/superpowers/specs/2026-10-15-study-plan-scope-design.md` (backend repo). It builds on `2026-09-14-study-plan-term-mode-design.md`.

**Repos:** backend `C:\Users\user\Desktop\Scholars\scholarscrib-backend`, frontend `C:\Users\user\Desktop\Scholars\scholarscrib-frontend`. Neither folder is a git repository, so there are no commit steps; each task ends with a **Verify** step instead.

**Commands:** backend `uv run pytest <path> -v` from the backend folder (run `uv sync` first if `uv run` reports missing packages). Frontend `node --import tsx --test --test-force-exit scripts/<file>.mts` and `npx tsc --noEmit` from the frontend folder.

## Global Constraints

- Class levels are `SS1 | SS2 | SS3`; terms are `FIRST | SECOND | THIRD`. The nine-slot order is SS1 First = 0 … SS3 Third = 8 (matches frontend `scopeOrdinal` in `src/lib/curriculum-scope.ts`).
- A plan only schedules topics where `topic.class_level == scope class` and `topic.term == scope term`. Gap-fill may only use topics with a lower scope ordinal. SS3 exam share (`select_exam_topics`) keeps its class-level filter and is not changed.
- Exam date and exam mode are accepted only when the **plan's** class is `SS3`.
- Topics without a curriculum level are excluded from planning (never defaulted to SS1 First).
- Calendar pacing (week of term) applies only when the plan's class equals the student's profile class and the plan's term is the live calendar term. Otherwise the plan starts at the scope's first topic.
- Mastery/pretest/revision loading must never make a replan fail.
- UI copy: prompt text is "Your account is set to {profile}. Update it to {chosen} as well?" with buttons "Update my class" and "Keep {profile}".

## Review Focus

1. A subject with zero topics in the chosen scope: no crash, no items from other scopes (Task 2).
2. A topic with no curriculum level: left out of plans, positions and options, not treated as SS1 First (Task 4).
3. Holiday or non-current term with the scope not matching the calendar: starts at the first topic, not the last (Task 2).
4. Student changes the plan's class/term after setting "where is your class": old positions are removed, not left pointing outside the scope (Task 5).
5. Mastery loading throws: replan still succeeds with empty mastery (Task 6).
6. Profile class update fails from the prompt: the plan is not saved and an error shows (Task 8).
7. Profile class is null: defaults fall back to SS1 and no prompt appears (Tasks 5, 7).
8. Delete with no active plan, or twice in a row: a clean 404, no crash (Task 10).

---

## File Structure

**Backend (modify unless noted)**
- `app/services/planner/mode.py`: add `scope_ordinal`; `plan_settings_problem` unchanged in shape.
- `app/services/planner/term_context.py`: add `default_term`.
- `app/services/planner/topics.py`: strict scope selection, new `_locate`, `calendar_topic_id` signature.
- `app/services/planner/term_plan.py`: `PlannerInput` gains `term`, `home_class_level`.
- `app/services/planner/service.py`: scope-aware services, `_plan_topics` exclusion, mastery loading.
- `app/database/models/planner.py`: two columns on `StudyPlan`.
- `alembic/versions/b7d41c9e5a02_add_study_plan_scope.py` (create).
- `app/api/schemas.py`: `StudyPlanIn.classLevel/term`.
- `app/api/responses.py`: `PlanPageOut.currentTerm`.
- `tests/test_plan_scope.py` (create), `tests/test_engines.py`, `tests/test_study_plan_payload.py`.

**Frontend (modify unless noted)**
- `src/lib/study-plan-scope.ts` (create): pure helpers.
- `scripts/test-study-plan-scope.mts` (create).
- `src/lib/study-plan.ts`, `src/lib/study-plan-route.ts`: scope in types and loader.
- `src/components/study-plan/plan-setup-form.tsx`, `src/components/study-plan/study-plan-view.tsx`.
- `src/components/study-plan/class-mismatch-modal.tsx` (create).
- `src/lib/classroom.ts`, `src/app/(dashboard)/classroom/[subjectSlug]/page.tsx`, `scripts/test-classroom.mts`.

---

### Task 1: Scope helpers

**Files:**
- Modify: `app/services/planner/mode.py`
- Modify: `app/services/planner/term_context.py`
- Create: `tests/test_plan_scope.py`

**Interfaces:**
- Produces: `scope_ordinal(class_level: str, term: str) -> int` (mode.py); `default_term(ctx: dict) -> str` (term_context.py).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_plan_scope.py`:

```python
from app.services.planner.mode import scope_ordinal
from app.services.planner.term_context import (
    TermRange,
    default_term,
    resolve_term_context,
)


def test_scope_ordinal_orders_the_nine_slots():
    assert scope_ordinal("SS1", "FIRST") == 0
    assert scope_ordinal("SS1", "THIRD") == 2
    assert scope_ordinal("SS2", "FIRST") == 3
    assert scope_ordinal("SS3", "THIRD") == 8
    assert scope_ordinal("SS1", "THIRD") < scope_ordinal("SS2", "FIRST")


def _terms():
    return [
        TermRange("2026/2027", "FIRST", "2026-09-07", "2026-12-15"),
        TermRange("2026/2027", "SECOND", "2027-01-06", "2027-04-10"),
    ]


def test_default_term_is_the_live_term_in_term():
    ctx = resolve_term_context("2026-10-15", _terms())
    assert default_term(ctx) == "FIRST"


def test_default_term_is_the_upcoming_term_in_a_holiday():
    ctx = resolve_term_context("2026-12-20", _terms())
    assert ctx["kind"] == "holiday"
    assert default_term(ctx) == "SECOND"


def test_default_term_falls_back_to_the_previous_then_first():
    ctx = {
        "kind": "holiday",
        "previous": TermRange("s", "THIRD", "a", "b"),
        "next": None,
    }
    assert default_term(ctx) == "THIRD"
    assert default_term({"kind": "holiday", "previous": None, "next": None}) == "FIRST"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plan_scope.py -v`
Expected: FAIL with `ImportError: cannot import name 'scope_ordinal'`.

- [ ] **Step 3: Implement**

In `app/services/planner/mode.py` change the domain import and add the function (after `at_or_below_class`):

```python
from app.domain import CLASS_LEVELS, TERMS
```

```python
def scope_ordinal(class_level: str, term: str) -> int:
    """Position in the nine-slot syllabus, SS1 First term being 0."""
    return CLASS_LEVELS.index(class_level) * len(TERMS) + TERMS.index(term)
```

In `app/services/planner/term_context.py` add after `term_header_label`:

```python
def default_term(ctx: dict) -> str:
    """The term a new plan should start on: the live one, else the upcoming one."""
    if ctx["kind"] == "in_term":
        return ctx["current"].term
    upcoming = ctx.get("next")
    if upcoming:
        return upcoming.term
    previous = ctx.get("previous")
    return previous.term if previous else "FIRST"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_plan_scope.py -v`
Expected: 4 passed.

- [ ] **Step 5: Verify**

Run: `uv run pytest tests/test_engines.py -q`. Expected: still passes (nothing else changed yet).

---

### Task 2: Strict scope topic selection

**Files:**
- Modify: `app/services/planner/topics.py`
- Modify: `app/services/planner/term_plan.py`
- Modify: `tests/test_engines.py:231-300`
- Modify: `tests/test_plan_scope.py`

**Interfaces:**
- Consumes: `scope_ordinal` (Task 1).
- Produces:
  - `select_term_topics(*, subject_id, class_level, term, follow_calendar, term_context, topics, graph, state, pretest_passed, position_topic_id, carry_over, carry_over_only=False) -> SubjectSelection` (`class_level` and `term` are the plan's scope).
  - `calendar_topic_id(topics, class_level, term, follow_calendar, ctx) -> str | None`.
  - `PlannerInput.term: str` and `PlannerInput.home_class_level: str` (required, placed right after `class_level`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_scope.py`:

```python
from app.services.learning.graph import GraphEdge, GraphNode, build_graph
from app.services.planner.topics import (
    PlanTopic,
    calendar_topic_id,
    select_term_topics,
)


def _topic(topic_id, class_level="SS1", term="FIRST", order=1):
    return PlanTopic(
        id=topic_id,
        subject_id="maths",
        title=topic_id,
        slug=topic_id,
        order_index=order,
        estimated_minutes=30,
        waec_weight=1,
        jamb_weight=1,
        prerequisite_topic_id=None,
        class_level=class_level,
        term=term,
    )


def _graph(topics, edges=()):
    nodes = [
        GraphNode(t.id, t.subject_id, t.title, t.slug, t.order_index) for t in topics
    ]
    return build_graph(
        nodes, [GraphEdge(f"e{i}", s, t) for i, (s, t) in enumerate(edges)]
    )


def _in_term(term, week, total=12):
    return {
        "kind": "in_term",
        "source": "configured",
        "current": TermRange("2026/2027", term, "2026-09-07", "2026-12-15"),
        "weekOfTerm": week,
        "totalWeeks": total,
        "weeksLeft": total - week,
    }


def _select(
    topics,
    edges=(),
    *,
    class_level="SS1",
    term="FIRST",
    follow=True,
    ctx=None,
    position=None,
):
    return select_term_topics(
        subject_id="maths",
        class_level=class_level,
        term=term,
        follow_calendar=follow,
        term_context=ctx or _in_term("FIRST", 1),
        topics=topics,
        graph=_graph(topics, edges),
        state={},
        pretest_passed=set(),
        position_topic_id=position,
        carry_over=[],
    )


def test_selection_never_leaves_the_scope():
    topics = [
        _topic("a1", order=1),
        _topic("a2", order=2),
        _topic("later-term", term="SECOND"),
        _topic("later-class", class_level="SS2"),
    ]
    # a later-term topic is a prerequisite of a current one: it must be ignored
    selection = _select(topics, edges=[("later-term", "a1")])
    ids = {c.topic.id for c in selection.candidates}
    assert ids <= {"a1", "a2"}
    assert "later-term" not in ids and "later-class" not in ids


def test_gap_fill_reaches_back_to_an_earlier_term():
    topics = [_topic("old", term="FIRST"), _topic("now", term="SECOND")]
    selection = _select(
        topics,
        edges=[("old", "now")],
        term="SECOND",
        ctx=_in_term("SECOND", 1),
    )
    reasons = [(c.reason, c.topic.id) for c in selection.candidates]
    assert ("GAP_FILL", "old") in reasons
    assert ("CURRENT", "now") in reasons


def test_empty_scope_gives_no_candidates():
    selection = _select([_topic("other", term="SECOND")], term="FIRST")
    assert selection.candidates == []
    assert selection.class_index == -1


def test_live_term_follows_the_calendar_only_for_the_home_class():
    topics = [_topic(f"t{n}", order=n) for n in range(1, 7)]
    ctx = _in_term("FIRST", 6)
    assert calendar_topic_id(topics, "SS1", "FIRST", True, ctx) == "t3"
    # a plan for another class, or another term, starts at the first topic
    assert calendar_topic_id(topics, "SS1", "FIRST", False, ctx) == "t1"
    other = [_topic(f"s{n}", term="SECOND", order=n) for n in range(1, 4)]
    assert calendar_topic_id(other, "SS1", "SECOND", True, ctx) == "s1"


def test_holiday_revises_the_end_of_the_previous_term_only():
    topics = [_topic(f"t{n}", order=n) for n in range(1, 4)]
    holiday = {
        "kind": "holiday",
        "source": "configured",
        "previous": TermRange("2026/2027", "FIRST", "2026-09-07", "2026-12-15"),
        "next": TermRange("2026/2027", "SECOND", "2027-01-06", "2027-04-10"),
    }
    assert calendar_topic_id(topics, "SS1", "FIRST", True, holiday) == "t3"
    second = [
        _topic("s1", term="SECOND", order=1),
        _topic("s2", term="SECOND", order=2),
    ]
    assert calendar_topic_id(second, "SS1", "SECOND", True, holiday) == "s1"


def test_a_position_outside_the_scope_is_ignored():
    topics = [_topic("a1", order=1), _topic("a2", order=2), _topic("b1", term="SECOND")]
    selection = _select(topics, position="b1")
    assert selection.term_topics and all(
        t.term == "FIRST" for t in selection.term_topics
    )
    assert selection.class_index == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plan_scope.py -v`
Expected: FAIL (`TypeError: select_term_topics() got an unexpected keyword argument 'term'`).

- [ ] **Step 3: Rewrite the selection code in `app/services/planner/topics.py`**

Add the import near the top: `from app.services.planner.mode import scope_ordinal` (replace the `at_or_below_class` import only if `select_exam_topics` no longer needs it; it still does, so import both: `from app.services.planner.mode import at_or_below_class, scope_ordinal`).

Replace `_scope`, `_locate_by_calendar` and `_locate_class` with:

```python
def _scope(topics: list[PlanTopic], class_level: str, term: str) -> list[PlanTopic]:
    return sorted(
        [
            topic
            for topic in topics
            if topic.class_level == class_level and topic.term == term
        ],
        key=_by_order,
    )


def _before_scope(topic: PlanTopic, class_level: str, term: str) -> bool:
    return scope_ordinal(topic.class_level, topic.term) < scope_ordinal(
        class_level, term
    )


def _locate(
    term_topics: list[PlanTopic],
    term: str,
    ctx: dict,
    position_topic_id: str | None,
    follow_calendar: bool,
) -> dict:
    """Where the class is inside this scope's topics."""
    if not term_topics:
        return {"termTopics": [], "classIndex": -1, "preview": []}
    count = len(term_topics)
    index = next(
        (i for i, topic in enumerate(term_topics) if topic.id == position_topic_id),
        -1,
    )
    if index < 0:
        if follow_calendar and ctx["kind"] == "in_term" and ctx["current"].term == term:
            index = min(
                count - 1,
                int(((ctx["weekOfTerm"] - 1) / ctx["totalWeeks"]) * count),
            )
        elif (
            follow_calendar
            and ctx["kind"] == "holiday"
            and ctx.get("previous")
            and ctx["previous"].term == term
        ):
            index = count - 1
        else:
            index = 0
    return {
        "termTopics": term_topics,
        "classIndex": index,
        "preview": term_topics[index + 1 : index + 2],
    }
```

Change `select_term_topics`: signature gains `term: str` and `follow_calendar: bool` (after `class_level`), and the head of the body becomes:

```python
term_topics_all = _scope(topics, class_level, term)
earlier = [t for t in topics if _before_scope(t, class_level, term)]
by_id = {topic.id: topic for topic in [*earlier, *term_topics_all]}


def unmastered(topic: PlanTopic) -> bool:
    return not _is_mastered(state, topic.id)


located = _locate(
    term_topics_all, term, term_context, position_topic_id, follow_calendar
)
```
Delete the old `allowed = [...]` list and the old `_locate_class(...)` call. The rest of the function (carry-over, seeds, gap-fill, `behind_by`) is unchanged: `by_id` now holds only earlier-scope and in-scope topics, so `_gaps_for` can never return a later-scope topic and `carry_over` of a later-scope topic is dropped.

Replace `calendar_topic_id` with:

```python
def calendar_topic_id(
    topics: list[PlanTopic],
    class_level: str,
    term: str,
    follow_calendar: bool,
    ctx: dict,
) -> str | None:
    term_topics = _scope(topics, class_level, term)
    located = _locate(term_topics, term, ctx, None, follow_calendar)
    if located["classIndex"] < 0:
        return None
    return located["termTopics"][located["classIndex"]].id
```

In `app/services/planner/term_plan.py`, add two required fields after `class_level: str` in `PlannerInput`:

```python
    term: str
    home_class_level: str
```
and in `plan_window` pass to `select_term_topics`:

```python
class_level = (planner.class_level,)
term = (planner.term,)
follow_calendar = (planner.class_level == planner.home_class_level,)
```

- [ ] **Step 4: Update the old tests in `tests/test_engines.py`**

In `test_plan_window_stays_inside_the_class` add to `PlannerInput(...)`: `term="FIRST", home_class_level="SS1",` (after `class_level="SS1",`). In `test_term_topic_selection_mid_term` add to `select_term_topics(...)`: `term="FIRST", follow_calendar=True,` and change the assertion line to `assert calendar_topic_id(topics, "SS1", "FIRST", True, context) == "t3"`.

- [ ] **Step 5: Run to verify**

Run: `uv run pytest tests/test_plan_scope.py tests/test_engines.py -v`
Expected: all pass.

- [ ] **Step 6: Verify**

Run: `uv run ruff check app/services/planner tests/test_plan_scope.py`. Expected: no errors.

---

### Task 3: Data layer (model, migration, request schema)

**Files:**
- Modify: `app/database/models/planner.py`
- Create: `alembic/versions/b7d41c9e5a02_add_study_plan_scope.py`
- Modify: `app/api/schemas.py:216-229`
- Modify: `app/api/responses.py:432-441`

**Interfaces:**
- Produces: `StudyPlan.scope_class_level: str`, `StudyPlan.scope_term: str`; `StudyPlanIn.classLevel: ClassLevel`, `StudyPlanIn.term: Term`; `PlanPageOut.currentTerm: str | None`.

- [ ] **Step 1: Model**

In `app/database/models/planner.py` add `ClassLevel,` to the `from app.database.models.enums import (...)` list (alphabetically first). In `StudyPlan`, after `study_days`:

```python
    scope_class_level: Mapped[str] = mapped_column("scopeClassLevel", ClassLevel)
    scope_term: Mapped[str] = mapped_column("scopeTerm", Term)
```

- [ ] **Step 2: Migration**

Create `alembic/versions/b7d41c9e5a02_add_study_plan_scope.py`:

```python
"""Add StudyPlan.scopeClassLevel and scopeTerm

Revision ID: b7d41c9e5a02
Revises: e8b2c4f7a613
Create Date: 2026-10-15 10:00:00.000000

A plan now stores the class and term it is for. Existing plans are backfilled
with the student's class (SS1 when unset) and the term the plan was created in,
read from AcademicTerm when configured, else the built-in calendar.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "b7d41c9e5a02"
down_revision = "e8b2c4f7a613"
branch_labels = None
depends_on = None


def upgrade() -> None:
    class_level = postgresql.ENUM(
        "SS1", "SS2", "SS3", name="ClassLevel", create_type=False
    )
    term = postgresql.ENUM("FIRST", "SECOND", "THIRD", name="Term", create_type=False)
    op.add_column("StudyPlan", sa.Column("scopeClassLevel", class_level, nullable=True))
    op.add_column("StudyPlan", sa.Column("scopeTerm", term, nullable=True))
    op.execute(
        """
        UPDATE "StudyPlan" p
        SET "scopeClassLevel" = COALESCE(
            (SELECT u."classLevel" FROM "User" u WHERE u.id = p."studentId"),
            'SS1'
        )
        """
    )
    op.execute(
        """
        UPDATE "StudyPlan" p
        SET "scopeTerm" = COALESCE(
            (
                SELECT t."term" FROM "AcademicTerm" t
                WHERE p."createdAt"::date BETWEEN t."startsOn" AND t."endsOn"
                LIMIT 1
            ),
            CASE
                WHEN EXTRACT(MONTH FROM p."createdAt") IN (1, 2, 3) THEN 'SECOND'::"Term"
                WHEN EXTRACT(MONTH FROM p."createdAt") = 4
                     AND EXTRACT(DAY FROM p."createdAt") <= 26 THEN 'SECOND'::"Term"
                WHEN EXTRACT(MONTH FROM p."createdAt") IN (4, 5, 6, 7) THEN 'THIRD'::"Term"
                ELSE 'FIRST'::"Term"
            END
        )
        """
    )
    op.alter_column("StudyPlan", "scopeClassLevel", nullable=False)
    op.alter_column("StudyPlan", "scopeTerm", nullable=False)


def downgrade() -> None:
    op.drop_column("StudyPlan", "scopeTerm")
    op.drop_column("StudyPlan", "scopeClassLevel")
```

- [ ] **Step 3: Request and response schemas**

In `app/api/schemas.py` `StudyPlanIn`, add as the first two fields:

```python
    classLevel: ClassLevel
    term: Term
```
(`ClassLevel` and `Term` are already imported from `app.domain`.)

In `app/api/responses.py` `PlanPageOut` add `currentTerm: str | None = None` after `termSource`.

- [ ] **Step 4: Verify**

Run: `uv run alembic heads` → expect a single head `b7d41c9e5a02`.
Run: `uv run python -c "from app.api.schemas import StudyPlanIn; print(sorted(StudyPlanIn.model_fields)[:3])"` → expect it lists `classLevel`.
Do **not** run `alembic upgrade` against a shared database without the user's go-ahead; ask first.

---

### Task 4: Topic loading, position options, position validation

**Files:**
- Modify: `app/services/planner/service.py:74-121` and `SetStudyPlanPositionsService`
- Modify: `tests/test_study_plan_payload.py`

**Interfaces:**
- Consumes: scope fields from Task 3.
- Produces: `position_options(topics: list[PlanTopic], class_level: str, term: str) -> list[dict]`; `_plan_topics(session, subject_id) -> list[PlanTopic]` (excludes unmapped topics).

- [ ] **Step 1: Write the failing tests**

In `tests/test_study_plan_payload.py` replace `test_position_options_offer_topics_up_to_the_class_in_curriculum_order` with:

```python
def test_position_options_offer_only_the_plans_scope_in_curriculum_order():
    topics = [
        _topic("ss1-first-b", "SS1", "FIRST", 2, "Sets"),
        _topic("ss1-first-a", "SS1", "FIRST", 1),
        _topic("ss1-second", "SS1", "SECOND", 1),
        _topic("ss2-first", "SS2", "FIRST", 1),
    ]

    options = position_options(topics, "SS1", "FIRST")

    assert [o["id"] for o in options] == ["ss1-first-a", "ss1-first-b"]
    assert options[1] == {
        "id": "ss1-first-b",
        "title": "Sets",
        "scope": "SS1 First term",
    }


async def test_plan_topics_leave_out_topics_without_a_curriculum_level(monkeypatch):
    from types import SimpleNamespace as NS

    from app.services.planner import service

    def row(topic_id, level_id):
        return NS(
            id=topic_id,
            subject_id="maths",
            title=topic_id,
            slug=topic_id,
            order_index=1,
            estimated_minutes=30,
            waec_weight=1,
            jamb_weight=1,
            prerequisite_topic_id=None,
            curriculum_level_id=level_id,
        )

    async def for_subject(_, subject_id):
        return [row("mapped", "lv1"), row("unmapped", None), row("dangling", "gone")]

    async def by_id(_, level_id):
        return NS(class_level="SS2", term="SECOND") if level_id == "lv1" else None

    monkeypatch.setattr(service.topics_repository, "for_subject", for_subject)
    monkeypatch.setattr(service.curriculum_levels_repository, "by_id", by_id)

    topics = await service._plan_topics(None, "maths")

    assert [(t.id, t.class_level, t.term) for t in topics] == [
        ("mapped", "SS2", "SECOND")
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_study_plan_payload.py -v`
Expected: FAIL (`position_options() takes 2 positional arguments`; unmapped topic still returned).

- [ ] **Step 3: Implement**

In `app/services/planner/service.py` add near the top `import logging` and `logger = logging.getLogger(__name__)` (after the imports), then replace `position_options` and `_plan_topics`:

```python
def position_options(
    topics: list[PlanTopic], class_level: str, term: str
) -> list[dict]:
    """Topics a student may pick as "my class is here": the plan's own term only."""
    allowed = [t for t in topics if t.class_level == class_level and t.term == term]
    allowed.sort(key=lambda t: (t.order_index, t.id))
    return [
        {
            "id": t.id,
            "title": t.title,
            "scope": f"{t.class_level} {t.term.capitalize()} term",
        }
        for t in allowed
    ]


async def _plan_topics(session: AsyncSession, subject_id: str) -> list[PlanTopic]:
    """Topics with a curriculum level. Unmapped topics are left out of every plan."""
    rows = await topics_repository.for_subject(session, subject_id)
    topics = []
    for topic in rows:
        curriculum = (
            await curriculum_levels_repository.by_id(session, topic.curriculum_level_id)
            if topic.curriculum_level_id
            else None
        )
        if curriculum is None:
            logger.warning(
                "Topic %s (subject %s) has no curriculum level; left out of study plans",
                topic.id,
                subject_id,
            )
            continue
        topics.append(
            PlanTopic(
                id=topic.id,
                subject_id=topic.subject_id,
                title=topic.title,
                slug=topic.slug,
                order_index=topic.order_index,
                estimated_minutes=topic.estimated_minutes,
                waec_weight=topic.waec_weight,
                jamb_weight=topic.jamb_weight,
                prerequisite_topic_id=topic.prerequisite_topic_id,
                class_level=curriculum.class_level,
                term=curriculum.term,
            )
        )
    return topics
```
Remove the now-unused `_TERMS` constant and the `from app.domain import CLASS_LEVELS` import if nothing else uses them (`ruff` will tell).

In `SetStudyPlanPositionsService._apply_position`, replace the block that checks `at_or_below_class` (from `if topic.curriculum_level_id:` to the `raise ApiError(400, "That topic is above your class")`) with:

```python
curriculum = (
    await curriculum_levels_repository.by_id(self.session, topic.curriculum_level_id)
    if topic.curriculum_level_id
    else None
)
if curriculum is None or (curriculum.class_level, curriculum.term) != (
    plan.scope_class_level,
    plan.scope_term,
):
    raise ApiError(400, "That topic is outside your plan's term")
```

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_study_plan_payload.py -v`
Expected: the two new tests pass; `test_plan_payload_carries_every_field_the_page_reads` is still failing until Task 5 (it builds a plan without scope) — that is expected here.

- [ ] **Step 5: Verify**

Run: `uv run ruff check app/services/planner/service.py`. Fix unused imports it reports.

---

### Task 5: Services use the plan's scope

**Files:**
- Modify: `app/services/planner/service.py` (`_check`, `GetStudyPlanService`, `CreateStudyPlanService`, `UpdateStudyPlanSettingsService`, `ReplanStudyPlanService.process`)
- Modify: `tests/test_study_plan_payload.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: plan payload `plan["scope"] = {"classLevel", "term"}`, `plan["followsCalendar"]: bool`; page payload `currentTerm`; `_drop_positions_outside_scope(session, plan) -> None`.

- [ ] **Step 1: Write the failing tests**

In `test_plan_payload_carries_every_field_the_page_reads`: add `scope_class_level="SS1", scope_term="FIRST",` to the `plan = NS(...)`; change `_topic("t1", "SS1", "FIRST", 1, "Sets")` stays; add assertions at the end:

```python
    assert payload["scope"] == {"classLevel": "SS1", "term": "FIRST"}
    assert payload["followsCalendar"] is True
```
(`GetStudyPlanService(None, "s1", "SS1")` — profile class SS1 equals scope class and ctx is in FIRST, so it follows the calendar.)

Append two tests:

```python
async def test_plan_for_another_class_does_not_follow_the_calendar(monkeypatch):
    from datetime import UTC, date, datetime
    from types import SimpleNamespace as NS

    from app.services.planner import service
    from app.services.planner.term_context import TermRange, resolve_term_context

    plan = NS(
        id="p1",
        subject_ids=["maths"],
        target_exam=None,
        target_date=None,
        force_exam_mode=False,
        study_days=[1],
        weekday_minutes=30,
        weekend_minutes=60,
        planned_through=date(2026, 9, 28),
        outline=[],
        overload=None,
        created_at=datetime(2026, 9, 14, tzinfo=UTC),
        scope_class_level="SS2",
        scope_term="SECOND",
    )

    async def empty(*_):
        return []

    async def active_ids(_):
        return {"maths"}

    async def plan_topics(_, subject_id):
        return [
            _topic("a", "SS2", "SECOND", 1),
            _topic("b", "SS2", "SECOND", 2),
            _topic("x", "SS1", "FIRST", 1),
        ]

    monkeypatch.setattr(service.subjects_repository, "active_ids", active_ids)
    monkeypatch.setattr(service.subjects_repository, "active_ordered", empty)
    monkeypatch.setattr(service.plan_items_repository, "for_plan", empty)
    monkeypatch.setattr(service.plan_positions_repository, "for_plan", empty)
    monkeypatch.setattr(service.topics_repository, "for_ids", empty)
    monkeypatch.setattr(service, "_plan_topics", plan_topics)

    ctx = resolve_term_context(
        "2026-09-14", [TermRange("2026/2027", "FIRST", "2026-09-07", "2026-11-29")]
    )
    payload = await service.GetStudyPlanService(None, "s1", "SS1")._serialize(plan, ctx)

    assert payload["scope"] == {"classLevel": "SS2", "term": "SECOND"}
    assert payload["followsCalendar"] is False
    assert payload["calendarPositions"] == {"maths": "a"}
    assert [o["id"] for o in payload["positionOptions"]["maths"]] == ["a", "b"]


async def test_changing_scope_drops_positions_outside_it(monkeypatch):
    from types import SimpleNamespace as NS

    from app.services.planner import service

    inside = NS(topic_id="in")
    outside = NS(topic_id="out")
    removed = []

    async def for_plan(_, plan_id):
        return [inside, outside]

    async def topic_by_id(_, topic_id):
        return NS(id=topic_id, curriculum_level_id=f"lv-{topic_id}")

    async def level_by_id(_, level_id):
        return (
            NS(class_level="SS2", term="SECOND")
            if level_id == "lv-in"
            else NS(class_level="SS1", term="FIRST")
        )

    async def remove(_, row):
        removed.append(row)

    monkeypatch.setattr(service.plan_positions_repository, "for_plan", for_plan)
    monkeypatch.setattr(service.plan_positions_repository, "remove", remove)
    monkeypatch.setattr(service.topics_repository, "by_id", topic_by_id)
    monkeypatch.setattr(service.curriculum_levels_repository, "by_id", level_by_id)

    plan = NS(id="p1", scope_class_level="SS2", scope_term="SECOND")
    await service._drop_positions_outside_scope(None, plan)

    assert removed == [outside]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_study_plan_payload.py -v`
Expected: FAIL (`KeyError: 'scope'`, `AttributeError: _drop_positions_outside_scope`).

- [ ] **Step 3: Implement**

In `app/services/planner/service.py`:

a) Imports: from `app.services.planner.term_context` add `default_term`.

b) `_check` becomes (class comes from the body, the plan's class):

```python
def _check(body: StudyPlanIn) -> None:
    target = body.targetDate.isoformat() if body.targetDate else None
    problem = plan_settings_problem(
        PlanSettingsCheck(
            class_level=body.classLevel,
            target_date=target,
            force_exam_mode=body.forceExamMode,
            study_days=body.studyDays,
            weekday_minutes=body.weekdayMinutes,
            weekend_minutes=body.weekendMinutes,
            today=lagos_day_key(),
        )
    )
    if problem:
        raise ApiError(400, problem)
    if bool(body.targetExam) != bool(body.targetDate):
        raise ApiError(400, "An exam target needs both an exam and a date")
```

c) Add the helper after `_ensure_subjects`:

```python
async def _drop_positions_outside_scope(session: AsyncSession, plan: StudyPlan) -> None:
    """A position belongs to one class and term; changing scope invalidates it."""
    for row in await plan_positions_repository.for_plan(session, plan.id):
        topic = (
            await topics_repository.by_id(session, row.topic_id)
            if row.topic_id
            else None
        )
        curriculum = (
            await curriculum_levels_repository.by_id(session, topic.curriculum_level_id)
            if topic and topic.curriculum_level_id
            else None
        )
        if curriculum is None or (curriculum.class_level, curriculum.term) != (
            plan.scope_class_level,
            plan.scope_term,
        ):
            await plan_positions_repository.remove(session, row)
```

d) `GetStudyPlanService.process`: use `profile_class = self.class_level or "SS1"`; return `"currentTerm": default_term(ctx)` and `"defaults": DEFAULT_MINUTES.get(profile_class)`; replan call keeps `profile_class`. `_days_to_exam`: `resolve_plan_mode(plan.scope_class_level, _day(plan.target_date), plan.force_exam_mode)`.

e) `_serialize`: replace the class lines with

```python
        class_level = plan.scope_class_level
        term = plan.scope_term
        follow_calendar = class_level == (self.class_level or "SS1")
        target = _day(plan.target_date) if plan.target_date else None
        mode = resolve_plan_mode(class_level, target, plan.force_exam_mode)
```
and in the per-subject loop:

```python
            calendar_positions[subject_id] = calendar_topic_id(
                topics, class_level, term, follow_calendar, ctx
            )
            options[subject_id] = position_options(topics, class_level, term)
```
and add to the returned dict:

```python
            "scope": {"classLevel": class_level, "term": term},
            "followsCalendar": follow_calendar
            and ctx["kind"] == "in_term"
            and ctx["current"].term == term,
```
Note: the existing test expects `followsCalendar is True` for SS1/FIRST in a FIRST-term context, and `False` for the SS2/SECOND plan; both hold.

f) `CreateStudyPlanService.process`: `_check(self.body)`; in `StudyPlan(...)` add `scope_class_level=self.body.classLevel, scope_term=self.body.term,`.

g) `UpdateStudyPlanSettingsService.process`: `_check(self.body)`; after setting `plan.study_days`, add `plan.scope_class_level = self.body.classLevel` and `plan.scope_term = self.body.term`; before the replan call: `await _drop_positions_outside_scope(self.session, plan)`.

h) `ReplanStudyPlanService.process`: replace the mode/PlannerInput lines:

```python
        scope_class = self.plan.scope_class_level
        mode = resolve_plan_mode(scope_class, target, self.plan.force_exam_mode)
```
and in `PlannerInput(...)`: `class_level=scope_class, term=self.plan.scope_term, home_class_level=self.class_level,`. (`self.class_level` stays the profile class passed by callers.)

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_study_plan_payload.py tests/test_plan_scope.py tests/test_engines.py -v`
Expected: all pass.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q` (whole backend suite) and `uv run ruff check app tests`. Expected: no new failures.

---

### Task 6: Mastery, pretest and revision-due reach the planner

**Files:**
- Modify: `app/services/planner/service.py` (`ReplanStudyPlanService`)
- Modify: `tests/test_plan_scope.py`

**Interfaces:**
- Consumes: `GetTopicMasteryService` (`app/services/learning/mastery_store.py`), `metrics_repository.passed_topic_ids`, `graph_colour`.
- Produces: `ReplanStudyPlanService._learning_inputs(nodes) -> tuple[dict, set[str], list[RevisionDue]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_scope.py`:

```python
async def test_learning_inputs_carry_mastery_pretest_and_decayed_revision(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace as NS

    from app.services.learning.mastery import TopicState
    from app.services.planner import service

    class FakeSession:
        @asynccontextmanager
        async def begin_nested(self):
            yield

    class FakeMastery:
        def __init__(self, session, student_id, topic_ids):
            pass

        async def process(self):
            return {
                "fresh": TopicState(
                    "fresh", mastery=90, observations=20, retention=0.95
                ),
                "fading": TopicState(
                    "fading", mastery=60, observations=10, retention=0.3
                ),
            }

    async def passed(_, student_id, topic_ids):
        return ["fresh"]

    monkeypatch.setattr(service, "GetTopicMasteryService", FakeMastery)
    monkeypatch.setattr(service.metrics_repository, "passed_topic_ids", passed)

    plan = NS(student_id="s1")
    replan = service.ReplanStudyPlanService(FakeSession(), plan, "SS1")
    nodes = [
        GraphNode("fresh", "maths", "Fresh", "fresh", 1),
        GraphNode("fading", "maths", "Fading", "fading", 2),
    ]
    state, pretest, due = await replan._learning_inputs(nodes)

    assert set(state) == {"fresh", "fading"}
    assert pretest == {"fresh"}
    assert [d.topic_id for d in due] == ["fading"]
    assert due[0].subject_id == "maths" and due[0].title == "Fading"


async def test_learning_inputs_fall_back_to_empty_when_loading_fails(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace as NS

    from app.services.planner import service

    class FakeSession:
        @asynccontextmanager
        async def begin_nested(self):
            yield

    class Boom:
        def __init__(self, *args):
            pass

        async def process(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(service, "GetTopicMasteryService", Boom)
    replan = service.ReplanStudyPlanService(FakeSession(), NS(student_id="s1"), "SS1")
    result = await replan._learning_inputs([GraphNode("t", "maths", "T", "t", 1)])

    assert result == ({}, set(), [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plan_scope.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'GetTopicMasteryService'`).

- [ ] **Step 3: Implement**

In `app/services/planner/service.py` add imports:

```python
from app.database.repositories.learning import metrics_repository
from app.services.learning.mastery import graph_colour
from app.services.learning.mastery_store import GetTopicMasteryService
from app.services.planner.layout import RevisionDue
```

Add to `ReplanStudyPlanService`:

```python
    async def _learning_inputs(
        self, nodes: list[GraphNode]
    ) -> tuple[dict, set[str], list[RevisionDue]]:
        """Mastery, pretests and fading topics. A failure here must not fail a replan."""
        topic_ids = [node.id for node in nodes]
        if not topic_ids:
            return {}, set(), []
        try:
            async with self.session.begin_nested():
                state = await GetTopicMasteryService(
                    self.session, self.plan.student_id, topic_ids
                ).process()
                passed = set(
                    await metrics_repository.passed_topic_ids(
                        self.session, self.plan.student_id, topic_ids
                    )
                )
        except Exception:
            logger.exception(
                "Could not load learning state for plan of student %s",
                self.plan.student_id,
            )
            return {}, set(), []
        due = [
            RevisionDue(
                topic_id=node.id,
                subject_id=node.subject_id,
                title=node.title,
                reason="Fading from memory, time to refresh it",
            )
            for node in nodes
            if node.id in state and graph_colour(state[node.id]) == "DECAYED"
        ]
        return state, passed, due
```

In `process()`, after `edges = await self._edges(nodes)` add `state, pretest_passed, revision_due = await self._learning_inputs(nodes)` and in `PlannerInput(...)` replace `state={},` with `state=state, pretest_passed=pretest_passed, revision_due=revision_due,`.

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_plan_scope.py -v`
Expected: all pass.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`. Expected: no new failures. Check `RevisionDue` import does not create a cycle (`uv run python -c "import app.services.planner"`).

---

### Task 7: Frontend scope types, helpers, loader

**Files:**
- Create: `src/lib/study-plan-scope.ts`
- Create: `scripts/test-study-plan-scope.mts`
- Modify: `src/lib/study-plan.ts`
- Modify: `src/lib/study-plan-route.ts`

**Interfaces:**
- Produces:
  - `type PlanScope = { classLevel: ClassLevel; term: Term }`
  - `defaultScope(profileClass: ClassLevel | null, currentTerm: Term | null): PlanScope`
  - `needsClassPrompt(chosen: ClassLevel, profile: ClassLevel | null): boolean`
  - `scopeHeader(scope: PlanScope, followsCalendar: boolean, termLabel: string): string`
  - `StudyPlanData.scope: PlanScope`, `StudyPlanData.followsCalendar: boolean`, `StudyPlanPageData.currentTerm: Term`

- [ ] **Step 1: Write the failing tests**

Create `scripts/test-study-plan-scope.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { defaultScope, needsClassPrompt, scopeHeader } from "../src/lib/study-plan-scope";

test("defaultScope uses the profile class and the current term", () => {
  assert.deepEqual(defaultScope("SS2", "SECOND"), { classLevel: "SS2", term: "SECOND" });
});

test("defaultScope falls back to SS1 and FIRST", () => {
  assert.deepEqual(defaultScope(null, null), { classLevel: "SS1", term: "FIRST" });
});

test("needsClassPrompt only fires when the class really differs", () => {
  assert.equal(needsClassPrompt("SS2", "SS1"), true);
  assert.equal(needsClassPrompt("SS1", "SS1"), false);
  assert.equal(needsClassPrompt("SS2", null), false);
});

test("scopeHeader shows the week of term only while following the calendar", () => {
  assert.equal(
    scopeHeader({ classLevel: "SS1", term: "FIRST" }, true, "1st term · Week 6 of 12"),
    "SS1 · 1st term · Week 6 of 12",
  );
  assert.equal(
    scopeHeader({ classLevel: "SS2", term: "SECOND" }, false, "1st term · Week 6 of 12"),
    "SS2 · 2nd term",
  );
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-study-plan-scope.mts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the helpers**

Create `src/lib/study-plan-scope.ts`:

```ts
import { TERM_LABELS, type ClassLevel, type Term } from "./curriculum-scope";

// The class and term a study plan is for. It starts as the student's own class
// and the live term, but the student can plan for another one.

export type PlanScope = { classLevel: ClassLevel; term: Term };

export function defaultScope(
  profileClass: ClassLevel | null,
  currentTerm: Term | null,
): PlanScope {
  return { classLevel: profileClass ?? "SS1", term: currentTerm ?? "FIRST" };
}

/** Planning for a class other than the profile's is worth asking about. */
export function needsClassPrompt(chosen: ClassLevel, profile: ClassLevel | null): boolean {
  return profile !== null && chosen !== profile;
}

export function scopeHeader(
  scope: PlanScope,
  followsCalendar: boolean,
  termLabel: string,
): string {
  return `${scope.classLevel} · ${followsCalendar ? termLabel : TERM_LABELS[scope.term]}`;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-study-plan-scope.mts`
Expected: 4 passing.

- [ ] **Step 5: Types and loader**

In `src/lib/study-plan.ts` add `import type { PlanScope } from "./study-plan-scope";`, then in `StudyPlanData` add

```ts
  scope: PlanScope;
  /** True while the plan's term is the live school term for the student's class. */
  followsCalendar: boolean;
```
and in `StudyPlanPageData` add `currentTerm: Term;` (import `Term` from `./curriculum-scope` alongside `ClassLevel`).

In `src/lib/study-plan-route.ts` (look at how `asClassLevel` is defined there and mirror it), add:

```ts
function asTerm(value: unknown): Term {
  return value === "FIRST" || value === "SECOND" || value === "THIRD" ? value : "FIRST";
}
```
Import `Term` from `./curriculum-scope`. In `asPlan` return add:

```ts
    scope: (() => {
      const raw = value.scope && typeof value.scope === "object"
        ? (value.scope as Record<string, unknown>)
        : {};
      return { classLevel: asClassLevel(raw.classLevel) ?? classLevel ?? "SS1", term: asTerm(raw.term) };
    })(),
    followsCalendar: asBoolean(value.followsCalendar),
```
and in `getStudyPlanPageData` return add `currentTerm: asTerm(data.currentTerm),`.

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit`. Expected: errors only in `plan-setup-form.tsx`/`study-plan-view.tsx` consumers if any construct these types (fixed in Task 8); none in the files changed here. Run `node --import tsx --test --test-force-exit scripts/test-study-plan-display.mts scripts/test-study-plan-scope.mts` → pass.

---

### Task 8: Setup form, class prompt, plan header

**Files:**
- Create: `src/components/study-plan/class-mismatch-modal.tsx`
- Modify: `src/components/study-plan/plan-setup-form.tsx`
- Modify: `src/components/study-plan/study-plan-view.tsx`

**Interfaces:**
- Consumes: `defaultScope`, `needsClassPrompt`, `scopeHeader` (Task 7); `useUpdateProfile` from `@/hooks/api/use-user`; `Modal` from `@/components/ui/modal`; `CLASS_LEVELS`, `TERMS`, `TERM_LABELS` from `@/lib/curriculum-scope`.
- Produces: `PlanSettings` now includes `classLevel: ClassLevel; term: Term`.

- [ ] **Step 1: The prompt component**

Create `src/components/study-plan/class-mismatch-modal.tsx`:

```tsx
"use client";

import { Modal } from "@/components/ui/modal";
import { buttonClass } from "@/components/ui/button";

/** Asked when a plan is for a different class than the account's. */
export function ClassMismatchModal({
  open,
  profileClass,
  chosenClass,
  busy,
  onUpdate,
  onKeep,
  onClose,
}: {
  open: boolean;
  profileClass: string;
  chosenClass: string;
  busy: boolean;
  onUpdate: () => void;
  onKeep: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      title="Update your class?"
      description={`Your account is set to ${profileClass}. Update it to ${chosenClass} as well?`}
      busy={busy}
      onClose={onClose}
      footer={
        <div className="flex flex-wrap justify-end gap-3">
          <button type="button" disabled={busy} onClick={onKeep} className={buttonClass("secondary", "md")}>
            Keep {profileClass}
          </button>
          <button type="button" disabled={busy} onClick={onUpdate} className={buttonClass("primary", "md")}>
            Update my class
          </button>
        </div>
      }
    >
      <p className="text-sm text-muted">
        Either way, this plan will be for {chosenClass}.
      </p>
    </Modal>
  );
}
```

- [ ] **Step 2: Setup form**

In `src/components/study-plan/plan-setup-form.tsx`:

- Imports: `CLASS_LEVELS, TERMS, TERM_LABELS, type ClassLevel, type Term` from `@/lib/curriculum-scope`; `defaultScope, needsClassPrompt` from `@/lib/study-plan-scope`; `useUpdateProfile` from `@/hooks/api/use-user`; `ClassMismatchModal` from `./class-mismatch-modal`.
- `PlanSettings` add `classLevel: ClassLevel; term: Term;`.
- Initial state (inside `useState` initialiser), before `subjectIds`:

```ts
    ...(plan?.scope ?? defaultScope(data.classLevel, data.currentTerm)),
```
- Replace `const isSS3 = data.classLevel === "SS3";` with `const isSS3 = settings.classLevel === "SS3";` (move it after `settings` is declared; it already is).
- Add state: `const [prompting, setPrompting] = useState(false);` and `const updateProfile = useUpdateProfile();`.
- Replace `submit` with:

```ts
  function payloadFor(): PlanSettings {
    return preparing && isSS3
      ? settings
      : { ...settings, targetExam: null, targetDate: null, forceExamMode: false };
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      const problem = await onSubmit(payloadFor());
      if (problem) setError(problem);
    } finally {
      setSaving(false);
    }
  }

  function submit() {
    if (needsClassPrompt(settings.classLevel, data.classLevel)) {
      setPrompting(true);
      return;
    }
    void save();
  }

  async function updateClassThenSave() {
    setSaving(true);
    setError("");
    try {
      await updateProfile.mutateAsync({ classLevel: settings.classLevel });
    } catch {
      setPrompting(false);
      setSaving(false);
      setError("We could not update your class, so the plan was not saved. Please try again.");
      return;
    }
    setPrompting(false);
    setSaving(false);
    await save();
  }

  function keepClassThenSave() {
    setPrompting(false);
    void save();
  }
```
- Replace the intro paragraph text with `Planning for {settings.classLevel}, {TERM_LABELS[settings.term]}. Tell us when you can study and we'll keep it realistic.`
- Add, above the Subjects block, two chip groups (reuse `chip()`):

```tsx
        <div>
          <span className="label">Class</span>
          <div className="flex flex-wrap gap-2">
            {CLASS_LEVELS.map((level) => (
              <button key={level} type="button" aria-pressed={settings.classLevel === level}
                onClick={() => setSettings({ ...settings, classLevel: level })}
                className={chip(settings.classLevel === level)}>
                {level}
              </button>
            ))}
          </div>
        </div>

        <div>
          <span className="label">Term</span>
          <div className="flex flex-wrap gap-2">
            {TERMS.map((term) => (
              <button key={term} type="button" aria-pressed={settings.term === term}
                onClick={() => setSettings({ ...settings, term })}
                className={chip(settings.term === term)}>
                {TERM_LABELS[term]}
              </button>
            ))}
          </div>
        </div>
```
- Before the closing `</div>` of the card, render the modal:

```tsx
      {data.classLevel && (
        <ClassMismatchModal
          open={prompting}
          profileClass={data.classLevel}
          chosenClass={settings.classLevel}
          busy={saving}
          onUpdate={updateClassThenSave}
          onKeep={keepClassThenSave}
          onClose={() => setPrompting(false)}
        />
      )}
```

- [ ] **Step 3: Plan page header**

In `src/components/study-plan/study-plan-view.tsx` import `scopeHeader` from `@/lib/study-plan-scope` and replace the `description` array's first two entries:

```ts
  const description = [
    plan
      ? scopeHeader(plan.scope, plan.followsCalendar, data.termLabel)
      : [data.classLevel, data.termLabel].filter(Boolean).join(" · "),
    plan?.targetExam && plan.mode !== "TERM" && data.daysToExam !== null && data.daysToExam > 0
      ? `${plan.targetExam} in ${data.daysToExam} days`
      : null,
  ].filter(Boolean).join(" · ");
```
and change `examPassed` to use the plan's class: `const examPassed = plan?.scope.classLevel === "SS3" && plan.targetDate != null && plan.targetDate < data.today;`.

- [ ] **Step 4: Verify**

Run: `npx tsc --noEmit` (expect clean), `npx eslint src/components/study-plan` (expect clean), then `npm test` (all frontend scripts).
Manual (needs both servers running; follow the `run` skill): as an SS1 profile open Study Plan → pick SS2 → Create. Expect the prompt. Check all three outcomes: "Update my class" (Settings now shows SS2), "Keep SS1" (Settings still SS1, plan is SS2), close/Escape (nothing saved). Also stop the backend profile route (or force an error) and confirm "Update my class" shows the error and saves no plan.

---

### Task 9: Classroom opens on the student's class

**Files:**
- Modify: `src/lib/classroom.ts`
- Modify: `scripts/test-classroom.mts`
- Modify: `src/app/(dashboard)/classroom/[subjectSlug]/page.tsx`

**Interfaces:**
- Produces: `browserKey(subjectId: string, initialClassLevel: string): string`.

- [ ] **Step 1: Reproduce first**

With both servers running, sign in as an SS2 student, open the classroom subject page for subject A, tap the SS1 tab, then open subject B. Record whether B opens on SS1 (state carried over) or on SS2. Then change the class in Settings and reopen subject A. Note which of the two causes in the spec (§1 item 5) you observe, and if the student's class has no topics for the subject, note that too. Write the result in your summary; the key fix below is correct for the first cause and harmless for the second.

- [ ] **Step 2: Write the failing test**

In `scripts/test-classroom.mts` add `browserKey` to the import from `../src/lib/classroom`, then append:

```ts
// ─── browserKey ────────────────────────────────────────────

test("browserKey changes with the subject and with the class", () => {
  assert.notEqual(browserKey("maths", "SS1"), browserKey("physics", "SS1"));
  assert.notEqual(browserKey("maths", "SS1"), browserKey("maths", "SS2"));
  assert.equal(browserKey("maths", "SS2"), browserKey("maths", "SS2"));
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-classroom.mts`
Expected: FAIL (`browserKey` is not exported).

- [ ] **Step 4: Implement**

In `src/lib/classroom.ts` add after `resolveClassLevel`:

```ts
/**
 * Remounts the class/term browser when the subject or the student's class
 * changes, so its tab state resets to the opening class instead of carrying
 * over from the previous subject.
 */
export function browserKey(subjectId: string, initialClassLevel: string): string {
  return `${subjectId}:${initialClassLevel}`;
}
```

In `src/app/(dashboard)/classroom/[subjectSlug]/page.tsx` import `browserKey` from `@/lib/classroom` and add to `<ClassTermBrowser ...>`: `key={browserKey(subject.id, initialClassLevel)}`.

- [ ] **Step 5: Run to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-classroom.mts`
Expected: pass.

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit` and `npm test`. Repeat the manual reproduction from Step 1: subject B must open on SS2, and after changing the class in Settings subject A must open on the new class.

---

### Task 10: Delete (deactivate) a plan, backend

**Files:**
- Modify: `app/services/planner/service.py`
- Modify: `app/services/planner/__init__.py`
- Modify: `app/api/student/study_plan.py`
- Modify: `tests/test_study_plan_payload.py`

**Interfaces:**
- Produces: `DeleteStudyPlanService(session, student_id).process() -> {"ok": True}`; route `DELETE /api/study-plan` (same `studyPlanner` entitlement as the other routes). Soft delete: sets `is_active = False`, rows and items stay.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_study_plan_payload.py`:

```python
async def test_delete_deactivates_the_active_plan_and_keeps_it(monkeypatch):
    from types import SimpleNamespace as NS

    from app.services.planner import service

    plan = NS(id="p1", is_active=True)
    locked = []

    async def active(_, student_id):
        return plan

    async def lock(_, *conditions):
        locked.append(conditions)

    monkeypatch.setattr(service.plans_repository, "active", active)
    monkeypatch.setattr(service.plans_repository, "lock", lock)

    result = await service.DeleteStudyPlanService(None, "s1").process()

    assert result == {"ok": True}
    assert plan.is_active is False
    assert len(locked) == 1


async def test_delete_without_an_active_plan_is_a_404(monkeypatch):
    import pytest

    from app.core.errors import ApiError
    from app.services.planner import service

    async def active(_, student_id):
        return None

    monkeypatch.setattr(service.plans_repository, "active", active)

    with pytest.raises(ApiError) as raised:
        await service.DeleteStudyPlanService(None, "s1").process()

    assert raised.value.status_code == 404
```
(If `ApiError` exposes the status under a different attribute name, check `app/core/errors.py` and adjust the last assertion.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_study_plan_payload.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'DeleteStudyPlanService'`).

- [ ] **Step 3: Implement**

In `app/services/planner/service.py`, after `UpdateStudyPlanSettingsService`:

```python
class DeleteStudyPlanService:
    """Soft delete: the plan stops being active; its items and history are kept."""

    def __init__(self, session: AsyncSession, student_id: str) -> None:
        self.session = session
        self.student_id = student_id

    async def process(self) -> dict:
        plan = await _active(self.session, self.student_id)
        if plan is None:
            raise ApiError(404, "No active study plan")
        # Same lock a replan takes, so a delete cannot race a rebuild.
        await plans_repository.lock(self.session, StudyPlan.id == plan.id)
        plan.is_active = False
        return {"ok": True}
```

In `app/services/planner/__init__.py` import `DeleteStudyPlanService` and add `"DeleteStudyPlanService",` to `__all__`.

In `app/api/student/study_plan.py` import `DeleteStudyPlanService` from `app.services.planner` and add after `patch_plan`:

```python
@router.delete(
    "",
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=OkOut,
)
async def delete_plan(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await DeleteStudyPlanService(session, student.id).process()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_study_plan_payload.py -v`
Expected: all pass.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q` and `uv run ruff check app tests`. Check nothing else reads inactive plans' items by mistake: `grep -rn "StudyPlanItem" app --include=*.py` and confirm every reader goes through the active plan (`pages.py` and `push.py` filter on `is_active`).

---

### Task 11: Delete a plan, frontend

**Files:**
- Modify: `src/hooks/api/use-study-plan.ts`
- Create: `src/components/study-plan/delete-plan-modal.tsx`
- Modify: `src/components/study-plan/plan-setup-form.tsx`
- Modify: `src/components/study-plan/study-plan-view.tsx`

**Interfaces:**
- Consumes: `endpoints.studyPlan.root`, `Modal`.
- Produces: `useDeleteStudyPlan()` mutation (no arguments); `PlanSetupForm` prop `onDelete?: () => Promise<string | null>`.

- [ ] **Step 1: Hook**

In `src/hooks/api/use-study-plan.ts` add:

```ts
export function useDeleteStudyPlan() {
  const invalidate = useInvalidateStudyPlan();
  return useMutation({
    mutationFn: () =>
      request<unknown>({ method: "DELETE", url: endpoints.studyPlan.root }),
    onSuccess: invalidate,
  });
}
```

- [ ] **Step 2: Confirmation modal**

Create `src/components/study-plan/delete-plan-modal.tsx`:

```tsx
"use client";

import { Modal } from "@/components/ui/modal";
import { buttonClass } from "@/components/ui/button";

export function DeletePlanModal({
  open,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  open: boolean;
  busy: boolean;
  error: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      title="Delete your study plan?"
      description="Your schedule goes away and you can build a new plan any time. Sessions you already completed stay in your progress."
      busy={busy}
      onClose={onClose}
      footer={
        <div className="flex flex-wrap justify-end gap-3">
          <button type="button" disabled={busy} onClick={onClose} className={buttonClass("secondary", "md")}>
            Keep my plan
          </button>
          <button type="button" disabled={busy} onClick={onConfirm} className={buttonClass("primary", "md")}>
            {busy ? "Deleting..." : "Delete plan"}
          </button>
        </div>
      }
    >
      {error && <p className="text-sm text-danger">{error}</p>}
    </Modal>
  );
}
```

- [ ] **Step 3: Form button**

In `plan-setup-form.tsx` add the prop `onDelete?: () => Promise<string | null>` to the component props, import `DeletePlanModal`, and add state:

```ts
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  async function confirmDelete() {
    if (!onDelete) return;
    setDeleting(true);
    setDeleteError("");
    const problem = await onDelete();
    setDeleting(false);
    if (problem) setDeleteError(problem);
  }
```
In the button row (after the Cancel button) add:

```tsx
        {plan && onDelete && (
          <button type="button" onClick={() => setConfirmingDelete(true)}
            className={cn(buttonClass("secondary", "lg"), "ml-auto text-danger")}>
            Delete plan
          </button>
        )}
```
and render, next to the class mismatch modal:

```tsx
      {onDelete && (
        <DeletePlanModal
          open={confirmingDelete}
          busy={deleting}
          error={deleteError}
          onConfirm={confirmDelete}
          onClose={() => { setConfirmingDelete(false); setDeleteError(""); }}
        />
      )}
```
On success the page refreshes and the form unmounts (`editing` becomes false in the view), so no extra reset is needed.

- [ ] **Step 4: View wiring**

In `study-plan-view.tsx` import `useDeleteStudyPlan`, create `const removePlan = useDeleteStudyPlan();`, add

```ts
  async function deletePlan() {
    const problem = await run(() => removePlan.mutateAsync());
    if (!problem) setEditing(false);
    return problem;
  }
```
and pass `onDelete={plan ? deletePlan : undefined}` to `<PlanSetupForm ... />`.

- [ ] **Step 5: Verify**

Run `npx tsc --noEmit`, `npx eslint src/components/study-plan src/hooks/api/use-study-plan.ts`, then `npm test`.
Manual: with a plan, Change plan → Delete plan → "Keep my plan" leaves it; "Delete plan" returns to the "No study plan yet" empty state; creating a new plan works afterwards. With the backend route stopped, confirm the modal shows the error and the plan is still there.

---

## Self-Review

**Spec coverage**
- §4 data model → Task 3. §5.1 scope helper → Task 1. §5.2 selection (scope, gap-fill, holiday, preview) → Task 2. §5.3 unmapped topics → Task 4. §5.4 mastery → Task 6. §6 API (scope fields, SS3-by-plan-class, positions in scope, payload, plan mode from scope) → Tasks 3, 4, 5. §7.1–7.3 → Tasks 7, 8. §7.4 classroom → Task 9. §8 error handling: empty scope (Task 2 test), unmapped (Task 4), migration (Task 3), prompt failure (Task 8 manual). §9 tests → covered per task; "Migration backfill" is verified by `alembic heads` only (no database test harness exists); flagged for the user to run on a staging copy before production.
- Deviation from the spec: §5.2 says skipped same/later-scope prerequisites are "logged once". The plan skips them silently (`by_id` never contains them), to avoid threading extra state through `_gaps_for`. Unmapped topics are still logged (Task 4).
- Spec §7.2's three outcomes are in Task 8's modal and manual checks.
- Spec §10 (plan deletion, soft delete) → Tasks 10 and 11.
- Not in the spec but required by it: `plan.followsCalendar` and `currentTerm` (header rule in §7.3 and defaults in §7.1), `_drop_positions_outside_scope` (Review Focus 4).

**Placeholders:** none. Revision-due reason text is a concrete string chosen here ("Fading from memory, time to refresh it"); it becomes the note on the revision item.

**Type consistency:** `select_term_topics(class_level, term, follow_calendar)`, `calendar_topic_id(topics, class_level, term, follow_calendar, ctx)`, `position_options(topics, class_level, term)`, `PlannerInput.term/home_class_level`, `plan.scope_class_level/scope_term`, `StudyPlanIn.classLevel/term`, frontend `PlanScope {classLevel, term}` — used identically across tasks.

**Risks to watch while executing**
- Task 2 changes a required `PlannerInput` shape; any other construction site breaks. Run `grep -rn "PlannerInput(" app tests` and fix any besides `service.py` and `test_engines.py`.
- `GetTopicMasteryService.process()` persists mastery rows; running it on every replan adds writes. It is wrapped in a savepoint, but watch replan latency on a real database.
- The calendar defaults assume the student's profile class is correct at the start of a session; promotion timing is out of scope.
