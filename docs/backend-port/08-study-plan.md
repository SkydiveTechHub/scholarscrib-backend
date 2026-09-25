# Study planner

Production entry point is `planWindow` via `src/lib/study-plan.ts`. The function `generatePlan` in `src/engines/planner/plan.ts` is an older full-horizon algorithm and **is not called** by routes. Port `planWindow` and its helpers. Leave `generatePlan` out unless you are deliberately reviving it.

Helpers: `mode.ts`, `days.ts`, `slots.ts`, `topics.ts`, `layout.ts`, `term-plan.ts`, `term-context.ts`, `replan.ts`, `completion.ts`, `outline.ts`.

Tests: `scripts/test-study-plan-*.mts`.

## Modes

Derived on each replan. Not stored, except `forceExamMode` and the target date that feed the derivation (`src/engines/planner/mode.ts`).

| Mode | When |
|---|---|
| `TERM` | student is not SS3, or there is no future `targetDate`, or the exam date is already past |
| `EXAM` | SS3, future `targetDate`, and `forceExamMode` |
| `BLENDED` | SS3, future `targetDate`, and not forced |

Blended exam-share of each day’s minutes:

- ≥ 120 days out → 0.10
- ≤ 42 days out → 0.50
- linear between those

The rest of the day stays on the term syllabus.

Default session lengths when the client does not send minutes (`DEFAULT_MINUTES`):

| Class | Weekday | Weekend |
|---|---|---|
| SS1 | 30 | 60 |
| SS2 | 45 | 90 |
| SS3 | 60 | 120 |

A day only counts as a study day if it is in `studyDays` and its minute budget is ≥ 30 (`SESSION_MINUTES` in `slots.ts`). `planSettingsProblem` rejects a plan whose chosen days are all under 30 minutes.

Exam target date is allowed only for SS3, and only in the future. `targetExam` and `targetDate` are a pair.

## Window

`WINDOW_DAYS = 14`. The planner materialises items from today through 14 days, not through the exam. `plannedThrough` stores the last date written. `CARRY_OVER_DAYS = 14`: missed work can be pulled forward for two weeks.

Replan is stale when `lagosDayKey(lastReplannedAt) < lagosDayKey(now)` (`replan.ts`). Reading the plan page calls `replanIfStale`.

## Term context

`src/engines/planner/term-context.ts`. If any `AcademicTerm` row overlaps today or starts within 60 days, use the configured calendar (`termSource` reflects that). Otherwise use the built-in Nigerian term dates in that file. Week-of-term is counted in Mondays. This is what decides which topics are “current” versus “preview”.

Class position (`StudyPlanPosition`) overrides the calendar: the topic the student says their class is on becomes the current topic for that subject, provided it is at or below their `classLevel`.

## Topic selection (`topics.ts`)

Term-mode priority:

1. `CARRY_OVER` — unfinished items from the previous window
2. `GAP_FILL` — weak or gapped topics already taught
3. `CURRENT` — this week’s syllabus topic
4. `CATCH_UP` — earlier topics in the term not yet done
5. `PREVIEW` — upcoming, only with spare time

`EXAM` mode uses `carryOverOnly` for the term side and fills the exam share from exam candidates.

Exam candidates: topics at or below the student’s class, mastery < `TARGET` (70), ordered by `waecWeight + jambWeight`.

## Layout (`layout.ts`)

| Rule | Value |
|---|---|
| Subjects per weekday | at most 2 |
| Subjects per weekend day | at most 3 |
| Gap-fill share of term minutes | 0.20 |
| Revision share | 0.20 |
| Mocks placed in a window | `MOCK_COUNT = 2` |
| Past questions | every 3rd exam slot |
| Runway (close to the exam) | mocks, then revision, then past questions |

`examCredit` accumulates fractional exam minutes so a 0.10 share still produces a real block once the credit reaches a session.

Activities written as `StudyPlanItem.activityType`: `LESSON`, `PRACTICE`, `REVISION`, `MOCK_EXAM`, `PAST_QUESTIONS`.

`outline` JSON is the week summary shown on the page. `overload` JSON is set when the selected subjects cannot fit the minute budget; the page shows it instead of silently dropping subjects.

## Replan transaction (`study-plan.ts`)

`replanIfStale`:

1. Lock the plan row `FOR NO KEY UPDATE`.
2. Compare-and-swap on `lastReplannedAt` so two readers do not double-write.
3. Past `PENDING` items whose date is before today become `MISSED`.
4. Delete future `PENDING` items (completed and skipped history stays).
5. Run `planWindow`.
6. Insert the new items. Items carried from a missed day set `carriedFromDate`.
7. Write `outline`, `overload`, `plannedThrough`, `lastReplannedAt`.

Create (`POST`) deactivates other `isActive` plans for the student, inserts the new row, and replans immediately. Update settings patches the active row and replans. Positions upsert or delete `StudyPlanPosition` rows and replan.

## Completion

`src/engines/planner/completion.ts` and `src/lib/study-plan-completion.ts`.

Auto-complete picks the oldest `PENDING` item on or before today, inside the carry-over window, whose activity matches the signal, and compare-and-swaps it to `COMPLETED` with `completionSource = AUTO` and `completedAt = now`.

| Signal | Matches activity |
|---|---|
| Lesson completed (progress pass or lesson route) | `LESSON` |
| Topic quiz / practice | `PRACTICE` |
| Card review on a topic | `REVISION` (the review signal; confirm `signalForAssessment` vs card path in `study-plan-completion.ts` when porting — card path is `markPlanFromCardReview`) |
| `PAST_PAPER` attempt | `PAST_QUESTIONS` |
| `MOCK_EXAM` or `CBT_PRACTICE` | `MOCK_EXAM` |

`signalForAssessment`: mock and CBT → `MOCK_EXAM`; past paper → `PAST_QUESTIONS`; otherwise `TOPIC_QUIZ` when the paper has topic ids. The lesson practice-exit path does not also emit a quiz signal for the same submit; it calls `markPlanFromLesson` on pass.

Manual `PATCH` of an item:

- Allowed statuses: `COMPLETED`, `SKIPPED`, `PENDING`.
- `completionSource = MANUAL` when the student sets completed.
- Lookback is 7 days: a manual complete cannot target an item older than that.
- Future items are not manually completable.
- Setting `PENDING` clears `completedAt` and `completionSource` when the rules allow reopening.

`MISSED` is written only by the replan, not by the item PATCH.

## Page payload

`getStudyPlanPageData` returns the fields the GET route sends: Lagos `today`, class level, term label and whether it came from admin terms or the fallback, days until the target exam (null in term mode), the default minute budgets for the class, the subject list the picker needs, and `plan` (items, positions, outline, overload, settings) or null when the student has never created one.

Display helpers in `src/lib/study-plan-display.ts` (hrefs, grouping, labels) are UI. The API can return the raw items and let the client format them.
