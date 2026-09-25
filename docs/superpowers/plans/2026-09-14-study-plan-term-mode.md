# Study Plan Term Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the exam-only study plan into a term-following, realistic, progressive plan for SS1–SS3, with SS3 blending into exam prep, automatic and manual completion, and rolling re-planning.

**Architecture:** A pure, deterministic planner in `src/engines/planner/` (day-key math → term context → topic selection → slots → window layout → outline) is driven by a DB layer in `src/lib/study-plan.ts` that re-plans a rolling 14-day window under a row lock. Completion is matched by a pure function and wired into the assessment-submit and flashcard-review routes. An admin page manages the term calendar.

**Tech Stack:** Next.js 16.2 (App Router, route handlers with `params: Promise<…>`), React 19, Prisma 6 on Supabase Postgres, Zod 4, Tailwind 4, `node:test` + `tsx` for tests.

**Spec:** `docs/superpowers/specs/2026-09-14-study-plan-term-mode-design.md`

## Global Constraints

- AGENTS.md: this Next.js version differs from training data. Before writing a route handler or page, read the matching guide in `node_modules/next/dist/docs/` and copy the patterns of existing files (e.g. `src/app/admin/api/materials/[id]/route.ts`).
- Planner code under `src/engines/planner/` is pure: no `db` import, no `Date.now()`, no `new Date()` without an argument. It works in `DayKey` strings (`YYYY-MM-DD`, Africa/Lagos civil day).
- Engine files import other source files by **relative** path (as `plan.ts` does), so `tsx` tests resolve them without path aliases.
- Tests are `node:test` files at `scripts/test-*.mts`. Every new test file must be added to the `"test"` script in `package.json`. Run a single file with `node --import tsx --test scripts/<file>.mts`.
- The repo has no database test harness. The spec's "integration" behaviours are covered by testing the extracted pure functions (`partitionForReplan`, `pickItemToComplete`, `manualStatusChange`) plus the manual checks in Task 17.
- Constants from the spec, verbatim: `TARGET = 70`, `GATE = 60`, `PRETEST_PASS = 80`, revision offsets `[1, 3, 7, 14]`, window 14 days, session 30 min, short session 15–29 min, gap-fill cap 20%, revision share 20%, subject cap 2 weekday / 3 weekend, exam share 0.10 (>120 days) → 0.50 (≤42 days), runway = last 20% of plan clamped 14–21 days, mock count 2 capped 180 min, default minutes SS1 30/60, SS2 45/90, SS3 60/120, carry-over look-back 14 days, manual-change look-back 7 days.
- Migrations cannot run through `prisma migrate` from this machine. Write SQL by hand, keep it LF-only, apply it **one statement at a time** in the Supabase SQL Editor, record the `_prisma_migrations` row, and verify through the catalog (never trust the editor's success message).
- Stop the dev server before `npx prisma generate` (EPERM on the query engine DLL otherwise; a stale client shows up as bogus `tsc` errors).
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ
  ```
- Work on branch `feat/study-plan-term-mode`.

### Deliberate refinements of the spec (decided while planning)

1. **`generatePlan` / `roundRobinPlan` are no longer called.** EXAM mode is the new layout with exam share 1.0 plus the shared runway logic, so availability and study days apply in every mode. The prerequisite gate is trivially satisfied when a graph has no edges, so no separate fallback is needed. `plan.ts` stays (its constants and tests are reused); deleting it is a follow-up.
2. **Outline and overload are persisted** on `StudyPlan` (`outline Json?`, `overload Json?`) at re-plan time instead of recomputed on every read, which would repeat the heavy mastery load.
3. **"Where is your class?" lives on the plan page**, not in the first-time setup form: its options depend on the plan's subjects, and its calendar guess is only meaningful once a plan exists.
4. **Exam dates travel as `YYYY-MM-DD` strings**, not ISO datetimes, so a date picker value is never shifted across timezones.
5. **Subject choice tie-break order** is: non-preview first → fewest uses this week → furthest behind class → subject id → topic already started → candidate rank. Putting "furthest behind" first would let one subject take every slot, because its `behindBy` does not change within a window.
6. **Gap-fill only follows prerequisites inside the same subject.** Cross-subject prerequisites are ignored for gap-fill.
7. **Practice goes on a later *day* than its topic's last lesson**, not merely a later slot.
8. **Unconfigured terms show a "Term dates are approximate" badge** on the plan page and a banner on the admin overview, instead of logging a warning once per day.

## File Structure

**Create — pure planner (`src/engines/planner/`)**
| File | Responsibility |
|---|---|
| `days.ts` | `DayKey` arithmetic and conversion to/from `@db.Date` values |
| `term-context.ts` | `resolveTermContext`, fallback calendar, term validation, header label, coverage check |
| `mode.ts` | `resolvePlanMode`, `examShare`, `computeRunwayStart`, `DEFAULT_MINUTES`, `planSettingsProblem` |
| `slots.ts` | `buildSlots`, `dayBudget` |
| `topics.ts` | `PlanTopic`, `selectTermTopics`, `selectExamTopics`, `calendarTopicId` |
| `layout.ts` | `layoutWindow`: fills slots, runway, revision pool, overload |
| `outline.ts` | `projectOutline` |
| `term-plan.ts` | `planWindow`: the orchestrator |
| `completion.ts` | `pickItemToComplete`, `signalForAssessment`, `manualStatusChange` |
| `replan.ts` | `isReplanStale`, `partitionForReplan` |

**Create — DB, routes, UI**
| File | Responsibility |
|---|---|
| `prisma/migrations/20260914000000_study_plan_term_mode/migration.sql` | schema change |
| `src/lib/academic-terms.ts` | term calendar reads/writes |
| `src/lib/study-plan-completion.ts` | auto-completion hooks (DB side) |
| `src/lib/study-plan-display.ts` | pure UI helpers: item links, window grouping, labels |
| `src/app/admin/api/academic-terms/route.ts`, `…/[id]/route.ts` | admin term API |
| `src/app/admin/(console)/terms/page.tsx`, `src/components/admin/academic-term-manager.tsx` | admin term page |
| `src/app/api/study-plan/positions/route.ts`, `src/app/api/study-plan/items/[id]/route.ts` | student APIs |
| `src/components/study-plan/plan-setup-form.tsx`, `plan-item-row.tsx`, `plan-schedule.tsx`, `class-position-panel.tsx` | plan UI pieces |
| `scripts/test-study-plan-*.mts` | tests |

**Modify:** `prisma/schema.prisma`, `src/lib/validators.ts`, `src/lib/study-plan.ts` (rewrite), `src/app/api/study-plan/route.ts`, `src/app/api/assessments/submit/route.ts`, `src/app/api/flashcards/review/route.ts`, `src/components/study-plan/study-plan-view.tsx` (rewrite), `src/app/(dashboard)/study-plan/page.tsx`, `src/lib/dashboard.ts`, `src/app/(dashboard)/dashboard/page.tsx`, `src/lib/admin-nav.ts`, `src/app/admin/(console)/page.tsx`, `package.json`.

---

### Task 1: Schema and migration

**Files:**
- Modify: `prisma/schema.prisma` (enums near line 132; `Subject` ~329; `Topic` ~397; `StudyPlan` 743–759; `StudyPlanItem` 761–777)
- Create: `prisma/migrations/20260914000000_study_plan_term_mode/migration.sql`

**Interfaces:**
- Produces: Prisma models `AcademicTerm`, `StudyPlanPosition`; enum `PlanCompletionSource`; `PlanItemStatus.MISSED`; the new `StudyPlan` / `StudyPlanItem` fields below. Later tasks use these exact names.

- [ ] **Step 1: Edit the enums**

In `prisma/schema.prisma`, replace the `PlanItemStatus` enum and add the new enum right after it:

```prisma
enum PlanItemStatus {
  PENDING
  COMPLETED
  SKIPPED
  MISSED
}

enum PlanCompletionSource {
  AUTO
  MANUAL
}
```

- [ ] **Step 2: Replace `StudyPlan` and `StudyPlanItem`, add the two new models**

```prisma
model StudyPlan {
  id              String    @id @default(cuid())
  studentId       String
  student         User      @relation(fields: [studentId], references: [id], onDelete: Cascade)
  targetExam      ExamType?
  targetDate      DateTime?
  subjectIds      Json // String[] of subject IDs
  forceExamMode   Boolean   @default(false)
  /// ISO weekdays, 1 = Monday … 7 = Sunday.
  studyDays       Int[]     @default([1, 2, 3, 4, 5, 6, 7])
  weekdayMinutes  Int       @default(60)
  weekendMinutes  Int       @default(60)
  plannedThrough  DateTime? @db.Date
  lastReplannedAt DateTime?
  /// OutlineWeek[] written at re-plan time.
  outline         Json?
  /// Overload | null written at re-plan time.
  overload        Json?
  isActive        Boolean   @default(true)

  items     StudyPlanItem[]
  positions StudyPlanPosition[]

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@index([studentId, isActive])
}

model StudyPlanItem {
  id               String                @id @default(cuid())
  studyPlanId      String
  studyPlan        StudyPlan             @relation(fields: [studyPlanId], references: [id], onDelete: Cascade)
  scheduledDate    DateTime              @db.Date
  subjectId        String
  subject          Subject               @relation(fields: [subjectId], references: [id])
  topicId          String?
  topic            Topic?                @relation(fields: [topicId], references: [id])
  activityType     PlanItemActivity
  durationMinutes  Int
  status           PlanItemStatus        @default(PENDING)
  notes            String?
  completedAt      DateTime?
  completionSource PlanCompletionSource?
  carriedFromDate  DateTime?             @db.Date

  @@index([studyPlanId, scheduledDate])
  @@index([studyPlanId, status])
  @@index([studyPlanId, scheduledDate, status])
}

/// "My class is on topic X" — one row per subject overrides the calendar guess.
model StudyPlanPosition {
  id          String    @id @default(cuid())
  studyPlanId String
  studyPlan   StudyPlan @relation(fields: [studyPlanId], references: [id], onDelete: Cascade)
  subjectId   String
  subject     Subject   @relation(fields: [subjectId], references: [id], onDelete: Cascade)
  topicId     String
  topic       Topic     @relation(fields: [topicId], references: [id], onDelete: Cascade)
  updatedAt   DateTime  @updatedAt

  @@unique([studyPlanId, subjectId])
}

/// The app-wide school calendar, managed by admins.
model AcademicTerm {
  id        String   @id @default(cuid())
  session   String
  term      Term
  startsOn  DateTime @db.Date
  endsOn    DateTime @db.Date
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@unique([session, term])
}
```

Add the back-relations: in `model Subject` add `studyPlanPositions StudyPlanPosition[]`, and in `model Topic` add `studyPlanPositions StudyPlanPosition[]`.

- [ ] **Step 3: Validate the schema**

Run: `npx prisma validate`
Expected: `The schema at prisma\schema.prisma is valid 🚀`

- [ ] **Step 4: Write the migration SQL**

Create `prisma/migrations/20260914000000_study_plan_term_mode/migration.sql`. Each statement stands alone so it can be pasted one at a time:

```sql
-- AlterEnum
ALTER TYPE "PlanItemStatus" ADD VALUE 'MISSED';

-- CreateEnum
CREATE TYPE "PlanCompletionSource" AS ENUM ('AUTO', 'MANUAL');

-- CreateTable
CREATE TABLE "AcademicTerm" (
    "id" TEXT NOT NULL,
    "session" TEXT NOT NULL,
    "term" "Term" NOT NULL,
    "startsOn" DATE NOT NULL,
    "endsOn" DATE NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "AcademicTerm_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "AcademicTerm_session_term_key" ON "AcademicTerm"("session", "term");

-- CreateTable
CREATE TABLE "StudyPlanPosition" (
    "id" TEXT NOT NULL,
    "studyPlanId" TEXT NOT NULL,
    "subjectId" TEXT NOT NULL,
    "topicId" TEXT NOT NULL,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "StudyPlanPosition_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "StudyPlanPosition_studyPlanId_subjectId_key" ON "StudyPlanPosition"("studyPlanId", "subjectId");

-- AddForeignKey
ALTER TABLE "StudyPlanPosition" ADD CONSTRAINT "StudyPlanPosition_studyPlanId_fkey" FOREIGN KEY ("studyPlanId") REFERENCES "StudyPlan"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "StudyPlanPosition" ADD CONSTRAINT "StudyPlanPosition_subjectId_fkey" FOREIGN KEY ("subjectId") REFERENCES "Subject"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "StudyPlanPosition" ADD CONSTRAINT "StudyPlanPosition_topicId_fkey" FOREIGN KEY ("topicId") REFERENCES "Topic"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AlterTable
ALTER TABLE "StudyPlan" ALTER COLUMN "targetExam" DROP NOT NULL;

-- AlterTable
ALTER TABLE "StudyPlan" ALTER COLUMN "targetDate" DROP NOT NULL;

-- AlterTable
ALTER TABLE "StudyPlan" ADD COLUMN "forceExamMode" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "studyDays" INTEGER[] DEFAULT ARRAY[1, 2, 3, 4, 5, 6, 7]::INTEGER[],
ADD COLUMN "weekdayMinutes" INTEGER NOT NULL DEFAULT 60,
ADD COLUMN "weekendMinutes" INTEGER NOT NULL DEFAULT 60,
ADD COLUMN "plannedThrough" DATE,
ADD COLUMN "lastReplannedAt" TIMESTAMP(3),
ADD COLUMN "outline" JSONB,
ADD COLUMN "overload" JSONB;

-- Data migration: carry the old daily hours over as both budgets.
UPDATE "StudyPlan" SET "weekdayMinutes" = LEAST(480, ROUND("dailyStudyHours" * 60)::INTEGER), "weekendMinutes" = LEAST(600, ROUND("dailyStudyHours" * 60)::INTEGER);

-- AlterTable
ALTER TABLE "StudyPlan" DROP COLUMN "dailyStudyHours";

-- AlterTable
ALTER TABLE "StudyPlanItem" ADD COLUMN "completedAt" TIMESTAMP(3),
ADD COLUMN "completionSource" "PlanCompletionSource",
ADD COLUMN "carriedFromDate" DATE;

-- CreateIndex
CREATE INDEX "StudyPlanItem_studyPlanId_scheduledDate_status_idx" ON "StudyPlanItem"("studyPlanId", "scheduledDate", "status");
```

Note: Prisma maps `Int[]` with a default to a nullable-array column, which is why `studyDays` has no `NOT NULL`, matching what `prisma migrate diff` emits for this schema.

- [ ] **Step 5: Check the SQL matches the schema and is LF-only**

Run: `npx prisma migrate diff --from-migrations prisma/migrations --to-schema-datamodel prisma/schema.prisma --script --shadow-database-url "$SHADOW_DATABASE_URL"` **only if** a local Postgres shadow database is available. Otherwise skip, and compare the SQL to the schema by eye, statement by statement.

Run: `tr -cd '\r' < prisma/migrations/20260914000000_study_plan_term_mode/migration.sql | wc -c`
Expected: `0`

- [ ] **Step 6: Regenerate the client and type-check**

Stop the dev server if it is running. Then:
Run: `npx prisma generate`
Expected: `Generated Prisma Client`

Run: `npx tsc --noEmit`
Expected: errors **only** in `src/lib/study-plan.ts`, `src/components/study-plan/study-plan-view.tsx` and `src/app/api/study-plan/route.ts` (they still reference `dailyStudyHours` and a required `targetDate`). Those files are rewritten in Tasks 12, 13 and 15. Any other error means the schema edit is wrong.

- [ ] **Step 7: Commit (do not apply to Supabase yet)**

The migration is applied to the live database in Task 17, together with the deploy, so production never runs old code against the new schema.

```bash
git add prisma/schema.prisma prisma/migrations/20260914000000_study_plan_term_mode/migration.sql
git commit -m "Add schema for study plan term mode

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 2: Day-key arithmetic

**Files:**
- Create: `src/engines/planner/days.ts`
- Test: `scripts/test-study-plan-days.mts`
- Modify: `package.json` (`test` script)

**Interfaces:**
- Produces: `type DayKey = string`; `addDays(key, n): DayKey`; `daysBetween(from, to): number` (to − from); `isoWeekday(key): number` (1 = Mon … 7 = Sun); `isWeekend(key): boolean`; `mondayOf(key): DayKey`; `dayKeyToDate(key): Date` (UTC midnight); `dateToDayKey(date): DayKey`.

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-days.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  addDays,
  dateToDayKey,
  dayKeyToDate,
  daysBetween,
  isoWeekday,
  isWeekend,
  mondayOf,
} from "../src/engines/planner/days";

test("addDays crosses month and year boundaries", () => {
  assert.equal(addDays("2026-09-30", 1), "2026-10-01");
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
});

test("daysBetween is signed", () => {
  assert.equal(daysBetween("2026-09-14", "2026-09-28"), 14);
  assert.equal(daysBetween("2026-09-28", "2026-09-14"), -14);
});

test("isoWeekday numbers Monday 1 and Sunday 7", () => {
  assert.equal(isoWeekday("2026-09-14"), 1);
  assert.equal(isoWeekday("2026-09-19"), 6);
  assert.equal(isoWeekday("2026-09-20"), 7);
  assert.equal(isWeekend("2026-09-19"), true);
  assert.equal(isWeekend("2026-09-18"), false);
});

test("mondayOf returns the same week's Monday", () => {
  assert.equal(mondayOf("2026-09-20"), "2026-09-14");
  assert.equal(mondayOf("2026-09-14"), "2026-09-14");
});

test("day keys round-trip through @db.Date values", () => {
  const date = dayKeyToDate("2026-09-14");
  assert.equal(date.toISOString(), "2026-09-14T00:00:00.000Z");
  assert.equal(dateToDayKey(date), "2026-09-14");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-days.mts`
Expected: FAIL with `Cannot find module` for `days`.

- [ ] **Step 3: Implement**

```ts
// src/engines/planner/days.ts

// Calendar-day arithmetic for the planner. Days are `YYYY-MM-DD` keys rather
// than Dates: the plan is laid out in the student's Lagos civil day, and a Date
// would silently re-bucket across the server's timezone.

export type DayKey = string;

const DAY_MS = 86_400_000;

function toUtcMs(key: DayKey): number {
  const [y, m, d] = key.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function fromUtcMs(ms: number): DayKey {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(key: DayKey, days: number): DayKey {
  return fromUtcMs(toUtcMs(key) + days * DAY_MS);
}

/** `to - from` in whole days. */
export function daysBetween(from: DayKey, to: DayKey): number {
  return Math.round((toUtcMs(to) - toUtcMs(from)) / DAY_MS);
}

/** ISO weekday: 1 = Monday … 7 = Sunday. */
export function isoWeekday(key: DayKey): number {
  const day = new Date(toUtcMs(key)).getUTCDay();
  return day === 0 ? 7 : day;
}

export function isWeekend(key: DayKey): boolean {
  return isoWeekday(key) >= 6;
}

export function mondayOf(key: DayKey): DayKey {
  return addDays(key, 1 - isoWeekday(key));
}

/** `@db.Date` columns are read and written by Prisma as UTC midnight. */
export function dayKeyToDate(key: DayKey): Date {
  return new Date(toUtcMs(key));
}

export function dateToDayKey(date: Date): DayKey {
  return date.toISOString().slice(0, 10);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-days.mts`
Expected: 5 passing, 0 failing.

- [ ] **Step 5: Register the test and commit**

In `package.json`, append ` scripts/test-study-plan-days.mts` to the end of the `"test"` script string.

```bash
git add src/engines/planner/days.ts scripts/test-study-plan-days.mts package.json
git commit -m "Add day-key arithmetic for the planner

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 3: Term context

**Files:**
- Create: `src/engines/planner/term-context.ts`
- Test: `scripts/test-study-plan-term-context.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `DayKey`, `addDays`, `daysBetween`, `mondayOf` (Task 2); `Term`, `TERM_LABELS` from `src/lib/curriculum-scope.ts`.
- Produces:
  - `type TermRange = { session: string; term: Term; startsOn: DayKey; endsOn: DayKey }`
  - `type TermSource = "configured" | "fallback"`
  - `type TermContext = { kind: "in_term"; source; current: TermRange; weekOfTerm: number; totalWeeks: number; weeksLeft: number } | { kind: "holiday"; source; previous: TermRange | null; next: TermRange | null }`
  - `resolveTermContext(today: DayKey, configured: readonly TermRange[]): TermContext`
  - `fallbackTerms(today: DayKey): TermRange[]`
  - `validateTermRanges(ranges: readonly TermRange[]): string[]`
  - `hasTermCoverage(configured: readonly TermRange[], today: DayKey, aheadDays?: number): boolean`
  - `termHeaderLabel(ctx: TermContext): string`

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-term-context.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fallbackTerms,
  hasTermCoverage,
  resolveTermContext,
  termHeaderLabel,
  validateTermRanges,
  type TermRange,
} from "../src/engines/planner/term-context";

const FIRST: TermRange = { session: "2026/2027", term: "FIRST", startsOn: "2026-09-08", endsOn: "2026-12-15" };
const SECOND: TermRange = { session: "2026/2027", term: "SECOND", startsOn: "2027-01-06", endsOn: "2027-04-10" };

test("in term: Monday-based week numbering", () => {
  const ctx = resolveTermContext("2026-09-14", [FIRST, SECOND]);
  assert.equal(ctx.kind, "in_term");
  if (ctx.kind !== "in_term") return;
  assert.equal(ctx.source, "configured");
  assert.equal(ctx.current.term, "FIRST");
  // Term starts Tue 8 Sep, so week 1 is the week of Mon 7 Sep.
  assert.equal(ctx.weekOfTerm, 2);
  assert.equal(ctx.totalWeeks, 15);
  assert.equal(ctx.weeksLeft, 13);
});

test("the first and last day of a term are in term", () => {
  assert.equal(resolveTermContext("2026-09-08", [FIRST]).kind, "in_term");
  assert.equal(resolveTermContext("2026-12-15", [FIRST]).kind, "in_term");
});

test("holiday between terms names both sides", () => {
  const ctx = resolveTermContext("2026-12-20", [FIRST, SECOND]);
  assert.equal(ctx.kind, "holiday");
  if (ctx.kind !== "holiday") return;
  assert.equal(ctx.previous?.term, "FIRST");
  assert.equal(ctx.next?.term, "SECOND");
});

test("no configured terms falls back to the built-in calendar", () => {
  const ctx = resolveTermContext("2026-09-14", []);
  assert.equal(ctx.source, "fallback");
  assert.equal(ctx.kind, "in_term");
});

test("a configured calendar far from today is ignored", () => {
  const old: TermRange = { session: "2024/2025", term: "FIRST", startsOn: "2024-09-09", endsOn: "2024-12-13" };
  assert.equal(resolveTermContext("2026-09-14", [old]).source, "fallback");
});

test("fallback covers every day of the year", () => {
  for (const day of ["2026-01-02", "2026-04-20", "2026-08-01", "2026-09-14", "2026-12-31"]) {
    const ctx = resolveTermContext(day, fallbackTerms(day));
    if (ctx.kind === "holiday") {
      assert.ok(ctx.previous || ctx.next, `no neighbour term for ${day}`);
    }
  }
});

test("validateTermRanges flags bad dates, overlaps and duplicates", () => {
  assert.deepEqual(validateTermRanges([FIRST, SECOND]), []);
  assert.equal(
    validateTermRanges([{ ...FIRST, endsOn: "2026-09-01" }]).length,
    1,
  );
  assert.equal(
    validateTermRanges([FIRST, { ...SECOND, startsOn: "2026-12-01" }]).length,
    1,
  );
  assert.equal(validateTermRanges([FIRST, { ...FIRST }]).length > 0, true);
  assert.equal(validateTermRanges([{ ...FIRST, session: "2026/2028" }]).length, 1);
});

test("hasTermCoverage looks at today and the next 30 days", () => {
  assert.equal(hasTermCoverage([FIRST], "2026-09-14"), true);
  assert.equal(hasTermCoverage([FIRST], "2026-12-20"), false);
  assert.equal(hasTermCoverage([SECOND], "2026-12-20"), true);
});

test("termHeaderLabel", () => {
  assert.equal(termHeaderLabel(resolveTermContext("2026-09-14", [FIRST])), "1st term · Week 2 of 15");
  assert.equal(
    termHeaderLabel(resolveTermContext("2026-12-20", [FIRST, SECOND])),
    "Holiday — revising 1st term",
  );
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-term-context.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/engines/planner/term-context.ts
import { TERM_LABELS, type Term } from "../../lib/curriculum-scope";
import { addDays, daysBetween, mondayOf, type DayKey } from "./days";

// Where today sits in the school calendar. Admins configure the terms; when they
// have not (or the configured calendar is stale), an approximate national
// calendar keeps plans working instead of failing.

export type TermRange = {
  session: string;
  term: Term;
  startsOn: DayKey;
  endsOn: DayKey;
};

export type TermSource = "configured" | "fallback";

export type TermContext =
  | {
      kind: "in_term";
      source: TermSource;
      current: TermRange;
      weekOfTerm: number;
      totalWeeks: number;
      weeksLeft: number;
    }
  | {
      kind: "holiday";
      source: TermSource;
      previous: TermRange | null;
      next: TermRange | null;
    };

/** A configured term this close to today means the calendar is being kept up. */
const COVERAGE_DAYS = 60;

/** The approximate national calendar for the sessions either side of today. */
export function fallbackTerms(today: DayKey): TermRange[] {
  const year = Number(today.slice(0, 4));
  const ranges: TermRange[] = [];
  for (const y of [year - 1, year]) {
    const session = `${y}/${y + 1}`;
    ranges.push(
      { session, term: "FIRST", startsOn: `${y}-09-08`, endsOn: `${y}-12-15` },
      { session, term: "SECOND", startsOn: `${y + 1}-01-06`, endsOn: `${y + 1}-04-10` },
      { session, term: "THIRD", startsOn: `${y + 1}-04-27`, endsOn: `${y + 1}-07-24` },
    );
  }
  return ranges;
}

function near(term: TermRange, today: DayKey, days: number): boolean {
  return (
    daysBetween(today, term.startsOn) <= days &&
    daysBetween(term.endsOn, today) <= days
  );
}

export function resolveTermContext(
  today: DayKey,
  configured: readonly TermRange[],
): TermContext {
  const covered = configured.some((term) => near(term, today, COVERAGE_DAYS));
  const source: TermSource = covered ? "configured" : "fallback";
  const terms = (covered ? [...configured] : fallbackTerms(today)).sort((a, b) =>
    a.startsOn.localeCompare(b.startsOn),
  );

  const current = terms.find((t) => t.startsOn <= today && today <= t.endsOn);
  if (current) {
    const firstMonday = mondayOf(current.startsOn);
    const totalWeeks = Math.floor(daysBetween(firstMonday, current.endsOn) / 7) + 1;
    const weekOfTerm = Math.floor(daysBetween(firstMonday, today) / 7) + 1;
    return {
      kind: "in_term",
      source,
      current,
      weekOfTerm,
      totalWeeks,
      weeksLeft: totalWeeks - weekOfTerm,
    };
  }

  const previous = [...terms].reverse().find((t) => t.endsOn < today) ?? null;
  const next = terms.find((t) => t.startsOn > today) ?? null;
  return { kind: "holiday", source, previous, next };
}

function termName(range: TermRange): string {
  return `${range.session} ${TERM_LABELS[range.term]}`;
}

/** Human-readable problems with a set of terms; empty when they are consistent. */
export function validateTermRanges(ranges: readonly TermRange[]): string[] {
  const errors: string[] = [];
  const seen = new Set<string>();

  for (const range of ranges) {
    const match = /^(\d{4})\/(\d{4})$/.exec(range.session);
    if (!match || Number(match[2]) !== Number(match[1]) + 1) {
      errors.push(`${range.session}: a session looks like 2026/2027.`);
    }
    if (range.endsOn <= range.startsOn) {
      errors.push(`${termName(range)}: the end date must be after the start date.`);
    }
    const key = `${range.session}:${range.term}`;
    if (seen.has(key)) errors.push(`${termName(range)} is already set.`);
    seen.add(key);
  }

  const sorted = [...ranges].sort((a, b) => a.startsOn.localeCompare(b.startsOn));
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].startsOn <= sorted[i - 1].endsOn) {
      errors.push(`${termName(sorted[i])} overlaps ${termName(sorted[i - 1])}.`);
    }
  }
  return errors;
}

/** Whether a configured term covers any day from today through `aheadDays` from now. */
export function hasTermCoverage(
  configured: readonly TermRange[],
  today: DayKey,
  aheadDays = 30,
): boolean {
  const until = addDays(today, aheadDays);
  return configured.some((t) => t.startsOn <= until && t.endsOn >= today);
}

export function termHeaderLabel(ctx: TermContext): string {
  if (ctx.kind === "in_term") {
    return `${TERM_LABELS[ctx.current.term]} · Week ${ctx.weekOfTerm} of ${ctx.totalWeeks}`;
  }
  return ctx.previous
    ? `Holiday — revising ${TERM_LABELS[ctx.previous.term]}`
    : "Holiday";
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-term-context.mts`
Expected: 9 passing.

- [ ] **Step 5: Register and commit**

Append ` scripts/test-study-plan-term-context.mts` to the `"test"` script.

```bash
git add src/engines/planner/term-context.ts scripts/test-study-plan-term-context.mts package.json
git commit -m "Resolve the school term for a day

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 4: Plan mode, exam share, runway, settings rules

**Files:**
- Create: `src/engines/planner/mode.ts`
- Test: `scripts/test-study-plan-mode.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `DayKey`, `addDays`, `daysBetween` (Task 2); `RUNWAY_FRACTION`, `RUNWAY_MIN_DAYS`, `RUNWAY_MAX_DAYS` from `./plan`; `ClassLevel` from `../../lib/curriculum-scope`.
- Produces:
  - `type PlanMode = "TERM" | "BLENDED" | "EXAM"`
  - `resolvePlanMode(p: { classLevel: ClassLevel | null; targetDate: DayKey | null; forceExamMode: boolean }): PlanMode`
  - `examShare(daysToExam: number): number`
  - `computeRunwayStart(planStart: DayKey, targetDate: DayKey): DayKey`
  - `DEFAULT_MINUTES: Record<ClassLevel, { weekdayMinutes: number; weekendMinutes: number }>`
  - `type PlanSettingsCheck = { classLevel: ClassLevel | null; targetDate: DayKey | null; forceExamMode: boolean; studyDays: readonly number[]; weekdayMinutes: number; weekendMinutes: number; today: DayKey }`
  - `planSettingsProblem(input: PlanSettingsCheck): string | null`

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-mode.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  computeRunwayStart,
  examShare,
  planSettingsProblem,
  resolvePlanMode,
} from "../src/engines/planner/mode";

test("resolvePlanMode", () => {
  assert.equal(resolvePlanMode({ classLevel: "SS1", targetDate: null, forceExamMode: false }), "TERM");
  // Exam fields are ignored below SS3 (a student whose class changed).
  assert.equal(resolvePlanMode({ classLevel: "SS2", targetDate: "2027-05-01", forceExamMode: true }), "TERM");
  assert.equal(resolvePlanMode({ classLevel: "SS3", targetDate: null, forceExamMode: false }), "TERM");
  assert.equal(resolvePlanMode({ classLevel: "SS3", targetDate: "2027-05-01", forceExamMode: false }), "BLENDED");
  assert.equal(resolvePlanMode({ classLevel: "SS3", targetDate: "2027-05-01", forceExamMode: true }), "EXAM");
});

test("examShare ramps from 0.10 to 0.50", () => {
  assert.equal(examShare(150), 0.1);
  assert.equal(examShare(120), 0.1);
  assert.ok(Math.abs(examShare(90) - 0.2538) < 0.001);
  assert.equal(examShare(42), 0.5);
  assert.equal(examShare(10), 0.5);
});

test("computeRunwayStart clamps the runway to 14..21 days", () => {
  // 61 days → 20% is 12, clamped up to 14.
  assert.equal(computeRunwayStart("2026-09-14", "2026-11-13"), "2026-10-31");
  // 200 days → 20% is 40, clamped down to 21.
  assert.equal(computeRunwayStart("2026-09-14", "2027-04-01"), "2027-03-12");
  // A plan shorter than the minimum is all runway.
  assert.equal(computeRunwayStart("2026-09-14", "2026-09-20"), "2026-09-14");
});

const base = {
  classLevel: "SS3" as const,
  targetDate: null,
  forceExamMode: false,
  studyDays: [1, 2, 3],
  weekdayMinutes: 60,
  weekendMinutes: 0,
  today: "2026-09-14",
};

test("planSettingsProblem accepts a sensible plan", () => {
  assert.equal(planSettingsProblem(base), null);
  assert.equal(planSettingsProblem({ ...base, targetDate: "2027-05-01", forceExamMode: true }), null);
});

test("planSettingsProblem rejects bad combinations", () => {
  assert.match(planSettingsProblem({ ...base, classLevel: "SS2", targetDate: "2027-05-01" }) ?? "", /SS3/);
  assert.match(planSettingsProblem({ ...base, forceExamMode: true }) ?? "", /exam date/);
  assert.match(planSettingsProblem({ ...base, targetDate: "2026-09-14" }) ?? "", /future/);
  assert.match(planSettingsProblem({ ...base, studyDays: [6, 7] }) ?? "", /study day/);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-mode.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/engines/planner/mode.ts
import type { ClassLevel } from "../../lib/curriculum-scope";
import { addDays, daysBetween, type DayKey } from "./days";
import { RUNWAY_FRACTION, RUNWAY_MAX_DAYS, RUNWAY_MIN_DAYS } from "./plan";

export type PlanMode = "TERM" | "BLENDED" | "EXAM";

/** Mode is derived, never stored, so a class change can't leave a stale mode behind. */
export function resolvePlanMode(p: {
  classLevel: ClassLevel | null;
  targetDate: DayKey | null;
  forceExamMode: boolean;
}): PlanMode {
  if (p.classLevel !== "SS3" || !p.targetDate) return "TERM";
  return p.forceExamMode ? "EXAM" : "BLENDED";
}

export const EXAM_SHARE_MIN = 0.1;
export const EXAM_SHARE_MAX = 0.5;
const RAMP_START_DAYS = 120;
const RAMP_END_DAYS = 42;

/** Fraction of a BLENDED plan's study time given to exam preparation. */
export function examShare(daysToExam: number): number {
  if (daysToExam >= RAMP_START_DAYS) return EXAM_SHARE_MIN;
  if (daysToExam <= RAMP_END_DAYS) return EXAM_SHARE_MAX;
  const progress = (RAMP_START_DAYS - daysToExam) / (RAMP_START_DAYS - RAMP_END_DAYS);
  return EXAM_SHARE_MIN + (EXAM_SHARE_MAX - EXAM_SHARE_MIN) * progress;
}

/** First day of the mock/past-questions runway: the last 20% of the plan, clamped 14..21 days. */
export function computeRunwayStart(planStart: DayKey, targetDate: DayKey): DayKey {
  const totalDays = Math.max(1, daysBetween(planStart, targetDate) + 1);
  const runwayDays = Math.min(
    RUNWAY_MAX_DAYS,
    Math.max(RUNWAY_MIN_DAYS, Math.round(totalDays * RUNWAY_FRACTION)),
    totalDays,
  );
  return addDays(targetDate, -(runwayDays - 1));
}

/** Suggested starting budgets; students can change them. */
export const DEFAULT_MINUTES: Record<ClassLevel, { weekdayMinutes: number; weekendMinutes: number }> = {
  SS1: { weekdayMinutes: 30, weekendMinutes: 60 },
  SS2: { weekdayMinutes: 45, weekendMinutes: 90 },
  SS3: { weekdayMinutes: 60, weekendMinutes: 120 },
};

export type PlanSettingsCheck = {
  classLevel: ClassLevel | null;
  targetDate: DayKey | null;
  forceExamMode: boolean;
  studyDays: readonly number[];
  weekdayMinutes: number;
  weekendMinutes: number;
  today: DayKey;
};

/** The rules zod can't express because they need the student's class or today's date. */
export function planSettingsProblem(input: PlanSettingsCheck): string | null {
  if (input.targetDate && input.classLevel !== "SS3") {
    return "Exam dates are for SS3 students. Your plan will follow your school term.";
  }
  if (input.forceExamMode && !input.targetDate) {
    return "Set an exam date before switching on exam mode.";
  }
  if (input.targetDate && input.targetDate <= input.today) {
    return "The exam date must be in the future.";
  }
  const hasTime = input.studyDays.some((day) =>
    (day >= 6 ? input.weekendMinutes : input.weekdayMinutes) > 0,
  );
  if (!hasTime) {
    return "Choose at least one study day that has study time on it.";
  }
  return null;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-mode.mts`
Expected: 5 passing.

- [ ] **Step 5: Register and commit**

Append ` scripts/test-study-plan-mode.mts` to the `"test"` script.

```bash
git add src/engines/planner/mode.ts scripts/test-study-plan-mode.mts package.json
git commit -m "Add plan mode, exam share and runway rules

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 5: Time slots

**Files:**
- Create: `src/engines/planner/slots.ts`
- Test: `scripts/test-study-plan-slots.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `DayKey`, `addDays`, `isoWeekday`, `isWeekend`, `mondayOf` (Task 2).
- Produces:
  - `type Availability = { studyDays: readonly number[]; weekdayMinutes: number; weekendMinutes: number }`
  - `type Slot = { date: DayKey; minutes: number; short: boolean; catchUp: boolean }`
  - `SESSION_MINUTES = 30`, `SHORT_SESSION_MIN = 15`
  - `dayBudget(date: DayKey, availability: Availability): number`
  - `buildSlots(start: DayKey, days: number, availability: Availability): Slot[]` (date-ordered)

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-slots.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSlots, dayBudget } from "../src/engines/planner/slots";

// 2026-09-14 is a Monday.
const availability = { studyDays: [1, 2, 3, 4, 6], weekdayMinutes: 45, weekendMinutes: 120 };

test("dayBudget uses the weekday or weekend figure, zero on rest days", () => {
  assert.equal(dayBudget("2026-09-14", availability), 45);
  assert.equal(dayBudget("2026-09-19", availability), 120);
  assert.equal(dayBudget("2026-09-18", availability), 0); // Friday not chosen
  assert.equal(dayBudget("2026-09-20", availability), 0); // Sunday not chosen
});

test("a 45 minute day is one full session plus one short session", () => {
  const monday = buildSlots("2026-09-14", 1, availability);
  assert.deepEqual(
    monday.map((s) => [s.minutes, s.short]),
    [[30, false], [15, true]],
  );
});

test("remainders under 15 minutes are dropped", () => {
  const slots = buildSlots("2026-09-14", 1, { studyDays: [1], weekdayMinutes: 40, weekendMinutes: 0 });
  assert.deepEqual(slots.map((s) => s.minutes), [30]);
});

test("a day's slots never exceed its budget", () => {
  const slots = buildSlots("2026-09-14", 14, availability);
  const perDay = new Map<string, number>();
  for (const s of slots) perDay.set(s.date, (perDay.get(s.date) ?? 0) + s.minutes);
  for (const [date, minutes] of perDay) {
    assert.ok(minutes <= dayBudget(date, availability), date);
  }
});

test("the catch-up slot is the first full slot of each week's last study day", () => {
  const slots = buildSlots("2026-09-14", 14, availability);
  const catchUps = slots.filter((s) => s.catchUp);
  assert.deepEqual(catchUps.map((s) => s.date), ["2026-09-19", "2026-09-26"]);
  const saturday = slots.filter((s) => s.date === "2026-09-19");
  assert.equal(saturday[0].catchUp, true);
  assert.equal(saturday.slice(1).some((s) => s.catchUp), false);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-slots.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/engines/planner/slots.ts
import { addDays, isoWeekday, isWeekend, mondayOf, type DayKey } from "./days";

export type Availability = {
  /** ISO weekdays, 1 = Monday … 7 = Sunday. */
  studyDays: readonly number[];
  weekdayMinutes: number;
  weekendMinutes: number;
};

export type Slot = {
  date: DayKey;
  minutes: number;
  /** 15–29 minutes: only revision fits. */
  short: boolean;
  /** Reserved for missed work first. */
  catchUp: boolean;
};

export const SESSION_MINUTES = 30;
export const SHORT_SESSION_MIN = 15;

export function dayBudget(date: DayKey, availability: Availability): number {
  if (!availability.studyDays.includes(isoWeekday(date))) return 0;
  return isWeekend(date) ? availability.weekendMinutes : availability.weekdayMinutes;
}

/** Splits each study day's budget into sessions. Never schedules more than the budget. */
export function buildSlots(start: DayKey, days: number, availability: Availability): Slot[] {
  const slots: Slot[] = [];
  for (let i = 0; i < days; i++) {
    const date = addDays(start, i);
    const budget = dayBudget(date, availability);
    const full = Math.floor(budget / SESSION_MINUTES);
    for (let s = 0; s < full; s++) {
      slots.push({ date, minutes: SESSION_MINUTES, short: false, catchUp: false });
    }
    const rest = budget - full * SESSION_MINUTES;
    if (rest >= SHORT_SESSION_MIN) {
      slots.push({ date, minutes: rest, short: true, catchUp: false });
    }
  }

  // Slots are date-ordered, so the last write per week is its last study day.
  const lastDayOfWeek = new Map<DayKey, DayKey>();
  for (const slot of slots) {
    if (!slot.short) lastDayOfWeek.set(mondayOf(slot.date), slot.date);
  }
  for (const date of lastDayOfWeek.values()) {
    const first = slots.find((s) => s.date === date && !s.short);
    if (first) first.catchUp = true;
  }
  return slots;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-slots.mts`
Expected: 5 passing.

- [ ] **Step 5: Register and commit**

Append ` scripts/test-study-plan-slots.mts` to the `"test"` script.

```bash
git add src/engines/planner/slots.ts scripts/test-study-plan-slots.mts package.json
git commit -m "Split study availability into session slots

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 6: Topic selection

**Files:**
- Create: `src/engines/planner/topics.ts`
- Test: `scripts/test-study-plan-topics.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `GraphNode`, `KnowledgeGraph`, `buildGraph`, `incomingEdges` (`../learning/graph`); `TopicStateMap` (`../learning/mastery`); `GATE`, `TARGET` (`../learning/availability`); `CLASS_LEVELS`, `ClassLevel`, `Term` (`../../lib/curriculum-scope`); `TermContext` (Task 3); `DayKey` (Task 2).
- Produces:
  - `type PlanTopic = GraphNode & { classLevel: ClassLevel; term: Term }`
  - `type CandidateReason = "CARRY_OVER" | "GAP_FILL" | "CURRENT" | "CATCH_UP" | "PREVIEW" | "EXAM"`
  - `type TopicCandidate = { topic: PlanTopic; reason: CandidateReason; carriedFrom: DayKey | null; unlocks: string | null }`
  - `type CarryOver = { topicId: string; subjectId: string; missedOn: DayKey }`
  - `type SubjectSelection = { subjectId: string; termTopics: PlanTopic[]; classIndex: number; behindBy: number; candidates: TopicCandidate[] }`
  - `type SelectTermTopicsInput = { subjectId; classLevel; termContext; topics: readonly PlanTopic[]; graph; state; pretestPassed: ReadonlySet<string>; positionTopicId: string | null; carryOver: readonly CarryOver[]; carryOverOnly?: boolean }`
  - `atOrBelowClass(topic: { classLevel: ClassLevel }, classLevel: ClassLevel): boolean`
  - `selectTermTopics(input: SelectTermTopicsInput): SubjectSelection`
  - `selectExamTopics(input: { topics: readonly PlanTopic[]; classLevel: ClassLevel; state: TopicStateMap }): TopicCandidate[]`
  - `calendarTopicId(topics: readonly PlanTopic[], classLevel: ClassLevel, ctx: TermContext): string | null`

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-topics.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildGraph, type GraphEdge } from "../src/engines/learning/graph";
import type { TopicState, TopicStateMap } from "../src/engines/learning/mastery";
import type { TermContext } from "../src/engines/planner/term-context";
import {
  calendarTopicId,
  selectExamTopics,
  selectTermTopics,
  type PlanTopic,
  type SelectTermTopicsInput,
} from "../src/engines/planner/topics";

function topic(id: string, overrides: Partial<PlanTopic> = {}): PlanTopic {
  return {
    id,
    subjectId: "maths",
    title: `Topic ${id}`,
    slug: id,
    orderIndex: 0,
    estimatedMinutes: 45,
    waecWeight: 0,
    jambWeight: 0,
    prerequisiteTopicId: null,
    classLevel: "SS1",
    term: "FIRST",
    ...overrides,
  };
}

function prereq(from: string, to: string): GraphEdge {
  return { id: `${from}->${to}`, from, to, kind: "PREREQUISITE", strength: 1, rationale: null };
}

/** Only `mastery` is read by the planner; the rest satisfies the type. */
function states(entries: Record<string, number>): TopicStateMap {
  return new Map(
    Object.entries(entries).map(([id, mastery]) => [id, { topicId: id, mastery } as unknown as TopicState]),
  );
}

function inTerm(term: "FIRST" | "SECOND" | "THIRD", weekOfTerm: number, totalWeeks = 12): TermContext {
  return {
    kind: "in_term",
    source: "configured",
    current: { session: "2026/2027", term, startsOn: "2026-09-07", endsOn: "2026-12-15" },
    weekOfTerm,
    totalWeeks,
    weeksLeft: totalWeeks - weekOfTerm,
  };
}

const ss1First = [1, 2, 3, 4, 5, 6].map((n) => topic(`t${n}`, { orderIndex: n }));
const ss2 = topic("ss2-a", { classLevel: "SS2", orderIndex: 1 });

function input(overrides: Partial<SelectTermTopicsInput> = {}): SelectTermTopicsInput {
  const topics = [...ss1First, ss2];
  return {
    subjectId: "maths",
    classLevel: "SS1",
    termContext: inTerm("FIRST", 6),
    topics,
    graph: buildGraph(topics, []),
    state: states({}),
    pretestPassed: new Set(),
    positionTopicId: null,
    carryOver: [],
    ...overrides,
  };
}

const reasons = (sel: ReturnType<typeof selectTermTopics>) =>
  sel.candidates.map((c) => `${c.reason}:${c.topic.id}`);

test("mid-term SS1: current, catch-up, one preview, never a later class", () => {
  // Week 6 of 12 over 6 topics → index floor(5/12*6) = 2 → t3.
  const sel = selectTermTopics(input());
  assert.equal(sel.classIndex, 2);
  assert.deepEqual(reasons(sel), ["CURRENT:t3", "CATCH_UP:t1", "CATCH_UP:t2", "PREVIEW:t4"]);
  assert.equal(sel.behindBy, 2);
  assert.equal(sel.candidates.some((c) => c.topic.id === "ss2-a"), false);
});

test("mastered topics are left out", () => {
  const sel = selectTermTopics(input({ state: states({ t1: 90, t3: 75 }) }));
  assert.deepEqual(reasons(sel), ["CATCH_UP:t2", "PREVIEW:t4"]);
});

test("a position ahead of the calendar moves the class", () => {
  const sel = selectTermTopics(input({ positionTopicId: "t5" }));
  assert.equal(sel.classIndex, 4);
  assert.equal(sel.candidates[0].reason, "CURRENT");
  assert.equal(sel.candidates[0].topic.id, "t5");
});

test("a position behind the calendar removes catch-up", () => {
  const sel = selectTermTopics(input({ positionTopicId: "t1" }));
  assert.deepEqual(reasons(sel), ["CURRENT:t1", "PREVIEW:t2"]);
});

test("a position above the student's class is ignored", () => {
  const sel = selectTermTopics(input({ positionTopicId: "ss2-a" }));
  assert.equal(sel.classIndex, 2);
});

test("gap-fill pulls weak earlier-class prerequisites, deepest first", () => {
  const g0 = topic("g0", { classLevel: "SS1", term: "SECOND", orderIndex: 1 });
  const g1 = topic("g1", { classLevel: "SS1", term: "THIRD", orderIndex: 1 });
  const strong = topic("strong", { classLevel: "SS1", term: "THIRD", orderIndex: 2 });
  const cur = topic("cur", { classLevel: "SS2", term: "FIRST", orderIndex: 1 });
  const topics = [g0, g1, strong, cur];
  const sel = selectTermTopics(
    input({
      classLevel: "SS2",
      termContext: inTerm("FIRST", 1),
      topics,
      graph: buildGraph(topics, [prereq("g0", "g1"), prereq("g1", "cur"), prereq("strong", "cur")]),
      state: states({ g0: 10, g1: 30, strong: 80 }),
    }),
  );
  assert.deepEqual(reasons(sel), ["GAP_FILL:g0", "GAP_FILL:g1", "CURRENT:cur"]);
  assert.equal(sel.candidates[1].unlocks, "Topic cur");
});

test("holiday after first term: class at the end of first term, second term previewed", () => {
  const second = [1, 2, 3].map((n) => topic(`s${n}`, { term: "SECOND", orderIndex: n }));
  const topics = [...ss1First, ...second];
  const sel = selectTermTopics(
    input({
      topics,
      graph: buildGraph(topics, []),
      state: states({ t1: 90, t2: 90, t3: 90, t4: 90, t5: 90 }),
      termContext: {
        kind: "holiday",
        source: "configured",
        previous: { session: "2026/2027", term: "FIRST", startsOn: "2026-09-07", endsOn: "2026-12-15" },
        next: { session: "2026/2027", term: "SECOND", startsOn: "2027-01-06", endsOn: "2027-04-10" },
      },
    }),
  );
  assert.equal(sel.classIndex, 5);
  assert.deepEqual(reasons(sel), ["CURRENT:t6", "PREVIEW:s1", "PREVIEW:s2"]);
});

test("carry-over comes first and keeps its missed date; mastered carry-over is dropped", () => {
  const sel = selectTermTopics(
    input({
      state: states({ t6: 95 }),
      carryOver: [
        { topicId: "t5", subjectId: "maths", missedOn: "2026-09-10" },
        { topicId: "t6", subjectId: "maths", missedOn: "2026-09-11" },
      ],
    }),
  );
  assert.equal(sel.candidates[0].reason, "CARRY_OVER");
  assert.equal(sel.candidates[0].topic.id, "t5");
  assert.equal(sel.candidates[0].carriedFrom, "2026-09-10");
  assert.equal(sel.candidates.some((c) => c.topic.id === "t6"), false);
});

test("carryOverOnly keeps only carry-over", () => {
  const sel = selectTermTopics(
    input({ carryOverOnly: true, carryOver: [{ topicId: "t5", subjectId: "maths", missedOn: "2026-09-10" }] }),
  );
  assert.deepEqual(reasons(sel), ["CARRY_OVER:t5"]);
});

test("selectExamTopics ranks weak topics by exam weight within the class", () => {
  const a = topic("a", { waecWeight: 1, jambWeight: 1 });
  const b = topic("b", { waecWeight: 3, jambWeight: 2 });
  const c = topic("c", { waecWeight: 3, jambWeight: 2 });
  const done = topic("done", { waecWeight: 9 });
  const later = topic("later", { classLevel: "SS3", waecWeight: 9 });
  const ranked = selectExamTopics({
    topics: [a, b, c, done, later],
    classLevel: "SS2",
    state: states({ b: 40, c: 20, done: 90 }),
  });
  assert.deepEqual(ranked.map((x) => x.topic.id), ["c", "b", "a"]);
  assert.equal(ranked[0].reason, "EXAM");
});

test("calendarTopicId is the calendar guess used by the position picker", () => {
  assert.equal(calendarTopicId([...ss1First, ss2], "SS1", inTerm("FIRST", 6)), "t3");
  assert.equal(calendarTopicId([ss2], "SS1", inTerm("FIRST", 6)), null);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-topics.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/engines/planner/topics.ts
import { incomingEdges, type GraphNode, type KnowledgeGraph } from "../learning/graph";
import type { TopicStateMap } from "../learning/mastery";
import { GATE, TARGET } from "../learning/availability";
import { CLASS_LEVELS, type ClassLevel, type Term } from "../../lib/curriculum-scope";
import type { DayKey } from "./days";
import type { TermContext } from "./term-context";

// Which topics a subject's plan should work on, and why. Term mode follows the
// class through the scheme of work; exam mode ranks weak topics by marks.

export type PlanTopic = GraphNode & { classLevel: ClassLevel; term: Term };

export type CandidateReason =
  | "CARRY_OVER"
  | "GAP_FILL"
  | "CURRENT"
  | "CATCH_UP"
  | "PREVIEW"
  | "EXAM";

export type TopicCandidate = {
  topic: PlanTopic;
  reason: CandidateReason;
  /** The missed session this topic came from, for "moved from …". */
  carriedFrom: DayKey | null;
  /** For gap-fill: the title of the topic this foundation unlocks. */
  unlocks: string | null;
};

export type CarryOver = { topicId: string; subjectId: string; missedOn: DayKey };

export type SubjectSelection = {
  subjectId: string;
  /** The class's current term topics in teaching order; the outline paces through these. */
  termTopics: PlanTopic[];
  /** Index into termTopics where the class is; -1 when there is none. */
  classIndex: number;
  /** How many topics the student trails the class by. */
  behindBy: number;
  /** In priority order: carry-over, gap-fill, current, catch-up, preview. */
  candidates: TopicCandidate[];
};

export type SelectTermTopicsInput = {
  subjectId: string;
  classLevel: ClassLevel;
  termContext: TermContext;
  /** Every topic in the subject, any class. */
  topics: readonly PlanTopic[];
  graph: KnowledgeGraph;
  state: TopicStateMap;
  pretestPassed: ReadonlySet<string>;
  positionTopicId: string | null;
  carryOver: readonly CarryOver[];
  /** EXAM mode pauses new term learning; only missed work carries on. */
  carryOverOnly?: boolean;
};

function mastery(state: TopicStateMap, topicId: string): number {
  return state.get(topicId)?.mastery ?? 0;
}

function byOrder(a: PlanTopic, b: PlanTopic): number {
  return a.orderIndex - b.orderIndex || a.id.localeCompare(b.id);
}

export function atOrBelowClass(topic: { classLevel: ClassLevel }, classLevel: ClassLevel): boolean {
  return CLASS_LEVELS.indexOf(topic.classLevel) <= CLASS_LEVELS.indexOf(classLevel);
}

function scopeTopics(topics: readonly PlanTopic[], classLevel: ClassLevel, term: Term): PlanTopic[] {
  return topics.filter((t) => t.classLevel === classLevel && t.term === term).sort(byOrder);
}

type ClassLocation = { termTopics: PlanTopic[]; classIndex: number; preview: PlanTopic[] };

function locateByCalendar(
  allowed: readonly PlanTopic[],
  classLevel: ClassLevel,
  ctx: TermContext,
): ClassLocation {
  if (ctx.kind === "in_term") {
    const termTopics = scopeTopics(allowed, classLevel, ctx.current.term);
    if (termTopics.length === 0) return { termTopics, classIndex: -1, preview: [] };
    const classIndex = Math.min(
      termTopics.length - 1,
      Math.floor(((ctx.weekOfTerm - 1) / ctx.totalWeeks) * termTopics.length),
    );
    return { termTopics, classIndex, preview: termTopics.slice(classIndex + 1, classIndex + 2) };
  }
  // Holiday: the class has finished the previous term. A FIRST term next means
  // a new class year, whose topics are above the student's class — no preview.
  const termTopics = ctx.previous ? scopeTopics(allowed, classLevel, ctx.previous.term) : [];
  const preview =
    ctx.next && ctx.next.term !== "FIRST"
      ? scopeTopics(allowed, classLevel, ctx.next.term).slice(0, 2)
      : [];
  return { termTopics, classIndex: termTopics.length - 1, preview };
}

function locateClass(input: SelectTermTopicsInput, allowed: readonly PlanTopic[]): ClassLocation {
  const position = input.positionTopicId
    ? allowed.find((t) => t.id === input.positionTopicId)
    : undefined;
  if (position) {
    const termTopics = scopeTopics(allowed, position.classLevel, position.term);
    const classIndex = termTopics.findIndex((t) => t.id === position.id);
    return { termTopics, classIndex, preview: termTopics.slice(classIndex + 1, classIndex + 2) };
  }
  return locateByCalendar(allowed, input.classLevel, input.termContext);
}

/** Weak prerequisites from earlier terms or classes, deepest foundations first. */
function gapsFor(
  target: PlanTopic,
  input: SelectTermTopicsInput,
  byId: ReadonlyMap<string, PlanTopic>,
  termIds: ReadonlySet<string>,
): PlanTopic[] {
  const out: PlanTopic[] = [];
  const visited = new Set<string>();
  const visit = (topicId: string) => {
    for (const edge of incomingEdges(input.graph, topicId)) {
      if (edge.kind !== "PREREQUISITE" || visited.has(edge.from)) continue;
      visited.add(edge.from);
      const prereq = byId.get(edge.from);
      if (!prereq || termIds.has(prereq.id)) continue;
      if (mastery(input.state, prereq.id) >= GATE || input.pretestPassed.has(prereq.id)) continue;
      visit(prereq.id);
      out.push(prereq);
    }
  };
  visit(target.id);
  return out;
}

export function selectTermTopics(input: SelectTermTopicsInput): SubjectSelection {
  const allowed = input.topics.filter((t) => atOrBelowClass(t, input.classLevel));
  const byId = new Map(allowed.map((t) => [t.id, t]));
  const unmastered = (t: PlanTopic) => mastery(input.state, t.id) < TARGET;
  const { termTopics, classIndex, preview } = locateClass(input, allowed);

  const candidates: TopicCandidate[] = [];
  const seen = new Set<string>();
  const add = (topic: PlanTopic, reason: CandidateReason, extra: Partial<TopicCandidate> = {}) => {
    if (seen.has(topic.id)) return;
    seen.add(topic.id);
    candidates.push({ topic, reason, carriedFrom: null, unlocks: null, ...extra });
  };

  for (const item of input.carryOver) {
    const t = byId.get(item.topicId);
    if (t && item.subjectId === input.subjectId && unmastered(t)) {
      add(t, "CARRY_OVER", { carriedFrom: item.missedOn });
    }
  }

  if (!input.carryOverOnly) {
    const current = classIndex >= 0 ? termTopics[classIndex] : null;
    const currentNeeded = current && unmastered(current) ? current : null;
    const catchUp = termTopics.slice(0, Math.max(0, classIndex)).filter(unmastered);
    const termIds = new Set(termTopics.map((t) => t.id));

    for (const t of [...(currentNeeded ? [currentNeeded] : []), ...catchUp]) {
      for (const gap of gapsFor(t, input, byId, termIds)) {
        add(gap, "GAP_FILL", { unlocks: t.title });
      }
    }
    if (currentNeeded) add(currentNeeded, "CURRENT");
    for (const t of catchUp) add(t, "CATCH_UP");
    for (const t of preview.filter(unmastered)) add(t, "PREVIEW");
  }

  const firstUnmastered = termTopics.findIndex(unmastered);
  const behindBy =
    classIndex < 0 || firstUnmastered < 0 ? 0 : Math.max(0, classIndex - firstUnmastered);

  return { subjectId: input.subjectId, termTopics, classIndex, behindBy, candidates };
}

export function selectExamTopics(input: {
  topics: readonly PlanTopic[];
  classLevel: ClassLevel;
  state: TopicStateMap;
}): TopicCandidate[] {
  const weight = (t: PlanTopic) => t.waecWeight + t.jambWeight;
  return input.topics
    .filter((t) => atOrBelowClass(t, input.classLevel) && mastery(input.state, t.id) < TARGET)
    .sort(
      (a, b) =>
        weight(b) - weight(a) ||
        mastery(input.state, a.id) - mastery(input.state, b.id) ||
        byOrder(a, b),
    )
    .map((topic) => ({ topic, reason: "EXAM" as const, carriedFrom: null, unlocks: null }));
}

/** Where the calendar says the class is in one subject, ignoring any override. */
export function calendarTopicId(
  topics: readonly PlanTopic[],
  classLevel: ClassLevel,
  ctx: TermContext,
): string | null {
  const allowed = topics.filter((t) => atOrBelowClass(t, classLevel));
  const { termTopics, classIndex } = locateByCalendar(allowed, classLevel, ctx);
  return classIndex >= 0 ? termTopics[classIndex].id : null;
}
```

Check the "gap-fill" test order: for `cur`, `visit("cur")` sees `g1` (weak) → visits `g1` → sees `g0` (weak) → pushes `g0`, then pushes `g1`; `strong` is skipped. Result `[g0, g1]`, then `CURRENT:cur`. That matches the test.

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-topics.mts`
Expected: 11 passing.

- [ ] **Step 5: Register and commit**

Append ` scripts/test-study-plan-topics.mts` to the `"test"` script.

```bash
git add src/engines/planner/topics.ts scripts/test-study-plan-topics.mts package.json
git commit -m "Select term and exam topics for the planner

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 7: Window layout

**Files:**
- Create: `src/engines/planner/layout.ts`
- Test: `scripts/test-study-plan-layout.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `KnowledgeGraph`, `incomingEdges`; `TopicStateMap`; `GATE`; `REVISION_OFFSETS`, `WEAK_MASTERY`, `MOCK_COUNT`, `PlanActivityType` (`./plan`); day helpers (Task 2); `SESSION_MINUTES`, `Slot` (Task 5); `PlanMode`, `examShare` (Task 4); `SubjectSelection`, `TopicCandidate` (Task 6).
- Produces:
  - `type WindowItemDraft = { date: DayKey; subjectId: string; topicId: string | null; activityType: PlanActivityType; durationMinutes: number; notes: string | null; carriedFrom: DayKey | null }`
  - `type FixedItem = { date: DayKey; subjectId: string; durationMinutes: number }`
  - `type RevisionDue = { topicId: string; subjectId: string; title: string; reason: string }`
  - `type Overload = { topicsBehind: number; suggestedExtraMinutesPerWeek: number }`
  - `type LayoutInput = { mode; slots; targetDate: DayKey | null; runwayStart: DayKey | null; selections; examCandidates; subjectIds; subjectNames; graph; state; pretestPassed; revisionDue; fixed }`
  - `layoutWindow(input: LayoutInput): { items: WindowItemDraft[]; overload: Overload | null }`
  - Constants `GAP_FILL_SHARE = 0.2`, `REVISION_SHARE = 0.2`, `MOCK_MINUTES_CAP = 180`; `subjectCap(date): number`.

**How the layout works (read before implementing):**
1. Fixed items (completed or skipped sessions already on today or later) use up time: each day's slots are removed from the end of the day until the fixed minutes are covered, and their subjects count towards that day's subject cap.
2. Every candidate topic becomes a queue of units. A term topic gets `ceil(estimatedMinutes / 30)` LESSON units (none if its pretest was passed), then 1 PRACTICE unit, or 2 when mastery is below `WEAK_MASTERY` (50). An exam topic gets a single PRACTICE unit.
3. Days are processed in order.
   - **Runway day** (mode is not TERM and the date is on or after `runwayStart`): a pending mock whose planned date has arrived takes the whole day, capped at 180 minutes. Otherwise each slot takes due revision first, then PAST_QUESTIONS, rotating through subjects.
   - **Any other day**, slot by slot:
     - A **short slot** takes only due revision.
     - A **catch-up slot** takes carry-over first, then due revision, and otherwise falls through to the normal steps below.
     - An **exam credit** builds up by `share` for each slot: 0 in TERM, 1 in EXAM, `examShare(daysToExam)` in BLENDED. When the credit reaches 1 the slot is an exam slot, and every third exam slot is PAST_QUESTIONS.
     - Otherwise, due revision is used if this week's revisions stay within 20% of the week's slots; if not, the slot takes the best eligible topic unit. If the preferred side (exam or term) has nothing, the other side is tried, and finally any due revision.
4. A topic unit is eligible when:
   - its subject fits the day's cap,
   - for GAP_FILL, the remaining gap budget (20% of the window's full-slot minutes) still covers the slot,
   - for a topic's first unit, every PREREQUISITE is satisfied: either it is in the plan with all its lessons placed on an earlier day, or it is not in the plan and its mastery is at least `GATE × strength` (or its pretest was passed),
   - for PRACTICE, the topic's last lesson is on an earlier day.
5. Placing a topic's last LESSON adds revision passes at +1/+3/+7/+14 days to the revision pool. A pass is taken by the first free slot on or after its due date, at most once per topic per day.
6. Overload: required term queues (not preview, not exam) with units left. `topicsBehind` is how many such topics there are; `suggestedExtraMinutesPerWeek` is the unplaced minutes divided by the number of weeks in the window, rounded up to a multiple of 30.

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-layout.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildGraph, type GraphEdge } from "../src/engines/learning/graph";
import type { TopicState, TopicStateMap } from "../src/engines/learning/mastery";
import { buildSlots, dayBudget, type Availability } from "../src/engines/planner/slots";
import { daysBetween } from "../src/engines/planner/days";
import {
  layoutWindow,
  type LayoutInput,
  type WindowItemDraft,
} from "../src/engines/planner/layout";
import type { PlanTopic, SubjectSelection, TopicCandidate } from "../src/engines/planner/topics";

const START = "2026-09-14"; // Monday
const EVERY_DAY_30: Availability = { studyDays: [1, 2, 3, 4, 5, 6, 7], weekdayMinutes: 30, weekendMinutes: 30 };

function topic(id: string, overrides: Partial<PlanTopic> = {}): PlanTopic {
  return {
    id, subjectId: "maths", title: `Topic ${id}`, slug: id, orderIndex: 0, estimatedMinutes: 30,
    waecWeight: 0, jambWeight: 0, prerequisiteTopicId: null, classLevel: "SS1", term: "FIRST",
    ...overrides,
  };
}

function cand(t: PlanTopic, reason: TopicCandidate["reason"] = "CURRENT", carriedFrom: string | null = null): TopicCandidate {
  return { topic: t, reason, carriedFrom, unlocks: null };
}

function selection(subjectId: string, candidates: TopicCandidate[], behindBy = 0): SubjectSelection {
  return { subjectId, termTopics: candidates.map((c) => c.topic), classIndex: 0, behindBy, candidates };
}

function states(entries: Record<string, number>): TopicStateMap {
  return new Map(Object.entries(entries).map(([id, mastery]) => [id, { topicId: id, mastery } as unknown as TopicState]));
}

function layout(overrides: Partial<LayoutInput> & { availability?: Availability; days?: number } = {}) {
  const { availability = EVERY_DAY_30, days = 14, ...rest } = overrides;
  const selections = rest.selections ?? [];
  const topics = selections.flatMap((s) => s.candidates.map((c) => c.topic));
  return layoutWindow({
    mode: "TERM",
    slots: buildSlots(START, days, availability),
    targetDate: null,
    runwayStart: null,
    selections,
    examCandidates: [],
    subjectIds: selections.map((s) => s.subjectId),
    subjectNames: {},
    graph: buildGraph(topics, []),
    state: states({}),
    pretestPassed: new Set(),
    revisionDue: [],
    fixed: [],
    ...rest,
  });
}

const of = (items: WindowItemDraft[], type: string) => items.filter((i) => i.activityType === type);

test("never schedules more than a day's budget, including fixed sessions", () => {
  const availability: Availability = { studyDays: [1, 2, 3, 4, 6], weekdayMinutes: 45, weekendMinutes: 120 };
  const topics = [1, 2, 3, 4, 5, 6].map((n) => topic(`t${n}`, { orderIndex: n, estimatedMinutes: 60 }));
  const { items } = layout({
    availability,
    selections: [selection("maths", topics.map((t) => cand(t)))],
    fixed: [{ date: START, subjectId: "maths", durationMinutes: 30 }],
  });
  const perDay = new Map<string, number>([[START, 30]]);
  for (const i of items) perDay.set(i.date, (perDay.get(i.date) ?? 0) + i.durationMinutes);
  for (const [date, minutes] of perDay) assert.ok(minutes <= dayBudget(date, availability), `${date}: ${minutes}`);
});

test("lesson, then practice on a later day, then spaced revision", () => {
  const t = topic("t1");
  const { items } = layout({ selections: [selection("maths", [cand(t)])] });
  const lesson = of(items, "LESSON");
  const practice = of(items, "PRACTICE");
  const revision = of(items, "REVISION");
  assert.equal(lesson.length, 1);
  assert.equal(practice.length, 2); // mastery 0 < 50 → two practice sessions
  assert.ok(practice.every((p) => p.date > lesson[0].date));
  // +1, +3, +7 fall inside 14 days; +14 does not.
  assert.equal(revision.length, 3);
  const offsets = [1, 3, 7];
  revision.forEach((r, i) => assert.ok(daysBetween(lesson[0].date, r.date) >= offsets[i]));
});

test("a passed pretest skips lessons", () => {
  const t = topic("t1");
  const { items } = layout({ selections: [selection("maths", [cand(t)])], pretestPassed: new Set(["t1"]) });
  assert.equal(of(items, "LESSON").length, 0);
  assert.ok(of(items, "PRACTICE").length > 0);
});

test("a dependent topic waits for its prerequisite's lessons", () => {
  const a = topic("a", { estimatedMinutes: 60 });
  const b = topic("b", { orderIndex: 1 });
  const edge: GraphEdge = { id: "a->b", from: "a", to: "b", kind: "PREREQUISITE", strength: 1, rationale: null };
  const { items } = layout({
    selections: [selection("maths", [cand(b), cand(a, "CATCH_UP")])],
    graph: buildGraph([a, b], [edge]),
  });
  const lastLessonA = of(items, "LESSON").filter((i) => i.topicId === "a").at(-1)!;
  const firstB = items.find((i) => i.topicId === "b")!;
  assert.ok(firstB.date > lastLessonA.date);
});

test("at most two subjects on a weekday and three on a weekend day", () => {
  const availability: Availability = { studyDays: [1, 2, 3, 4, 5, 6, 7], weekdayMinutes: 150, weekendMinutes: 150 };
  const subjects = ["a", "b", "c", "d"].map((s) =>
    selection(s, [1, 2, 3].map((n) => cand(topic(`${s}${n}`, { subjectId: s, orderIndex: n, estimatedMinutes: 60 })))),
  );
  const { items } = layout({ availability, selections: subjects });
  const perDay = new Map<string, Set<string>>();
  for (const i of items) perDay.set(i.date, (perDay.get(i.date) ?? new Set()).add(i.subjectId));
  for (const [date, set] of perDay) {
    const weekend = ["2026-09-19", "2026-09-20", "2026-09-26", "2026-09-27"].includes(date);
    assert.ok(set.size <= (weekend ? 3 : 2), `${date}: ${[...set].join(",")}`);
  }
});

test("the catch-up slot takes carry-over and marks where it came from", () => {
  const availability: Availability = { studyDays: [1, 2, 3, 4, 5, 6, 7], weekdayMinutes: 0, weekendMinutes: 60 };
  const missed = topic("missed");
  const current = topic("current", { orderIndex: 1 });
  const { items } = layout({
    availability,
    selections: [selection("maths", [cand(missed, "CARRY_OVER", "2026-09-10"), cand(current)])],
  });
  // First study slot of the week is Sunday's first slot (last study day = Sunday).
  const saturday = items.filter((i) => i.date === "2026-09-19");
  assert.equal(saturday[0].topicId, "current");
  const sunday = items.filter((i) => i.date === "2026-09-20");
  assert.equal(sunday[0].topicId, "missed");
  assert.equal(sunday[0].carriedFrom, "2026-09-10");
});

test("term mode never schedules past questions or mocks", () => {
  const { items } = layout({ selections: [selection("maths", [cand(topic("t1"))])] });
  assert.equal(of(items, "PAST_QUESTIONS").length + of(items, "MOCK_EXAM").length, 0);
});

test("blended runway: a mock on the runway start, then revision and past questions", () => {
  const { items } = layout({
    mode: "BLENDED",
    availability: { studyDays: [1, 2, 3, 4, 5, 6, 7], weekdayMinutes: 60, weekendMinutes: 60 },
    targetDate: "2026-09-27",
    runwayStart: "2026-09-14",
    selections: [selection("maths", [cand(topic("t1"))])],
    subjectIds: ["maths"],
    subjectNames: { maths: "Mathematics" },
  });
  assert.equal(items[0].activityType, "MOCK_EXAM");
  assert.equal(items[0].date, START);
  assert.equal(items[0].durationMinutes, 60);
  assert.equal(of(items, "MOCK_EXAM").length, 2);
  assert.equal(of(items, "LESSON").length, 0);
  assert.ok(of(items, "PAST_QUESTIONS").every((i) => i.notes === "Past questions — Mathematics"));
});

test("exam mode before the runway fills slots with exam practice and past questions", () => {
  const exam = [1, 2, 3].map((n) => cand(topic(`e${n}`, { orderIndex: n }), "EXAM"));
  const { items } = layout({
    mode: "EXAM",
    targetDate: "2027-03-01",
    runwayStart: "2027-02-10",
    examCandidates: exam,
    subjectIds: ["maths"],
  });
  assert.ok(items.length > 0);
  assert.ok(items.every((i) => ["PRACTICE", "PAST_QUESTIONS", "REVISION"].includes(i.activityType)));
  assert.ok(of(items, "PAST_QUESTIONS").length > 0);
});

test("gap-fill takes at most 20% of the window", () => {
  const gaps = [1, 2, 3, 4, 5, 6].map((n) => cand(topic(`g${n}`, { orderIndex: n, estimatedMinutes: 90 }), "GAP_FILL"));
  const current = cand(topic("cur", { orderIndex: 10, estimatedMinutes: 90 }));
  const { items } = layout({ selections: [selection("maths", [...gaps, current])] });
  const gapMinutes = items
    .filter((i) => i.topicId?.startsWith("g") && i.activityType !== "REVISION")
    .reduce((n, i) => n + i.durationMinutes, 0);
  assert.ok(gapMinutes <= 14 * 30 * 0.2, `gap minutes ${gapMinutes}`);
});

test("too much work for the time reports overload instead of cramming", () => {
  const topics = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => cand(topic(`t${n}`, { orderIndex: n, estimatedMinutes: 60 }), "CATCH_UP"));
  const { overload } = layout({ selections: [selection("maths", topics)] });
  assert.ok(overload);
  assert.ok(overload.topicsBehind > 0);
  assert.equal(overload.suggestedExtraMinutesPerWeek % 30, 0);
});

test("preview topics never cause overload", () => {
  const previews = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => cand(topic(`p${n}`, { orderIndex: n, estimatedMinutes: 60 }), "PREVIEW"));
  assert.equal(layout({ selections: [selection("maths", previews)] }).overload, null);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-layout.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/engines/planner/layout.ts
import { incomingEdges, type KnowledgeGraph } from "../learning/graph";
import type { TopicStateMap } from "../learning/mastery";
import { GATE } from "../learning/availability";
import { MOCK_COUNT, REVISION_OFFSETS, WEAK_MASTERY, type PlanActivityType } from "./plan";
import { addDays, daysBetween, isWeekend, mondayOf, type DayKey } from "./days";
import { SESSION_MINUTES, type Slot } from "./slots";
import { examShare, type PlanMode } from "./mode";
import type { SubjectSelection, TopicCandidate } from "./topics";

// Fills a window of time slots with sessions. Pure and deterministic: the same
// input always yields the same items, in date order.

export type WindowItemDraft = {
  date: DayKey;
  subjectId: string;
  topicId: string | null;
  activityType: PlanActivityType;
  durationMinutes: number;
  notes: string | null;
  carriedFrom: DayKey | null;
};

/** A completed or skipped session already on the calendar; it keeps its time. */
export type FixedItem = { date: DayKey; subjectId: string; durationMinutes: number };

export type RevisionDue = { topicId: string; subjectId: string; title: string; reason: string };

export type Overload = { topicsBehind: number; suggestedExtraMinutesPerWeek: number };

export type LayoutInput = {
  mode: PlanMode;
  slots: readonly Slot[];
  targetDate: DayKey | null;
  runwayStart: DayKey | null;
  selections: readonly SubjectSelection[];
  examCandidates: readonly TopicCandidate[];
  subjectIds: readonly string[];
  subjectNames: Readonly<Record<string, string>>;
  graph: KnowledgeGraph;
  state: TopicStateMap;
  pretestPassed: ReadonlySet<string>;
  revisionDue: readonly RevisionDue[];
  fixed: readonly FixedItem[];
};

export const GAP_FILL_SHARE = 0.2;
export const REVISION_SHARE = 0.2;
export const MOCK_MINUTES_CAP = 180;
/** Every third exam slot is a past-questions session. */
const PAST_QUESTIONS_EVERY = 3;

export function subjectCap(date: DayKey): number {
  return isWeekend(date) ? 3 : 2;
}

type Unit = "LESSON" | "PRACTICE";

type Queue = {
  candidate: TopicCandidate;
  exam: boolean;
  /** Position in the candidate lists: the within-subject priority. */
  rank: number;
  units: Unit[];
  lessonsLeft: number;
  lastLessonDate: DayKey | null;
  started: boolean;
};

type PoolEntry = { dueOn: DayKey; order: number; topicId: string; subjectId: string; note: string };

function groupByDate(slots: readonly Slot[]): [DayKey, Slot[]][] {
  const days = new Map<DayKey, Slot[]>();
  for (const slot of slots) {
    const list = days.get(slot.date) ?? [];
    list.push({ ...slot });
    days.set(slot.date, list);
  }
  return [...days.entries()];
}

/** Drops each day's last slots until that day's fixed minutes are covered. */
function freeDays(slots: readonly Slot[], fixed: readonly FixedItem[]): [DayKey, Slot[]][] {
  const used = new Map<DayKey, number>();
  for (const f of fixed) used.set(f.date, (used.get(f.date) ?? 0) + f.durationMinutes);
  return groupByDate(slots).map(([date, daySlots]) => {
    let remaining = used.get(date) ?? 0;
    while (remaining > 0 && daySlots.length > 0) {
      remaining -= (daySlots.pop() as Slot).minutes;
    }
    return [date, daySlots];
  });
}

function noteFor(candidate: TopicCandidate, unit: Unit): string {
  const title = candidate.topic.title;
  if (candidate.reason === "EXAM") return `Exam prep — practise ${title}`;
  if (unit === "PRACTICE") return `Practise ${title} questions`;
  switch (candidate.reason) {
    case "CARRY_OVER":
      return "Catch-up — from a missed session";
    case "GAP_FILL":
      return candidate.unlocks ? `Foundation for ${candidate.unlocks}` : "Build this foundation first";
    case "CURRENT":
      return "Your class is on this topic now";
    case "CATCH_UP":
      return "Catch up with your class";
    case "PREVIEW":
      return "Get ahead — coming up next in class";
  }
}

export function layoutWindow(input: LayoutInput): {
  items: WindowItemDraft[];
  overload: Overload | null;
} {
  const days = freeDays(input.slots, input.fixed);
  const items: WindowItemDraft[] = [];

  const daySubjects = new Map<DayKey, Set<string>>();
  const subjectsOn = (date: DayKey) => {
    let set = daySubjects.get(date);
    if (!set) daySubjects.set(date, (set = new Set()));
    return set;
  };
  for (const f of input.fixed) subjectsOn(f.date).add(f.subjectId);
  const allowSubject = (date: DayKey, subjectId: string) => {
    const set = subjectsOn(date);
    return set.has(subjectId) || set.size < subjectCap(date);
  };

  const place = (slot: Slot, draft: Omit<WindowItemDraft, "date" | "durationMinutes">) => {
    subjectsOn(slot.date).add(draft.subjectId);
    items.push({ date: slot.date, durationMinutes: slot.minutes, ...draft });
  };

  // ── Queues ───────────────────────────────────────────────
  const queues = new Map<string, Queue>();
  const behindBy = new Map<string, number>();
  let rank = 0;
  const addQueue = (candidate: TopicCandidate, exam: boolean) => {
    const id = candidate.topic.id;
    if (queues.has(id)) return;
    const mastery = input.state.get(id)?.mastery ?? 0;
    const lessons = exam || input.pretestPassed.has(id)
      ? 0
      : Math.max(1, Math.ceil(candidate.topic.estimatedMinutes / SESSION_MINUTES));
    const practice = exam ? 1 : mastery < WEAK_MASTERY ? 2 : 1;
    const units: Unit[] = [
      ...Array<Unit>(lessons).fill("LESSON"),
      ...Array<Unit>(practice).fill("PRACTICE"),
    ];
    queues.set(id, {
      candidate, exam, rank: rank++, units, lessonsLeft: lessons, lastLessonDate: null, started: false,
    });
  };
  for (const selection of input.selections) {
    behindBy.set(selection.subjectId, selection.behindBy);
    for (const candidate of selection.candidates) addQueue(candidate, false);
  }
  for (const candidate of input.examCandidates) addQueue(candidate, true);

  const fullMinutes = days.reduce(
    (n, [, daySlots]) => n + daySlots.filter((s) => !s.short).reduce((m, s) => m + s.minutes, 0),
    0,
  );
  let gapMinutesLeft = Math.floor(fullMinutes * GAP_FILL_SHARE);

  const prereqsReady = (queue: Queue, date: DayKey): boolean => {
    for (const edge of incomingEdges(input.graph, queue.candidate.topic.id)) {
      if (edge.kind !== "PREREQUISITE") continue;
      const planned = queues.get(edge.from);
      if (planned && !planned.exam) {
        if (planned.lessonsLeft > 0) return false;
        if (planned.lastLessonDate !== null && planned.lastLessonDate >= date) return false;
        continue;
      }
      const mastery = input.state.get(edge.from)?.mastery ?? 0;
      if (mastery < GATE * edge.strength && !input.pretestPassed.has(edge.from)) return false;
    }
    return true;
  };

  const eligible = (queue: Queue, date: DayKey, slot: Slot): boolean => {
    const unit = queue.units[0];
    if (!unit) return false;
    if (!allowSubject(date, queue.candidate.topic.subjectId)) return false;
    if (queue.candidate.reason === "GAP_FILL" && gapMinutesLeft < slot.minutes) return false;
    if (!queue.started && !prereqsReady(queue, date)) return false;
    if (unit === "PRACTICE" && queue.lastLessonDate !== null && queue.lastLessonDate >= date) {
      return false;
    }
    return true;
  };

  const weekUses = new Map<string, number>(); // `${monday}:${subjectId}`
  const usesOf = (date: DayKey, subjectId: string) => weekUses.get(`${mondayOf(date)}:${subjectId}`) ?? 0;

  const catchUpDates = new Set(input.slots.filter((s) => s.catchUp).map((s) => s.date));
  /** Missed work waits for the week's catch-up slot while one is still coming. */
  const catchUpAhead = (slot: Slot) =>
    !slot.catchUp &&
    [...catchUpDates].some((d) => d >= slot.date && mondayOf(d) === mondayOf(slot.date));

  const pickTopic = (slot: Slot, exam: boolean, only?: TopicCandidate["reason"]): Queue | null => {
    const options = [...queues.values()].filter(
      (q) =>
        q.exam === exam &&
        (only
          ? q.candidate.reason === only
          : q.candidate.reason !== "CARRY_OVER" || !catchUpAhead(slot)) &&
        eligible(q, slot.date, slot),
    );
    options.sort((a, b) => {
      const sa = a.candidate.topic.subjectId;
      const sb = b.candidate.topic.subjectId;
      return (
        Number(a.candidate.reason === "PREVIEW") - Number(b.candidate.reason === "PREVIEW") ||
        usesOf(slot.date, sa) - usesOf(slot.date, sb) ||
        (behindBy.get(sb) ?? 0) - (behindBy.get(sa) ?? 0) ||
        sa.localeCompare(sb) ||
        Number(b.started) - Number(a.started) ||
        a.rank - b.rank
      );
    });
    return options[0] ?? null;
  };

  // ── Revision pool ────────────────────────────────────────
  let poolOrder = 0;
  const firstDate = input.slots[0]?.date ?? "";
  const pool: PoolEntry[] = input.revisionDue.map((r) => ({
    dueOn: firstDate, order: poolOrder++, topicId: r.topicId, subjectId: r.subjectId, note: r.reason,
  }));
  const revisedOn = new Set<string>();

  const takeRevision = (date: DayKey): PoolEntry | null => {
    const options = pool
      .filter((p) => p.dueOn <= date && !revisedOn.has(`${date}:${p.topicId}`) && allowSubject(date, p.subjectId))
      .sort((a, b) => a.dueOn.localeCompare(b.dueOn) || a.order - b.order);
    const entry = options[0];
    if (!entry) return null;
    pool.splice(pool.indexOf(entry), 1);
    revisedOn.add(`${date}:${entry.topicId}`);
    return entry;
  };

  const weekRevisions = new Map<DayKey, number>();
  const weekSlots = new Map<DayKey, number>();

  const placeRevision = (slot: Slot, entry: PoolEntry) => {
    const week = mondayOf(slot.date);
    weekRevisions.set(week, (weekRevisions.get(week) ?? 0) + 1);
    place(slot, {
      subjectId: entry.subjectId, topicId: entry.topicId, activityType: "REVISION", notes: entry.note, carriedFrom: null,
    });
  };

  const placeUnit = (slot: Slot, queue: Queue) => {
    const unit = queue.units.shift() as Unit;
    const t = queue.candidate.topic;
    queue.started = true;
    if (queue.candidate.reason === "GAP_FILL") gapMinutesLeft -= slot.minutes;
    const key = `${mondayOf(slot.date)}:${t.subjectId}`;
    weekUses.set(key, (weekUses.get(key) ?? 0) + 1);
    if (unit === "LESSON") {
      queue.lessonsLeft -= 1;
      queue.lastLessonDate = slot.date;
      if (queue.lessonsLeft === 0) {
        for (const offset of REVISION_OFFSETS) {
          pool.push({
            dueOn: addDays(slot.date, offset), order: poolOrder++, topicId: t.id, subjectId: t.subjectId,
            note: `Revision pass — ${t.title} (+${offset}d)`,
          });
        }
      }
    }
    place(slot, {
      subjectId: t.subjectId, topicId: t.id, activityType: unit,
      notes: noteFor(queue.candidate, unit), carriedFrom: queue.candidate.carriedFrom,
    });
  };

  let subjectTurn = 0;
  const placePastQuestions = (slot: Slot): boolean => {
    for (let i = 0; i < input.subjectIds.length; i++) {
      const subjectId = input.subjectIds[(subjectTurn + i) % input.subjectIds.length];
      if (!allowSubject(slot.date, subjectId)) continue;
      subjectTurn = (subjectTurn + i + 1) % input.subjectIds.length;
      const name = input.subjectNames[subjectId];
      place(slot, {
        subjectId, topicId: null, activityType: "PAST_QUESTIONS",
        notes: name ? `Past questions — ${name}` : "Past questions practice", carriedFrom: null,
      });
      return true;
    }
    return false;
  };

  // ── Runway mocks ─────────────────────────────────────────
  const pendingMocks: DayKey[] = [];
  if (input.mode !== "TERM" && input.runwayStart && input.targetDate) {
    const runwayLength = daysBetween(input.runwayStart, input.targetDate) + 1;
    for (let i = 0; i < MOCK_COUNT; i++) {
      pendingMocks.push(addDays(input.runwayStart, Math.floor((runwayLength * i) / MOCK_COUNT)));
    }
  }
  const inRunway = (date: DayKey) =>
    input.mode !== "TERM" && input.runwayStart !== null && date >= input.runwayStart;

  const shareFor = (date: DayKey): number => {
    if (input.mode === "TERM") return 0;
    if (input.mode === "EXAM" || !input.targetDate) return 1;
    return examShare(daysBetween(date, input.targetDate));
  };

  // ── Main loop ────────────────────────────────────────────
  let examCredit = 0;
  let examSlots = 0;

  const placeWork = (slot: Slot, exam: boolean): boolean => {
    if (exam) {
      if (input.mode === "TERM") return false;
      examSlots += 1;
      if (examSlots % PAST_QUESTIONS_EVERY === 0 && placePastQuestions(slot)) return true;
      const queue = pickTopic(slot, true);
      if (queue) {
        placeUnit(slot, queue);
        return true;
      }
      return placePastQuestions(slot);
    }
    const week = mondayOf(slot.date);
    const revisionsSoFar = weekRevisions.get(week) ?? 0;
    if (revisionsSoFar + 1 <= (weekSlots.get(week) ?? 0) * REVISION_SHARE) {
      const entry = takeRevision(slot.date);
      if (entry) {
        placeRevision(slot, entry);
        return true;
      }
    }
    const queue = pickTopic(slot, false);
    if (!queue) return false;
    placeUnit(slot, queue);
    return true;
  };

  for (const [date, daySlots] of days) {
    if (daySlots.length === 0) continue;

    if (inRunway(date)) {
      if (pendingMocks.length > 0 && pendingMocks[0] <= date) {
        pendingMocks.shift();
        const minutes = Math.min(MOCK_MINUTES_CAP, daySlots.reduce((n, s) => n + s.minutes, 0));
        const subjectId = input.subjectIds[0];
        if (subjectId) {
          subjectsOn(date).add(subjectId);
          items.push({
            date, subjectId, topicId: null, activityType: "MOCK_EXAM", durationMinutes: minutes,
            notes: "Full mock exam — timed conditions", carriedFrom: null,
          });
        }
        continue;
      }
      for (const slot of daySlots) {
        const entry = takeRevision(date);
        if (entry) placeRevision(slot, entry);
        else if (!slot.short) placePastQuestions(slot);
      }
      continue;
    }

    for (const slot of daySlots) {
      if (slot.short) {
        const entry = takeRevision(date);
        if (entry) placeRevision(slot, entry);
        continue;
      }
      const week = mondayOf(date);
      weekSlots.set(week, (weekSlots.get(week) ?? 0) + 1);

      if (slot.catchUp) {
        const carried = pickTopic(slot, false, "CARRY_OVER");
        if (carried) {
          placeUnit(slot, carried);
          continue;
        }
        const entry = takeRevision(date);
        if (entry) {
          placeRevision(slot, entry);
          continue;
        }
      }

      examCredit += shareFor(date);
      const wantExam = examCredit >= 1;
      if (wantExam) examCredit -= 1;
      if (placeWork(slot, wantExam) || placeWork(slot, !wantExam)) continue;
      const entry = takeRevision(date);
      if (entry) placeRevision(slot, entry);
    }
  }

  // ── Overload ─────────────────────────────────────────────
  const unfinished = [...queues.values()].filter(
    (q) => !q.exam && q.candidate.reason !== "PREVIEW" && q.units.length > 0,
  );
  let overload: Overload | null = null;
  if (unfinished.length > 0 && input.slots.length > 0) {
    const unplacedMinutes = unfinished.reduce((n, q) => n + q.units.length * SESSION_MINUTES, 0);
    const windowDays = daysBetween(input.slots[0].date, input.slots[input.slots.length - 1].date) + 1;
    const weeks = Math.max(1, Math.ceil(windowDays / 7));
    overload = {
      topicsBehind: unfinished.length,
      suggestedExtraMinutesPerWeek: Math.ceil(unplacedMinutes / weeks / SESSION_MINUTES) * SESSION_MINUTES,
    };
  }

  return { items, overload };
}
```

Missed work waits for its week's catch-up slot (`catchUpAhead`). After that slot has passed, or in a week with no catch-up slot left, carry-over competes as a normal candidate with the highest within-subject rank.

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-layout.mts`
Expected: 12 passing. If "lesson, then practice…" fails on the revision count, print `items` and check that a revision pass is taken by the first free slot on or after its due date. The 20% revision share must not block a slot that has no topic unit: the fallback `takeRevision` after `placeWork` handles that.

- [ ] **Step 5: Register and commit**

Append ` scripts/test-study-plan-layout.mts` to the `"test"` script.

```bash
git add src/engines/planner/layout.ts scripts/test-study-plan-layout.mts package.json
git commit -m "Lay out a plan window from slots and topic candidates

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 8: Outline and the planner entry point

**Files:**
- Create: `src/engines/planner/outline.ts`, `src/engines/planner/term-plan.ts`
- Test: `scripts/test-study-plan-window.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: Tasks 2–7.
- Produces:
  - `type OutlineWeek = { weekStart: DayKey; label: string | null; topics: { subjectId: string; title: string }[] }`
  - `projectOutline(input: { today: DayKey; from: DayKey; until: DayKey | null; mode: PlanMode; runwayStart: DayKey | null; termContext: TermContext; selections: readonly SubjectSelection[] }): OutlineWeek[]`
  - `WINDOW_DAYS = 14`
  - `type PlannerSubject = { id: string; name: string; topics: readonly PlanTopic[] }`
  - `type PlannerInput = { today: DayKey; planStart: DayKey; mode: PlanMode; classLevel: ClassLevel; targetDate: DayKey | null; termContext: TermContext; availability: Availability; subjects: readonly PlannerSubject[]; graph: KnowledgeGraph; state: TopicStateMap; pretestPassed: ReadonlySet<string>; positions: ReadonlyMap<string, string>; revisionDue: readonly RevisionDue[]; carryOver: readonly CarryOver[]; fixed: readonly FixedItem[] }`
  - `type PlannerOutput = { items: WindowItemDraft[]; outline: OutlineWeek[]; overload: Overload | null; plannedThrough: DayKey; runwayStart: DayKey | null }`
  - `planWindow(input: PlannerInput): PlannerOutput`

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-window.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildGraph } from "../src/engines/learning/graph";
import type { TermContext } from "../src/engines/planner/term-context";
import type { PlanTopic } from "../src/engines/planner/topics";
import { planWindow, type PlannerInput } from "../src/engines/planner/term-plan";
import { projectOutline } from "../src/engines/planner/outline";

function topic(id: string, overrides: Partial<PlanTopic> = {}): PlanTopic {
  return {
    id, subjectId: "maths", title: `Topic ${id}`, slug: id, orderIndex: 0, estimatedMinutes: 45,
    waecWeight: 1, jambWeight: 1, prerequisiteTopicId: null, classLevel: "SS1", term: "FIRST",
    ...overrides,
  };
}

const FIRST_TERM: TermContext = {
  kind: "in_term",
  source: "configured",
  current: { session: "2026/2027", term: "FIRST", startsOn: "2026-09-07", endsOn: "2026-11-29" },
  weekOfTerm: 2,
  totalWeeks: 12,
  weeksLeft: 10,
};

const ss1 = [1, 2, 3, 4, 5, 6].map((n) => topic(`a${n}`, { orderIndex: n }));
const ss2 = [1, 2].map((n) => topic(`b${n}`, { classLevel: "SS2", orderIndex: n }));
const ss3 = [1, 2].map((n) => topic(`c${n}`, { classLevel: "SS3", orderIndex: n }));

function input(overrides: Partial<PlannerInput> = {}): PlannerInput {
  const topics = [...ss1, ...ss2, ...ss3];
  return {
    today: "2026-09-14",
    planStart: "2026-09-14",
    mode: "TERM",
    classLevel: "SS1",
    targetDate: null,
    termContext: FIRST_TERM,
    availability: { studyDays: [1, 2, 3, 4, 6], weekdayMinutes: 30, weekendMinutes: 60 },
    subjects: [{ id: "maths", name: "Mathematics", topics }],
    graph: buildGraph(topics, []),
    state: new Map(),
    pretestPassed: new Set(),
    positions: new Map(),
    revisionDue: [],
    carryOver: [],
    fixed: [],
    mocksTaken: 0,
    ...overrides,
  };
}

test("an SS1 term plan covers 14 days with SS1 topics only and no exam work", () => {
  const out = planWindow(input());
  assert.equal(out.plannedThrough, "2026-09-27");
  assert.ok(out.items.length > 0);
  const ids = new Set(out.items.map((i) => i.topicId));
  for (const t of [...ss2, ...ss3]) assert.equal(ids.has(t.id), false);
  assert.equal(out.items.some((i) => i.activityType === "PAST_QUESTIONS" || i.activityType === "MOCK_EXAM"), false);
  assert.equal(out.runwayStart, null);
});

test("an SS3 plan 10 days from the exam is all runway and ends on the exam day", () => {
  const out = planWindow(input({
    classLevel: "SS3",
    mode: "BLENDED",
    targetDate: "2026-09-23",
    planStart: "2026-08-01",
  }));
  assert.equal(out.plannedThrough, "2026-09-23");
  // 2026-08-01 → 2026-09-23 is 54 days; 20% rounds to 11, clamped up to 14.
  assert.equal(out.runwayStart, "2026-09-10");
  assert.ok(out.items.some((i) => i.activityType === "MOCK_EXAM"));
  assert.ok(out.items.every((i) => i.date <= "2026-09-23"));
});

test("a passed exam date plans nothing", () => {
  const out = planWindow(input({ classLevel: "SS3", mode: "BLENDED", targetDate: "2026-09-01" }));
  assert.deepEqual(out.items, []);
  assert.equal(out.plannedThrough, "2026-09-14");
});

test("projectOutline paces the class through the term after the window", () => {
  const out = planWindow(input());
  assert.ok(out.outline.length > 0);
  assert.equal(out.outline[0].weekStart, "2026-09-28");
  assert.ok(out.outline.every((w) => w.weekStart <= "2026-11-29"));
  assert.equal(out.outline.at(-1)!.topics[0].title, "Topic a6");
});

test("projectOutline labels runway weeks and has nothing in a holiday", () => {
  const weeks = projectOutline({
    today: "2026-09-14",
    from: "2026-09-28",
    until: "2026-10-25",
    mode: "BLENDED",
    runwayStart: "2026-10-12",
    termContext: FIRST_TERM,
    selections: [],
  });
  assert.equal(weeks.at(-1)!.label, "Exam runway — mocks and past questions");
  const holiday = projectOutline({
    today: "2026-12-20",
    from: "2027-01-04",
    until: null,
    mode: "TERM",
    runwayStart: null,
    termContext: { kind: "holiday", source: "configured", previous: null, next: null },
    selections: [],
  });
  assert.deepEqual(holiday, []);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-window.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `outline.ts`**

```ts
// src/engines/planner/outline.ts
import { addDays, daysBetween, mondayOf, type DayKey } from "./days";
import type { PlanMode } from "./mode";
import type { TermContext } from "./term-context";
import type { SubjectSelection } from "./topics";

export type OutlineWeek = {
  weekStart: DayKey;
  label: string | null;
  topics: { subjectId: string; title: string }[];
};

/** Keeps the page and the stored JSON small for a plan months from its exam. */
const MAX_OUTLINE_WEEKS = 20;

/** A rough week-by-week view after the detailed window: where the class will be. */
export function projectOutline(input: {
  today: DayKey;
  from: DayKey;
  until: DayKey | null;
  mode: PlanMode;
  runwayStart: DayKey | null;
  termContext: TermContext;
  selections: readonly SubjectSelection[];
}): OutlineWeek[] {
  if (!input.until || input.from > input.until) return [];
  const ctx = input.termContext;
  const weeks: OutlineWeek[] = [];

  for (
    let weekStart = mondayOf(input.from);
    weekStart <= input.until && weeks.length < MAX_OUTLINE_WEEKS;
    weekStart = addDays(weekStart, 7)
  ) {
    if (input.runwayStart && weekStart >= mondayOf(input.runwayStart)) {
      weeks.push({ weekStart, label: "Exam runway — mocks and past questions", topics: [] });
      continue;
    }
    if (input.mode === "EXAM") {
      weeks.push({ weekStart, label: "Exam revision", topics: [] });
      continue;
    }
    if (ctx.kind !== "in_term" || weekStart > ctx.current.endsOn) {
      weeks.push({ weekStart, label: "Next term — topics follow the school calendar", topics: [] });
      continue;
    }

    const weeksAhead = Math.floor(daysBetween(mondayOf(input.today), weekStart) / 7);
    const topics = input.selections.flatMap((selection) => {
      const count = selection.termTopics.length;
      if (count === 0 || selection.classIndex < 0) return [];
      const pace = count / ctx.totalWeeks;
      const index = Math.min(count - 1, selection.classIndex + Math.floor(weeksAhead * pace));
      return [{ subjectId: selection.subjectId, title: selection.termTopics[index].title }];
    });
    weeks.push({ weekStart, label: null, topics });
  }
  return weeks;
}
```

- [ ] **Step 4: Implement `term-plan.ts`**

```ts
// src/engines/planner/term-plan.ts
import type { KnowledgeGraph } from "../learning/graph";
import type { TopicStateMap } from "../learning/mastery";
import type { ClassLevel } from "../../lib/curriculum-scope";
import { addDays, daysBetween, type DayKey } from "./days";
import { layoutWindow, type FixedItem, type Overload, type RevisionDue, type WindowItemDraft } from "./layout";
import { computeRunwayStart, type PlanMode } from "./mode";
import { projectOutline, type OutlineWeek } from "./outline";
import { buildSlots, type Availability } from "./slots";
import type { TermContext } from "./term-context";
import {
  selectExamTopics,
  selectTermTopics,
  type CarryOver,
  type PlanTopic,
} from "./topics";

// The planner's single entry point. See
// docs/superpowers/specs/2026-09-14-study-plan-term-mode-design.md §5.

export const WINDOW_DAYS = 14;

export type PlannerSubject = { id: string; name: string; topics: readonly PlanTopic[] };

export type PlannerInput = {
  today: DayKey;
  /** The day the plan was created: anchors the exam runway. */
  planStart: DayKey;
  mode: PlanMode;
  classLevel: ClassLevel;
  targetDate: DayKey | null;
  termContext: TermContext;
  availability: Availability;
  subjects: readonly PlannerSubject[];
  graph: KnowledgeGraph;
  state: TopicStateMap;
  pretestPassed: ReadonlySet<string>;
  /** subjectId → topicId: "my class is on topic X". */
  positions: ReadonlyMap<string, string>;
  revisionDue: readonly RevisionDue[];
  carryOver: readonly CarryOver[];
  fixed: readonly FixedItem[];
  /** Mock exams on/after the runway start already completed or skipped. */
  mocksTaken: number;
};

export type PlannerOutput = {
  items: WindowItemDraft[];
  outline: OutlineWeek[];
  overload: Overload | null;
  plannedThrough: DayKey;
  runwayStart: DayKey | null;
};

export function planWindow(input: PlannerInput): PlannerOutput {
  const examBound = input.mode !== "TERM" && input.targetDate !== null;
  const days = examBound
    ? Math.max(0, Math.min(WINDOW_DAYS, daysBetween(input.today, input.targetDate as DayKey) + 1))
    : WINDOW_DAYS;
  const plannedThrough = addDays(input.today, Math.max(1, days) - 1);

  const selections = input.subjects.map((subject) =>
    selectTermTopics({
      subjectId: subject.id,
      classLevel: input.classLevel,
      termContext: input.termContext,
      topics: subject.topics,
      graph: input.graph,
      state: input.state,
      pretestPassed: input.pretestPassed,
      positionTopicId: input.positions.get(subject.id) ?? null,
      carryOver: input.carryOver,
      carryOverOnly: input.mode === "EXAM",
    }),
  );

  const examCandidates =
    input.mode === "TERM"
      ? []
      : selectExamTopics({
          topics: input.subjects.flatMap((s) => s.topics),
          classLevel: input.classLevel,
          state: input.state,
        });

  const runwayStart = examBound
    ? computeRunwayStart(input.planStart, input.targetDate as DayKey)
    : null;

  const { items, overload } = layoutWindow({
    mode: input.mode,
    slots: buildSlots(input.today, days, input.availability),
    targetDate: input.targetDate,
    runwayStart,
    selections,
    examCandidates,
    subjectIds: input.subjects.map((s) => s.id),
    subjectNames: Object.fromEntries(input.subjects.map((s) => [s.id, s.name])),
    graph: input.graph,
    state: input.state,
    pretestPassed: input.pretestPassed,
    revisionDue: input.revisionDue,
    fixed: input.fixed,
    mocksTaken: input.mocksTaken,
  });

  const until = examBound
    ? input.targetDate
    : input.termContext.kind === "in_term"
      ? input.termContext.current.endsOn
      : null;

  const outline = projectOutline({
    today: input.today,
    from: addDays(plannedThrough, 1),
    until,
    mode: input.mode,
    runwayStart,
    termContext: input.termContext,
    selections,
  });

  return { items, outline, overload, plannedThrough, runwayStart };
}
```

How the outline test's last week works out: week 2 of 12 gives `classIndex = floor(1/12 × 6) = 0` and a pace of 0.5 topics per week. The last outline week starts 11-23, 10 weeks after Monday 09-14, so its index is `min(5, 0 + floor(10 × 0.5)) = 5`, which is "Topic a6".

- [ ] **Step 5: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-window.mts`
Expected: 5 passing.

- [ ] **Step 6: Register and commit**

Append ` scripts/test-study-plan-window.mts` to the `"test"` script.

```bash
git add src/engines/planner/outline.ts src/engines/planner/term-plan.ts scripts/test-study-plan-window.mts package.json
git commit -m "Add the planner entry point and term outline

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 9: Completion matching and re-plan partition

**Files:**
- Create: `src/engines/planner/completion.ts`, `src/engines/planner/replan.ts`
- Test: `scripts/test-study-plan-tracking.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `DayKey`, `addDays` (Task 2); `PlanActivityType` (`./plan`); `FixedItem` (Task 7); `CarryOver` (Task 6); `lagosDayKey` (`../../lib/streak`).
- Produces (`completion.ts`):
  - `type PlanItemStatusValue = "PENDING" | "COMPLETED" | "SKIPPED" | "MISSED"`
  - `type TrackedItem = { id: string; date: DayKey; subjectId: string; topicId: string | null; activityType: PlanActivityType; status: PlanItemStatusValue }`
  - `type CompletionSignal = { kind: "LESSON_COMPLETED"; topicId: string } | { kind: "TOPIC_QUIZ"; topicIds: readonly string[] } | { kind: "CARD_REVIEWED"; topicId: string } | { kind: "MOCK_EXAM" } | { kind: "PAST_PAPER"; subjectId: string | null }`
  - `pickItemToComplete(items: readonly TrackedItem[], signal: CompletionSignal, today: DayKey): string | null`
  - `signalForAssessment(input: { assessmentType: string; subjectId: string | null; topicIds: readonly string[]; practiceExit: boolean }): CompletionSignal | null`
  - `type ManualStatus = "COMPLETED" | "SKIPPED" | "PENDING"`
  - `MANUAL_LOOKBACK_DAYS = 7`
  - `manualStatusChange(item: { date: DayKey }, requested: ManualStatus, today: DayKey, plannedThrough: DayKey | null): { ok: true; status: PlanItemStatusValue } | { ok: false; error: string }`
- Produces (`replan.ts`):
  - `CARRY_OVER_DAYS = 14`
  - `type ExistingItem = TrackedItem & { durationMinutes: number }`
  - `type ReplanPartition = { markMissed: string[]; deletePending: string[]; fixed: FixedItem[]; carryOver: CarryOver[] }`
  - `isReplanStale(lastReplannedAt: Date | null, now: Date): boolean`
  - `partitionForReplan(items: readonly ExistingItem[], today: DayKey): ReplanPartition`

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-tracking.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  manualStatusChange,
  pickItemToComplete,
  signalForAssessment,
  type TrackedItem,
} from "../src/engines/planner/completion";
import { isReplanStale, partitionForReplan, type ExistingItem } from "../src/engines/planner/replan";

const TODAY = "2026-09-14";

function item(id: string, overrides: Partial<ExistingItem> = {}): ExistingItem {
  return {
    id, date: TODAY, subjectId: "maths", topicId: "t1", activityType: "LESSON",
    status: "PENDING", durationMinutes: 30, ...overrides,
  };
}

test("a lesson completes the oldest matching pending lesson on or before today", () => {
  const items: TrackedItem[] = [
    item("today", { date: TODAY }),
    item("older", { date: "2026-09-12" }),
    item("future", { date: "2026-09-15" }),
    item("other-topic", { date: "2026-09-10", topicId: "t2" }),
  ];
  assert.equal(pickItemToComplete(items, { kind: "LESSON_COMPLETED", topicId: "t1" }, TODAY), "older");
});

test("future sessions and finished sessions are never auto-completed", () => {
  const items: TrackedItem[] = [
    item("future", { date: "2026-09-15" }),
    item("done", { status: "COMPLETED" }),
    item("missed", { status: "MISSED", date: "2026-09-10" }),
  ];
  assert.equal(pickItemToComplete(items, { kind: "LESSON_COMPLETED", topicId: "t1" }, TODAY), null);
});

test("a topic quiz completes practice before revision", () => {
  const items: TrackedItem[] = [
    item("rev", { activityType: "REVISION", date: "2026-09-10" }),
    item("prac", { activityType: "PRACTICE" }),
  ];
  assert.equal(pickItemToComplete(items, { kind: "TOPIC_QUIZ", topicIds: ["t1", "t9"] }, TODAY), "prac");
  assert.equal(pickItemToComplete([items[0]], { kind: "TOPIC_QUIZ", topicIds: ["t1"] }, TODAY), "rev");
});

test("cards, mocks and past papers match their own activity", () => {
  const items: TrackedItem[] = [
    item("rev", { activityType: "REVISION" }),
    item("mock", { activityType: "MOCK_EXAM", topicId: null }),
    item("pq-eng", { activityType: "PAST_QUESTIONS", topicId: null, subjectId: "english" }),
    item("pq-maths", { activityType: "PAST_QUESTIONS", topicId: null }),
  ];
  assert.equal(pickItemToComplete(items, { kind: "CARD_REVIEWED", topicId: "t1" }, TODAY), "rev");
  assert.equal(pickItemToComplete(items, { kind: "MOCK_EXAM" }, TODAY), "mock");
  assert.equal(pickItemToComplete(items, { kind: "PAST_PAPER", subjectId: "maths" }, TODAY), "pq-maths");
});

test("signalForAssessment maps assessment types", () => {
  const base = { subjectId: "maths", topicIds: ["t1"], practiceExit: false };
  assert.deepEqual(signalForAssessment({ ...base, assessmentType: "MOCK_EXAM" }), { kind: "MOCK_EXAM" });
  assert.deepEqual(signalForAssessment({ ...base, assessmentType: "CBT_PRACTICE" }), { kind: "MOCK_EXAM" });
  assert.deepEqual(signalForAssessment({ ...base, assessmentType: "PAST_PAPER" }), { kind: "PAST_PAPER", subjectId: "maths" });
  assert.deepEqual(signalForAssessment({ ...base, assessmentType: "TOPIC_QUIZ" }), { kind: "TOPIC_QUIZ", topicIds: ["t1"] });
  // A lesson's practice exit is topic practice even when its paper is exam-sourced.
  assert.deepEqual(
    signalForAssessment({ ...base, assessmentType: "PAST_PAPER", practiceExit: true }),
    { kind: "TOPIC_QUIZ", topicIds: ["t1"] },
  );
  assert.equal(signalForAssessment({ ...base, assessmentType: "TOPIC_QUIZ", topicIds: [] }), null);
});

test("manualStatusChange limits the date range and turns a past undo into missed", () => {
  const plannedThrough = "2026-09-27";
  assert.deepEqual(manualStatusChange({ date: TODAY }, "COMPLETED", TODAY, plannedThrough), { ok: true, status: "COMPLETED" });
  assert.deepEqual(manualStatusChange({ date: "2026-09-10" }, "PENDING", TODAY, plannedThrough), { ok: true, status: "MISSED" });
  assert.deepEqual(manualStatusChange({ date: "2026-09-15" }, "PENDING", TODAY, plannedThrough), { ok: true, status: "PENDING" });
  assert.equal(manualStatusChange({ date: "2026-09-06" }, "COMPLETED", TODAY, plannedThrough).ok, false);
  assert.equal(manualStatusChange({ date: "2026-09-28" }, "SKIPPED", TODAY, plannedThrough).ok, false);
});

test("isReplanStale compares Lagos days", () => {
  const now = new Date("2026-09-14T08:00:00Z");
  assert.equal(isReplanStale(null, now), true);
  assert.equal(isReplanStale(new Date("2026-09-14T00:30:00Z"), now), false);
  // 23:30 UTC on the 13th is 00:30 on the 14th in Lagos.
  assert.equal(isReplanStale(new Date("2026-09-13T23:30:00Z"), now), false);
  assert.equal(isReplanStale(new Date("2026-09-13T22:30:00Z"), now), true);
});

test("partitionForReplan keeps finished work, drops future pending, carries missed topics", () => {
  const items: ExistingItem[] = [
    item("past-pending", { date: "2026-09-12", topicId: "a" }),
    item("old-missed", { date: "2026-08-20", status: "MISSED", topicId: "old" }),
    item("recent-missed", { date: "2026-09-10", status: "MISSED", topicId: "a" }),
    item("today-pending", { date: TODAY }),
    item("future-pending", { date: "2026-09-20" }),
    item("today-done", { date: TODAY, status: "COMPLETED", subjectId: "english", durationMinutes: 45 }),
    item("future-skipped", { date: "2026-09-16", status: "SKIPPED" }),
    item("past-done", { date: "2026-09-11", status: "COMPLETED" }),
    item("missed-no-topic", { date: "2026-09-13", status: "MISSED", topicId: null }),
  ];
  const p = partitionForReplan(items, TODAY);
  assert.deepEqual(p.markMissed, ["past-pending"]);
  assert.deepEqual(p.deletePending.sort(), ["future-pending", "today-pending"]);
  assert.deepEqual(p.fixed, [
    { date: TODAY, subjectId: "english", durationMinutes: 45 },
    { date: "2026-09-16", subjectId: "maths", durationMinutes: 30 },
  ]);
  // Topic "a" missed twice → one carry-over, dated the latest miss.
  assert.deepEqual(p.carryOver, [{ topicId: "a", subjectId: "maths", missedOn: "2026-09-12" }]);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-tracking.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `completion.ts`**

```ts
// src/engines/planner/completion.ts
import { addDays, type DayKey } from "./days";
import type { PlanActivityType } from "./plan";

// Which plan session a piece of learning activity completes. Pure: the DB side
// lives in src/lib/study-plan-completion.ts.

export type PlanItemStatusValue = "PENDING" | "COMPLETED" | "SKIPPED" | "MISSED";

export type TrackedItem = {
  id: string;
  date: DayKey;
  subjectId: string;
  topicId: string | null;
  activityType: PlanActivityType;
  status: PlanItemStatusValue;
};

export type CompletionSignal =
  | { kind: "LESSON_COMPLETED"; topicId: string }
  | { kind: "TOPIC_QUIZ"; topicIds: readonly string[] }
  | { kind: "CARD_REVIEWED"; topicId: string }
  | { kind: "MOCK_EXAM" }
  | { kind: "PAST_PAPER"; subjectId: string | null };

/** The oldest open session on or before today that the activity satisfies. */
export function pickItemToComplete(
  items: readonly TrackedItem[],
  signal: CompletionSignal,
  today: DayKey,
): string | null {
  const open = items
    .filter((i) => i.status === "PENDING" && i.date <= today)
    .sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id));
  const first = (match: (i: TrackedItem) => boolean) => open.find(match)?.id ?? null;

  switch (signal.kind) {
    case "LESSON_COMPLETED":
      return first((i) => i.activityType === "LESSON" && i.topicId === signal.topicId);
    case "TOPIC_QUIZ": {
      const topics = new Set(signal.topicIds);
      const onTopic = (i: TrackedItem) => i.topicId !== null && topics.has(i.topicId);
      return (
        first((i) => i.activityType === "PRACTICE" && onTopic(i)) ??
        first((i) => i.activityType === "REVISION" && onTopic(i))
      );
    }
    case "CARD_REVIEWED":
      return first((i) => i.activityType === "REVISION" && i.topicId === signal.topicId);
    case "MOCK_EXAM":
      return first((i) => i.activityType === "MOCK_EXAM");
    case "PAST_PAPER":
      return first(
        (i) =>
          i.activityType === "PAST_QUESTIONS" &&
          (signal.subjectId === null || i.subjectId === signal.subjectId),
      );
  }
}

export function signalForAssessment(input: {
  assessmentType: string;
  subjectId: string | null;
  topicIds: readonly string[];
  practiceExit: boolean;
}): CompletionSignal | null {
  if (!input.practiceExit) {
    if (input.assessmentType === "MOCK_EXAM" || input.assessmentType === "CBT_PRACTICE") {
      return { kind: "MOCK_EXAM" };
    }
    if (input.assessmentType === "PAST_PAPER") {
      return { kind: "PAST_PAPER", subjectId: input.subjectId };
    }
  }
  return input.topicIds.length > 0 ? { kind: "TOPIC_QUIZ", topicIds: [...input.topicIds] } : null;
}

export type ManualStatus = "COMPLETED" | "SKIPPED" | "PENDING";

export const MANUAL_LOOKBACK_DAYS = 7;

export function manualStatusChange(
  item: { date: DayKey },
  requested: ManualStatus,
  today: DayKey,
  plannedThrough: DayKey | null,
): { ok: true; status: PlanItemStatusValue } | { ok: false; error: string } {
  const tooOld = item.date < addDays(today, -MANUAL_LOOKBACK_DAYS);
  const tooFar = plannedThrough !== null && item.date > plannedThrough;
  if (tooOld || tooFar) {
    return { ok: false, error: "This session can no longer be changed." };
  }
  if (requested === "PENDING") {
    return { ok: true, status: item.date < today ? "MISSED" : "PENDING" };
  }
  return { ok: true, status: requested };
}
```

- [ ] **Step 4: Implement `replan.ts`**

```ts
// src/engines/planner/replan.ts
import { lagosDayKey } from "../../lib/streak";
import type { TrackedItem } from "./completion";
import { addDays, type DayKey } from "./days";
import type { FixedItem } from "./layout";
import type { CarryOver } from "./topics";

export const CARRY_OVER_DAYS = 14;

export type ExistingItem = TrackedItem & { durationMinutes: number };

export type ReplanPartition = {
  /** Pending sessions whose day has passed. */
  markMissed: string[];
  /** Pending sessions today or later: regenerated from scratch. */
  deletePending: string[];
  /** Completed or skipped sessions today or later: they keep their time. */
  fixed: FixedItem[];
  /** Recently missed topics, one entry per topic, dated its latest miss. */
  carryOver: CarryOver[];
};

export function isReplanStale(lastReplannedAt: Date | null, now: Date): boolean {
  return lastReplannedAt === null || lagosDayKey(lastReplannedAt) < lagosDayKey(now);
}

export function partitionForReplan(items: readonly ExistingItem[], today: DayKey): ReplanPartition {
  const markMissed: string[] = [];
  const deletePending: string[] = [];
  const fixed: FixedItem[] = [];
  const latestMiss = new Map<string, CarryOver>();
  const carryFrom = addDays(today, -CARRY_OVER_DAYS);

  const sorted = [...items].sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id));
  for (const item of sorted) {
    const pastPending = item.status === "PENDING" && item.date < today;
    if (pastPending) markMissed.push(item.id);
    else if (item.status === "PENDING") deletePending.push(item.id);
    else if (item.status !== "MISSED" && item.date >= today) {
      fixed.push({ date: item.date, subjectId: item.subjectId, durationMinutes: item.durationMinutes });
    }

    const missed = pastPending || item.status === "MISSED";
    if (missed && item.topicId && item.date >= carryFrom) {
      const previous = latestMiss.get(item.topicId);
      if (!previous || previous.missedOn < item.date) {
        latestMiss.set(item.topicId, { topicId: item.topicId, subjectId: item.subjectId, missedOn: item.date });
      }
    }
  }

  const carryOver = [...latestMiss.values()].sort(
    (a, b) => a.missedOn.localeCompare(b.missedOn) || a.topicId.localeCompare(b.topicId),
  );
  return { markMissed, deletePending, fixed, carryOver };
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-tracking.mts`
Expected: 8 passing.

- [ ] **Step 6: Register and commit**

Append ` scripts/test-study-plan-tracking.mts` to the `"test"` script.

```bash
git add src/engines/planner/completion.ts src/engines/planner/replan.ts scripts/test-study-plan-tracking.mts package.json
git commit -m "Match activity to plan sessions and partition re-plans

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 10: Validators

**Files:**
- Modify: `src/lib/validators.ts` (the `// ─── Study Plan` block at ~line 269, and the `GenerateStudyPlanInput` type at ~line 467)
- Test: `scripts/test-study-plan-validators.mts`
- Modify: `package.json`

**Interfaces:**
- Produces:
  - `studyPlanSettingsSchema` and `type StudyPlanSettingsInput`: `{ subjectIds: string[]; studyDays: number[]; weekdayMinutes: number; weekendMinutes: number; targetExam?: "WAEC" | "JAMB" | "NECO" | null; targetDate?: string | null; forceExamMode: boolean }`
  - `studyPlanPositionsSchema`: `{ positions: { subjectId: string; topicId: string | null }[] }`
  - `studyPlanItemStatusSchema`: `{ status: "COMPLETED" | "SKIPPED" | "PENDING" }`
  - `academicTermSchema` and `type AcademicTermInput`: `{ session: string; term: "FIRST" | "SECOND" | "THIRD"; startsOn: string; endsOn: string }`
- Removes: `generateStudyPlanSchema`, `GenerateStudyPlanInput`.

- [ ] **Step 1: Write the failing test**

```ts
// scripts/test-study-plan-validators.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  academicTermSchema,
  studyPlanItemStatusSchema,
  studyPlanPositionsSchema,
  studyPlanSettingsSchema,
} from "../src/lib/validators";

const settings = { subjectIds: ["m"], studyDays: [1, 3, 6], weekdayMinutes: 45, weekendMinutes: 90 };

test("term plan settings parse and default forceExamMode", () => {
  const parsed = studyPlanSettingsSchema.parse(settings);
  assert.equal(parsed.forceExamMode, false);
});

test("exam and exam date go together", () => {
  assert.equal(studyPlanSettingsSchema.safeParse({ ...settings, targetExam: "WAEC" }).success, false);
  assert.equal(studyPlanSettingsSchema.safeParse({ ...settings, targetDate: "2027-05-01" }).success, false);
  assert.equal(
    studyPlanSettingsSchema.safeParse({ ...settings, targetExam: "WAEC", targetDate: "2027-05-01" }).success,
    true,
  );
});

test("settings reject out-of-range values", () => {
  assert.equal(studyPlanSettingsSchema.safeParse({ ...settings, studyDays: [] }).success, false);
  assert.equal(studyPlanSettingsSchema.safeParse({ ...settings, studyDays: [0] }).success, false);
  assert.equal(studyPlanSettingsSchema.safeParse({ ...settings, weekdayMinutes: 481 }).success, false);
  assert.equal(studyPlanSettingsSchema.safeParse({ ...settings, targetExam: "WAEC", targetDate: "1 May" }).success, false);
});

test("positions, item status and academic terms", () => {
  assert.equal(studyPlanPositionsSchema.safeParse({ positions: [{ subjectId: "m", topicId: null }] }).success, true);
  assert.equal(studyPlanItemStatusSchema.safeParse({ status: "MISSED" }).success, false);
  assert.equal(
    academicTermSchema.safeParse({ session: "2026/2027", term: "FIRST", startsOn: "2026-09-08", endsOn: "2026-12-15" }).success,
    true,
  );
  assert.equal(
    academicTermSchema.safeParse({ session: "2026-27", term: "FIRST", startsOn: "2026-09-08", endsOn: "2026-12-15" }).success,
    false,
  );
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-validators.mts`
Expected: FAIL: `studyPlanSettingsSchema` is not exported.

- [ ] **Step 3: Replace the Study Plan block**

```ts
// ─── Study Plan ───────────────────────────────────

/** A calendar day, `YYYY-MM-DD`. Dates travel as days so no timezone can shift them. */
const dayKeySchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Use a date like 2027-05-01");

export const studyPlanSettingsSchema = z
  .object({
    subjectIds: z.array(z.string()).min(1).max(20),
    studyDays: z.array(z.number().int().min(1).max(7)).min(1).max(7),
    weekdayMinutes: z.number().int().min(0).max(480),
    weekendMinutes: z.number().int().min(0).max(600),
    targetExam: z.enum(["WAEC", "JAMB", "NECO"]).nullable().optional(),
    targetDate: dayKeySchema.nullable().optional(),
    forceExamMode: z.boolean().default(false),
  })
  .refine((v) => (v.targetExam == null) === (v.targetDate == null), {
    message: "Choose both an exam and its date, or neither.",
    path: ["targetDate"],
  });

export const studyPlanPositionsSchema = z.object({
  positions: z
    .array(z.object({ subjectId: z.string(), topicId: z.string().nullable() }))
    .max(20),
});

export const studyPlanItemStatusSchema = z.object({
  status: z.enum(["COMPLETED", "SKIPPED", "PENDING"]),
});

export const academicTermSchema = z.object({
  session: z.string().regex(/^\d{4}\/\d{4}$/, "Use the form 2026/2027"),
  term: z.enum(["FIRST", "SECOND", "THIRD"]),
  startsOn: dayKeySchema,
  endsOn: dayKeySchema,
});
```

Near line 467, replace `export type GenerateStudyPlanInput = z.infer<typeof generateStudyPlanSchema>;` with:

```ts
export type StudyPlanSettingsInput = z.infer<typeof studyPlanSettingsSchema>;
export type AcademicTermInput = z.infer<typeof academicTermSchema>;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-validators.mts`
Expected: 4 passing.

- [ ] **Step 5: Register and commit**

Append ` scripts/test-study-plan-validators.mts` to the `"test"` script. (`tsc` still fails in the old study plan route until Task 13; that is expected.)

```bash
git add src/lib/validators.ts scripts/test-study-plan-validators.mts package.json
git commit -m "Validate study plan settings, positions and academic terms

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 11: Admin term calendar

**Files:**
- Create: `src/lib/academic-terms.ts`
- Create: `src/app/admin/api/academic-terms/route.ts`
- Create: `src/app/admin/api/academic-terms/[id]/route.ts`
- Create: `src/app/admin/(console)/terms/page.tsx`
- Create: `src/components/admin/academic-term-manager.tsx`
- Modify: `src/lib/admin-nav.ts` (add a nav entry)
- Modify: `src/app/admin/(console)/page.tsx` (add a coverage warning)

**Interfaces:**
- Consumes: `academicTermSchema`, `AcademicTermInput` (Task 10); `validateTermRanges`, `hasTermCoverage`, `TermRange` (Task 3); `dayKeyToDate`, `dateToDayKey` (Task 2); `lagosDayKey`; `requireAdminApi`, `requireAdminPage`; `recordAudit`.
- Produces:
  - `type AcademicTermRow = TermRange & { id: string }`
  - `listAcademicTerms(): Promise<AcademicTermRow[]>`
  - `saveAcademicTerm(input: AcademicTermInput, id?: string): Promise<{ ok: true; term: AcademicTermRow } | { ok: false; status: 400 | 404; errors: string[] }>`
  - `deleteAcademicTerm(id: string): Promise<boolean>`

- [ ] **Step 1: Read the Next.js route handler guide**

Run: `ls node_modules/next/dist/docs/` and open the route handler / dynamic params guide. Confirm that `params` is a `Promise` (it is in `src/app/admin/api/materials/[id]/route.ts`).

- [ ] **Step 2: Write `src/lib/academic-terms.ts`**

```ts
import { db } from "./db";
import { dateToDayKey, dayKeyToDate } from "@/engines/planner/days";
import { validateTermRanges, type TermRange } from "@/engines/planner/term-context";
import type { AcademicTermInput } from "./validators";

export type AcademicTermRow = TermRange & { id: string };

export async function listAcademicTerms(): Promise<AcademicTermRow[]> {
  const rows = await db.academicTerm.findMany({ orderBy: { startsOn: "asc" } });
  return rows.map((row) => ({
    id: row.id,
    session: row.session,
    term: row.term,
    startsOn: dateToDayKey(row.startsOn),
    endsOn: dateToDayKey(row.endsOn),
  }));
}

/**
 * Creates (no id) or updates a term. Checked against every other term, so
 * overlaps are reported as a message rather than surfacing as a DB error.
 */
export async function saveAcademicTerm(
  input: AcademicTermInput,
  id?: string,
): Promise<
  | { ok: true; term: AcademicTermRow }
  | { ok: false; status: 400 | 404; errors: string[] }
> {
  const existing = await listAcademicTerms();
  if (id && !existing.some((t) => t.id === id)) {
    return { ok: false, status: 404, errors: ["Term not found."] };
  }
  const others = existing.filter((t) => t.id !== id);
  const errors = validateTermRanges([...others, input]);
  if (errors.length > 0) return { ok: false, status: 400, errors };

  const data = {
    session: input.session,
    term: input.term,
    startsOn: dayKeyToDate(input.startsOn),
    endsOn: dayKeyToDate(input.endsOn),
  };
  const row = id
    ? await db.academicTerm.update({ where: { id }, data })
    : await db.academicTerm.create({ data });
  return {
    ok: true,
    term: { id: row.id, session: row.session, term: row.term, startsOn: input.startsOn, endsOn: input.endsOn },
  };
}

export async function deleteAcademicTerm(id: string): Promise<boolean> {
  const { count } = await db.academicTerm.deleteMany({ where: { id } });
  return count > 0;
}
```

- [ ] **Step 3: Write `src/app/admin/api/academic-terms/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { academicTermSchema } from "@/lib/validators";
import { listAcademicTerms, saveAcademicTerm } from "@/lib/academic-terms";

export const dynamic = "force-dynamic";

// GET /admin/api/academic-terms — the school calendar, earliest first
export async function GET() {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;
    return NextResponse.json(await listAcademicTerms());
  } catch (error) {
    console.error("Error listing academic terms:", error);
    return NextResponse.json({ error: "Failed to list terms" }, { status: 500 });
  }
}

// POST /admin/api/academic-terms — add a term
export async function POST(req: NextRequest) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;

    const parsed = academicTermSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const saved = await saveAcademicTerm(parsed.data);
    if (!saved.ok) {
      return NextResponse.json({ error: saved.errors.join(" ") }, { status: saved.status });
    }

    await recordAudit({
      actorId: guard.actor.id,
      action: "academic-term.create",
      entity: "AcademicTerm",
      entityId: saved.term.id,
      summary: `Set ${saved.term.session} ${saved.term.term} term: ${saved.term.startsOn} to ${saved.term.endsOn}`,
    });

    return NextResponse.json(saved.term, { status: 201 });
  } catch (error) {
    console.error("Error creating academic term:", error);
    return NextResponse.json({ error: "Failed to save term" }, { status: 500 });
  }
}
```

- [ ] **Step 4: Write `src/app/admin/api/academic-terms/[id]/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { academicTermSchema } from "@/lib/validators";
import { deleteAcademicTerm, saveAcademicTerm } from "@/lib/academic-terms";

export const dynamic = "force-dynamic";

// PATCH /admin/api/academic-terms/[id] — replace a term's dates
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;
    const { id } = await params;

    const parsed = academicTermSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const saved = await saveAcademicTerm(parsed.data, id);
    if (!saved.ok) {
      return NextResponse.json({ error: saved.errors.join(" ") }, { status: saved.status });
    }

    await recordAudit({
      actorId: guard.actor.id,
      action: "academic-term.update",
      entity: "AcademicTerm",
      entityId: id,
      summary: `Changed ${saved.term.session} ${saved.term.term} term to ${saved.term.startsOn} – ${saved.term.endsOn}`,
    });

    return NextResponse.json(saved.term);
  } catch (error) {
    console.error("Error updating academic term:", error);
    return NextResponse.json({ error: "Failed to save term" }, { status: 500 });
  }
}

// DELETE /admin/api/academic-terms/[id]
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;
    const { id } = await params;

    if (!(await deleteAcademicTerm(id))) {
      return NextResponse.json({ error: "Term not found" }, { status: 404 });
    }

    await recordAudit({
      actorId: guard.actor.id,
      action: "academic-term.delete",
      entity: "AcademicTerm",
      entityId: id,
      summary: "Deleted an academic term",
    });

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Error deleting academic term:", error);
    return NextResponse.json({ error: "Failed to delete term" }, { status: 500 });
  }
}
```

- [ ] **Step 5: Write the admin page and manager component**

`src/app/admin/(console)/terms/page.tsx`:

```tsx
import { requireAdminPage } from "@/lib/admin-session";
import { PageHeader } from "@/components/ui/page-header";
import { listAcademicTerms } from "@/lib/academic-terms";
import { AcademicTermManager } from "@/components/admin/academic-term-manager";

export const dynamic = "force-dynamic";

export default async function AdminTermsPage() {
  // The layout's check does not re-run on client-side navigation between admin
  // routes, so each page carries its own.
  await requireAdminPage();
  const terms = await listAcademicTerms();

  return (
    <div>
      <PageHeader
        title="Term dates"
        description="The school calendar study plans follow. Set each term's first and last day."
      />
      <AcademicTermManager terms={terms} />
    </div>
  );
}
```

`src/components/admin/academic-term-manager.tsx`. Before writing it, open `src/components/admin/material-manager.tsx` and `admin-table.tsx` and match their class names; the structure below uses the shared table exports.

```tsx
"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { AdminTable, AdminTd, AdminTh, AdminTr } from "@/components/admin/admin-table";
import { StatusBanner } from "@/components/admin/status-banner";
import { buttonClass } from "@/components/ui/button";
import { TERM_LABELS, type Term } from "@/lib/curriculum-scope";
import type { AcademicTermRow } from "@/lib/academic-terms";

type Draft = { id?: string; session: string; term: Term; startsOn: string; endsOn: string };

const EMPTY: Draft = { session: "", term: "FIRST", startsOn: "", endsOn: "" };

export function AcademicTermManager({ terms }: { terms: AcademicTermRow[] }) {
  const router = useRouter();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState("");
  const [pending, startTransition] = useTransition();

  async function send(url: string, method: string, body?: unknown) {
    setError("");
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setError(data.error ?? "Could not save the term.");
      return false;
    }
    startTransition(() => router.refresh());
    return true;
  }

  async function save() {
    const { id, ...body } = draft;
    const ok = await send(
      id ? `/admin/api/academic-terms/${id}` : "/admin/api/academic-terms",
      id ? "PATCH" : "POST",
      body,
    );
    if (ok) setDraft(EMPTY);
  }

  return (
    <div className="space-y-6">
      {error && <StatusBanner tone="error" title={error} />}

      <AdminTable>
        <thead>
          <tr>
            <AdminTh>Session</AdminTh>
            <AdminTh>Term</AdminTh>
            <AdminTh>Starts</AdminTh>
            <AdminTh>Ends</AdminTh>
            <AdminTh>
              <span className="sr-only">Actions</span>
            </AdminTh>
          </tr>
        </thead>
        <tbody>
          {terms.map((t) => (
            <AdminTr key={t.id}>
              <AdminTd>{t.session}</AdminTd>
              <AdminTd>{TERM_LABELS[t.term]}</AdminTd>
              <AdminTd>{t.startsOn}</AdminTd>
              <AdminTd>{t.endsOn}</AdminTd>
              <AdminTd className="text-right">
                <button type="button" className={buttonClass("ghost", "sm")} onClick={() => setDraft({ ...t })}>
                  Edit
                </button>
                <button
                  type="button"
                  className={buttonClass("ghost", "sm")}
                  onClick={() => send(`/admin/api/academic-terms/${t.id}`, "DELETE")}
                >
                  Delete
                </button>
              </AdminTd>
            </AdminTr>
          ))}
        </tbody>
      </AdminTable>

      <form
        className="card grid grid-cols-1 gap-4 p-5 sm:grid-cols-5"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label className="text-sm">
          <span className="label">Session</span>
          <input className="input" placeholder="2026/2027" value={draft.session}
            onChange={(e) => setDraft({ ...draft, session: e.target.value })} required />
        </label>
        <label className="text-sm">
          <span className="label">Term</span>
          <select className="input" value={draft.term}
            onChange={(e) => setDraft({ ...draft, term: e.target.value as Term })}>
            {(["FIRST", "SECOND", "THIRD"] as const).map((term) => (
              <option key={term} value={term}>{TERM_LABELS[term]}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="label">First day</span>
          <input className="input" type="date" value={draft.startsOn}
            onChange={(e) => setDraft({ ...draft, startsOn: e.target.value })} required />
        </label>
        <label className="text-sm">
          <span className="label">Last day</span>
          <input className="input" type="date" value={draft.endsOn}
            onChange={(e) => setDraft({ ...draft, endsOn: e.target.value })} required />
        </label>
        <div className="flex items-end gap-2">
          <button type="submit" disabled={pending} className={buttonClass("primary", "md")}>
            {draft.id ? "Save changes" : "Add term"}
          </button>
          {draft.id && (
            <button type="button" className={buttonClass("ghost", "md")} onClick={() => setDraft(EMPTY)}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
```

If `AdminTable`, `AdminTh`, `AdminTd` or `AdminTr` take different props, or `buttonClass` has no `"ghost"`/`"sm"` variant, adapt to what `admin-table.tsx` and `button.tsx` actually export. Don't add new variants.

- [ ] **Step 6: Add the nav entry**

In `src/lib/admin-nav.ts`, add `LuCalendarDays` to the `react-icons/lu` import, and add to the `"Content"` group's `items` after Library:

```ts
      { name: "Term dates", href: "/admin/terms", icon: LuCalendarDays },
```

Run: `node --import tsx --test scripts/test-admin-nav.mts`
Expected: all passing. If a mobile-bar test counts items, update only the expectation that counts Content items.

- [ ] **Step 7: Add the coverage warning to the admin overview**

In `src/app/admin/(console)/page.tsx`, add the imports:

```ts
import { listAcademicTerms } from "@/lib/academic-terms";
import { hasTermCoverage } from "@/engines/planner/term-context";
import { lagosDayKey } from "@/lib/streak";
```

After `const hasGaps = …`, add:

```ts
  const termsCovered = hasTermCoverage(await listAcademicTerms(), lagosDayKey(new Date()));
```

Directly under the `<PageHeader … />`, add:

```tsx
      {!termsCovered && (
        <StatusBanner
          tone="info"
          title="No academic term set for today or the next 30 days"
          message="Study plans are using the approximate national calendar until term dates are added."
          action={<Link href="/admin/terms" className="font-semibold underline">Set term dates</Link>}
          className="mb-6"
        />
      )}
```

- [ ] **Step 8: Type-check the new files**

Run: `npx tsc --noEmit 2>&1 | grep -E "academic-term|admin-nav|admin/\(console\)/(page|terms)"`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add src/lib/academic-terms.ts src/app/admin/api/academic-terms "src/app/admin/(console)/terms" src/components/admin/academic-term-manager.tsx src/lib/admin-nav.ts "src/app/admin/(console)/page.tsx"
git commit -m "Let admins manage the academic term calendar

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 12: Study plan data layer

**Files:**
- Rewrite: `src/lib/study-plan.ts`
- Create: `src/lib/study-plan-display.ts` (pure page helpers)
- Test: `scripts/test-study-plan-display.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `planWindow`, `PlannerSubject` (Task 8); `partitionForReplan`, `isReplanStale`, `CARRY_OVER_DAYS` (Task 9); `manualStatusChange`, `ManualStatus` (Task 9); `resolveTermContext`, `termHeaderLabel`, `TermRange`, `TermSource` (Task 3); `resolvePlanMode`, `planSettingsProblem`, `DEFAULT_MINUTES`, `PlanMode` (Task 4); `calendarTopicId`, `atOrBelowClass`, `PlanTopic` (Task 6); `OutlineWeek` (Task 8); `Overload` (Task 7); `listAcademicTerms` (Task 11); `StudyPlanSettingsInput` (Task 10); `computePathState` (`./learning-path`); `loadRevisionExtras`, `revisionQueue` (`@/engines/learning/revision`); `relevantTrackCategories` (`./subjects`); `lagosDayKey` (`./streak`).
- Produces (`study-plan.ts`):
  - `replanIfStale(userId: string, options?: { force?: boolean; now?: Date }): Promise<void>`
  - `createStudyPlan(userId: string, settings: StudyPlanSettingsInput): Promise<{ ok: true; planId: string } | { ok: false; status: 400 | 404; error: string }>`
  - `updateStudyPlanSettings(userId: string, settings: StudyPlanSettingsInput): Promise<{ ok: true } | { ok: false; status: 400 | 404; error: string }>`
  - `setClassPositions(userId: string, positions: { subjectId: string; topicId: string | null }[]): Promise<{ ok: true } | { ok: false; status: 400 | 404; error: string }>`
  - `setPlanItemStatus(userId: string, itemId: string, requested: ManualStatus): Promise<{ ok: true; status: string } | { ok: false; status: 400 | 404; error: string }>`
  - `getStudyPlanPageData(userId: string): Promise<StudyPlanPageData>`
  - Types `StudyPlanItemData`, `StudyPlanData`, `StudyPlanPageData`, `PositionOption` (below).
- Produces (`study-plan-display.ts`):
  - `planItemHref(item: { activityType: string; topicSlug: string | null; subject: { slug: string } }): string`
  - `groupWindow<T extends { date: string; status: string }>(items: readonly T[], today: string): { today: T[]; recentMissed: T[]; thisWeek: { date: string; items: T[] }[]; nextWeek: { date: string; items: T[] }[] }`
  - `ACTIVITY_LABELS: Record<string, string>`

- [ ] **Step 1: Write the failing test for the display helpers**

```ts
// scripts/test-study-plan-display.mts
import { test } from "node:test";
import assert from "node:assert/strict";
import { groupWindow, planItemHref } from "../src/lib/study-plan-display";

const subject = { slug: "mathematics" };

test("planItemHref sends each activity to the page that completes it", () => {
  assert.equal(planItemHref({ activityType: "LESSON", topicSlug: "sets", subject }), "/classroom/mathematics/sets/study");
  assert.equal(planItemHref({ activityType: "PRACTICE", topicSlug: "sets", subject }), "/classroom/mathematics/sets/practice");
  assert.equal(planItemHref({ activityType: "REVISION", topicSlug: "sets", subject }), "/classroom/mathematics/sets/quiz");
  assert.equal(planItemHref({ activityType: "REVISION", topicSlug: null, subject }), "/flashcards");
  assert.equal(planItemHref({ activityType: "PAST_QUESTIONS", topicSlug: null, subject }), "/practice/past-questions/mathematics");
  assert.equal(planItemHref({ activityType: "MOCK_EXAM", topicSlug: null, subject }), "/practice/mock-exam");
});

test("groupWindow splits today, recent misses, this week and next week", () => {
  const items = [
    { id: "m", date: "2026-09-11", status: "MISSED" },
    { id: "d", date: "2026-09-12", status: "COMPLETED" },
    { id: "a", date: "2026-09-16", status: "PENDING" },
    { id: "t", date: "2026-09-16", status: "COMPLETED" },
    { id: "b", date: "2026-09-18", status: "PENDING" },
    { id: "c", date: "2026-09-20", status: "PENDING" },
    { id: "n", date: "2026-09-22", status: "PENDING" },
  ];
  const g = groupWindow(items, "2026-09-16");
  assert.deepEqual(g.today.map((i) => i.id), ["a", "t"]);
  assert.deepEqual(g.recentMissed.map((i) => i.id), ["m"]);
  assert.deepEqual(g.thisWeek.map((d) => d.date), ["2026-09-18", "2026-09-20"]);
  assert.deepEqual(g.nextWeek.map((d) => d.date), ["2026-09-22"]);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-study-plan-display.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/study-plan-display.ts`**

```ts
import { addDays, mondayOf } from "@/engines/planner/days";

// Pure helpers for the study plan page. No React, no database.

export const ACTIVITY_LABELS: Record<string, string> = {
  LESSON: "Lesson",
  PRACTICE: "Practice",
  REVISION: "Revision",
  PAST_QUESTIONS: "Past questions",
  MOCK_EXAM: "Mock exam",
};

/** The page where doing the session also marks it done. */
export function planItemHref(item: {
  activityType: string;
  topicSlug: string | null;
  subject: { slug: string };
}): string {
  const topic = item.topicSlug ? `/classroom/${item.subject.slug}/${item.topicSlug}` : null;
  switch (item.activityType) {
    case "LESSON":
      return topic ? `${topic}/study` : `/classroom/${item.subject.slug}`;
    case "PRACTICE":
      return topic ? `${topic}/practice` : `/classroom/${item.subject.slug}`;
    case "REVISION":
      return topic ? `${topic}/quiz` : "/flashcards";
    case "PAST_QUESTIONS":
      return `/practice/past-questions/${item.subject.slug}`;
    case "MOCK_EXAM":
      return "/practice/mock-exam";
    default:
      return "/study-plan";
  }
}

type Day<T> = { date: string; items: T[] };

function byDay<T extends { date: string }>(items: readonly T[]): Day<T>[] {
  const days = new Map<string, T[]>();
  for (const item of items) days.set(item.date, [...(days.get(item.date) ?? []), item]);
  return [...days.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, list]) => ({ date, items: list }));
}

export function groupWindow<T extends { date: string; status: string }>(
  items: readonly T[],
  today: string,
) {
  const nextMonday = addDays(mondayOf(today), 7);
  const followingMonday = addDays(nextMonday, 7);
  return {
    today: items.filter((i) => i.date === today),
    recentMissed: items.filter((i) => i.date < today && i.status === "MISSED"),
    thisWeek: byDay(items.filter((i) => i.date > today && i.date < nextMonday)),
    nextWeek: byDay(items.filter((i) => i.date >= nextMonday && i.date < followingMonday)),
  };
}
```

- [ ] **Step 4: Run the display test to verify it passes**

Run: `node --import tsx --test scripts/test-study-plan-display.mts`
Expected: 2 passing.

- [ ] **Step 5: Rewrite `src/lib/study-plan.ts`**

Replace the entire file with:

```ts
import { Prisma } from "@prisma/client";
import { db } from "./db";
import { computePathState } from "./learning-path";
import { loadRevisionExtras, revisionQueue } from "@/engines/learning/revision";
import { relevantTrackCategories } from "./subjects";
import { lagosDayKey } from "./streak";
import { listAcademicTerms } from "./academic-terms";
import type { StudyPlanSettingsInput } from "./validators";
import type { ClassLevel } from "./curriculum-scope";
import { TERM_LABELS } from "./curriculum-scope";
import { addDays, dateToDayKey, dayKeyToDate, daysBetween, type DayKey } from "@/engines/planner/days";
import { manualStatusChange, type ManualStatus } from "@/engines/planner/completion";
import {
  computeRunwayStart,
  DEFAULT_MINUTES,
  planSettingsProblem,
  resolvePlanMode,
  type PlanMode,
} from "@/engines/planner/mode";
import type { Overload } from "@/engines/planner/layout";
import type { OutlineWeek } from "@/engines/planner/outline";
import { CARRY_OVER_DAYS, isReplanStale, partitionForReplan } from "@/engines/planner/replan";
import { resolveTermContext, termHeaderLabel, type TermSource } from "@/engines/planner/term-context";
import { planWindow, type PlannerSubject } from "@/engines/planner/term-plan";
import { atOrBelowClass, calendarTopicId, type PlanTopic } from "@/engines/planner/topics";

// Study plan persistence: settings, the rolling re-plan, and the page payload.
// See docs/superpowers/specs/2026-09-14-study-plan-term-mode-design.md §6–§9.

type Failure = { ok: false; status: 400 | 404; error: string };

/**
 * Accounts created before class level was collected have none. Treating them as
 * SS3 keeps every topic reachable instead of hiding most of the syllabus.
 */
const UNKNOWN_CLASS_LEVEL: ClassLevel = "SS3";

/** Generous waits for the Supabase pooler, which can take seconds to hand out a connection. */
const REPLAN_TRANSACTION = { maxWait: 15_000, timeout: 30_000 } as const;

export type StudyPlanSubject = { id: string; name: string; code: string; slug: string };

export type StudyPlanItemData = {
  id: string;
  date: DayKey;
  subjectId: string;
  topicId: string | null;
  topicSlug: string | null;
  topicTitle: string | null;
  activityType: string;
  durationMinutes: number;
  status: string;
  notes: string | null;
  carriedFrom: DayKey | null;
  completionSource: string | null;
  subject: { name: string; code: string; slug: string };
};

export type PositionOption = { id: string; title: string; scope: string };

export type StudyPlanData = {
  id: string;
  mode: PlanMode;
  subjectIds: string[];
  studyDays: number[];
  weekdayMinutes: number;
  weekendMinutes: number;
  targetExam: string | null;
  targetDate: DayKey | null;
  forceExamMode: boolean;
  plannedThrough: DayKey | null;
  outline: OutlineWeek[];
  overload: Overload | null;
  items: StudyPlanItemData[];
  /** subjectId → the student's override, if any. */
  positions: Record<string, string>;
  /** subjectId → where the calendar thinks the class is. */
  calendarPositions: Record<string, string | null>;
  /** subjectId → topics the student may choose as "my class is here". */
  positionOptions: Record<string, PositionOption[]>;
};

export type StudyPlanPageData = {
  today: DayKey;
  classLevel: ClassLevel | null;
  termLabel: string;
  termSource: TermSource;
  daysToExam: number | null;
  defaults: { weekdayMinutes: number; weekendMinutes: number };
  subjects: StudyPlanSubject[];
  plan: StudyPlanData | null;
};

function asStringArray(value: Prisma.JsonValue): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

async function loadTermRanges() {
  return (await listAcademicTerms()).map((t) => ({
    session: t.session, term: t.term, startsOn: t.startsOn, endsOn: t.endsOn,
  }));
}

/** Every topic in the chosen subjects, with the class and term it belongs to. */
async function loadPlanTopics(subjectIds: readonly string[]): Promise<Map<string, PlanTopic[]>> {
  const rows = await db.topic.findMany({
    where: { subjectId: { in: [...subjectIds] } },
    select: {
      id: true, subjectId: true, title: true, slug: true, orderIndex: true, estimatedMinutes: true,
      waecWeight: true, jambWeight: true, prerequisiteTopicId: true,
      curriculumLevel: { select: { classLevel: true, term: true } },
    },
  });
  const bySubject = new Map<string, PlanTopic[]>();
  for (const { curriculumLevel, ...topic } of rows) {
    const list = bySubject.get(topic.subjectId) ?? [];
    list.push({ ...topic, classLevel: curriculumLevel.classLevel, term: curriculumLevel.term });
    bySubject.set(topic.subjectId, list);
  }
  return bySubject;
}

/**
 * Rebuilds the plan's detailed window when it was last built before today (Lagos
 * time), or always when `force` is set. Completed, skipped and missed sessions
 * are never changed; pending sessions from today onwards are regenerated.
 */
export async function replanIfStale(
  userId: string,
  options: { force?: boolean; now?: Date } = {},
): Promise<void> {
  const now = options.now ?? new Date();
  const plan = await db.studyPlan.findFirst({
    where: { studentId: userId, isActive: true },
    orderBy: { createdAt: "desc" },
    include: { positions: { select: { subjectId: true, topicId: true } } },
  });
  if (!plan) return;
  if (!options.force && !isReplanStale(plan.lastReplannedAt, now)) return;

  const today = lagosDayKey(now);
  const subjectIds = asStringArray(plan.subjectIds);

  // The heavy reads happen before the transaction. Holding the row lock across
  // them would stall every other write to this plan for seconds.
  const [user, subjects, topicsBySubject, terms, pathState] = await Promise.all([
    db.user.findUnique({ where: { id: userId }, select: { classLevel: true } }),
    db.subject.findMany({ where: { id: { in: subjectIds } }, select: { id: true, name: true } }),
    loadPlanTopics(subjectIds),
    loadTermRanges(),
    computePathState(db, userId, subjectIds, now),
  ]);
  const { graph, state, pretestPassed } = pathState;
  const extras = await loadRevisionExtras(db, userId, graph);
  const chosen = new Set(subjectIds);
  const revisionDue = revisionQueue(state, graph, extras, { now })
    .filter((item) => chosen.has(item.subjectId))
    .map((item) => ({ topicId: item.topicId, subjectId: item.subjectId, title: item.title, reason: item.reason }));

  const classLevel = user?.classLevel ?? UNKNOWN_CLASS_LEVEL;
  const targetDate = plan.targetDate ? dateToDayKey(plan.targetDate) : null;
  const plannerSubjects: PlannerSubject[] = subjects.map((s) => ({
    id: s.id, name: s.name, topics: topicsBySubject.get(s.id) ?? [],
  }));

  await db.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT 1 FROM "StudyPlan" WHERE "id" = ${plan.id} FOR NO KEY UPDATE`;
    const locked = await tx.studyPlan.findUnique({
      where: { id: plan.id },
      select: { isActive: true, lastReplannedAt: true },
    });
    // Another request re-planned while we were loading.
    if (!locked?.isActive) return;
    if (!options.force && !isReplanStale(locked.lastReplannedAt, now)) return;

    // Legacy plans can hold months of pending sessions; mark every past one.
    await tx.studyPlanItem.updateMany({
      where: { studyPlanId: plan.id, status: "PENDING", scheduledDate: { lt: dayKeyToDate(today) } },
      data: { status: "MISSED" },
    });

    const recent = await tx.studyPlanItem.findMany({
      where: { studyPlanId: plan.id, scheduledDate: { gte: dayKeyToDate(addDays(today, -CARRY_OVER_DAYS)) } },
      select: { id: true, scheduledDate: true, subjectId: true, topicId: true, activityType: true, status: true, durationMinutes: true },
    });
    const partition = partitionForReplan(
      recent.map((row) => ({ ...row, date: dateToDayKey(row.scheduledDate) })),
      today,
    );

    await tx.studyPlanItem.deleteMany({
      where: { studyPlanId: plan.id, status: "PENDING", scheduledDate: { gte: dayKeyToDate(today) } },
    });

    const mode = resolvePlanMode({ classLevel, targetDate, forceExamMode: plan.forceExamMode });
    const runwayStart =
      mode !== "TERM" && targetDate ? computeRunwayStart(lagosDayKey(plan.createdAt), targetDate) : null;
    // Mocks already sat or skipped are not offered again; missed ones are.
    const mocksTaken = runwayStart
      ? await tx.studyPlanItem.count({
          where: {
            studyPlanId: plan.id,
            activityType: "MOCK_EXAM",
            status: { in: ["COMPLETED", "SKIPPED"] },
            scheduledDate: { gte: dayKeyToDate(runwayStart) },
          },
        })
      : 0;

    const output = planWindow({
      today,
      planStart: lagosDayKey(plan.createdAt),
      mode,
      classLevel,
      targetDate,
      termContext: resolveTermContext(today, terms),
      availability: {
        studyDays: plan.studyDays,
        weekdayMinutes: plan.weekdayMinutes,
        weekendMinutes: plan.weekendMinutes,
      },
      subjects: plannerSubjects,
      graph,
      state,
      pretestPassed,
      positions: new Map(plan.positions.map((p) => [p.subjectId, p.topicId])),
      revisionDue,
      carryOver: partition.carryOver,
      fixed: partition.fixed,
      mocksTaken,
    });

    if (output.items.length > 0) {
      await tx.studyPlanItem.createMany({
        data: output.items.map((item) => ({
          studyPlanId: plan.id,
          scheduledDate: dayKeyToDate(item.date),
          subjectId: item.subjectId,
          topicId: item.topicId,
          activityType: item.activityType,
          durationMinutes: item.durationMinutes,
          notes: item.notes,
          carriedFromDate: item.carriedFrom ? dayKeyToDate(item.carriedFrom) : null,
        })),
      });
    }

    await tx.studyPlan.update({
      where: { id: plan.id },
      data: {
        plannedThrough: dayKeyToDate(output.plannedThrough),
        lastReplannedAt: now,
        outline: output.outline as unknown as Prisma.InputJsonValue,
        overload: output.overload ? (output.overload as unknown as Prisma.InputJsonValue) : Prisma.DbNull,
      },
    });
  }, REPLAN_TRANSACTION);
}

async function checkSettings(
  userId: string,
  settings: StudyPlanSettingsInput,
): Promise<{ ok: true; subjectIds: string[] } | Failure> {
  const user = await db.user.findUnique({ where: { id: userId }, select: { classLevel: true } });
  const problem = planSettingsProblem({
    classLevel: user?.classLevel ?? null,
    targetDate: settings.targetDate ?? null,
    forceExamMode: settings.forceExamMode,
    studyDays: settings.studyDays,
    weekdayMinutes: settings.weekdayMinutes,
    weekendMinutes: settings.weekendMinutes,
    today: lagosDayKey(new Date()),
  });
  if (problem) return { ok: false, status: 400, error: problem };

  const subjects = await db.subject.findMany({
    where: { id: { in: settings.subjectIds } },
    select: { id: true },
  });
  if (subjects.length === 0) return { ok: false, status: 404, error: "No valid subjects found" };
  return { ok: true, subjectIds: subjects.map((s) => s.id) };
}

function settingsData(settings: StudyPlanSettingsInput, subjectIds: string[]) {
  return {
    subjectIds,
    studyDays: [...new Set(settings.studyDays)].sort((a, b) => a - b),
    weekdayMinutes: settings.weekdayMinutes,
    weekendMinutes: settings.weekendMinutes,
    targetExam: settings.targetExam ?? null,
    targetDate: settings.targetDate ? dayKeyToDate(settings.targetDate) : null,
    forceExamMode: settings.forceExamMode,
  };
}

/** Retires any active plan and builds a new one. */
export async function createStudyPlan(
  userId: string,
  settings: StudyPlanSettingsInput,
): Promise<{ ok: true; planId: string } | Failure> {
  const checked = await checkSettings(userId, settings);
  if (!checked.ok) return checked;

  const plan = await db.$transaction(async (tx) => {
    await tx.studyPlan.updateMany({ where: { studentId: userId, isActive: true }, data: { isActive: false } });
    return tx.studyPlan.create({
      data: { studentId: userId, ...settingsData(settings, checked.subjectIds) },
      select: { id: true },
    });
  }, REPLAN_TRANSACTION);

  await replanIfStale(userId, { force: true });
  return { ok: true, planId: plan.id };
}

/** Changes the active plan's settings in place, keeping its history. */
export async function updateStudyPlanSettings(
  userId: string,
  settings: StudyPlanSettingsInput,
): Promise<{ ok: true } | Failure> {
  const checked = await checkSettings(userId, settings);
  if (!checked.ok) return checked;

  const { count } = await db.studyPlan.updateMany({
    where: { studentId: userId, isActive: true },
    data: settingsData(settings, checked.subjectIds),
  });
  if (count === 0) return { ok: false, status: 404, error: "No active study plan" };

  // Positions for subjects no longer in the plan would never be read again.
  await db.studyPlanPosition.deleteMany({
    where: { studyPlan: { studentId: userId, isActive: true }, subjectId: { notIn: checked.subjectIds } },
  });

  await replanIfStale(userId, { force: true });
  return { ok: true };
}

export async function setClassPositions(
  userId: string,
  positions: { subjectId: string; topicId: string | null }[],
): Promise<{ ok: true } | Failure> {
  const [plan, user] = await Promise.all([
    db.studyPlan.findFirst({ where: { studentId: userId, isActive: true }, select: { id: true, subjectIds: true } }),
    db.user.findUnique({ where: { id: userId }, select: { classLevel: true } }),
  ]);
  if (!plan) return { ok: false, status: 404, error: "No active study plan" };

  const planSubjects = new Set(asStringArray(plan.subjectIds));
  const classLevel = user?.classLevel ?? UNKNOWN_CLASS_LEVEL;
  const topicIds = positions.map((p) => p.topicId).filter((id): id is string => id !== null);
  const topics = await db.topic.findMany({
    where: { id: { in: topicIds } },
    select: { id: true, subjectId: true, curriculumLevel: { select: { classLevel: true } } },
  });
  const topicById = new Map(topics.map((t) => [t.id, t]));

  for (const position of positions) {
    if (!planSubjects.has(position.subjectId)) {
      return { ok: false, status: 400, error: "That subject is not in your plan." };
    }
    if (position.topicId === null) continue;
    const topic = topicById.get(position.topicId);
    if (
      !topic ||
      topic.subjectId !== position.subjectId ||
      !atOrBelowClass(topic.curriculumLevel, classLevel)
    ) {
      return { ok: false, status: 400, error: "That topic can't be chosen for this subject." };
    }
  }

  await db.$transaction(
    positions.map((position) =>
      position.topicId === null
        ? db.studyPlanPosition.deleteMany({ where: { studyPlanId: plan.id, subjectId: position.subjectId } })
        : db.studyPlanPosition.upsert({
            where: { studyPlanId_subjectId: { studyPlanId: plan.id, subjectId: position.subjectId } },
            create: { studyPlanId: plan.id, subjectId: position.subjectId, topicId: position.topicId },
            update: { topicId: position.topicId },
          }),
    ),
  );

  await replanIfStale(userId, { force: true });
  return { ok: true };
}

export async function setPlanItemStatus(
  userId: string,
  itemId: string,
  requested: ManualStatus,
): Promise<{ ok: true; status: string } | Failure> {
  const item = await db.studyPlanItem.findFirst({
    where: { id: itemId, studyPlan: { studentId: userId, isActive: true } },
    select: { id: true, scheduledDate: true, studyPlan: { select: { plannedThrough: true } } },
  });
  if (!item) return { ok: false, status: 404, error: "Session not found" };

  const change = manualStatusChange(
    { date: dateToDayKey(item.scheduledDate) },
    requested,
    lagosDayKey(new Date()),
    item.studyPlan.plannedThrough ? dateToDayKey(item.studyPlan.plannedThrough) : null,
  );
  if (!change.ok) return { ok: false, status: 400, error: change.error };

  const completed = change.status === "COMPLETED";
  await db.studyPlanItem.update({
    where: { id: item.id },
    data: {
      status: change.status,
      completionSource: completed ? "MANUAL" : null,
      completedAt: completed ? new Date() : null,
    },
  });
  return { ok: true, status: change.status };
}

export async function getStudyPlanPageData(userId: string): Promise<StudyPlanPageData> {
  // A failed re-plan must not take the page down: the previous window still shows.
  try {
    await replanIfStale(userId);
  } catch (error) {
    console.error("Study plan re-plan failed:", error);
  }

  const now = new Date();
  const today = lagosDayKey(now);
  const [user, terms, plan] = await Promise.all([
    db.user.findUnique({ where: { id: userId }, select: { track: true, classLevel: true } }),
    loadTermRanges(),
    db.studyPlan.findFirst({
      where: { studentId: userId, isActive: true },
      orderBy: { createdAt: "desc" },
      include: { positions: { select: { subjectId: true, topicId: true } } },
    }),
  ]);

  const subjects = await db.subject.findMany({
    where: { trackCategory: { in: [...relevantTrackCategories(user?.track)] } },
    orderBy: { name: "asc" },
    select: { id: true, name: true, code: true, slug: true },
  });

  const classLevel = user?.classLevel ?? null;
  const termContext = resolveTermContext(today, terms);
  const base = {
    today,
    classLevel,
    termLabel: termHeaderLabel(termContext),
    termSource: termContext.source,
    defaults: DEFAULT_MINUTES[classLevel ?? "SS1"],
    subjects,
  };
  if (!plan) return { ...base, daysToExam: null, plan: null };

  const subjectIds = asStringArray(plan.subjectIds);
  const effectiveClass = classLevel ?? UNKNOWN_CLASS_LEVEL;
  const [items, topicsBySubject] = await Promise.all([
    db.studyPlanItem.findMany({
      where: {
        studyPlanId: plan.id,
        scheduledDate: {
          gte: dayKeyToDate(addDays(today, -7)),
          ...(plan.plannedThrough ? { lte: plan.plannedThrough } : {}),
        },
      },
      orderBy: [{ scheduledDate: "asc" }, { id: "asc" }],
      include: {
        subject: { select: { name: true, code: true, slug: true } },
        topic: { select: { slug: true, title: true } },
      },
    }),
    loadPlanTopics(subjectIds),
  ]);

  const positionOptions: Record<string, PositionOption[]> = {};
  const calendarPositions: Record<string, string | null> = {};
  for (const subjectId of subjectIds) {
    const topics = (topicsBySubject.get(subjectId) ?? []).filter((t) => atOrBelowClass(t, effectiveClass));
    positionOptions[subjectId] = [...topics]
      .sort((a, b) =>
        a.classLevel.localeCompare(b.classLevel) ||
        ["FIRST", "SECOND", "THIRD"].indexOf(a.term) - ["FIRST", "SECOND", "THIRD"].indexOf(b.term) ||
        a.orderIndex - b.orderIndex)
      .map((t) => ({ id: t.id, title: t.title, scope: `${t.classLevel} ${TERM_LABELS[t.term]}` }));
    calendarPositions[subjectId] = calendarTopicId(topics, effectiveClass, termContext);
  }

  const targetDate = plan.targetDate ? dateToDayKey(plan.targetDate) : null;
  return {
    ...base,
    daysToExam: targetDate ? Math.max(0, daysBetween(today, targetDate)) : null,
    plan: {
      id: plan.id,
      mode: resolvePlanMode({ classLevel: effectiveClass, targetDate, forceExamMode: plan.forceExamMode }),
      subjectIds,
      studyDays: plan.studyDays,
      weekdayMinutes: plan.weekdayMinutes,
      weekendMinutes: plan.weekendMinutes,
      targetExam: plan.targetExam,
      targetDate,
      forceExamMode: plan.forceExamMode,
      plannedThrough: plan.plannedThrough ? dateToDayKey(plan.plannedThrough) : null,
      outline: (plan.outline as unknown as OutlineWeek[] | null) ?? [],
      overload: (plan.overload as unknown as Overload | null) ?? null,
      positions: Object.fromEntries(plan.positions.map((p) => [p.subjectId, p.topicId])),
      calendarPositions,
      positionOptions,
      items: items.map((item) => ({
        id: item.id,
        date: dateToDayKey(item.scheduledDate),
        subjectId: item.subjectId,
        topicId: item.topicId,
        topicSlug: item.topic?.slug ?? null,
        topicTitle: item.topic?.title ?? null,
        activityType: item.activityType,
        durationMinutes: item.durationMinutes,
        status: item.status,
        notes: item.notes,
        carriedFrom: item.carriedFromDate ? dateToDayKey(item.carriedFromDate) : null,
        completionSource: item.completionSource,
        subject: item.subject,
      })),
    },
  };
}
```

Notes for the implementer:
- `a.classLevel.localeCompare(b.classLevel)` sorts SS1 < SS2 < SS3 correctly because they share a prefix.
- If `tsc` rejects `Prisma.DbNull` for a `Json?` field, use `Prisma.JsonNull`.

- [ ] **Step 6: Type-check the new lib files**

Run: `npx tsc --noEmit 2>&1 | grep -E "src/lib/study-plan(-display)?\.ts"`
Expected: no output. The route and view errors are fixed in Tasks 13 and 15.

- [ ] **Step 7: Register and commit**

Append ` scripts/test-study-plan-display.mts` to the `"test"` script.

```bash
git add src/lib/study-plan.ts src/lib/study-plan-display.ts scripts/test-study-plan-display.mts package.json
git commit -m "Persist term-mode plans with a locked rolling re-plan

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 13: Student study plan API

**Files:**
- Create: `src/lib/study-plan-route.ts`
- Rewrite: `src/app/api/study-plan/route.ts`
- Create: `src/app/api/study-plan/positions/route.ts`
- Create: `src/app/api/study-plan/items/[id]/route.ts`

**Interfaces:**
- Consumes: `createStudyPlan`, `updateStudyPlanSettings`, `setClassPositions`, `setPlanItemStatus`, `getStudyPlanPageData` (Task 12); schemas (Task 10); `auth`, `denyUnlessEntitled`.
- Produces HTTP:
  - `GET /api/study-plan` → `StudyPlanPageData`
  - `POST /api/study-plan` (settings) → `201 { planId }`
  - `PATCH /api/study-plan` (settings) → `{ ok: true }`
  - `PUT /api/study-plan/positions` → `{ ok: true }`
  - `PATCH /api/study-plan/items/[id]` → `{ status }`
  - Errors → `{ error }` with 400, 401, 403 (entitlement) or 404.

- [ ] **Step 1: Create the shared guard `src/lib/study-plan-route.ts`**

```ts
import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { denyUnlessEntitled } from "@/lib/entitlements";

/**
 * Signed in and entitled to the study planner. The planner is a paid feature,
 * enforced here: hiding the page does not stop a direct call to these routes.
 */
export async function requireStudyPlanner(): Promise<
  { ok: true; userId: string } | { ok: false; response: NextResponse }
> {
  const session = await auth();
  if (!session?.user?.id) {
    return { ok: false, response: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }
  const denied = await denyUnlessEntitled(session, "studyPlanner");
  if (denied) return { ok: false, response: denied };
  return { ok: true, userId: session.user.id };
}
```

- [ ] **Step 2: Rewrite `src/app/api/study-plan/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireStudyPlanner } from "@/lib/study-plan-route";
import { studyPlanSettingsSchema } from "@/lib/validators";
import { createStudyPlan, getStudyPlanPageData, updateStudyPlanSettings } from "@/lib/study-plan";

export const dynamic = "force-dynamic";

// GET /api/study-plan — the plan page payload (re-plans first if stale)
export async function GET() {
  try {
    const g = await requireStudyPlanner();
    if (!g.ok) return g.response;
    return NextResponse.json(await getStudyPlanPageData(g.userId));
  } catch (error) {
    console.error("Error fetching study plan:", error);
    return NextResponse.json({ error: "Failed to fetch study plan" }, { status: 500 });
  }
}

// POST /api/study-plan — create a plan, retiring the active one
export async function POST(req: NextRequest) {
  try {
    const g = await requireStudyPlanner();
    if (!g.ok) return g.response;

    const parsed = studyPlanSettingsSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const result = await createStudyPlan(g.userId, parsed.data);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
    return NextResponse.json({ planId: result.planId }, { status: 201 });
  } catch (error) {
    console.error("Error creating study plan:", error);
    return NextResponse.json({ error: "Failed to create study plan" }, { status: 500 });
  }
}

// PATCH /api/study-plan — change the active plan's settings and re-plan
export async function PATCH(req: NextRequest) {
  try {
    const g = await requireStudyPlanner();
    if (!g.ok) return g.response;

    const parsed = studyPlanSettingsSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const result = await updateStudyPlanSettings(g.userId, parsed.data);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Error updating study plan:", error);
    return NextResponse.json({ error: "Failed to update study plan" }, { status: 500 });
  }
}
```

- [ ] **Step 3: Create `src/app/api/study-plan/positions/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireStudyPlanner } from "@/lib/study-plan-route";
import { studyPlanPositionsSchema } from "@/lib/validators";
import { setClassPositions } from "@/lib/study-plan";

export const dynamic = "force-dynamic";

// PUT /api/study-plan/positions — "my class is on topic X" per subject; null follows the calendar
export async function PUT(req: NextRequest) {
  try {
    const g = await requireStudyPlanner();
    if (!g.ok) return g.response;

    const parsed = studyPlanPositionsSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const result = await setClassPositions(g.userId, parsed.data.positions);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Error saving class positions:", error);
    return NextResponse.json({ error: "Failed to save" }, { status: 500 });
  }
}
```

- [ ] **Step 4: Create `src/app/api/study-plan/items/[id]/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireStudyPlanner } from "@/lib/study-plan-route";
import { studyPlanItemStatusSchema } from "@/lib/validators";
import { setPlanItemStatus } from "@/lib/study-plan";

export const dynamic = "force-dynamic";

// PATCH /api/study-plan/items/[id] — tick, skip or undo one session
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const g = await requireStudyPlanner();
    if (!g.ok) return g.response;
    const { id } = await params;

    const parsed = studyPlanItemStatusSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const result = await setPlanItemStatus(g.userId, id, parsed.data.status);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
    return NextResponse.json({ status: result.status });
  } catch (error) {
    console.error("Error updating study plan session:", error);
    return NextResponse.json({ error: "Failed to update session" }, { status: 500 });
  }
}
```

- [ ] **Step 5: Type-check the routes**

Run: `npx tsc --noEmit 2>&1 | grep -E "api/study-plan|study-plan-route"`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add src/app/api/study-plan src/lib/study-plan-route.ts
git commit -m "Add study plan settings, position and session APIs

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 14: Automatic completion hooks

**Files:**
- Create: `src/lib/study-plan-completion.ts`
- Modify: `src/app/api/assessments/submit/route.ts` (the `after()` blocks, ~lines 53–91)
- Modify: `src/app/api/flashcards/review/route.ts` (after a successful `recordFlashcardReview`)

**Interfaces:**
- Consumes: `pickItemToComplete`, `signalForAssessment`, `CompletionSignal` (Task 9); `dateToDayKey`, `dayKeyToDate` (Task 2); `lagosDayKey`.
- Produces:
  - `completePlanItemFor(studentId: string, signal: CompletionSignal): Promise<void>`: never throws
  - `markPlanFromAttempt(studentId: string, attemptId: string, practiceExit: boolean): Promise<void>`
  - `markPlanFromLesson(studentId: string, subjectSlug: string, topicSlug: string): Promise<void>`
  - `markPlanFromCardReview(studentId: string, flashcardId: string): Promise<void>`

- [ ] **Step 1: Write `src/lib/study-plan-completion.ts`**

```ts
import { db } from "./db";
import { lagosDayKey } from "./streak";
import { addDays, dateToDayKey, dayKeyToDate } from "@/engines/planner/days";
import {
  pickItemToComplete,
  signalForAssessment,
  type CompletionSignal,
} from "@/engines/planner/completion";
import { CARRY_OVER_DAYS } from "@/engines/planner/replan";

// Marks study plan sessions done from real learning activity. Best-effort: a
// failure here is logged and never fails the lesson, quiz or review itself.

export async function completePlanItemFor(studentId: string, signal: CompletionSignal): Promise<void> {
  try {
    const today = lagosDayKey(new Date());
    const items = await db.studyPlanItem.findMany({
      where: {
        status: "PENDING",
        studyPlan: { studentId, isActive: true },
        scheduledDate: { gte: dayKeyToDate(addDays(today, -CARRY_OVER_DAYS)), lte: dayKeyToDate(today) },
      },
      select: { id: true, scheduledDate: true, subjectId: true, topicId: true, activityType: true, status: true },
    });
    const id = pickItemToComplete(
      items.map((row) => ({ ...row, date: dateToDayKey(row.scheduledDate) })),
      signal,
      today,
    );
    if (!id) return;
    // Compare-and-set on PENDING so a manual tick racing this is never overwritten.
    await db.studyPlanItem.updateMany({
      where: { id, status: "PENDING" },
      data: { status: "COMPLETED", completionSource: "AUTO", completedAt: new Date() },
    });
  } catch (error) {
    console.error("Study plan auto-completion failed:", error);
  }
}

export async function markPlanFromAttempt(
  studentId: string,
  attemptId: string,
  practiceExit: boolean,
): Promise<void> {
  try {
    const attempt = await db.assessmentAttempt.findFirst({
      where: { id: attemptId, studentId, status: "COMPLETED" },
      select: {
        assessment: {
          select: {
            assessmentType: true,
            subjectId: true,
            questions: { select: { question: { select: { topicId: true } } } },
          },
        },
      },
    });
    if (!attempt) return;
    const topicIds = [
      ...new Set(
        attempt.assessment.questions
          .map((q) => q.question.topicId)
          .filter((id): id is string => id !== null),
      ),
    ];
    const signal = signalForAssessment({
      assessmentType: attempt.assessment.assessmentType,
      subjectId: attempt.assessment.subjectId,
      topicIds,
      practiceExit,
    });
    if (signal) await completePlanItemFor(studentId, signal);
  } catch (error) {
    console.error("Study plan attempt completion failed:", error);
  }
}

export async function markPlanFromLesson(
  studentId: string,
  subjectSlug: string,
  topicSlug: string,
): Promise<void> {
  try {
    const topic = await db.topic.findFirst({
      where: { slug: topicSlug, subject: { slug: subjectSlug } },
      select: { id: true },
    });
    if (topic) await completePlanItemFor(studentId, { kind: "LESSON_COMPLETED", topicId: topic.id });
  } catch (error) {
    console.error("Study plan lesson completion failed:", error);
  }
}

export async function markPlanFromCardReview(studentId: string, flashcardId: string): Promise<void> {
  try {
    const card = await db.flashcard.findUnique({
      where: { id: flashcardId },
      select: {
        deck: {
          select: {
            topicId: true,
            lesson: { select: { subtopic: { select: { topicId: true } } } },
          },
        },
      },
    });
    const topicId = card?.deck.topicId ?? card?.deck.lesson?.subtopic?.topicId ?? null;
    if (topicId) await completePlanItemFor(studentId, { kind: "CARD_REVIEWED", topicId });
  } catch (error) {
    console.error("Study plan card completion failed:", error);
  }
}
```

Before relying on the flashcard query, check the relation path in `src/lib/flashcards.ts:509–530`. It reads `deck.topicId` and `deck.lesson.subtopic.topic.id`, so `subtopic.topicId` should exist, but confirm the field name in `prisma/schema.prisma` (`model Subtopic`). Also confirm `Topic` has `subject` and `slug` as used in `markPlanFromLesson`.

- [ ] **Step 2: Hook the assessment submit route**

In `src/app/api/assessments/submit/route.ts`, add the import:

```ts
import { markPlanFromAttempt, markPlanFromLesson } from "@/lib/study-plan-completion";
```

In the `if (outcome.outcome === "graded")` block, inside the existing `after(async () => { … })` and after the `awardAchievements` try/catch, add:

```ts
        await markPlanFromAttempt(studentId, attemptId, Boolean(practiceExit));
```

In the `practiceExit` block, replace the `if (recorded.status !== "ok") { … }` statement with:

```ts
          if (recorded.status !== "ok") {
            console.error(
              `Practice exit not recorded (${recorded.status}) for attempt ${attemptId}`,
            );
          } else if (recorded.result.passed) {
            await markPlanFromLesson(studentId, practiceExit.subjectSlug, practiceExit.topicSlug);
          }
```

- [ ] **Step 3: Hook the flashcard review route**

In `src/app/api/flashcards/review/route.ts`, change the first import line to `import { NextRequest, NextResponse, after } from "next/server";`. Add:

```ts
import { markPlanFromCardReview } from "@/lib/study-plan-completion";
```

Just before `return NextResponse.json(result);`, add:

```ts
    const studentId = session.user.id;
    const { flashcardId } = parsed.data;
    // After the response: plan bookkeeping must not slow down the review loop.
    after(() => markPlanFromCardReview(studentId, flashcardId));
```

- [ ] **Step 4: Type-check and run the full test suite**

Run: `npx tsc --noEmit 2>&1 | grep -E "study-plan-completion|assessments/submit|flashcards/review"`
Expected: no output.

Run: `npm test`
Expected: all suites pass.

- [ ] **Step 5: Commit**

```bash
git add src/lib/study-plan-completion.ts src/app/api/assessments/submit/route.ts src/app/api/flashcards/review/route.ts
git commit -m "Mark study plan sessions done from lessons, quizzes and reviews

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 15: Student plan page

**Files:**
- Rewrite: `src/components/study-plan/study-plan-view.tsx`
- Create: `src/components/study-plan/plan-setup-form.tsx`
- Create: `src/components/study-plan/plan-item-row.tsx`
- Create: `src/components/study-plan/plan-schedule.tsx`
- Create: `src/components/study-plan/class-position-panel.tsx`
- Modify: `src/app/(dashboard)/study-plan/page.tsx`

**Interfaces:**
- Consumes: `StudyPlanPageData`, `StudyPlanData`, `StudyPlanItemData`, `StudyPlanSubject` (Task 12); `planItemHref`, `groupWindow`, `ACTIVITY_LABELS` (Task 12); the HTTP API (Task 13); UI primitives `Badge`, `PageHeader`, `EmptyState`, `buttonClass`, `cn`, and the `card` / `label` / `input` CSS classes already used by the current view.
- Produces: `<StudyPlanView data={StudyPlanPageData} />`

Every mutation calls the API and then `router.refresh()`, which re-runs the server page (and its re-plan). No client copy of the plan is kept.

- [ ] **Step 1: Update the page**

Replace the body of `src/app/(dashboard)/study-plan/page.tsx` after the entitlement check. Change the upgrade `PageHeader` description to the new copy as well:

```tsx
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { isEntitled, tierOfSession } from "@/lib/entitlements";
import { requiredTierFor } from "@/lib/subscription";
import { PageHeader } from "@/components/ui/page-header";
import { UpgradePrompt } from "@/components/billing/upgrade-prompt";
import { getStudyPlanPageData } from "@/lib/study-plan";
import { StudyPlanView } from "@/components/study-plan/study-plan-view";

const DESCRIPTION =
  "A realistic weekly schedule that keeps you in step with your class — and gets you exam-ready when it's time.";

export default async function StudyPlanPage() {
  const session = await auth();
  if (!session?.user?.id) redirect("/login");

  if (!(await isEntitled(session.user.id, tierOfSession(session), "studyPlanner"))) {
    return (
      <div className="space-y-8">
        <PageHeader title="Study plan" description={DESCRIPTION} />
        <UpgradePrompt
          feature="The study planner"
          requiredTier={requiredTierFor("studyPlanner")}
          description="Get a week-by-week plan that follows your school term, fills the gaps you've missed, and moves missed sessions instead of letting them pile up."
        />
      </div>
    );
  }

  return <StudyPlanView data={await getStudyPlanPageData(session.user.id)} />;
}
```

- [ ] **Step 2: Create `plan-item-row.tsx`**

```tsx
"use client";

import Link from "next/link";
import { useState } from "react";
import { LuCheck, LuRotateCcw, LuSkipForward } from "react-icons/lu";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { ACTIVITY_LABELS, planItemHref } from "@/lib/study-plan-display";
import type { StudyPlanItemData } from "@/lib/study-plan";

const TONE: Record<string, "blue" | "green" | "purple" | "amber" | "red"> = {
  LESSON: "blue",
  PRACTICE: "green",
  REVISION: "purple",
  PAST_QUESTIONS: "amber",
  MOCK_EXAM: "red",
};

function weekdayName(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "long", timeZone: "UTC" });
}

export function PlanItemRow({
  item,
  onStatus,
}: {
  item: StudyPlanItemData;
  onStatus: (id: string, status: "COMPLETED" | "SKIPPED" | "PENDING") => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const done = item.status === "COMPLETED";
  const skipped = item.status === "SKIPPED";
  const missed = item.status === "MISSED";

  async function change(status: "COMPLETED" | "SKIPPED" | "PENDING") {
    setBusy(true);
    try {
      await onStatus(item.id, status);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-xl border px-3 py-2.5",
        done ? "border-tone-green-line bg-tone-green-soft/70" : "border-border bg-secondary/30",
        (skipped || missed) && "opacity-70",
      )}
    >
      <Badge variant={done ? "green" : (TONE[item.activityType] ?? "neutral")}>
        {ACTIVITY_LABELS[item.activityType] ?? item.activityType}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className={cn("truncate text-sm font-semibold", done ? "text-tone-green-ink" : "text-foreground")}>
          {item.subject.code || item.subject.name}
          {item.topicTitle ? ` — ${item.topicTitle}` : ""}
        </p>
        <p className="truncate text-xs text-muted">
          {item.durationMinutes} min
          {item.notes ? ` · ${item.notes}` : ""}
          {item.carriedFrom ? ` · Moved from ${weekdayName(item.carriedFrom)}` : ""}
          {done && item.completionSource === "AUTO" ? " · Done automatically" : ""}
          {skipped ? " · Skipped" : ""}
          {missed ? " · Missed" : ""}
        </p>
      </div>
      <div className="flex items-center gap-1">
        {!done && !skipped && (
          <Link href={planItemHref(item)} className="rounded-lg px-2.5 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10">
            Open
          </Link>
        )}
        {done || skipped ? (
          <button type="button" disabled={busy} onClick={() => change("PENDING")}
            className="rounded-lg p-1.5 text-muted hover:bg-secondary" aria-label="Undo">
            <LuRotateCcw className="h-4 w-4" />
          </button>
        ) : (
          <>
            <button type="button" disabled={busy} onClick={() => change("COMPLETED")}
              className="rounded-lg p-1.5 text-success hover:bg-success/10" aria-label="Mark done">
              <LuCheck className="h-4 w-4" />
            </button>
            {!missed && (
              <button type="button" disabled={busy} onClick={() => change("SKIPPED")}
                className="rounded-lg p-1.5 text-muted hover:bg-secondary" aria-label="Skip">
                <LuSkipForward className="h-4 w-4" />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
```

If `LuRotateCcw` or `LuSkipForward` is missing from the installed `react-icons` version, run `grep -o "LuRotateCcw\|LuSkipForward\|LuUndo2" node_modules/react-icons/lu/index.d.ts` and use an icon that exists.

- [ ] **Step 3: Create `plan-schedule.tsx`**

```tsx
"use client";

import { Badge } from "@/components/ui/badge";
import { groupWindow } from "@/lib/study-plan-display";
import type { StudyPlanData, StudyPlanItemData } from "@/lib/study-plan";
import { PlanItemRow } from "./plan-item-row";

type OnStatus = (id: string, status: "COMPLETED" | "SKIPPED" | "PENDING") => Promise<void>;

function dayTitle(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("en-GB", {
    weekday: "short", day: "numeric", month: "short", timeZone: "UTC",
  });
}

function DayList({ title, days, onStatus }: { title: string; days: { date: string; items: StudyPlanItemData[] }[]; onStatus: OnStatus }) {
  if (days.length === 0) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-bold uppercase tracking-wider text-muted">{title}</h2>
      {days.map((day) => {
        const done = day.items.filter((i) => i.status === "COMPLETED").length;
        const catchUp = day.items.some((i) => i.carriedFrom);
        return (
          <div key={day.date} className="card p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-xs font-bold text-foreground">{dayTitle(day.date)}</span>
              <div className="flex gap-2">
                {catchUp && <Badge variant="amber">Catch-up</Badge>}
                <Badge variant={done === day.items.length ? "green" : "neutral"}>{done}/{day.items.length}</Badge>
              </div>
            </div>
            <div className="space-y-2">
              {day.items.map((item) => <PlanItemRow key={item.id} item={item} onStatus={onStatus} />)}
            </div>
          </div>
        );
      })}
    </section>
  );
}

export function PlanSchedule({ plan, today, onStatus }: { plan: StudyPlanData; today: string; onStatus: OnStatus }) {
  const groups = groupWindow(plan.items, today);
  const todayDone = groups.today.filter((i) => i.status === "COMPLETED").length;

  return (
    <div className="space-y-8">
      <section className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold tracking-tight text-foreground">Today</h2>
          {groups.today.length > 0 && (
            <span className="text-sm font-semibold text-muted">{todayDone} of {groups.today.length} done</span>
          )}
        </div>
        {groups.today.length === 0 ? (
          <p className="text-sm text-muted">Nothing planned today — rest, or get ahead from this week's list.</p>
        ) : (
          <div className="space-y-2">
            {groups.today.map((item) => <PlanItemRow key={item.id} item={item} onStatus={onStatus} />)}
          </div>
        )}
      </section>

      {groups.recentMissed.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted">Missed this week</h2>
          <p className="text-xs text-muted">These have been moved to your next free slots. Studied offline? Tick them off.</p>
          {groups.recentMissed.map((item) => <PlanItemRow key={item.id} item={item} onStatus={onStatus} />)}
        </section>
      )}

      <DayList title="This week" days={groups.thisWeek} onStatus={onStatus} />
      <DayList title="Next week" days={groups.nextWeek} onStatus={onStatus} />

      {plan.outline.length > 0 && (
        <details className="card p-5">
          <summary className="cursor-pointer text-sm font-bold text-foreground">Rest of the plan</summary>
          <ul className="mt-4 space-y-3">
            {plan.outline.map((week) => (
              <li key={week.weekStart} className="text-sm">
                <span className="font-semibold text-foreground">Week of {dayTitle(week.weekStart)}</span>
                <span className="text-muted">
                  {" — "}
                  {week.label ?? (week.topics.map((t) => t.title).join(", ") || "Revision")}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create `class-position-panel.tsx`**

```tsx
"use client";

import { useState } from "react";
import { buttonClass } from "@/components/ui/button";
import type { StudyPlanData, StudyPlanSubject } from "@/lib/study-plan";

const FOLLOW = "";

export function ClassPositionPanel({
  plan,
  subjects,
  onSave,
}: {
  plan: StudyPlanData;
  subjects: StudyPlanSubject[];
  onSave: (positions: { subjectId: string; topicId: string | null }[]) => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(plan.subjectIds.map((id) => [id, plan.positions[id] ?? FOLLOW])),
  );
  const [saving, setSaving] = useState(false);
  const names = Object.fromEntries(subjects.map((s) => [s.id, s.name]));

  async function save() {
    setSaving(true);
    try {
      await onSave(plan.subjectIds.map((subjectId) => ({ subjectId, topicId: values[subjectId] || null })));
    } finally {
      setSaving(false);
    }
  }

  return (
    <details className="card p-5">
      <summary className="cursor-pointer text-sm font-bold text-foreground">Where is your class?</summary>
      <p className="mt-2 text-xs text-muted">
        We guess from the school calendar. If your teacher is ahead or behind, pick the topic your class is on now.
      </p>
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        {plan.subjectIds.map((subjectId) => {
          const guessId = plan.calendarPositions[subjectId];
          const options = plan.positionOptions[subjectId] ?? [];
          const guess = options.find((o) => o.id === guessId);
          return (
            <label key={subjectId} className="text-sm">
              <span className="label">{names[subjectId] ?? "Subject"}</span>
              <select className="input" value={values[subjectId]}
                onChange={(e) => setValues({ ...values, [subjectId]: e.target.value })}>
                <option value={FOLLOW}>
                  Follow the calendar{guess ? ` (${guess.title})` : ""}
                </option>
                {options.map((o) => (
                  <option key={o.id} value={o.id}>{o.scope} — {o.title}</option>
                ))}
              </select>
              {!guessId && (
                <span className="mt-1 block text-xs text-muted">
                  No topics for this term yet in {names[subjectId] ?? "this subject"} — revision only.
                </span>
              )}
            </label>
          );
        })}
      </div>
      <button type="button" disabled={saving} onClick={save} className={buttonClass("primary", "md", "mt-4")}>
        {saving ? "Updating plan..." : "Update my plan"}
      </button>
    </details>
  );
}
```

- [ ] **Step 5: Create `plan-setup-form.tsx`**

```tsx
"use client";

import { useState } from "react";
import { LuCheck, LuSparkles } from "react-icons/lu";
import { buttonClass } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { StudyPlanData, StudyPlanPageData } from "@/lib/study-plan";

const DAYS = [
  { value: 1, label: "Mon" }, { value: 2, label: "Tue" }, { value: 3, label: "Wed" },
  { value: 4, label: "Thu" }, { value: 5, label: "Fri" }, { value: 6, label: "Sat" }, { value: 7, label: "Sun" },
];

export type PlanSettings = {
  subjectIds: string[];
  studyDays: number[];
  weekdayMinutes: number;
  weekendMinutes: number;
  targetExam: "WAEC" | "JAMB" | "NECO" | null;
  targetDate: string | null;
  forceExamMode: boolean;
};

function chip(selected: boolean) {
  return cn(
    "rounded-full border px-3.5 py-1.5 text-xs font-semibold transition-all",
    selected
      ? "border-primary bg-primary text-primary-foreground shadow-soft"
      : "border-border bg-card text-muted hover:border-primary/30",
  );
}

export function PlanSetupForm({
  data,
  plan,
  onSubmit,
  onCancel,
}: {
  data: StudyPlanPageData;
  plan: StudyPlanData | null;
  onSubmit: (settings: PlanSettings) => Promise<string | null>;
  onCancel?: () => void;
}) {
  const [settings, setSettings] = useState<PlanSettings>(() => ({
    subjectIds: plan?.subjectIds ?? [],
    studyDays: plan?.studyDays ?? [1, 2, 3, 4, 6],
    weekdayMinutes: plan?.weekdayMinutes ?? data.defaults.weekdayMinutes,
    weekendMinutes: plan?.weekendMinutes ?? data.defaults.weekendMinutes,
    targetExam: (plan?.targetExam as PlanSettings["targetExam"]) ?? null,
    targetDate: plan?.targetDate ?? null,
    forceExamMode: plan?.forceExamMode ?? false,
  }));
  const [preparing, setPreparing] = useState(Boolean(plan?.targetDate));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const isSS3 = data.classLevel === "SS3";

  const toggle = <K extends "subjectIds" | "studyDays">(key: K, value: PlanSettings[K][number]) =>
    setSettings((s) => {
      const list = s[key] as (string | number)[];
      const next = list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
      return { ...s, [key]: next };
    });

  async function submit() {
    setSaving(true);
    setError("");
    const payload = preparing && isSS3
      ? settings
      : { ...settings, targetExam: null, targetDate: null, forceExamMode: false };
    const problem = await onSubmit(payload);
    setSaving(false);
    if (problem) setError(problem);
  }

  return (
    <div className="card mb-8 p-6">
      <h2 className="text-lg font-bold tracking-tight text-foreground">
        {plan ? "Change your plan" : "Set up your study plan"}
      </h2>
      <p className="mt-1 text-sm text-muted">
        {data.classLevel ? `For ${data.classLevel}` : "For your class"} — change your class in Settings.
        Tell us when you can study and we'll keep it realistic.
      </p>

      <div className="mt-6 space-y-6">
        <div>
          <span className="label">Subjects</span>
          <div className="flex flex-wrap gap-2">
            {data.subjects.map((subject) => {
              const selected = settings.subjectIds.includes(subject.id);
              return (
                <button key={subject.id} type="button" aria-pressed={selected}
                  onClick={() => toggle("subjectIds", subject.id)} className={chip(selected)}>
                  {selected && <LuCheck className="mr-1 inline h-3 w-3" />}
                  {subject.code || subject.name}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <span className="label">Study days</span>
          <div className="flex flex-wrap gap-2">
            {DAYS.map((day) => {
              const selected = settings.studyDays.includes(day.value);
              return (
                <button key={day.value} type="button" aria-pressed={selected}
                  onClick={() => toggle("studyDays", day.value)} className={chip(selected)}>
                  {day.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <label>
            <span className="label">Minutes on a school day</span>
            <input className="input" type="number" min={0} max={480} step={15} value={settings.weekdayMinutes}
              onChange={(e) => setSettings({ ...settings, weekdayMinutes: Number(e.target.value) || 0 })} />
          </label>
          <label>
            <span className="label">Minutes on a weekend day</span>
            <input className="input" type="number" min={0} max={600} step={15} value={settings.weekendMinutes}
              onChange={(e) => setSettings({ ...settings, weekendMinutes: Number(e.target.value) || 0 })} />
          </label>
        </div>
        <p className="-mt-3 text-xs text-muted">Keep it realistic — a plan you keep beats a plan you abandon.</p>

        {isSS3 && (
          <div className="rounded-xl border border-border p-4">
            <label className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <input type="checkbox" checked={preparing} onChange={(e) => setPreparing(e.target.checked)} />
              Preparing for an exam?
            </label>
            {preparing && (
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <span className="label">Exam</span>
                  <div className="flex gap-2">
                    {(["WAEC", "JAMB", "NECO"] as const).map((exam) => (
                      <button key={exam} type="button" aria-pressed={settings.targetExam === exam}
                        onClick={() => setSettings({ ...settings, targetExam: exam })}
                        className={cn(chip(settings.targetExam === exam), "flex-1 py-2.5")}>
                        {exam}
                      </button>
                    ))}
                  </div>
                </div>
                <label>
                  <span className="label">Exam date</span>
                  <input className="input" type="date" value={settings.targetDate ?? ""}
                    onChange={(e) => setSettings({ ...settings, targetDate: e.target.value || null })} />
                </label>
                <label className="flex items-center gap-2 text-sm md:col-span-2">
                  <input type="checkbox" checked={settings.forceExamMode}
                    onChange={(e) => setSettings({ ...settings, forceExamMode: e.target.checked })} />
                  Exam mode now — pause new term topics and focus on exam revision
                </label>
              </div>
            )}
          </div>
        )}
      </div>

      {error && <p className="mt-4 text-sm text-danger">{error}</p>}

      <div className="mt-6 flex gap-3">
        <button type="button" onClick={submit}
          disabled={saving || settings.subjectIds.length === 0 || settings.studyDays.length === 0}
          className={buttonClass("primary", "lg")}>
          <LuSparkles className="h-4 w-4" />
          {saving ? "Building your plan..." : plan ? "Update plan" : "Create plan"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className={buttonClass("secondary", "lg")}>Cancel</button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Rewrite `study-plan-view.tsx`**

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LuCalendar, LuPlus, LuSettings2, LuTriangleAlert } from "react-icons/lu";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { buttonClass } from "@/components/ui/button";
import type { StudyPlanPageData } from "@/lib/study-plan";
import { ClassPositionPanel } from "./class-position-panel";
import { PlanSchedule } from "./plan-schedule";
import { PlanSetupForm, type PlanSettings } from "./plan-setup-form";

/**
 * Everything arrives from the server page, which re-plans first. Mutations call
 * the API and refresh, so the server stays the only copy of the plan.
 */
export function StudyPlanView({ data }: { data: StudyPlanPageData }) {
  const router = useRouter();
  const { plan } = data;
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");

  async function request(url: string, method: string, body: unknown): Promise<string | null> {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}));
      return payload.error ?? "Something went wrong. Please try again.";
    }
    router.refresh();
    return null;
  }

  async function saveSettings(settings: PlanSettings) {
    const problem = await request("/api/study-plan", plan ? "PATCH" : "POST", settings);
    if (!problem) setEditing(false);
    return problem;
  }

  async function setStatus(id: string, status: "COMPLETED" | "SKIPPED" | "PENDING") {
    setError((await request(`/api/study-plan/items/${id}`, "PATCH", { status })) ?? "");
  }

  async function savePositions(positions: { subjectId: string; topicId: string | null }[]) {
    setError((await request("/api/study-plan/positions", "PUT", { positions })) ?? "");
  }

  const description = [
    data.classLevel,
    data.termLabel,
    plan?.targetExam && data.daysToExam !== null ? `${plan.targetExam} in ${data.daysToExam} days` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Study Plan"
        description={description}
        action={
          plan && !editing && (
            <button type="button" onClick={() => setEditing(true)} className={buttonClass("secondary", "md")}>
              <LuSettings2 className="h-4 w-4" />
              Change plan
            </button>
          )
        }
      />

      {error && <p className="mb-4 text-sm text-danger">{error}</p>}

      {editing && (
        <PlanSetupForm data={data} plan={plan} onSubmit={saveSettings} onCancel={plan ? () => setEditing(false) : undefined} />
      )}

      {!plan && !editing && (
        <EmptyState
          tone="primary"
          icon={<LuCalendar className="h-6 w-6" />}
          title="No study plan yet"
          description="Pick your subjects and when you can study. We'll plan your term week by week."
          action={
            <button type="button" onClick={() => setEditing(true)} className={buttonClass("primary", "lg")}>
              <LuPlus className="h-4 w-4" />
              Create a study plan
            </button>
          }
        />
      )}

      {plan && !editing && (
        <div className="space-y-6">
          <div className="flex flex-wrap gap-2">
            {plan.mode === "BLENDED" && <Badge variant="purple">Term + exam prep</Badge>}
            {plan.mode === "EXAM" && <Badge variant="red">Exam mode</Badge>}
            {data.termSource === "fallback" && <Badge variant="neutral">Term dates are approximate</Badge>}
          </div>

          {plan.overload && (
            <div role="status" className="flex items-start gap-3 rounded-xl border border-amber-300/50 bg-amber-50/60 px-4 py-3 text-sm">
              <LuTriangleAlert className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
              <p>
                You're about {plan.overload.topicsBehind} {plan.overload.topicsBehind === 1 ? "topic" : "topics"} behind.
                Adding {plan.overload.suggestedExtraMinutesPerWeek} minutes a week would help you catch up.
              </p>
            </div>
          )}

          <PlanSchedule plan={plan} today={data.today} onStatus={setStatus} />
          <ClassPositionPanel plan={plan} subjects={data.subjects} onSave={savePositions} />
        </div>
      )}
    </div>
  );
}
```

If the `amber-*` utilities don't match the design tokens, reuse the `tone-*` classes that `PlanItemRow` uses, or `StatusBanner`'s tone classes.

- [ ] **Step 7: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no errors anywhere.

Run: `npm run lint`
Expected: no errors in `src/components/study-plan/`, `src/lib/study-plan*`, `src/engines/planner/`.

- [ ] **Step 8: Commit**

```bash
git add src/components/study-plan "src/app/(dashboard)/study-plan/page.tsx"
git commit -m "Rebuild the study plan page around the term plan

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 16: Dashboard "today" count

**Files:**
- Modify: `src/lib/dashboard.ts` (`loadStats` ~lines 74–165, the returned data ~line 277, and the `DashboardData` type ~line 46)
- Modify: `src/app/(dashboard)/dashboard/page.tsx` (~lines 184–189)

**Interfaces:**
- Consumes: `lagosDayKey`, `dayKeyToDate`.
- Produces: `DashboardData.todayPlan: { done: number; total: number } | null`

- [ ] **Step 1: Count today's sessions in `loadStats`**

In `src/lib/dashboard.ts`, add the imports:

```ts
import { lagosDayKey } from "./streak";
import { dayKeyToDate } from "@/engines/planner/days";
```

At the top of `loadStats`, add:

```ts
  const todayWhere = {
    scheduledDate: dayKeyToDate(lagosDayKey(new Date())),
    studyPlan: { studentId: userId, isActive: true },
  } as const;
```

Add two entries at the **end** of the `db.$transaction([...])` array:

```ts
    db.studyPlanItem.count({ where: todayWhere }),
    db.studyPlanItem.count({ where: { ...todayWhere, status: "COMPLETED" } }),
```

and name them at the end of the destructuring, after `activePlanCount,`:

```ts
    todayPlanTotal,
    todayPlanDone,
```

In the object `loadStats` returns, after `hasStudyPlan: activePlanCount > 0,`, add:

```ts
    todayPlan: todayPlanTotal > 0 ? { done: todayPlanDone, total: todayPlanTotal } : null,
```

- [ ] **Step 2: Pass it through**

In the `DashboardData` type, after `hasStudyPlan: boolean;`, add:

```ts
  /** Today's study plan sessions, when the active plan has any today. */
  todayPlan: { done: number; total: number } | null;
```

Where the data is assembled (after `hasStudyPlan: stats.hasStudyPlan,`), add `todayPlan: stats.todayPlan,`. If the page destructures `hasStudyPlan` from the data, add `todayPlan` next to it.

- [ ] **Step 3: Show it on the hero link**

In `src/app/(dashboard)/dashboard/page.tsx`, replace the link text `{hasStudyPlan ? "View study plan" : "Create study plan"}` with:

```tsx
                {todayPlan
                  ? `Today: ${todayPlan.done} of ${todayPlan.total} done`
                  : hasStudyPlan
                    ? "View study plan"
                    : "Create study plan"}
```

- [ ] **Step 4: Type-check and commit**

Run: `npx tsc --noEmit`
Expected: no errors.

```bash
git add src/lib/dashboard.ts "src/app/(dashboard)/dashboard/page.tsx"
git commit -m "Show today's study plan progress on the dashboard

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GzW4W7LN2GBNkSGxM7siaZ"
```

---

### Task 17: Verification, migration and rollout

**Files:** none new, apart from fixes found here.

- [ ] **Step 1: Run every automated check**

Run: `npm test`
Expected: all suites pass, including the 10 new `test-study-plan-*` files.

Run: `npm run typecheck:tests`
Expected: no errors.

Run: `npx tsc --noEmit`
Expected: no errors.

Run: `npm run lint`
Expected: no errors.

Run: `npm run build`
Expected: build succeeds. Stop the dev server first if Prisma reports EPERM.

- [ ] **Step 2: Apply the migrations to Supabase in three stages: additive → deploy → drop**

This step changes the live database. **Ask the user before running it**, and do it only when they are ready to deploy this branch.

The change is split into two migrations so there is always a safe order:

- `20260914000000_study_plan_term_mode` is **additive**: new tables, enum values, nullable/defaulted columns, and a copy of `dailyStudyHours` into `weekdayMinutes`/`weekendMinutes`. The old code keeps working against it (it still reads and writes `dailyStudyHours`).
- `20260914000001_drop_daily_study_hours` only drops `dailyStudyHours`. It must wait until no running instance uses the old code. Until then the column lingers harmlessly: it is `NOT NULL DEFAULT 2`, so inserts from the new client (which has no such field) stay valid.

**Stage A — additive migration (before deploying):**

1. Confirm the file is LF-only: `tr -cd '\r' < prisma/migrations/20260914000000_study_plan_term_mode/migration.sql | wc -c` → `0`.
2. Compute the checksum: `sha256sum prisma/migrations/20260914000000_study_plan_term_mode/migration.sql`.
3. In the Supabase SQL Editor, run each statement of the migration **separately**, in file order.
4. Record the migration:

```sql
INSERT INTO "_prisma_migrations" (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
VALUES (gen_random_uuid()::text, '<sha256 from step A2>', now(), '20260914000000_study_plan_term_mode', NULL, NULL, now(), 1);
```

5. Run the Step 3 catalog checks for the additive migration (at this stage `dailyStudyHours` is **still present**).

**Stage B — deploy the branch.** Wait until every old instance has drained. Plans created by old code between Stage A and Stage B get the column defaults (60/60 minutes) rather than a copy of their hours; if any exist, re-run the `UPDATE "StudyPlan" SET "weekdayMinutes" = …` statement from the additive migration for rows whose `"createdAt"` is after Stage A.

**Stage C — drop migration (after deploying):**

1. Confirm the file is LF-only: `tr -cd '\r' < prisma/migrations/20260914000001_drop_daily_study_hours/migration.sql | wc -c` → `0`.
2. Compute the checksum: `sha256sum prisma/migrations/20260914000001_drop_daily_study_hours/migration.sql`.
3. Run its single statement in the SQL Editor.
4. Record it with its own row:

```sql
INSERT INTO "_prisma_migrations" (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
VALUES (gen_random_uuid()::text, '<sha256 from step C2>', now(), '20260914000001_drop_daily_study_hours', NULL, NULL, now(), 1);
```

5. Re-run the `StudyPlan` column check from Step 3: `dailyStudyHours` is now gone.

- [ ] **Step 3: Verify the catalog, not the editor's message**

```sql
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name IN ('AcademicTerm', 'StudyPlanPosition', 'StudyPlan', 'StudyPlanItem')
ORDER BY table_name, column_name;
```

Expected:
- `AcademicTerm` and `StudyPlanPosition` exist.
- `StudyPlan` has `forceExamMode`, `studyDays`, `weekdayMinutes`, `weekendMinutes`, `plannedThrough`, `lastReplannedAt`, `outline` and `overload`.
- After Stage A: `StudyPlan` **still has** `dailyStudyHours`, and existing plans' `weekdayMinutes`/`weekendMinutes` equal `dailyStudyHours * 60` (capped at 480/600): `SELECT count(*) FROM "StudyPlan" WHERE "weekdayMinutes" <> LEAST(480, ROUND("dailyStudyHours" * 60)::INTEGER);` → `0`.
- After Stage C: `StudyPlan` has **no** `dailyStudyHours`.
- Both migrations have a row: `SELECT migration_name, checksum, finished_at FROM "_prisma_migrations" WHERE migration_name LIKE '20260914%' ORDER BY migration_name;` returns `20260914000000_study_plan_term_mode` (after Stage A) and `20260914000001_drop_daily_study_hours` (after Stage C), each with the checksum computed for its own file.
- `StudyPlan.targetExam` and `targetDate` are nullable.
- `StudyPlanItem` has `completedAt`, `completionSource` and `carriedFromDate`.

```sql
SELECT t.typname, e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
WHERE t.typname IN ('PlanItemStatus', 'PlanCompletionSource') ORDER BY t.typname, e.enumsortorder;

SELECT indexname FROM pg_indexes WHERE tablename IN ('AcademicTerm', 'StudyPlanPosition', 'StudyPlanItem');

SELECT conname FROM pg_constraint WHERE conrelid = '"StudyPlanPosition"'::regclass;
```

Expected:
- `MISSED` is listed, along with `AUTO` and `MANUAL`.
- The indexes `AcademicTerm_session_term_key`, `StudyPlanPosition_studyPlanId_subjectId_key` and `StudyPlanItem_studyPlanId_scheduledDate_status_idx` exist.
- There are three `_fkey` constraints plus the primary key.

Check the unique constraint behaviourally, in a transaction that always rolls back:

```sql
BEGIN;
INSERT INTO "AcademicTerm" (id, session, term, "startsOn", "endsOn", "updatedAt") VALUES ('chk1', '2099/2100', 'FIRST', '2099-09-01', '2099-12-01', now());
INSERT INTO "AcademicTerm" (id, session, term, "startsOn", "endsOn", "updatedAt") VALUES ('chk2', '2099/2100', 'FIRST', '2099-09-02', '2099-12-02', now());
ROLLBACK;
```

Expected: the second insert fails with a unique violation.

- [ ] **Step 4: Manual check in the running app**

Start the app with `npm run dev`. Then, with a STANDARD-tier test account:

1. **Admin:** open `/admin`. The "No academic term set" banner shows. Add 2026/2027 1st term (2026-09-08 → 2026-12-15) on `/admin/terms`. The banner disappears. Adding an overlapping 2nd term shows an error.
2. **SS1 student:** create a plan with Maths and English, Mon–Thu plus Sat, 30/60 minutes. The header reads "SS1 · 1st term · Week 2 of 15". Today's sessions are SS1 topics only, there are no past questions or mocks, and no day's total exceeds its budget.
3. Tick a session, reload, and it stays done. Undo it. Skip another session.
4. Open a LESSON session's "Open" link, finish the lesson practice with a pass, and return. The session shows "Done automatically".
5. Open "Where is your class?", pick a later Maths topic, and save. Today's Maths sessions change to that topic.
6. **SS3 student:** create a plan with WAEC about 60 days out. The header shows "WAEC in 60 days", the "Term + exam prep" badge is shown, and roughly half the sessions are exam practice or past questions. Switch on "Exam mode now": new term lessons stop.
7. **Concurrent re-plans:** in the SQL Editor, run `UPDATE "StudyPlan" SET "lastReplannedAt" = now() - interval '2 days' WHERE id = '<plan id>';`. Open the plan page in two tabs at the same moment. Then check `SELECT "scheduledDate", count(*) FROM "StudyPlanItem" WHERE "studyPlanId" = '<plan id>' AND status = 'PENDING' GROUP BY 1 ORDER BY 1;`: the counts must match what one re-plan produces (compare with a single-tab reload after resetting again), not double.
8. **Legacy plan:** an existing pre-migration plan opens without errors. Its past pending sessions show as missed, and its window now follows its availability.
9. **Dashboard:** the hero link reads "Today: X of Y done".
10. **SS3 plan with a past exam date:** on an existing SS3 plan, set `"targetDate"` to a past date in the SQL Editor and `"lastReplannedAt"` to NULL. The page shows "Your exam date has passed — your plan is following your school term…", no "WAEC in 0 days" countdown and no exam badge, and the window is filled with term sessions. "Change plan" opens with "Preparing for an exam?" unticked and no date, and saving succeeds.
11. **SS2 plan with recent pending items:** take an existing SS2 plan that has pending sessions from the last few days. After its first re-plan they show as missed, and the new window is **not** flooded with catch-up sessions (a plan whose `plannedThrough` was NULL gets no carry-over). The header has no exam countdown even if the plan kept legacy exam fields.
12. **Progress survives the daily re-plan:** complete a LESSON session, then run `UPDATE "StudyPlan" SET "lastReplannedAt" = now() - interval '1 day' WHERE id = '<plan id>';` and reload. That topic's next session is PRACTICE, not the lesson again, and its revision passes appear on later days — including when the lesson raised mastery to 70 or more.

- [ ] **Step 5: Report and finish the branch**

Report the results of Steps 1–4 honestly, including anything that did not work. Then use superpowers:finishing-a-development-branch.

