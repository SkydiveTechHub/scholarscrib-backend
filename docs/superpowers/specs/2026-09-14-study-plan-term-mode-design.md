# Study Plan: Term Mode for SS1–SS3 — Design

**Date:** 2026-09-14
**Status:** Approved in brainstorming, pending spec review

## 1. Problem

The study plan today is an exam countdown. It only helps SS3 students close to WAEC/JAMB/NECO:

- An exam (`WAEC | JAMB | NECO`) and an exam date are required (`src/lib/validators.ts:271-276`, `src/components/study-plan/study-plan-view.tsx:134,313`).
- The plan always ends in a 14–21 day mock/past-questions runway (`src/engines/planner/plan.ts:71-73, 373-430`).
- Topic priority uses `waecWeight + jambWeight` (`src/engines/learning/recommend.ts:24`).
- The planner ignores `User.classLevel` and the term structure. An SS1 student gets SS3 topics (`src/lib/learning-path.ts:41-45`).
- Every day gets the same `dailyStudyHours`. There are no rest days and no catch-up.
- `StudyPlanItem.status` is never updated. The only write is `createMany`, so missed work never moves.

## 2. Goals

1. SS1 and SS2 students (and SS3 students before exam season) get a useful plan that follows the school term.
2. Plans are **realistic**: they never schedule more than the student's stated availability, and they have lighter days and a weekly catch-up slot.
3. Plans are **progressive**: prerequisites come first, then learn → practice → revise at spaced intervals, gated on mastery.
4. SS3 moves smoothly from term study into exam preparation, with a manual override.
5. Completion is tracked (automatic plus manual) and missed work is rescheduled.

**Non-goals:** reminders or push notifications, AI-generated plans, teacher or parent views, changing the subscription gate (the plan stays behind the STANDARD `studyPlanner` entitlement).

## 3. Decisions made

| Question | Decision |
|---|---|
| How SS1/SS2 decide what to study | Default to the national term plan. Students can set "my class is on topic X" per subject. |
| Source of term dates | One app-wide calendar managed by an admin |
| When SS3 switches to exam mode | Automatic blend once an exam date is set, with a "Exam mode now" override |
| Availability input | Study days plus weekday minutes and weekend minutes |
| Completion | Automatic from learning activity, plus a manual tick, skip and undo |
| Architecture | One planner with modes, a rolling 2-week detailed window, and an outline for the rest |

## 4. Data model

The migration SQL is hand-written with LF line endings, applied through the Supabase SQL Editor and checked against the catalog (see project memory notes).

### 4.1 `AcademicTerm` (new)

| Field | Type | Notes |
|---|---|---|
| `id` | `String @id @default(cuid())` | |
| `session` | `String` | e.g. `"2026/2027"` |
| `term` | `Term` | existing enum `FIRST \| SECOND \| THIRD` |
| `startsOn` | `DateTime @db.Date` | |
| `endsOn` | `DateTime @db.Date` | must be after `startsOn` |
| `createdAt` / `updatedAt` | `DateTime` | |

- `@@unique([session, term])`. Term date ranges must not overlap; the admin API checks this.
- `resolveTermContext(date, terms)` (pure function) returns one of:
  - `{ kind: "in_term", term, session, weekOfTerm, totalWeeks, weeksLeft }`
  - `{ kind: "holiday", previous, next }`, where either may be null
  - `{ kind: "unconfigured" }`. Callers then use a built-in approximate calendar: FIRST roughly 8 Sep–15 Dec, SECOND 6 Jan–10 Apr, THIRD 27 Apr–24 Jul.
- Weeks are Monday-based, in Africa/Lagos time.

### 4.2 `StudyPlan` (changed)

- `targetExam ExamType?` and `targetDate DateTime?` become optional.
- Added:
  - `forceExamMode Boolean @default(false)`
  - `studyDays Int[]`: ISO weekdays 1 = Mon … 7 = Sun, non-empty
  - `weekdayMinutes Int`: 0–480
  - `weekendMinutes Int`: 0–600
  - `plannedThrough DateTime? @db.Date`
  - `lastReplannedAt DateTime?`
- Removed: `dailyStudyHours`. Data migration: `studyDays = {1..7}` and `weekdayMinutes = weekendMinutes = round(dailyStudyHours * 60)`.
- **Mode** is computed, never stored (`resolvePlanMode(plan, classLevel)`):
  - `forceExamMode` gives `EXAM`
  - `classLevel = SS3` with a `targetDate` gives `BLENDED`
  - anything else gives `TERM`
- `forceExamMode` and `targetDate` are only accepted when `classLevel = SS3`. If a student's class changes away from SS3, they are ignored.

### 4.3 `StudyPlanPosition` (new): "my class is on topic X"

| Field | Type |
|---|---|
| `id` | `String @id @default(cuid())` |
| `studyPlanId` | FK → `StudyPlan`, cascade delete |
| `subjectId` | FK → `Subject` |
| `topicId` | FK → `Topic` |
| `updatedAt` | `DateTime` |

- `@@unique([studyPlanId, subjectId])`.
- No row means "follow the calendar".
- The topic must belong to that subject and be at or below the student's class level; the API checks this.

### 4.4 `StudyPlanItem` (changed)

- `PlanItemStatus` gains `MISSED`, giving `PENDING | COMPLETED | SKIPPED | MISSED`.
- Added:
  - `completedAt DateTime?`
  - `completionSource PlanCompletionSource?`, a new enum `AUTO | MANUAL`
  - `carriedFromDate DateTime? @db.Date`
- Index `@@index([studyPlanId, scheduledDate, status])`.

## 5. Planning logic

Everything in `src/engines/planner/` stays pure and deterministic: data in, drafts out, no database access.

### 5.1 Inputs

```ts
type PlannerInput = {
  today: Date;                      // Lagos day
  mode: "TERM" | "BLENDED" | "EXAM";
  classLevel: ClassLevel;
  termContext: TermContext;
  targetDate?: Date;
  availability: { studyDays: number[]; weekdayMinutes: number; weekendMinutes: number };
  subjects: SubjectInput[];         // topics with classLevel, term, orderIndex, estimatedMinutes, weights
  graph: TopicGraph;                // prerequisite edges
  mastery: Map<topicId, TopicState>;
  positions: Map<subjectId, topicId>;
  revisionDue: RevisionItem[];
  carryOver: MissedItem[];          // topics from MISSED items
  windowDays: 14;
};
type PlannerOutput = {
  items: PlanItemDraft[];           // detailed window only
  outline: OutlineWeek[];           // after the window, to term end or exam
  overload?: { topicsBehind: number; suggestedExtraMinutesPerWeek: number };
};
```

### 5.2 Step 1: pick topics (`selectTermTopics`)

For each subject in `TERM` mode:

1. **Where the class is.** Use `positions[subject]` if set. Otherwise take this term's topics for the student's class, sorted by `orderIndex`, and use index `floor(weekOfTerm / totalWeeks * count)`, clamped to the list.
   - During a holiday, where the class is = end of the previous term.
   - Previews come from the next term's first two topics.
2. **Candidates, in priority order:**
   1. **Carry-over:** topics from MISSED items.
   2. **Gap-fill:** prerequisites (edge kind `PREREQUISITE`) of current or catch-up topics with mastery below `GATE` (60), from earlier terms or classes. They are ordered topologically and capped at 20% of the window's minutes.
   3. **Current:** the topic where the class is, if mastery is below `TARGET` (70).
   4. **Catch-up:** this term's topics before where the class is, with mastery below `TARGET`.
   5. **Revision due:** from `revisionDue`.
   6. **Preview:** the next topic after where the class is. Only included if there is time left and its prerequisites are satisfied.
3. Topics above the student's class level are never selected.

### 5.3 Exam share (`examShare`)

- `BLENDED` mode: the fraction of window minutes given to exam preparation depends on days until `targetDate`:
  - more than 120 days: 0.10
  - between 120 and 42 days: rises in a straight line from 0.10 to 0.50
  - 42 days or fewer (before the runway): 0.50
  - inside the runway (the existing `computePlanWindow` rule, last 20% clamped to 14–21 days): today's runway logic, restricted to topics at or below the class level
- The exam share is filled with weak SS1–SS3 topics ranked by `waecWeight + jambWeight` (revision and practice) and `PAST_QUESTIONS` sessions.
- If every SS3 topic in the chosen subjects is at `TARGET` or above, the term share becomes 0.
- `EXAM` mode: the current `generatePlan` behaviour, with topics restricted to the student's class level or below.

### 5.4 Step 2: build slots (`buildSlots`)

- For each date in the window whose ISO weekday is in `studyDays`, the day's budget is `weekdayMinutes` (Mon–Fri) or `weekendMinutes` (Sat–Sun).
- The budget is split into 30-minute slots. A remainder of 15–29 minutes becomes one short slot, which only takes REVISION. Anything under 15 minutes is dropped.
- **Catch-up slot:** the first slot of the last study day in each calendar week.
- **Invariant:** total scheduled minutes on a day never exceed that day's budget.

### 5.5 Step 3: fill the window (`layoutWindow`)

- **Target mix of the non-exam minutes each week:** about 50% LESSON, 25% PRACTICE, 20% REVISION. The catch-up slot covers the rest.
- **Catch-up slots** take carry-over first, otherwise REVISION.
- **Topic sessions:**
  - LESSON sessions = `ceil(estimatedMinutes / 30)`.
  - If the student passed the pretest (80 or higher), skip the lessons and go straight to practice.
  - 1–2 PRACTICE sessions, always in a *later* slot than the topic's last lesson.
- **Prerequisite gate:** a topic's first LESSON can only be placed if every prerequisite has mastery at `GATE` or above, or has a lesson placed earlier in the window.
- **Revision passes** at +1, +3, +7 and +14 days after the topic's last lesson, each moved to the next study day with a free slot.
- **Daily subject cap:** 2 subjects on a weekday, 3 on a weekend day.
- **Subject choice:** pick the subject that is furthest behind (current index minus index of the first unmastered topic), then the one used least this week, then the lowest `orderIndex`.
- **Moved items:** items placed from carry-over get `carriedFromDate` set.

### 5.6 Overload and outline

- **Overload:** if the required candidates (carry-over, gap-fill, current, catch-up) don't fit the window, place them in that priority order and return `overload`:
  - `topicsBehind` = number of unplaced required topics
  - `suggestedExtraMinutesPerWeek` = unplaced minutes ÷ 2, rounded up to the nearest 30
- **Outline (`projectOutline`):** a quick week-by-week projection of which topic comes next per subject, from the end of the window to the term end (TERM) or `targetDate` (BLENDED/EXAM). It is computed when read and not saved.

### 5.7 Compatibility

- The existing `roundRobinPlan` fallback (used when there are no graph edges) is kept but gets the same topic set and slot rules. `PAST_QUESTIONS` is only included in EXAM or BLENDED mode.

## 6. Completion

### 6.1 Automatic

`markPlanItemsFromEvent(userId, event)` in `src/lib/study-plan-completion.ts` runs wherever a `LearningEvent` is written. It finds the active plan's **oldest** PENDING item with `scheduledDate <= today`, the same `topicId`, and a matching activity:

| Event | Activity it completes |
|---|---|
| Lesson completed | LESSON |
| Practice or topic quiz attempt submitted | PRACTICE |
| Revision quiz, or flashcard review on the topic | REVISION |
| Mock exam attempt submitted | MOCK_EXAM (no topic match needed) |
| Past paper attempt submitted | PAST_QUESTIONS (subject match) |

- **Update:** sets `status = COMPLETED`, `completionSource = AUTO` and `completedAt = now`, completing at most one item per event.
- **Failures:** caught and logged, and never fail the learning request (same pattern as `0d62d47`).
- **Future items:** if nothing matches today or earlier, nothing is completed. Studying ahead shows up as mastery at the next re-plan.

### 6.2 Manual

`PATCH /api/study-plan/items/[id]`, body `{ status: "COMPLETED" | "SKIPPED" | "PENDING" }`.

- **Owner only**, and the entitlement is checked.
- **Only items dated between `today - 7` and `plannedThrough`.** This lets a student tick a recently MISSED item they actually did offline, and a ticked MISSED item becomes COMPLETED.
- **Values set:** `COMPLETED` sets `completionSource = MANUAL` and `completedAt`. `PENDING` clears both.
- **No re-plan** is triggered.

## 7. Re-planning

`replanIfStale(userId, { force?: boolean })` in `src/lib/study-plan.ts`:

1. **Runs when:**
   - the plan page loads and `lastReplannedAt`'s Lagos day is before today, or
   - `force` is set by a settings change (availability, subjects, positions, exam date, exam mode override).
2. **Locking:** runs in a transaction that locks the `StudyPlan` row first (`SELECT … FOR NO KEY UPDATE`, as in `registerDevice`) and checks staleness again after locking.
3. **Missed items:** PENDING items dated before today become `MISSED`.
4. **Clearing the window:** PENDING items dated today or later are deleted. COMPLETED, SKIPPED and MISSED items are never changed. Today's already-completed items take up slots when the planner rebuilds the window.
5. **Rebuild:** load the inputs, run the planner, `createMany` the new drafts, and set `plannedThrough = today + 13` and `lastReplannedAt = now`.
6. **Carry-over:** MISSED items from the last 14 days whose topic is still below `TARGET`.
7. **Plan creation:** `POST /api/study-plan` keeps its current job (deactivate the old plan, create a new one) and then calls the same planner path with `force`.

## 8. API and validation changes

`generateStudyPlanSchema` becomes:

```ts
{
  subjectIds: z.array(z.string()).min(1),
  studyDays: z.array(z.number().int().min(1).max(7)).min(1),
  weekdayMinutes: z.number().int().min(0).max(480),
  weekendMinutes: z.number().int().min(0).max(600),
  targetExam: z.enum(["WAEC", "JAMB", "NECO"]).optional(),
  targetDate: z.string().datetime().optional(),
  forceExamMode: z.boolean().default(false),
}
```

- Refinements:
  - `targetExam` and `targetDate` must be given together.
  - Both are only accepted for SS3.
  - `forceExamMode` requires `targetDate`.
  - At least one chosen study day must have more than 0 minutes.
- **New endpoints:**
  - `PATCH /api/study-plan`: update availability, subjects, exam fields; force a re-plan.
  - `PUT /api/study-plan/positions`: `[{ subjectId, topicId | null }]` (null deletes the override); force a re-plan.
  - `PATCH /api/study-plan/items/[id]`: see §6.2.
  - Admin CRUD `/api/admin/academic-terms`: follows the existing admin API auth pattern.

## 9. UI

### 9.1 Admin

- **Academic terms page:** list, create and edit terms, with overlap and date-order validation shown inline.
- **Admin dashboard warning:** "No academic term configured for today / the next 30 days".

### 9.2 Setup form (`study-plan-view.tsx`, split into smaller components)

- **Class level and track:** read-only, from the profile, with a link to settings.
- **Subjects:** chips, as today.
- **Study days:** 7 day chips.
- **Time:** weekday and weekend minute inputs. Suggested defaults by class: SS1 30/60, SS2 45/90, SS3 60/120.
- **SS3 only:** an optional "Preparing for an exam?" section with exam, date and an "Exam mode now" switch.
- **"Where is your class?":** collapsed by default. One topic select per chosen subject, set to the calendar's guess. It lists topics at or below the student's class level.

### 9.3 Plan page

- **Header:**
  - `"{classLevel} · {term} Term · Week {n} of {total}"`
  - holidays: `"Holiday — revising {term} Term"`
  - BLENDED/EXAM adds `"· {exam} in {days} days"`
- **Today card:** today's items with an "Open" link to the lesson/quiz/mock, plus tick, skip and undo buttons. Moved items show "Moved from {weekday}".
- **This week and next week:** in detail, with the catch-up slot labelled.
- **Rest of term:** the collapsed outline.
- **Overload banner:** when `overload` is present.
- **Runway badge:** kept for runway days.
- **Page description:** "A realistic weekly schedule that keeps you in step with your class — and gets you exam-ready when it's time."
- **Empty state copy:** no longer mentions an exam date.

### 9.4 Dashboard

- The study plan link shows "Today: {done} of {total} done" when a plan exists.

## 10. Error handling

- **No curriculum for the class or term in a subject:** that subject contributes only revision and gap-fill, and the page shows "No topics for this term yet in {subject}".
- **Unconfigured terms:** use the built-in calendar (§4.1) and log a warning once per day.
- **Zero available minutes in the window:** no items; the page asks the student to add study time.
- **Planner exception during a re-plan:** roll back the transaction, keep the existing items, log it, and show the existing plan.
- **Auto-completion errors:** swallowed and logged (§6.1).

## 11. Testing

**Unit tests (pure planner):**
- `resolveTermContext`: in term, week boundaries, holiday, unconfigured.
- `selectTermTopics`: SS1 mid-term; position ahead of and behind the calendar; prerequisite gap from an earlier class; never above class level; gap-fill capped at 20%.
- `examShare`: at 150, 90, 42 and 10 days (runway).
- `buildSlots`: short remainders, non-study days, catch-up slot placement, never over budget.
- `layoutWindow`: practice after its lesson, revision at +1/+3/+7/+14 moved to study days, pretest skip, subject caps, overload output.
- `projectOutline`: ends at term end or exam date.

**Integration tests (database):**
- Auto-completion matching and earliest-first order; no match for future items.
- Re-plan keeps COMPLETED/SKIPPED/MISSED and marks past PENDING as MISSED.
- Two re-plans at once produce a single set of items.
- Migration of `dailyStudyHours` plans.
- API validation refinements (SS3-only exam fields).

**Manual:**
- Run the app and create a plan as an SS1 student mid-term.
- Tick an item, and complete a lesson so the plan marks its item done automatically.
- Change "where is your class".
- Create an SS3 plan with an exam date about 60 days out and check the blend.

## 12. Rollout

1. Apply the migration (SQL Editor) and check the catalog.
2. Admin enters term dates for 2026/2027.
3. Existing plans migrate automatically. Their first page load after deploy triggers a re-plan into the new format. Existing SS3 plans keep their exam date and get BLENDED mode.
