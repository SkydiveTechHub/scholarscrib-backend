# Today's Session — Phase 1 Design

**Date:** 2026-10-12
**Status:** Draft, pending review
**Scope:** Phase 1 of 3. Replaces the dashboard's three topic rails with one time-boxed daily session, and fixes the engine inputs it depends on. Exam readiness (phase 2) and error diagnosis / progress signal (phase 3) are out of scope here.

## 1. Problem

The dashboard shows three lists of topics: "Keep learning", "Tighten your gaps" and "Revise today". All three rank topics by weakness, so a student sees overlapping lists and must decide for themselves what to do first and for how long. A tool for learning should answer one question: **what should I do in the next ~20 minutes, and why?**

Specific defects in what exists (`app/services/pages.py`, `app/services/learning/mastery.py`):

1. The "reason" is a generic label ("Next in your learning path"), not evidence the student can check.
2. Retention is guessed from days since last effort and a stability that depends only on the mastery band. Real per-card spaced-repetition data (`FlashcardReview.stability`, `last_reviewed_at`, `lapses`) exists and is unused for this.
3. A topic that is due only for flashcards or a scheduled revision, with retention ≥ 0.85, gets `priority = 0` (`decay` is 0) and sinks to the bottom of the revision queue.
4. The lists reshuffle on every load, so finishing an item makes the list change underneath the student.
5. Nothing records whether students follow the recommendations, so we cannot tell if the feature helps.

## 2. Goals

1. One ordered queue of 3–4 actions for today, each with minutes, a direct link and an evidence-based reason.
2. The order follows learning science: retrieval of fading material first, then focused practice on a weak topic, then new material.
3. The queue is **stable for the day** and shows progress ("2 of 4 done").
4. Retention uses real flashcard data where it exists.
5. Completion is recorded per item so we can measure whether sessions are followed and whether they help.
6. Where evidence is thin the UI says so instead of showing precise-looking numbers.

**Non-goals:** exam readiness or score prediction (phase 2); misconception, hint and confidence features (covered by `2026-10-09-repair-stage-design.md`); error diagnosis and 14-day progress deltas (phase 3); changing mastery scoring or `SCORING_VERSION`; changing the planner or study-plan UI; the classroom topic graph.

## 3. Decisions

| Question | Decision |
|---|---|
| Replace or add | Replace the three rails. Remove `learningPicks`, `gaps`, `revision`, `revisionTotal` from the dashboard payload and delete the three components if nothing else imports them (verify first) |
| Session length | 20 minutes default, at most 4 items. If the student has a study plan with pending items today, those come first and the engine only fills the remaining minutes |
| Order | Retrieval → focused practice → new material (or the unfinished lesson) |
| Stability | The session is generated once per Lagos day and stored. It does not reshuffle. Items are marked done as the student works |
| Mastery scoring | **Unchanged.** See 4.3 |
| Hidden state | Still hidden until `hasActivity` is true. No session is stored before then |
| Where it lives | A new pure composer in `app/services/learning/session.py`, called by `GetDashboardService`. The dashboard service stops building rails |

## 4. Engine changes

### 4.1 Retention estimate

Add `topic_retention(state, card_retention)` in `app/services/learning/mastery.py`:

- If the student has **≥ 3 reviewed cards** on the topic, retention is the mean current retrievability of those cards, computed from each card's `stability` and `last_reviewed_at` with the same function the scheduler uses (`retention()` in `evidence.py` takes days and stability in days; the unit of `FlashcardReview.stability` must be confirmed in `app/services/srs/scheduler.py` before wiring, see §10).
- Otherwise fall back to the existing curve from `TopicState.retention`.
- Expose which source was used (`retentionSource: "CARDS" | "ESTIMATE"`) so the UI can word it honestly.

Flashcards stay **out of mastery** (`CHANNEL_WEIGHTS` is unchanged; building a deck is optional and is not held against a student). They inform retention only.

A new repository method `reviews_repository.retrievability_by_topic(session, student_id, now) -> dict[topic_id, (mean_retrievability, card_count)]` supplies the numbers, using the same deck/lesson topic join as `due_counts_by_topic`.

### 4.2 Fix for the zero-priority bug

The revision priority becomes `urgency = max(decay, DUE_FLOOR)` where `DUE_FLOOR = 0.05` applies whenever the topic is due only because of cards or a schedule. It ensures an item that is due always outranks one that is not. The exam-weight and dependents multipliers are kept, with `exam_weight` floored at 1.0 so a topic without weights is still rankable. Constants live in `evidence.py`.

### 4.3 Guess floor — no change

Multiple-choice guessing is already absorbed by the scoring scale: a wrong answer scores 0.0–0.35 and a correct one 0.85–1.0 (`DIFFICULTY_OUTCOMES`), plus a prior of 0.45 with strength 4. For INTERMEDIATE questions, 70 mastery needs about 65% accuracy, and a student who guesses at random on 4-option questions lands near 36. This is a deliberate compression, not an explicit guess correction. Phase 1 leaves it alone: changing it bumps `SCORING_VERSION` and refolds every student's mastery, and the repair-stage spec also plans a bump to 4. Any change should be made once, jointly, with real data.

### 4.4 Evidence sufficiency

`evidence_level(state)` returns `NONE` (no observations), `THIN` (confidence < `CONFIDENCE_FLOOR`) or `ENOUGH`. Reasons only quote percentages when `evidenceLevel == "ENOUGH"` or `retentionSource == "CARDS"` (card retention has its own sufficiency gate of `CARD_RETENTION_MIN_CARDS` reviewed cards); otherwise they quote counts ("2 questions answered"). This extends, and reuses, `evidence-display.ts` on the frontend.

## 5. Session composer

`compose_session(inputs) -> list[SessionItem]` is a pure function. Inputs: topic states, topics, leverage, due-card counts and retrievability per topic, cadence-due topics, today's pending plan items, the in-progress lesson, recent per-topic accuracy, exam weights, `now`, and the minutes budget. No database access inside, so it is easy to test.

### 5.1 Algorithm

1. **Budget.** `budget = 20`. Plan items for today with status `PENDING` or `COMPLETED` come first (source `PLAN`). Only `PENDING` ones subtract their duration, so finishing the plan does not hide the rest of the session. If plan minutes ≥ budget, no engine items are added.
2. **Retrieval slot (≤ 8 min).** Candidates: topics with `last_effort_at` set and (retention < `DECAY_RETENTION` or cards due or cadence due). Highest fixed priority from 4.2 wins. Minutes: `min(8, ceil(1 + due_cards * 0.5))` when cards are due, otherwise 5. Kind `RETRIEVE`, `href` = topic practice page (or the flashcard deck when cards are due and exactly one deck applies).
3. **Practice slot (8 min).** Candidates: topics that are `available`, `mastery < TARGET`, with `evidence_level != NONE`, excluding the retrieval topic. Value: `(TARGET - mastery)/TARGET * (1 + leverage) * exam_weight`. `WEAK` topics (confidence ≥ 0.35 and mastery < 50) get a 1.5× multiplier. Topics at `THIN` evidence are included but never shown a percentage. Kind `PRACTICE`, 8 minutes (sized so a 20-minute session still has room for the new-material slot). The in-progress lesson's topic is held out of the retrieval and practice slots so the `CONTINUE` item is not pre-empted.
4. **New slot (remaining minutes, ≥ 5).** First the in-progress lesson (kind `CONTINUE`). Otherwise the first available topic by curriculum order with `evidence_level == NONE`, all prerequisites at ≥ `GATE` mastery or pretest-passed, and not already used. Kind `LEARN`, minutes from `topic.estimated_minutes` capped to the remaining budget.
5. Drop any slot with no candidate. If all are empty return `[]`, and the UI shows a neutral "Nothing urgent today — pick any subject" card instead of a fake item.

Items are never from deactivated subjects (the existing `active_ids` filter stays).

### 5.2 Reasons

Templates, filled from real numbers only:

- RETRIEVE: `"Retention about {r}% — last studied {d} days ago"` / `"{n} flashcards due"` / `"Scheduled revision is due"`. The first form is used only when `retentionSource == "CARDS"`; with an estimate it reads `"Probably fading — last studied {d} days ago"`.
- PRACTICE: `"{c} of your last {n} answers here were right"` when n ≥ 3, plus `"· unlocks {u} topics"` when `unlocks ≥ 1`, plus `"· ~{w} exam weight"` when weight > 0.
- LEARN: `"New topic — you're ready for it"` when the topic has no solid prerequisite (the composer has no subject names), else `"{prereq} is solid ({m}%) — this is the next step"`.
- CONTINUE: `"Continue where you left off"`.

Recent accuracy comes from a new query `learning_events_repository.recent_outcomes_by_topic(session, student_id, topic_ids, limit=8)` returning the last ≤ 8 `QUESTION_ANSWERED` outcomes per topic.

## 6. Persistence and completion tracking

New table `DailySession`:

| Field | Type | Notes |
|---|---|---|
| `id` | cuid | |
| `student_id` | FK `User` | cascade on delete |
| `day` | String(10) | Lagos day key from `lagos_day_key()`; unique with `student_id` |
| `items` | JSONB | the composed list, each item with `id`, `kind`, `topicId`, `subjectId`, `minutes`, `reason`, `doneAt` (nullable), `source` |
| `generated_at` | timestamptz | |

Behaviour:

- `GetDashboardService` loads today's row. If missing and `hasActivity`, it composes and inserts one. The write happens inside the existing request, wrapped like `_persist` in `mastery_store.py` (a failure is logged and the freshly composed list is still returned, unstored).
- **Done criteria**, evaluated on each load for items without `doneAt`, using today's learning events for the topic: `RETRIEVE` — at least 5 `QUESTION_ANSWERED` or `CARD_REVIEWED` events (or all due cards reviewed); `PRACTICE` — at least 5 `QUESTION_ANSWERED`; `LEARN`/`CONTINUE` — at least one `LESSON_BLOCK_COMPLETED` (or `LESSON_COMPLETED`) event on the topic; no service emits `LESSON_COMPLETED` today, so the first checkpointed block is the practical signal; `PLAN` items follow the plan item's `status`. Constants in `evidence.py`.
- `doneAt` is written once and never cleared.
- Stored rows are the measurement source for completion rate. No separate analytics table is added in phase 1.

Migration: standard Alembic revision with an index on `(student_id, day)` (unique). Reversible; no data backfill.

## 7. API and frontend

Backend (`app/api/responses.py`, `app/services/pages.py`):

- `DashboardOut` gains `session: {minutes: int, done: int, total: int, items: SessionItem[]}`.
- Remove `learningPicks`, `gaps`, `revision`, `revisionTotal`, and `keepLearning` (verify nothing else reads `keepLearning` first).
- `subjects` keeps its current role (referenced subjects, keyed by id).

Frontend (`scholarscrib-frontend`):

- `src/lib/api/types.ts`: add `SessionItem`; remove the three rail types.
- `src/lib/dashboard.ts`: map `session`; the hero button's "Today: X of Y done" stays derived from today's plan items (it links to `/study-plan`), not from the session.
- New `src/components/path/todays-session.tsx`: a numbered list, one primary "Start" action per item, minutes, reason, a check mark when done, and a progress line. Links are built from `kind` and the `subjects` map using the existing routes (`/classroom/[subject]/[topic]/practice`, `/study`, `/flashcards`).
- `src/app/(dashboard)/dashboard/page.tsx`: replace the two sections with one "Today's session" section, hidden until `hasActivity`.
- Delete `next-topics.tsx`, `gap-list.tsx`, `revision-queue.tsx` only if `grep` shows no other importers. `graph-view.tsx`, `view-toggle.tsx` and `pretest-dialog.tsx` are untouched.
- Respect `AGENTS.md`: read the relevant guide in `node_modules/next/dist/docs/` before writing the Next.js code.

## 8. Testing

Backend (`tests/`, in the style of `test_engines.py`):

- `compose_session`: order is retrieval, practice, new; budget is respected; plan items take precedence and fill the budget; an empty candidate set returns `[]`; deactivated subjects excluded; no topic appears twice.
- Priority fix: a topic due only for cards with retention 0.9 outranks a topic that is not due.
- `topic_retention`: ≥ 3 cards uses card retrievability; < 3 falls back; `retentionSource` is correct.
- Evidence gating: reasons never contain a percentage for `THIN`/`NONE`.
- Done criteria for each kind, including idempotent `doneAt`.
- `DailySession`: second load on the same day returns the stored list unchanged; a new Lagos day generates a new row; a failed insert still returns the composed list.

Frontend: `tsc` and lint pass; manual check of the empty, no-plan, plan-present and all-done states.

## 9. Rollout and measurement

Ship behind no flag: the old rails are removed in the same change, so there is no dual path to maintain. Measure from `DailySession.items`: fraction of generated sessions with ≥ 1 and with all items done, by kind. Review after two weeks. If completion is under about 25% for a kind, shorten or drop that kind.

## 10. Open items to resolve in the plan

1. Unit of `FlashcardReview.stability` (days vs other) in `app/services/srs/scheduler.py`; adjust 4.1 if it differs.
2. Confirm no other consumers of `keepLearning`, `learningPicks`, `gaps`, `revision`, and the three rail components.
3. Which deck link to use when several decks have cards due for one topic (default: the flashcards index).
4. Whether the 20-minute default should scale by class level (`DEFAULT_MINUTES` in `planner/mode.py` holds 30/45/60 weekday minutes). Default stays 20 until real completion data exists.
5. The repair-stage spec's `SCORING_VERSION` bump to 4 is independent of this work; no coordination is needed unless its migration is applied first, in which case the new `DailySession` revision must chain after it.
