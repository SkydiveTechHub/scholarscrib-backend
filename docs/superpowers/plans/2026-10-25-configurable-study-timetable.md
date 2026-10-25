# Configurable Study Timetable — Phase 1 Findings and Implementation Plan

**Brief:** `scholarscrib-configurable-study-timetable.md` (Downloads). **Status:** Phase 1 (discovery and baseline) done. No planner or schema code has changed. The only code added is `tests/test_planner_baseline.py`.

**Repos:** backend `scholarscrib-backend`, frontend `scholarscrib-frontend`. Both are git repositories (this was wrong in the first version of this document). Nothing from this work has been committed; every phase ends with a **Verify** step and the changes are in the working tree for review.

**Instructions files:** The backend has no `CLAUDE.md` or `AGENTS.md`. The frontend's `AGENTS.md` warns that this Next.js (16.2) differs from older versions, so read `node_modules/next/dist/docs/` before Phase 5 and Phase 6 UI work.

**Commands:**
- Backend: `uv run pytest`, `uv run ruff format`, `uv run ruff check`, `uv run pyright` (from the backend folder).
- Frontend: `npm test`, `npx tsc -p tsconfig.scripts.json --noEmit`, `npm run lint`.

## 1. Request-to-database flow today

```
POST/PATCH /api/study-plan      app/api/student/study_plan.py   (require_feature("studyPlanner"))
  body: StudyPlanIn             app/api/schemas.py:217
  -> CreateStudyPlanService / UpdateStudyPlanSettingsService      planner/service.py
       _check() -> plan_settings_problem()                         planner/mode.py
       _ensure_subjects()  (exists, active, in track or CORE)
       writes StudyPlan columns
       -> ReplanStudyPlanService.process()
GET /api/study-plan -> GetStudyPlanService
       lazily replans if last_replanned_at is before today (Lagos)  <- a write inside a GET
```

**Replan lifecycle** (`ReplanStudyPlanService.process`, service.py:655):
1. Lock the plan row (`plans_repository.lock`).
2. `_miss_and_clear`: PENDING items dated before today become MISSED and each feeds a `CarryOver`. PENDING items from today onward are **deleted**. COMPLETED, SKIPPED and MISSED items are never touched.
3. Load topics (those without a curriculum level are skipped), the prerequisite graph, mastery, pretest passes, DECAYED topics (as `RevisionDue`), positions, and completed-lesson counts.
4. `plan_window()` (`term_plan.py`) is pure. It runs `build_slots`, then `select_term_topics` and `select_exam_topics` per subject, then `layout_window`, then `project_outline`.
5. `_persist_output` inserts new `StudyPlanItem` rows (new ids) and sets `outline`, `overload`, `planned_through` and `last_replanned_at`.

Anything else that reads or writes plans:
- `MarkStudyPlanItemService` auto-completes the earliest PENDING item matching an activity and subject. It is called from `assessments.py` and `flashcards.py`.
- `SetStudyPlanItemStatusService` is the manual path. It has a 7-day lookback and refuses future items.
- `learning/session.py` (Today's session) copies today's items by `planItemId`. `reconcile_plan` re-points stored ids after a replan, matching on `(activityType, topicId, subject)`. Item ids already change on every replan.
- `pages.py` (dashboard today list), `push.py` (morning count), `console.py` and `identity.py` (admin, announcement audience by `target_exam`).
- Tutor: `rag/tools/study_plan.py` (the five propose tools; `CreateStudyPlanArgs` and `UpdateStudyPlanArgs` **subclass `StudyPlanIn`**) and `rag/tools/reads.py::_compact_plan`, which **whitelists** the plan fields the model sees.

## 2. Settings persisted today versus missing

| Persisted on `StudyPlan` | Notes |
|---|---|
| `subjectIds` (JSONB), `studyDays` (int[] ISO 1–7), `weekdayMinutes`, `weekendMinutes` | Minutes are one number for Mon–Fri and one for Sat–Sun. |
| `targetExam`, `targetDate`, `forceExamMode`, `scopeClassLevel`, `scopeTerm` | |
| `outline` (JSONB), `overload` (JSONB), `plannedThrough`, `lastReplannedAt`, `isActive`, `createdAt` | JSONB is already used for plan-level derived data. |

`StudyPlanPosition` stores the student's per-subject class position. `StudyPlanItem` stores the generated tasks.

**Missing, as the brief requires:**
- Per-weekday minutes, a mode, and per-subject day assignments.
- A preferred/pinned strength.
- A record of unmet allocations. `overload` only counts unplaced units and has no subject or day detail.
- A "why this slot" reason on items. Only the free-text `notes` field exists.

## 3. Invariants confirmed by `tests/test_planner_baseline.py` (32 tests, pass)

- The same inputs give the same plan.
- Nothing is placed on a non-study day, and a day is never filled past its minutes.
- Subject cap is 2 per weekday and 3 per weekend day. Short slots (15 minutes or more) only carry REVISION.
- Lesson count is `ceil(minutes/30)` minus lessons already done. A topic with no mastery data counts as weak and gets 2 PRACTICE; mastery of 50 or more gets 1.
- A passed pretest means no lessons. A mastered topic is not scheduled.
- Practice lands strictly after the topic's last lesson.
- Dependents wait for prerequisite lessons. Unmastered out-of-term prerequisites are scheduled first as "Foundation for …".
- Missed work returns with `carried_from`, preferring the weekly catch-up slot (Saturday for the default week).
- Revision follows the last lesson, DECAYED topics are revised without new lessons, and revision is capped near 20% **only while topic work remains**.
- Leftover slots are filled with revision once work runs out (pinned deliberately).
- Overload is reported only when units are left unplaced.
- TERM plans no exam work. The window stops at the exam date. The runway has exactly 2 mocks and only MOCK_EXAM, PAST_QUESTIONS and REVISION. EXAM mode has no new lessons but still does carry-overs. BLENDED mixes term and exam work. Outline labels are checked.

**Not covered by Phase 1 tests (DB-bound; add in Phase 4):**
- Delete-and-regenerate, missed-marking and idempotent replan at the service level.
- Lagos midnight rollover. `lagos_day_key` is monkeypatched elsewhere, so this only needs an extra case.
- Exact GAP_FILL cap arithmetic. The test uses a loose bound.

## 4. Conflicts and risks found in the existing code

1. **Subject cap versus custom allocation.** `allow_subject` stops a third subject on a weekday, so a student could pin three subjects to a Monday and the layout would silently drop one. *Decision:* a configured allocation raises the cap for that day to the number of allocated subjects. This is documented and tested. Smart mode keeps 2/3.
2. **Delete-and-regenerate versus "don't shuffle future tasks".** Every replan deletes all future PENDING items. Pins live in configuration, not items, so a deterministic layout honours them every time. Stability then comes from determinism. Phase 4 adds a test that an unchanged replan yields identical `(date, subject, topic, activity)` tuples.
3. **Item ids change on every replan.** `learning/session.py::reconcile_plan` already copes by matching on activity and topic. Keep item ids out of the timetable config, and run the session tests after Phase 4.
4. **Lazy replan inside GET.** A failure there rolls back with the request. Phase 4 verifies that a failed `plan_window` leaves items untouched (the delete happens before it today, so confirm the transaction boundary).
5. **Revision fills leftover slots.** A student assigning "Maths on Monday" with no eligible Maths work will see revision (any subject) placed. Per brief 7.3 the config policy is: fall back to the day's other allocated subjects, otherwise leave the slot empty and report an unmet allocation. Revision for a non-allocated subject is **not** used on a custom-allocated day.
6. **Tutor schemas inherit `StudyPlanIn`.** New optional fields flow into the tutor tool schemas automatically and into `rag/tools/study_plan.py` validation, but `_compact_plan` in `reads.py` must be extended so the tutor can see the config.
7. **No database-backed tests exist, and compose has no Postgres.** Tests monkeypatch repositories. The brief asks for fresh and upgrade migration tests, which need a real Postgres (e.g. a CI service container). This is a gap to raise with the user in Phase 2.
8. **Two existing `tsc` errors** in `scripts/test-tutor-chat.mts` and `scripts/test-tutor-sse.mts`. They are unrelated and pre-existing. Do not "fix" them silently, and compare against this baseline.

## 5. Schema and API decisions (proposed; confirm at the start of Phase 2)

**Storage: one nullable JSONB column `StudyPlan.timetable`, no new table.**
- Reasons: it is 1:1 with the plan, small (at most 7 days × 20 subjects), always read and written whole, and it follows the precedent of `subjectIds`, `outline` and `overload`.
- `NULL` means Smart mode. Old plans need no backfill and no data rewrite, and the migration is one `ADD COLUMN`.
- Shape (Pydantic is the authority; `version` makes replans reproducible):
  ```
  { "version": 1,
    "mode": "SMART" | "CUSTOM",
    "dayMinutes": { "1": 45, "6": 90 },
    "allocations": [ { "day": 1, "subjectId": "...", "minutes": 30|null,
                       "strength": "PREFERRED" | "PINNED" } ] }
  ```
- **No redundant representation.** `studyDays`, `weekdayMinutes` and `weekendMinutes` stay the source of truth for study days and default minutes. `dayMinutes` holds **overrides only**. "Rest/unavailable" is simply a weekday not in `studyDays`, so it needs no new field. Allocations on a rest day are rejected.
- Not storing config on `StudyPlanItem` (brief 6.2). Reason text can go into `notes` first; a structured reason column is optional for Phase 6.

**API: extend, don't fork.**
- `StudyPlanIn` gains an optional `timetable`. Omitting it keeps today's behavior, including for the tutor tools and old clients.
- `GET /study-plan` returns `plan.timetable` (null for legacy), plus `plan.unmet` (structured unmet allocations) beside `overload`.
- Add `POST /study-plan/preview` (same body, dry run). It runs validation and `plan_window` with no writes and returns warnings, conflicts and the first window. No other endpoints are needed in Phase 3.
- Validation lives in a new pure module `planner/timetable.py` so REST, the tutor and the preview all call the same function.

## 6. File-by-file plan

### Phase 2 — model and validation (no behavior change)

| File | Change |
|---|---|
| `app/database/models/planner.py` | Add `timetable: Mapped[dict \| None] = mapped_column(JSONB)` on `StudyPlan`. |
| `alembic/versions/<new>_add_study_plan_timetable.py` | `down_revision = "c6d8e0f2a413"` (single head, verified with `alembic heads`). One `add_column`; downgrade drops it. |
| `app/api/schemas.py` | `TimetableIn`, `AllocationIn`; `StudyPlanIn.timetable: TimetableIn \| None = None`. Shape validation only (ranges, duplicates, enums). |
| `app/services/planner/timetable.py` (new) | `timetable_problems(timetable, study_days, weekday/weekend minutes, subject_ids, mode)` returning errors and warnings. Cross-field rules: days in `studyDays`, subjects in the plan, day total ≤ day budget, minutes ≥ 30, no duplicates. |
| `app/services/planner/service.py` | `_check` calls the validator. Create and Update persist `plan.timetable`. `_serialize` returns it. Replan is unchanged in this phase. |
| `app/api/responses.py` | `PlanPageOut` already uses `plan: dict`, so no change is needed. |
| `tests/test_planner_timetable.py` (new) | Valid, invalid, conflicting and legacy configs. |

### Phase 3 — API and preview

| File | Change |
|---|---|
| `app/api/student/study_plan.py` | Add `POST /preview` behind `require_feature("studyPlanner")`. |
| `app/services/planner/service.py` | `PreviewStudyPlanService` builds `PlannerInput` from a body without persisting (reuse `_build_subjects`, `_learning_inputs`). |
| `app/api/responses.py` | `PlanPreviewOut` (warnings, conflicts, items). |
| `tests/test_study_plan_payload.py` | Add: old clients omitting `timetable`, 404 on another student's plan, and `GET` returns `timetable: null` for legacy. |

### Phase 4 — timetable-aware layout (the only planner change)

| File | Change |
|---|---|
| `planner/slots.py` | `Availability` gains optional `day_minutes`. `day_budget` prefers it. The default path is unchanged. |
| `planner/term_plan.py` | `PlannerInput.timetable` (optional). Pass it through. Output gains `unmet`. |
| `planner/layout.py` | In `pick_topic`, add allocation preference ahead of the existing sort keys, and a hard filter for PINNED. Raise `subject_cap` per day when allocations exist. Add the unmet-allocation record. Keep `prereqs_ready`, gap-fill, revision and exam logic untouched. |
| `planner/service.py` | `_persist_output` writes `unmet` (a new JSONB column or inside `overload`; decide in Phase 2). `_miss_and_clear` is unchanged. |
| `planner/outline.py` | Deferred (brief §9). The outline stays approximate and is labelled as a projection. |
| `tests/test_planner_baseline.py` | Must keep passing **unchanged**. Add `tests/test_planner_timetable_layout.py` for preferred, pinned, rest-day, no-fabrication and per-mode cases. |

### Phase 5–6 — frontend

| File | Change |
|---|---|
| `src/types/study-plan.ts`, `src/lib/study-plan.ts` | Timetable and unmet types on `StudyPlanData`. |
| `src/lib/study-plan-route.ts` | Tolerant `asTimetable` / `asUnmet` mappers, matching the file's existing style. |
| `src/lib/api/types.ts`, `endpoints.ts` | Preview endpoint and types. |
| `src/hooks/api/use-study-plan.ts` | `usePreviewStudyPlan`; type `settings` in `useSaveStudyPlan` (currently `unknown`). |
| `src/components/study-plan/plan-setup-form.tsx` | Add steps (basics, availability, timetable, review). Extend `PlanSettings`. |
| `src/components/study-plan/timetable-editor.tsx` (new) | Day cards, add, edit, remove (non-drag), preferred/pinned. |
| `plan-schedule.tsx`, `plan-item-row.tsx`, `study-plan-view.tsx` | Weekly view, unmet and overload guidance, "projection" label. |
| `scripts/test-study-plan-*.mts` (new) | node:test cases for the pure helpers (client validation, display). |

### Phase 7 — tutor

| File | Change |
|---|---|
| `rag/tools/study_plan.py` | Almost nothing: `timetable` arrives through the `StudyPlanIn` subclass. Review the proposal `summary` guidance and add tests. |
| `rag/tools/reads.py` | `_compact_plan` includes `timetable` and `unmet`. |
| `rag/prompts.py` | Describe the new fields. |
| `tests/test_tutor_actions.py` | Invalid, edited, rejected, entitlement-denied and approved cases. |

## 7. Open questions for the product owner

1. Is a Postgres test service available in CI, so migration upgrade and fresh-install tests can be automated? Without it those checks would be manual.
2. Should a day's allocation total be allowed to exceed the day's minutes (e.g. "reduce the extras")? This plan **rejects** it, per brief 6.3.
3. Is PINNED allowed to override the exam runway (mocks and past questions only), or does exam work always win? This plan proposes that **the runway wins**, with the conflict shown to the student.

## 8. Phase 2 status (done)

Persisted and validated the timetable. Generated scheduling is unchanged: `layout.py`, `slots.py`, `term_plan.py`, `topics.py` and `outline.py` are untouched, and `test_planner_baseline.py` passes unchanged.

**Changed:** `models/planner.py` (`timetable` JSONB), `alembic/versions/d7a3f5c1e208_add_study_plan_timetable.py` (one `ADD COLUMN`), `api/schemas.py` (`TimetableIn`, `AllocationIn`, `DayMinutesIn`, `StudyPlanIn.timetable`), `planner/timetable.py` (new), `planner/mode.py` (`PlanSettingsCheck.day_minutes`), `planner/service.py` (`_check`, create, update, `_serialize`), `rag/tools/study_plan.py` (validation plus `_dump`), tests (`test_planner_timetable.py` new, `test_study_plan_payload.py` fixtures gained `timetable=None`).

**Deviations from the Phase 2 sketch above:**
1. `dayMinutes` is a list of `{day, minutes}` rows, not a `{"1": 45}` object. JSON object keys are strings and `Weekday` is an int literal, so a list avoids a coercion trap.
2. **Omitted versus null.** On update, an omitted `timetable` keeps the stored one; an explicit `null` resets to Smart. Old clients that PATCH without it can't wipe a student's choices. If the kept timetable no longer fits the new days or subjects, the update is rejected with 400 and a message, not silently trimmed.
3. **Tutor path fixed now, not in Phase 7.** The tutor dumps every field, so an unset timetable would have become an explicit `null` and reset the plan. `rag/tools/study_plan.py::_dump` drops an unset timetable, on both propose and resume.
4. Allocation `minutes` must be a multiple of 30 (the planner's session size) and `minutes: null` reserves one session. A day's allocations cannot exceed its whole-session capacity, per decision 2 in section 7.
5. A Smart timetable with nothing in it is normalized to `NULL`, and allocations are stored sorted, so equal intent is equal data.
6. `unmet` is **not** added yet. It is a Phase 4 output, so adding a column now would be speculative.

**Verified:** 448 backend tests pass, `ruff check` and `ruff format` are clean, `pyright` is 0 errors on the touched modules, `alembic heads` is a single head `d7a3f5c1e208`, and the offline upgrade SQL is exactly `ALTER TABLE "StudyPlan" ADD COLUMN timetable JSONB`.

**Not verified:** the migration has not been run against a real Postgres (no test database, see section 4 item 7). The downgrade has only been read, not run.

## 9. Phase 3 status (done): API and preview contract

Still no change to scheduling. `layout.py`, `slots.py`, `term_plan.py`, `topics.py` and `outline.py` are untouched.

### Contract (what the frontend builds against)

**Read.** `GET /api/study-plan` returns `plan.timetable`: `null` for Smart/legacy plans, otherwise `{version, mode, dayMinutes[{day,minutes}], allocations[{day,subjectId,minutes|null,strength}]}`. No separate read endpoint is needed.

**Write.** `POST /api/study-plan` and `PATCH /api/study-plan/{id}` accept the same optional `timetable` (see section 8 for omitted versus null). Invalid input is a 400 `{"error": "..."}` carrying the first problem.

**Preview.** `POST /api/study-plan/preview` takes the same body as create/update (`StudyPlanIn`) and returns 200:

```
{ "valid": bool,
  "errors":   [ {code, message, day|null, subjectId|null} ],   // all of them
  "warnings": [ {code, message, day|null, subjectId|null} ],   // only when valid
  "summary":  null | { mode, weeklyAvailableMinutes, weeklyAllocatedMinutes,
                       days[{day,name,availableMinutes,allocatedMinutes,unallocatedMinutes,
                             subjects[{subjectId,minutes,strength}]}],
                       restDays[{day,name}],
                       coverage[{subjectId,daysPerWeek,minutesPerWeek,pinnedDays}] } }
```

- Error codes: `SETTINGS`, `SUBJECTS`, `REST_DAY`, `DAY_TIME_DUPLICATE`, `NEEDS_CUSTOM`, `NO_ALLOCATIONS`, `SUBJECT_NOT_IN_PLAN`, `DUPLICATE_ALLOCATION`, `BAD_STEP`, `OVER_CAPACITY`.
- Warning codes: `SUBJECT_NOT_ON_TIMETABLE`, `DAY_FULLY_BOOKED`, `RUNWAY_REPLACES_TIMETABLE`.
- Malformed JSON shapes (day 9, strength "MAYBE") are rejected by the schema as 400 `{"error": "Validation failed", "details": [...]}`. That is this API's existing handler, not 422.
- Authorization is the same dependency as the other plan routes (`require_feature("studyPlanner")`), so a signed-out request is 401.
- A preview judges the body as sent: an omitted `timetable` previews as no timetable, whereas a save would keep the stored one. The client should send the full settings it intends to save.

### Decisions and findings

1. **`summary` describes the request, not a schedule.** The planner does not read the timetable until Phase 4, so a preview cannot honestly list tasks. A `window` (first 14 days) is added to the preview in Phase 4, when it can reflect the timetable. Until then the UI must not imply the generated plan will follow the timetable.
2. **Unallocated subjects.** In CUSTOM mode, a plan subject with no allocation is allowed and is warned about. The Phase 4 policy is that it can use leftover time on any day (stated in the warning text).
3. **Fully booked days warn, they don't fail.** A day whose allocations use every whole session leaves no room for revision or catch-up; that is a student choice, so it is a warning.
4. **The runway warning uses the plan's own start date** (the active plan's `createdAt`, else today), matching `compute_runway_start`, so the number of days shown matches what the planner will use.
5. **The study planner is currently open to every tier** (`BETA_OPEN_FEATURES` in `app/domain.py`). The preview follows the shared gate, so it is open too; the entitlement test turns the beta flag off to prove the gate still applies when the beta ends.
6. Validation now collects every issue (`timetable_issues`). Create/update still raise the first one with the same messages as in Phase 2 (`timetable_problem` is unchanged for callers).

**Changed:** `planner/timetable.py` (issues, summary, warnings), `planner/service.py` (`_settings_problem`, `_timetable_issues`, `PreviewStudyPlanService`), `planner/__init__.py`, `api/responses.py` (`PlanIssueOut`, `PlanPreviewOut`), `api/student/study_plan.py` (route), `tests/test_study_plan_preview_api.py` (new, 12 tests: auth, entitlement, contract, multi-error, no side effects).

**Verified:** 461 backend tests pass, `ruff check` clean, `pyright` 0 errors on `app/services/planner` and `app/api`.

**Not verified:** behavior against a real database (the tests stub the repositories), and the frontend does not use the new fields yet.

## 10. Phase 4 status (done): timetable-aware layout

The only phase that changes scheduling. `tests/test_planner_baseline.py` (32 tests) passes **unchanged**, and Smart plans with no overrides produce an identical plan (`test_no_allocations_and_no_overrides_plan_exactly_as_before`).

### Final scheduling rules

1. **Hours.** `dayMinutes` overrides the weekday/weekend minutes for single days, in Smart and Custom plans. `studyDays` is still the only list of study days; a rest day never gets tasks.
2. **Reservation** (`slots.reserve_slots`). In Custom mode each allocation reserves `minutes / 30` full sessions (one if `minutes` is null) on its weekday. Pinned allocations are reserved first, then by subject id, so the same timetable always reserves the same slots. Short slots are never reserved.
3. **Catch-up slot.** The week's catch-up slot is reserved last, and only when the day has no other full slot. A reserved catch-up slot stops being one (so carry-over is no longer deferred to it).
4. **Serving a reserved slot** (`layout.place_reserved`). The slot's subject gets, in order: carry-over and topic work chosen by the existing rules (exam work when the credit says so), then revision for that subject.
   - **PINNED:** never given to another subject. With no eligible work it stays empty and is reported.
   - **PREFERRED:** if its subject has nothing eligible, the day's other chosen subjects are tried (sorted by id), then the planner's normal choice. This is the "move".
   - Either way, an allocation not served by its own subject is reported.
5. **Subject cap.** The weekday limit of 2 and weekend limit of 3 rise to the number of subjects the student put on that day (never lower). Smart days keep 2/3.
6. **Unreserved time.** Full slots no allocation reserves, and all short slots, are planned exactly as before, so subjects not on the timetable get the time that is left over.
7. **Unchanged academic rules.** Prerequisite gating, mastery gate, lesson counts, practice-after-last-lesson, gap-fill cap, revision offsets and share, spaced revision of decayed topics, blended exam credit, past-question cadence, mocks, and overload. Allocations only decide which subject a slot may serve.
8. **Exam runway.** Replaces the timetable (decision 3 in section 7). Days from `runwayStart` on use the old runway branch untouched, and no allocations are reported unmet there.
9. **Priority when constraints collide:** rest day > runway/exam essentials > prerequisites and practice timing > pinned > preferred > flexible planning.

### Unmet allocations

`plan_window` returns `unmet: [{date, subjectId, strength, minutes, reason}]`, stored in the new `StudyPlan.unmet` JSONB (migration `e2c5b8d1a7f3`, one `ADD COLUMN`, `NULL` = nothing unmet) and returned by `GET /study-plan` as `plan.unmet` (always a list). Reasons: `NO_ELIGIBLE_WORK`, `NO_CAPACITY` (allocated sessions that found no slot; validation normally prevents it). One entry per 30-minute slot, so the UI groups by subject and day.

### Replan safety

- **Completed and skipped work from today on now keeps its time** (`FixedItem`, built in `ReplanStudyPlanService._settled_items`). **This is a behavior change for Smart plans too.** Before, a replan on the same day (a settings edit, for example) regenerated the whole day on top of sessions already finished, so a day could be booked past its minutes. Removing the right slots (full for a 30-minute item, the short slot for a shorter one) is covered by tests. Settled sessions of an allocated subject are not reserved a second time.
- Missed sessions: unchanged (marked MISSED, become carry-over). Pending future items are regenerated; pins live in the timetable, not in items, so regeneration reproduces them.
- **Idempotent:** replanning twice with the same inputs gives the same set of items with no duplicates (tested with an in-memory store through the real service).
- **Failure:** if planning raises, the error propagates and nothing is persisted. The request's transaction (`db_session`) rolls back the earlier MISSED/removed changes.
- Day boundaries use `lagos_day_key()`, unchanged.

### Preview now includes the schedule

`POST /study-plan/preview` returns `window`: `{plannedThrough, items[{date, subjectId, subjectName, topicId, topicTitle, activityType, durationMinutes, notes, carriedFrom}], unmet, overload}` (`null` when the request is invalid). It builds the planner input exactly as a replan does (`ReplanStudyPlanService.build_input`) but read-only: missed sessions and today's finished work are read without being changed, and the mastery lookup, which caches what it derives, runs inside a savepoint that is rolled back.

### Deviations and findings

1. `layout_window` now returns three values (items, overload, unmet). Its only caller is `plan_window`.
2. `StudyPlan.unmet` is a separate column (not inside `overload`): different shape and lifecycle.
3. The outline is **not** timetable-aware (deferred per brief section 9). It remains a linear projection through the term and is labelled as such in Phase 6. Tests for TERM, BLENDED and EXAM outline labels remain in the baseline.
4. Revision for an unallocated subject can fill a **released** preferred slot (the planner's normal choice). Section 4 item 5 of this document said otherwise; this supersedes it.
5. A PREFERRED fallback to another chosen subject also reports the original as unmet, even though the slot was used.
6. `exam_slots` (the past-question cadence counter) can advance more than once per slot when a reserved slot tries more than one subject. This only affects Custom plans in exam modes and not which tasks are essential.

**Changed:** `slots.py` (`SlotAllocation`, `Availability` fields, `reserve_slots`), `layout.py` (reservation, cap, `place_reserved`, `_free_days`, unmet), `term_plan.py`, `service.py` (`_availability`, `build_input`, `_settled_items`, `pending_carry_over`, `_carry_over`, preview `window`, `read_only`), `models/planner.py` and a migration (`unmet`), `responses.py` (`window`).

**Tests added:** `test_planner_timetable_layout.py` (27: reservation, pins, preferred fallback, cap, no fabricated work, prerequisites, practice timing, catch-up, exam modes, runway, settled work), `test_study_plan_replan.py` (14: availability, lifecycle, history, idempotence, failure, read-only, preview window).

**Verified:** 502 backend tests pass, `ruff check` and `ruff format --check` clean, `pyright` 0 errors on `app/services/planner`, `app/api` and `app/services/rag/tools`. The offline SQL for both migrations, up and down, is a single `ADD COLUMN` / `DROP COLUMN` each. Whole-app `pyright` reports 3 errors in `assessments/service.py` and `repositories/coverage.py`, files this work does not touch.

**Not verified:** nothing has run against a real Postgres (migrations, the savepoint rollback, concurrent replans), and the frontend does not use `unmet` or `window` yet.


## 11. Phase 5 status (done, with one verification gap): frontend timetable editor

Frontend only. Read `node_modules/next/dist/docs` first, as `AGENTS.md` requires; the new components are plain client components, so no Next-specific API is used beyond `"use client"`.

### What a student can do now

The plan setup form is a four-step flow: **Basics** (class, term, subjects, exam), **Study time** (study days, usual minutes, per-day minutes), **Subjects** (Smart or Custom, then a card per weekday), **Review** (the backend's preview). Smart is the default and needs no extra work: Next, Next, Next, Create.

- **Timetable editor** (`timetable-editor.tsx`): a mode choice (radio group); in Custom mode a card per weekday with add, change subject, change time, preferred/pinned, **move to another day** (the non-drag alternative), and a two-step remove ("Remove X from Monday?" Yes/Keep). Rest days are shown and cannot take subjects. Pinned shows a pin icon and the word "Pinned", not colour alone. A short "How this works" box explains preferred, pinned, and that unplaced subjects get leftover time.
- **Availability** (`availability-editor.tsx`): day toggles with full names, usual school-day and weekend minutes, an optional time per day with a reset button, and a weekly total. Minutes commit on blur or Enter, so typing "45" never stores "4".
- **Review** (`timetable-review.tsx`): loading, failed (with retry and "you can still save"), invalid (every error, with "Go back and fix"), and ready: warnings with the subject named, week summary, coverage per subject, unmet sessions grouped by subject with what to do, overload as guidance, and the first two weeks, labelled as a preview that can change.
- **Unsaved changes:** a discard prompt on Cancel, a "You have unsaved changes" note, and a browser leave warning while dirty. "Dirty" compares meaning, not order.
- **Never silently discarding:** turning a study day off removes only that day's own time override. Subjects already on it are kept and flagged, and the step cannot be left until they are fixed.

### Files

New: `lib/study-timetable.ts` (draft model, validation that mirrors the backend, payload conversion, tolerant response parsing, wording), `lib/study-plan-settings.ts` (`PlanSettings` with `timetable`, `formSignature`), `components/study-plan/{timetable-editor,availability-editor,timetable-review,minutes-field,chip}`. Changed: `plan-setup-form.tsx` (stepper), `study-plan-view.tsx` (import), `hooks/api/use-study-plan.ts` (`usePreviewStudyPlan`, typed save), `lib/api/endpoints.ts` (`preview`), `lib/study-plan.ts` and `study-plan-route.ts` (`timetable`, `unmet` on the plan). The form always sends `timetable` (explicit `null` for Smart), so editing a plan never leaves a stale timetable behind, matching the backend rule from section 8.

### Verification

- `npm test`: **825 of 826 pass.** The one failure, `intervalLabel is human-readable` (`test-spaced-repetition.mts`, `'in now' !== 'now'`), fails identically with this work stashed, so it was already failing.
- New tests: `test-study-timetable.mts` (33: draft and payload, validation parity with the backend, parsing, wording) and `test-study-timetable-ui.mts` (21: server-renders each component and checks accessible names, states, warning and unmet wording, and the unsaved-changes signature).
- `tsc --noEmit` on `src/`: clean. `eslint` on every file touched: clean. `tsc` on the test scripts still reports 12 errors, all in other scripts and all there before (none mention study-plan files).
- With a throwaway page, Next 16's dev server compiled and server-rendered the whole setup form with HTTP 200. The page and its mock API are deleted.

### Not verified (gap)

**Interactive behaviour was not exercised in a browser.** The browser extension was not connected, so clicking through the steps, keyboard operation, focus movement between steps, the discard dialog, the browser leave warning, the mobile layout and dark mode were **not** checked. Component tests cover what is rendered, not what happens on click. Also not exercised: the form against the real backend (the preview was only tested against the backend's own tests and the frontend's parsing of its documented shape).

### Deliberate scope limits

The plan page itself is untouched in this phase: it does not yet show the timetable, `unmet` or the longer-range outline as a projection. That is Phase 6.


## 12. Phase 6 status (done, same browser gap): plan view and safe editing

Frontend only; the backend is unchanged in this phase.

### What the plan page shows now

In order: mode badges and notices, the **overload guidance** (now with an "Add study time" button and "or one more study day"), the **unmet notice**, **This week at a glance**, the schedule, **Your timetable**, and the class-position panel.

- **This week at a glance** (`week-overview.tsx`): Monday to Sunday of today's week. Each day shows sessions and minutes, how many are done or missed, and the subjects; rest days and today are labelled in words.
- **Sessions** (`plan-item-row.tsx`): already showed subject, topic, activity, duration, status, the planner's reason (`notes`) and "Moved from <day>". They now also say **"On your timetable"** or **"Pinned on your timetable"** (with a pin icon) when the session sits on a day and subject the student chose.
- **Your timetable** (`timetable-summary.tsx`): the student's recurring preference, kept apart from generated sessions: a badge (Smart or Custom subjects), each day's subjects with minutes, pins marked in words, rest days, and per-day times. It says the topics inside each session are picked for the student. Buttons: **Change study time**, **Edit subjects** (or **Choose subjects by day** on a Smart plan).
- **Unmet notice** (`unmet-notice.tsx`): sessions from today on that the planner could not give to their subject, grouped per subject with the days, a plain explanation, and **Edit subjects** / **Add study time**. Past dates are not shown. It is a status message, not an error.
- **Outline** is now titled **"Rest of the plan (a rough projection)"** and says it is a guide to where the class will be, does not follow the timetable or what the student finishes or misses, and that real sessions are planned two weeks at a time. This is the honest label for the deferred timetable-aware outline (section 10, item 3).
- **Editing from the plan page:** those buttons open the setup form on the step they name (`initialStep`), so "Edit subjects" lands on the subjects, not on step one. The page form is the same four-step form from Phase 5, with the same discard prompt.

### Completed history

Saving from the plan page is the same PATCH as before. The backend keeps completed, skipped and missed sessions and regenerates only pending future ones (section 8; section 10, "Replan safety"). No frontend code touches item history.

### Verification

- `npm test`: **844 of 845 pass** (845 tests, up from 826). The one failure is `intervalLabel is human-readable`, which fails the same way with this work stashed.
- New and extended tests: `test-study-plan-display.mts` (+9: `weekdayOf`, `weekOverview`, `timetableMatch`, `groupUnmet`) and `test-study-plan-page.mts` (new, 13: server-renders the session row, schedule, week overview, timetable card, unmet notice and outline wording).
- `tsc --noEmit` on `src/`: clean. `eslint` on every file touched: clean. The test-script `tsc` still shows the same 12 existing errors, none in study-plan files.

### Not verified

**Still not exercised in a browser** (the extension was not connected), so the plan page layout, the week grid on a phone, dark mode, and the buttons opening the form on the right step have not been seen or clicked. The component tests prove what is rendered, not how it looks or behaves. This is the main outstanding risk for Phases 5 and 6 together.

### Deliberate scope limits

- A session does not yet link back to the exact timetable row it came from; it is matched by weekday and subject, so a session that the planner moved to a different weekday shows no timetable mark (which is the correct reading, since it was moved).
- The "why" for an unmet pinned session is generic (nothing was ready to study). The planner does not yet report whether the cause was a prerequisite or an empty subject.
- No change to the backend outline (still approximate); no per-item "moved because" beyond the existing carry-over note.

### Phase 6 addendum: first real run found a rollout hazard

The first run of the new form showed "We could not check this plan" on the Review step. Cause: the frontend's `.env` points at the deployed backend, which does not yet have `POST /api/study-plan/preview` (it answers `405 {"detail":"Method Not Allowed"}`, whereas the existing create route answers `401 {"error":"Unauthorized"}`). The form was working as designed against a server that was one release behind.

That exposed a worse problem. An older backend ignores request fields it does not know, so saving a **custom timetable would appear to succeed and be silently dropped**. The frontend now guards against it:

- `lib/plan-preview-state.ts`: a 404 or 405 from the preview route means "unsupported server", with any other failure shown with its own message (or its status code when the body has none).
- On an unsupported server the Review step says so, offers **Use the smart timetable**, and **disables Save while a timetable (custom subjects or per-day times) would be sent**. A plan with no timetable can still be saved.
- Tests: `test-plan-preview-state.mts` (5) and two render tests for the new message. Frontend: 851 of 852 pass (the same existing flashcard failure).

**Deployment order matters (feeds Phase 8):** deploy the backend and run its migrations (`d7a3f5c1e208`, `e2c5b8d1a7f3`) **before** the frontend ships, or the new form degrades to smart-only. The backend change is backward compatible with the old frontend (omitted `timetable` keeps the stored one), so backend first is always safe. The frontend guard above covers the opposite order.


## 13. Phase 7 status (done, with one gap): AI tutor

The tutor still never builds a schedule. It proposes settings, the student approves a card, and the same `Create/UpdateStudyPlanSettingsService` the REST API uses runs the deterministic planner.

### How it flows now

1. **Propose** (`rag/tools/study_plan.py`): arguments are validated by the same `StudyPlanIn` schema and the same timetable rules as REST (rest days, subjects in the plan, duplicates, 30-minute steps, day capacity). An invalid proposal never becomes a card: the tool returns the reason so the tutor can ask the student what to change. Nothing is written.
2. **Approve or edit**: the card edits only the existing scalar fields (minutes, class, term, exam). `resolve_resume_args` merges the edit and `parse_validated_args` re-runs the full validation, so an edit that breaks the timetable (for example lowering Monday's minutes below its subjects) is refused. The timetable itself cannot be rewritten or added from the card. Rejecting drops any edit and runs nothing.
3. **Execute**: entitlement is checked first (`_deny_feature`), arguments are re-validated, then the service runs. An `ApiError` from the service is shown to the student as the reply.
4. **Report back**: after create, update or class-position changes, the reply now names timetable sessions the rebuilt plan could not give to their subject (`unmet_sentence`: grouped per subject, days, pinned marked). The result also carries `unmet` for clients. A failure to describe the outcome is logged and dropped, never reported as a failed change.

### Two safety decisions

- **A `null` timetable from the model is never a reset.** The service reads a present `timetable: null` as "return to smart". Models often fill optional fields with null, which would silently wipe a student's timetable on an unrelated update. For the tutor only a real object is a change: `{"mode": "SMART"}` resets, and omitting or nulling `timetable` keeps the saved one. REST keeps its own explicit-null meaning (section 8). The prompt says "Never send null".
- **Propose-time check of the saved timetable.** An update that omits `timetable` keeps the saved one, which may no longer fit (a day was dropped). The tool now reads the student's plan (only if it belongs to them) and refuses the proposal with the reason, so the student never sees a card that would fail on approval. The service still enforces the same rule on execution.

### What the tutor can read and is told

- `get_study_plan` now returns `timetable` (null means smart) and `unmetTimetableSessions` (first 10; date, subject, strength).
- `ACTION_SYSTEM` teaches: weekday numbering, 30-minute steps, preferred versus pinned, never use rest days or overfill a day, copy-and-change the whole timetable, leave it out to keep it, `{"mode": "SMART"}` to reset, never send null, never promise a topic, a Timetable section on the card, "upcoming sessions are rebuilt from today and finished sessions stay", and what to do when a timetable does not fit or sessions were unmet.
- The tool schema now carries field descriptions (`AllocationIn`, `DayMinutesIn`, `TimetableIn`) that also appear in the API docs.

### Verification

- `uv run pytest`: **540 passed** (38 new in `tests/test_tutor_timetable.py`). `ruff check`, `ruff format --check` clean; `pyright` 0 errors on the planner, API and tutor-tools code.
- Covered: invalid and malformed timetables refused with a reason; a valid proposal runs nothing; null versus `{"mode":"SMART"}`; saved timetable still fits or not (including another student's plan not being read into the proposal); edits that break the timetable refused, a timetable that cannot be rewritten from the card, rejection dropping edits; entitlement denial before and after approval; approval through the real graph running the service with the timetable; the unmet note, its absence, and its failure being swallowed; a service error reaching the student; what the tutor reads; and the prompt and schema wording.

### Not verified

**No real model was used.** The graph tests drive the approval flow with a scripted model, so they prove the rules the code enforces (what can be proposed, edited, approved and executed). They do not prove that a real LLM follows the new prompt well: that it asks before proposing, copies the saved timetable correctly, uses `{"mode": "SMART"}` to reset, or writes a good card. That needs a few real conversations. The frontend tutor UI was not changed and not exercised.


## 14. Phase 8: regression, rollout and final report

### 14.1 Regression run (final)

| Check | Result |
|---|---|
| Backend `uv run pytest` | **540 passed** |
| Backend `ruff check` / `ruff format --check` | clean / 367 files formatted |
| Backend `pyright` on planner, API, tutor tools | 0 errors |
| Backend `pyright` on the whole app | 3 errors, all in `assessments/service.py` and `repositories/coverage.py` (not touched by this work) |
| Alembic | single head `e2c5b8d1a7f3` |
| Frontend `npm test` | **851 of 852 pass**; the one failure is `intervalLabel is human-readable` (`'in now' !== 'now'`, flashcards), which fails identically without this work |
| Frontend `tsc --noEmit` (src) | clean |
| Frontend `tsc` on test scripts | 12 errors, all in other scripts and all pre-existing; 0 in study-plan files |
| Frontend `eslint` on every file added or changed | clean |

### 14.2 Real-database run (new in this phase)

Everything before this phase stubbed the repositories. This phase ran the real migrations and the real services against a throwaway **PostgreSQL 16.2** on 127.0.0.1 (started with `pgserver`, installed for the run only; the project's dependencies were not changed). The harness refuses to run unless the database host is local; the backend `.env` points at a cloud database, which was not touched. **35 of 35 checks passed.**

- **Fresh install:** an empty database upgrades to head; `StudyPlan` gains `timetable` and `unmet`, both `jsonb` and nullable.
- **Upgrade with existing plans:** a database at the previous head holding a plan with completed and missed items upgrades with the plan still active, `timetable` and `unmet` NULL, and every item unchanged. Downgrade drops exactly the two columns and keeps every item; upgrading again works.
- **Services on the migrated schema:** create with a custom timetable (stored as JSONB, pinned subject placed, no day past its minutes); replanning twice gives identical sessions with no duplicates; a completed session survives a settings change and still fits its day; an overdue pending session becomes MISSED and returns as a catch-up carrying its original date; an update that omits `timetable` keeps it and an explicit null resets it; an invalid timetable is a 400 and changes nothing; a planner failure rolled back leaves the plan exactly as it was; the page payload serialises.
- **Preview writes nothing**, including the mastery cache: a control proves a normal mastery lookup does write a row, so the check could have failed.
- **Daily replan and the Lagos day boundary:** opening the plan twice in a day replans once; the first open after the day rolls over replans; 00:30 Lagos (23:30 UTC the day before) replans; a replan stamped after UTC midnight is not repeated that Lagos day.

Two bugs in the harness itself were found and fixed along the way (an SQL alias that collided with `Row.t`, and typed parameters); they were not product bugs. The harness is not part of the repository.

### 14.3 Findings from this phase

1. **Extra replan in one hour (harmless, left as is).** `last_replanned_at` is a UTC timestamp compared with the Lagos day key by its UTC date. Lagos is ahead of UTC, so the comparison can only replan one extra time (between 00:00 and 01:00 Lagos), never skip a needed replan. Replans are idempotent, so the cost is one repeated pass.
2. **Duplicate element ids in the study-time editor** (a day with two problems repeated the same `id`). Fixed: one container with one id per day.
3. **Automated accessibility audit:** axe-core run over 22 rendered component states (every state of the editor, availability, review, plan view, session rows, and the setup form's first three steps) found **no violations** across 457 rule checks; a deliberately broken control was flagged, so the audit can fail. Limits: it ran in jsdom, which cannot check colour contrast, focus order, keyboard behaviour, touch size or layout, so none of those were verified.

### 14.4 Rollout

**Feature flag: none added.** The project has no per-feature flag mechanism (only the tier gate `BETA_OPEN_FEATURES`). The change is already staged by design: the smart timetable is the default and the new options are opt-in; the backend accepts old and new clients; the new frontend degrades safely against an old backend (section 8, Phase 6 addendum). Adding a flag would add code for no extra safety.

**Order (backend first, always):**
1. Commit and deploy the **backend**. Migrations `d7a3f5c1e208` then `e2c5b8d1a7f3` must run. `deploy_script.sh` and `deployment_hook.sh` both run `alembic upgrade head(s)`, but the `Dockerfile` `CMD` does not. **Confirm which path your host uses**, and check afterwards with `alembic current` that it reports `e2c5b8d1a7f3`.
2. Smoke-test with an existing account: open the study plan page (should look the same, with the new cards), then create a plan on the Review step (the check should now succeed instead of showing the unsupported-server message).
3. Deploy the **frontend**. If it ships first, the form still works for smart plans and blocks saving a custom timetable it knows the server would drop.

**Migration risk:** each migration is one nullable `ADD COLUMN` with no default and no backfill, a metadata-only change in PostgreSQL (no table rewrite, a momentary lock). Verified on 16.2; your hosted version was not checked.

**Rollback:**
- *Frontend:* redeploy the previous build. Safe at any time: an older client omits `timetable`, which keeps the stored one (verified on a real database).
- *Backend code:* redeploy the previous build and **leave the columns in place**. The extra nullable columns are ignored by the old code (reasoned, not run). Custom timetables already saved stay stored but are not applied until the new code returns.
- *Database:* `alembic downgrade c6d8e0f2a413` works (verified) but **permanently deletes every saved timetable and unmet record**. Only do this if it must be removed, and first save them: `select id, timetable, unmet from "StudyPlan" where timetable is not null`.

**Behavior change for every plan, not only custom ones:** a replan on the same day now keeps completed and skipped sessions' time instead of re-booking it (section 10). Students will see their day no longer overfilled after a settings change.

### 14.5 Definition of Done

| Item | Status |
|---|---|
| Smart timetable behaves as before | Done: the 32 baseline tests pass unchanged; legacy plans upgrade and load on a real database |
| Per-day availability | Done |
| Subjects on preferred days | Done |
| UI distinguishes preferred, pinned, unavailable | Built and rendered in tests; **not seen in a browser** |
| Backend validates and persists | Done, including on a real database |
| Planner respects the configuration | Done |
| Existing academic rules tested | Done |
| Capacity conflicts and unplaced work surfaced | Done (unmet sessions, overload guidance, review warnings) |
| Replans keep completed history and pinned assignments | Done, verified on a real database |
| TERM, BLENDED and EXAM covered | Done in unit tests; the long-range outline is not timetable-aware |
| Outline compatible or documented as deferred | Documented as deferred and labelled "a rough projection" in the UI |
| Tutor follows propose, approve, revalidate, entitlement, service | Done; tested with a scripted model, **not with a real LLM** |
| Migrations and legacy plans tested | Done on PostgreSQL 16.2 |
| Tests, type checks, lint, formatting | Done, with the pre-existing exceptions in 14.1 |
| Responsive and accessibility checks | **Partial:** automated structural audit only; no real browser, keyboard, mobile, contrast or screen-reader check |
| Documentation | Done (this document) |

### 14.6 Final report

**Summary.** Students can choose the days, the minutes for each day, and (optionally) which subjects go on which days, as preferred or pinned. The deterministic planner still picks the topics and activities, now inside that structure, and says plainly when a request cannot be met. The tutor can propose the same changes through the existing approval flow.

**Architecture.** The timetable is the student's intent, stored in one JSONB column on the plan and kept apart from the generated sessions. The pipeline is unchanged: clear pending work, gather curriculum and mastery, select topics, lay out slots, persist, project the outline. The only change to scheduling is that slots can be reserved for a subject and the layout respects those reservations (section 10).

**Files changed (by area).**
- *Backend, migrations:* `alembic/versions/d7a3f5c1e208_add_study_plan_timetable.py`, `e2c5b8d1a7f3_add_study_plan_unmet.py`.
- *Backend, domain and planner:* `app/database/models/planner.py`; `app/services/planner/{timetable (new), slots, layout, term_plan, mode, service, __init__}.py`.
- *Backend, API:* `app/api/{schemas, responses}.py`, `app/api/student/study_plan.py`.
- *Backend, tutor:* `app/services/rag/{prompts}.py`, `app/services/rag/tools/{study_plan, reads, context}.py`.
- *Backend, tests:* `test_planner_baseline`, `test_planner_timetable`, `test_planner_timetable_layout`, `test_study_plan_preview_api`, `test_study_plan_replan`, `test_tutor_timetable` (new); `test_study_plan_payload` (fixtures).
- *Frontend:* `lib/{study-timetable, study-plan-settings, plan-preview-state}.ts` (new); `lib/{study-plan, study-plan-route, study-plan-display, day-keys}.ts`, `lib/api/endpoints.ts`, `hooks/api/use-study-plan.ts`; components `timetable-editor`, `availability-editor`, `timetable-review`, `timetable-summary`, `unmet-notice`, `week-overview`, `minutes-field`, `chip` (new) and `plan-setup-form`, `plan-item-row`, `plan-schedule`, `study-plan-view` (changed); tests `test-study-timetable`, `test-study-timetable-ui`, `test-study-plan-page`, `test-plan-preview-state` (new) and `test-study-plan-display` (extended).
- *Documentation:* this file.

**Data and API changes.** `StudyPlan.timetable` and `StudyPlan.unmet` (JSONB, nullable). `StudyPlanIn.timetable` (optional; omitted keeps, explicit null resets for REST). `POST /api/study-plan/preview` returning `{valid, errors, warnings, summary, window}`. `GET /api/study-plan` returns `plan.timetable` and `plan.unmet`. Full shapes in sections 5, 8 and 9.

**Scheduling rules and fallbacks.** Section 10. In short: rest days never get work; the exam run-up overrides the timetable; prerequisites and practice timing outrank pins; a pinned slot is only ever its subject's (empty and reported if nothing is ready); a preferred slot falls back to the day's other chosen subjects, then to the planner's normal choice; unplaced work is reported, never invented.

**Safety.** Completed, skipped and missed sessions are never deleted or rewritten; only pending future sessions are regenerated, deterministically, so repeated replans give the same plan. Finished work keeps its time. A failed replan rolls back completely. An older client cannot erase a timetable; the tutor cannot erase one with a null; an update that would break a saved timetable is refused with a reason.

**Known gaps (not done, or not verified).**
1. **Not seen or used in a real browser:** layout, mobile, dark mode, keyboard operation, focus movement, the discard dialog and browser leave warning, and the buttons opening the form on the right step. The browser extension was never connected.
2. **Not tried with a real LLM:** the tutor prompt may need tuning after a few real conversations.
3. **Long-range outline is not timetable-aware** (deferred by the brief; labelled in the UI).
4. **Unmet reasons are generic.** The planner does not say whether a prerequisite or an empty subject caused it.
5. **Hosted database version and the hosted deploy path** (whether migrations run automatically) were not checked.
6. **Pre-existing failures** left alone: one flashcard test, 12 test-script type errors, 3 whole-app pyright errors.
7. **Nothing from Phase 7 onward, and none of the frontend, is committed** as of this writing.
