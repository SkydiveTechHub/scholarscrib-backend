# Study Plan: Explicit Class/Term Scope and Strict Topic Filtering — Design

**Date:** 2026-10-15
**Status:** Design approved in chat, pending spec review
**Builds on:** `2026-09-14-study-plan-term-mode-design.md`

## 1. Problem

A student on an SS1 first-term plan can end up with topics from other terms or class levels.

1. **Unmapped topics default to SS1 First term.** `_plan_topics` (`app/services/planner/service.py`) starts every topic at `SS1`/`FIRST` and keeps that when `curriculum_level_id` is empty or the lookup fails. Such a topic is treated as SS1 first-term content, enters the term list and shifts the week-to-topic index.
2. **"At or below class" ignores term.** `at_or_below_class` compares class levels only.
   - `_gaps_for` (`topics.py`) can pull a prerequisite from a later term of the same class.
   - `position_options` lists every term of the student's class, and `SetStudyPlanPositionsService` accepts them (it checks class only).
3. **The plan follows the profile, not the student's choice.** The plan's class comes from `student.class_level`, and its term from today's calendar. A student cannot plan for another term, and the class cannot differ from settings.
4. **Mastery never reaches the planner.** `ReplanStudyPlanService` passes `state={}`, `pretest_passed` is never filled and `revision_due` is never filled. "Skip what you already know" and "revise what is fading" therefore do nothing.
5. **The classroom may not open on the student's class.** `ClassTermBrowser` already defaults to the settings class, but it keeps its tab in `useState(initialClassLevel)`. That state survives navigation between subjects and after a settings change.

## 2. Goals

1. A plan has an explicit **class and term scope**, chosen by the student, defaulting to their settings class and the current calendar term.
2. The planner only schedules topics inside that scope. Gap-fill may reach back to earlier scopes, never forward. SS3 exam share is unchanged.
3. If the chosen class differs from the settings class, the student is asked whether to update their class.
4. Mastery, pretest and revision-due data feed the planner.
5. The classroom subject page opens on the settings class every time.

**Non-goals:** changing the exam-share ramp, the slot or layout rules, or the subscription gate.

## 3. Decisions

| Question | Decision |
|---|---|
| Later-term content in a plan | Never, except SS3 exam share (§5.1 of the term-mode spec) |
| Who picks the term | The student, in plan setup. Defaults to the current calendar term |
| Class differs from settings | Prompt: update the profile class, or keep it and only plan the chosen class |
| Pacing for a non-current term | Start at that term's first topic and advance as topics are mastered |
| Exam mode | Tied to the **plan's** class being SS3, not the profile class |
| Mastery wiring | Include in this change |

## 4. Data model

`StudyPlan` gains two columns:

| Field | Type | Notes |
|---|---|---|
| `scopeClassLevel` | `ClassLevel` | not null |
| `scopeTerm` | `Term` | not null |

- Alembic migration backfills existing plans with `scopeClassLevel = User.classLevel` (SS1 when null) and `scopeTerm` from the term calendar for the plan's creation date (built-in calendar when unconfigured; FIRST as the last resort).
- Scope is stored, not computed, so a later settings or calendar change does not silently move a plan.

## 5. Planner

### 5.1 Scope helper

Add `scope_ordinal(class_level, term)` in `planner/mode.py`, mirroring the frontend `scopeOrdinal` (SS1 First = 0 … SS3 Third = 8). Replace `at_or_below_class(topic.class_level, …)` calls in term selection with:

- `in_scope(topic, scope)`: topic's class and term equal the plan's scope.
- `before_scope(topic, scope)`: topic's ordinal is lower than the scope's. Used for gap-fill only.

`select_exam_topics` keeps its class-level filter (exam share is the one deliberate exception).

### 5.2 Topic selection (`select_term_topics`)

- `term_topics` = topics in scope, by `order_index`.
- **Where the class is:**
  - A position, if set, and it must be in scope.
  - Otherwise, if the scope equals the current calendar term and class, the existing week-of-term index.
  - Otherwise index 0, and the first unmastered topic becomes current as mastery rises.
- **Gap-fill:** `_gaps_for` only accepts a prerequisite that satisfies `before_scope`. A prerequisite in the same or a later scope that is not already in `term_topics` is skipped (not logged; only unmapped topics are logged, see §5.3).
- **Holiday handling** keeps today's behaviour when the scope equals the previous or next calendar term. Otherwise the scope's own topics are used and the calendar is ignored.
- **Preview** is the next topic in scope only. It never crosses into the next term.

### 5.3 Unmapped topics

`_plan_topics` stops defaulting. A topic without a `curriculum_level_id` is **excluded** from planning and gets a warning log (topic id, subject). The admin curriculum screen already lists topics by level, so the data is fixable there. `calendar_topic_id` and `position_options` use the same filtered list.

### 5.4 Mastery wiring

In `ReplanStudyPlanService`:

- `state` comes from `GetTopicMasteryService(session, student_id, topic_ids).process()` over the plan's subjects' topics (the existing mastery store).
- `pretest_passed` comes from `metrics_repository.passed_topic_ids` over the same topics.
- `revision_due` comes from topics where mastery state shows decay due (`graph_colour == "DECAYED"`). The exact selector is confirmed in the plan phase against `RevisionDue`.
- If loading fails, log it and fall back to empty values so a replan still succeeds.

## 6. API

`StudyPlanIn` gains `classLevel: ClassLevel` and `term: Term`, both required on create and update (the form always sends them).

- **Validation (`plan_settings_problem`):**
  - Exam date and exam mode require `classLevel == SS3` (the plan's class, not the profile).
  - Everything else is unchanged.
- **`SetStudyPlanPositionsService`:** the topic must be in the plan's scope. Error: "That topic is outside your plan's term".
- **`GetStudyPlanService`:** returns `scope: {classLevel, term}`, `positionOptions` limited to the scope, and `profileClassLevel` so the UI can detect a mismatch.
- **Plan mode** is resolved from the plan's scope class, replacing `self.class_level`. `DEFAULT_MINUTES` also keys on the scope class.
- **Profile update** reuses the existing endpoint the settings academic form calls. No new endpoint.

## 7. Frontend

### 7.1 Plan setup form (`plan-setup-form.tsx`)

- New **Class** chips (SS1–SS3) and **Term** chips (1st–3rd), above Subjects. Defaults are the profile class and the current calendar term from the page data.
- Changing the class resets the "where is your class" selections, since they belong to the old scope.
- Exam section shows only when the chosen class is SS3.
- Copy changes from "For {class} — change your class in Settings" to "Planning for {class}, {term}".

### 7.2 Class mismatch prompt

On submit, if `chosen class !== profileClassLevel`, show a dialog before saving:

> Your account is set to SS1. Update it to SS2 as well?
> **[Update my class]** **[Keep SS1 in settings]**

- **Update my class:** call the profile update, then save the plan.
- **Keep:** save the plan only.
- **Dismiss/cancel:** nothing is saved.
- If the profile update fails, show the error and do not save the plan.

### 7.3 Plan page header

`"{class} · {term} Term"`, plus the week of term only when the scope is the live calendar term.

### 7.4 Classroom

In `app/(dashboard)/classroom/[subjectSlug]/page.tsx`, render `ClassTermBrowser` with `key={`${subject.id}:${initialClassLevel}`}` so the tab resets on subject or class change. The reproduction steps are recorded in the plan phase; if the cause turns out to be the empty-class fallback instead, the fix is the same key plus a visible note when the class has no topics for that subject.

## 8. Error handling

- **Scope with no topics for a subject:** that subject contributes only gap-fill and revision, and the page shows "No topics for {class} {term} in {subject} yet" (existing copy path).
- **Unmapped topics:** excluded, logged. They are not an error for the student.
- **Existing plans:** migrated with the backfill in §4 and replanned on first load as today.
- **Mismatch prompt, profile update fails:** plan not saved, error shown.

## 9. Testing

**Unit (pure planner):**
- SS1 First scope never yields SS1 Second/Third, SS2 or SS3 topics, including through gap-fill, preview and positions.
- Gap-fill accepts earlier-scope prerequisites and skips same or later ones.
- A non-current scope starts at index 0.
- A current scope still follows week of term.
- Unmapped topics are excluded.
- Exam share still draws from all terms at or below class.

**Integration:**
- `StudyPlanIn` requires scope; exam fields rejected unless scope class is SS3.
- Position outside scope rejected.
- Replan passes mastery: a mastered current topic is skipped; a pretest-passed topic skips lessons.
- Migration backfill sets scope for existing plans.
- Replan survives a mastery loading failure.

**Frontend:**
- Setup form defaults to profile class and current term.
- Mismatch prompt: each of its three outcomes.
- Classroom subject page opens on the settings class after navigating from another subject.

**Manual:** create an SS2 plan as an SS1 profile, accept and decline the prompt, then check the plan and the classroom.

## 10. Plan deletion (added 2026-10-15)

Students could replace a plan but never remove one. Add a **soft delete**:

- `DELETE /api/study-plan` sets the active plan's `isActive = false` under the same row lock a replan takes. Items, positions and history stay in the database. The route uses the `studyPlanner` entitlement.
- No active plan returns 404 "No active study plan".
- UI: a "Delete plan" button in the "Change plan" panel opens a confirmation modal ("Keep my plan" / "Delete plan"). After success the page shows the empty state, and the student can create a new plan.
- Out of scope: hard delete and restoring a deleted plan.

## 11. Rollout

1. Apply the Alembic migration.
2. Deploy backend, then frontend.
3. Check the logs for unmapped-topic warnings and fix them in the admin curriculum screen.
