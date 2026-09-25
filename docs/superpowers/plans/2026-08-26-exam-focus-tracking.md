# Exam Focus Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record how many times a student left an in-progress exam, and show that count on high-stakes results.

**Architecture:** A pure rules module decides what counts as leaving (`visibilitychange` only, absences under 3s dropped). The count is held in a ref, mirrored into the existing `localStorage` session, and sent inside the submit request that already fires. The server clamps it and stores one integer column on `AssessmentAttempt`.

**Tech Stack:** Next.js 16 (App Router), React 19, Prisma 6 + PostgreSQL (Supabase), Zod 4, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-08-26-exam-focus-tracking-design.md`

## Global Constraints

- **Never change `STORAGE_VERSION`** (currently `3`, `src/components/assessment/exam-state.ts:60`). `parseStoredSession` returns `null` when `parsed.v !== STORAGE_VERSION` (line 97), so bumping it discards every in-progress exam the moment the deploy lands. The new field is optional and defaults to `0`.
- **No auto-submit, no punishment UI, no on-screen warning counter.** Recording only.
- **No new network requests during an exam.** No `sendBeacon`, no polling. The count rides in the existing `POST /api/assessments/submit` body.
- **`visibilitychange` only — never `blur`.**
- **Away floor is exactly 3000 ms**, exported as a named constant. An absence *equal to* the floor is ignored.
- **Migrations are hand-applied through the Supabase SQL Editor**, never `prisma migrate` — `DIRECT_URL` does not resolve from this machine.
- **Migration files must be written with LF endings.** `core.autocrlf=true` silently drifts Prisma migration checksums.
- **Run the whole suite with `npm test`** (609 tests pass in this worktree before this work starts). New test files must be appended to the `test` script in `package.json`.
- Display copy is factual and draws no conclusion: `Left the exam N times` (singular: `Left the exam once`).

---

### Task 1: Pure away-event rules

The decision logic, with no React and no DOM, in the style of `src/components/assessment/exam-state.ts`.

**Files:**
- Create: `src/components/assessment/exam-focus.ts`
- Create: `scripts/test-exam-focus.mts`
- Modify: `package.json` (the `test` script)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `export const AWAY_FLOOR_MS = 3000`
  - `export function countsAsAway(hiddenAt: number | null, visibleAt: number): boolean`
  - `export function nextAwayCount(current: number, hiddenAt: number | null, visibleAt: number): number`
  - `export function sanitiseAwayCount(value: unknown): number`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-exam-focus.mts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AWAY_FLOOR_MS,
  countsAsAway,
  nextAwayCount,
  sanitiseAwayCount,
} from "../src/components/assessment/exam-focus";

const HIDDEN_AT = 1_770_000_000_000;

test("the away floor is three seconds", () => {
  assert.equal(AWAY_FLOOR_MS, 3000);
});

test("an absence longer than the floor counts", () => {
  assert.equal(countsAsAway(HIDDEN_AT, HIDDEN_AT + 3001), true);
});

test("an absence shorter than the floor does not count", () => {
  assert.equal(countsAsAway(HIDDEN_AT, HIDDEN_AT + 2999), false);
});

test("an absence exactly at the floor does not count", () => {
  assert.equal(countsAsAway(HIDDEN_AT, HIDDEN_AT + AWAY_FLOOR_MS), false);
});

test("a return with no recorded departure does not count", () => {
  assert.equal(countsAsAway(null, HIDDEN_AT + 9000), false);
});

test("a clock that jumped backwards does not count", () => {
  assert.equal(countsAsAway(HIDDEN_AT, HIDDEN_AT - 5000), false);
});

test("a counted absence increments the running total", () => {
  assert.equal(nextAwayCount(4, HIDDEN_AT, HIDDEN_AT + 5000), 5);
});

test("an uncounted absence leaves the running total alone", () => {
  assert.equal(nextAwayCount(4, HIDDEN_AT, HIDDEN_AT + 1000), 4);
});

test("a stored count survives a resume", () => {
  assert.equal(sanitiseAwayCount(7), 7);
});

test("a session stored before this field existed reads as zero", () => {
  assert.equal(sanitiseAwayCount(undefined), 0);
});

test("a corrupt stored count reads as zero", () => {
  assert.equal(sanitiseAwayCount("many"), 0);
  assert.equal(sanitiseAwayCount(-3), 0);
  assert.equal(sanitiseAwayCount(Number.NaN), 0);
  assert.equal(sanitiseAwayCount(2.7), 2);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-exam-focus.mts`

Expected: FAIL with `ERR_MODULE_NOT_FOUND — Cannot find module '.../src/components/assessment/exam-focus'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/components/assessment/exam-focus.ts`:

```typescript
// What counts as a student leaving an exam.
//
// Kept free of React and the DOM so the rules can be unit-tested; the session
// hook only reads the clock and applies the answer.

/**
 * How long a student must be away before it is recorded.
 *
 * Below this sits everything innocent that hides a tab on a phone: a
 * screenshot, a glanced-at notification, a mistap. An absence *equal* to the
 * floor is not counted — the boundary belongs to the benign side.
 */
export const AWAY_FLOOR_MS = 3000;

/**
 * Whether a return to visibility should be recorded as having left.
 *
 * `hiddenAt` is null when the session never saw the matching departure — a
 * session resumed while already visible, for instance. Nothing to measure, so
 * nothing is counted rather than something guessed.
 */
export function countsAsAway(hiddenAt: number | null, visibleAt: number): boolean {
  if (hiddenAt == null) return false;
  return visibleAt - hiddenAt > AWAY_FLOOR_MS;
}

/** The running total after a return to visibility. */
export function nextAwayCount(
  current: number,
  hiddenAt: number | null,
  visibleAt: number,
): number {
  return countsAsAway(hiddenAt, visibleAt) ? current + 1 : current;
}

/**
 * A count read back from storage, which may predate this field or be corrupt.
 * Anything unusable is zero rather than a reason to discard the whole session.
 */
export function sanitiseAwayCount(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return 0;
  return Math.floor(value);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-exam-focus.mts`

Expected: PASS, 11 tests.

- [ ] **Step 5: Register the test file**

In `package.json`, append ` scripts/test-exam-focus.mts` to the end of the `test` script's file list (it currently ends with `scripts/test-exam-guard.mts"`).

- [ ] **Step 6: Run the whole suite**

Run: `npm test`

Expected: PASS, 620 tests (609 existing + 11 new), 0 failures.

- [ ] **Step 7: Commit**

```bash
git add src/components/assessment/exam-focus.ts scripts/test-exam-focus.mts package.json
git commit -m "feat(exam): rules for what counts as leaving an exam"
```

---

### Task 2: Carry the count through the stored session

`parseStoredSession` must accept sessions with and without the new field.

**Files:**
- Modify: `src/components/assessment/exam-state.ts:53-57` (the `StoredSession` type), and `parseStoredSession` (lines 85-131)
- Modify: `scripts/test-exam-state.mts`

**Interfaces:**
- Consumes: `sanitiseAwayCount` from Task 1.
- Produces: `StoredSession.awayEvents: number` — always a number after parsing, even when the stored JSON omitted it.

- [ ] **Step 1: Give the existing test helper a default**

`scripts/test-exam-state.mts` already has a `storedSession()` helper (lines 42-59) that builds a
whole `StoredSession`. Step 4 makes `awayEvents` a required field on that type, which stops the
helper compiling — so give it a default first. Add this line to the object it returns, after
`currentIndex: 0,`:

```typescript
    awayEvents: 0,
```

- [ ] **Step 2: Write the failing test**

Append to `scripts/test-exam-state.mts`, reusing that helper rather than hand-building session
objects — every other test in the file goes through it.

```typescript
test("parseStoredSession defaults awayEvents on a session stored before the field existed", () => {
  // A session written by the client that shipped before this field existed. The
  // key is absent entirely, which is exactly what a live resume will look like
  // on the deploy that adds it.
  const { awayEvents, ...legacy } = storedSession();
  assert.equal(parseStoredSession(JSON.stringify(legacy), NOW)?.awayEvents, 0);
});

test("parseStoredSession keeps a stored awayEvents count", () => {
  const session = storedSession({ awayEvents: 4 });
  assert.equal(parseStoredSession(JSON.stringify(session), NOW)?.awayEvents, 4);
});

test("parseStoredSession does not discard a session over a corrupt awayEvents", () => {
  // The count is worth far less than the answers it travels with: a bad value
  // is floored to zero, never a reason to throw the session away.
  const session = { ...storedSession(), awayEvents: "lots" };
  const parsed = parseStoredSession(JSON.stringify(session), NOW);
  assert.equal(parsed?.attemptId, "attempt_1");
  assert.equal(parsed?.awayEvents, 0);
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-exam-state.mts`

Expected: FAIL on the first new test — `awayEvents` is `undefined`, not `0`.

- [ ] **Step 4: Write the minimal implementation**

In `src/components/assessment/exam-state.ts`, add the import at the top of the file:

```typescript
import { sanitiseAwayCount } from "./exam-focus";
```

Change the `StoredSession` type (currently lines 53-57) to:

```typescript
export type StoredSession = SessionData & {
  v: number;
  answers: AnswerMap;
  currentIndex: number;
  /**
   * How many times the student has left the exam so far.
   *
   * Optional on the wire and defaulted on read, deliberately: bumping
   * STORAGE_VERSION to make it required would discard every in-progress exam
   * on the deploy that shipped it.
   */
  awayEvents: number;
};
```

In `parseStoredSession`, replace the final `return parsed;` (line 130) with:

```typescript
  return { ...parsed, awayEvents: sanitiseAwayCount(parsed.awayEvents) };
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-exam-state.mts`

Expected: PASS, all tests including the 3 new ones.

- [ ] **Step 6: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`

Expected: clean — no errors. The worktree typechecks green at baseline.

- [ ] **Step 7: Commit**

```bash
git add src/components/assessment/exam-state.ts scripts/test-exam-state.mts
git commit -m "feat(exam): carry the away count through the stored session"
```

---

### Task 3: Track and submit the count

**Files:**
- Modify: `src/components/assessment/use-exam-session.ts` — `writeStored` (lines 56-72), the start/resume effect, the timer effect (lines ~236-247), and `handleSubmit` (lines ~290-330)

**Interfaces:**
- Consumes: `nextAwayCount` (Task 1), `StoredSession.awayEvents` (Task 2).
- Produces: an `awayEvents: number` field in the `POST /api/assessments/submit` request body, sent only when greater than zero.

- [ ] **Step 1: Thread the count through `writeStored`**

Change the signature and body of `writeStored`:

```typescript
function writeStored(
  sessionKey: string,
  data: SessionData,
  answers: AnswerMap,
  currentIndex: number,
  awayEvents: number,
) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      storageKeyFor(sessionKey),
      JSON.stringify({
        ...data,
        v: STORAGE_VERSION,
        answers,
        currentIndex,
        awayEvents,
      }),
    );
  } catch {
    // storage disabled / quota — the in-memory session still works
  }
}
```

- [ ] **Step 2: Add the refs and update the call sites**

Add the import:

```typescript
import { nextAwayCount } from "./exam-focus";
```

Next to `submittedRef` (around line 130), add:

```typescript
  /**
   * How many times the student has left, and when the current absence began.
   * Refs, not state: a tab switch must not re-render a 180-question paper, and
   * the count is only ever read at submit time.
   */
  const awayCountRef = useRef(0);
  const hiddenAtRef = useRef<number | null>(null);
```

In `persist` (around line 137), pass the count:

```typescript
  const persist = useCallback(
    (index: number) => {
      if (!data) return;
      writeStored(sessionKey, data, answersRef.current, index, awayCountRef.current);
    },
    [data, sessionKey],
  );
```

In the start/resume effect, in the **resume** branch (after `answersRef.current = stored.answers;`) add:

```typescript
        awayCountRef.current = stored.awayEvents;
```

And in the **new session** branch, change the `writeStored(session, ...)` call to pass `0`:

```typescript
        writeStored(sessionKey, session, answersRef.current, 0, 0);
```

- [ ] **Step 3: Record absences**

Add a new effect immediately after the timer effect (which ends around line 247). Do **not** add this to the timer effect — that one is skipped entirely for untimed sessions (`if (deadlineAt == null) return;`), and a quick quiz still needs counting.

```typescript
  // ── Focus tracking ───────────────────────────────────────
  // `visibilitychange` only, never `blur`: blur fires for the devtools, the URL
  // bar and a `<select>` popup, none of which mean the student left. The count
  // is recorded on *return*, so the absence is measured rather than assumed,
  // and anything under `AWAY_FLOOR_MS` is dropped — on a phone that is a
  // screenshot or a notification banner, not cheating.
  useEffect(() => {
    if (!attemptId) return;

    function onVisibilityChange() {
      if (document.hidden) {
        hiddenAtRef.current = Date.now();
        return;
      }
      const previous = awayCountRef.current;
      awayCountRef.current = nextAwayCount(
        previous,
        hiddenAtRef.current,
        Date.now(),
      );
      hiddenAtRef.current = null;
      // Written through on the spot rather than waiting for the next answer or
      // navigation: a student who leaves, comes back and immediately refreshes
      // would otherwise resume with the absence forgotten.
      if (awayCountRef.current !== previous) persist(currentIndex);
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [attemptId, persist, currentIndex]);
```

The extra deps re-register the listener whenever the student changes question. That is
user-paced and costs a pair of `addEventListener`/`removeEventListener` calls — far cheaper
than threading another ref through, and it keeps `persist` un-stale.

- [ ] **Step 4: Send it with the submission**

In `handleSubmit`, change the `body` of the fetch:

```typescript
        body: JSON.stringify({
          attemptId,
          answers: buildSubmission(questions, answersRef.current),
          // Omitted when zero: the server defaults it, and the common case
          // should not carry a field that says nothing.
          ...(awayCountRef.current > 0
            ? { awayEvents: awayCountRef.current }
            : {}),
        }),
```

- [ ] **Step 5: Typecheck and run the suite**

Run: `npx tsc --noEmit -p tsconfig.json` then `npm test`

Expected: no type errors; 623 tests pass.

- [ ] **Step 6: Lint**

Run: `npm run lint`

Expected: 0 errors. Six pre-existing warnings about unused vars in unrelated files are fine.

- [ ] **Step 7: Commit**

```bash
git add src/components/assessment/use-exam-session.ts
git commit -m "feat(exam): count how often a student leaves an exam"
```

---

### Task 4: Accept, clamp and store the count

**Files:**
- Modify: `src/lib/validators.ts:71-90` (`submitAssessmentSchema`)
- Modify: `prisma/schema.prisma:495-516` (`AssessmentAttempt`)
- Create: `prisma/migrations/20260827000000_attempt_away_events/migration.sql`
- Modify: `src/lib/assessment-submit.ts` — `submitAttempt` signature and the claim `updateMany` (lines 177-189)
- Modify: `src/app/api/assessments/submit/route.ts:31` (destructuring) and line 33 (the call)

**Interfaces:**
- Consumes: the `awayEvents` field in the request body (Task 3).
- Produces: `AssessmentAttempt.awayEvents: number`, and `submitAttempt(studentId, attemptId, answers, awayEvents?)`.

- [ ] **Step 1: Widen the validator**

In `src/lib/validators.ts`, add to `submitAssessmentSchema`'s object — after the `answers` array, before the closing `})`:

```typescript
  // Client-reported and forgeable, so bounded rather than trusted. Optional so
  // a client from before this shipped, or a session stored back then, still
  // submits successfully; absent means zero.
  awayEvents: z.number().int().min(0).max(10_000).optional(),
```

- [ ] **Step 2: Add the column to the Prisma schema**

In `prisma/schema.prisma`, inside `model AssessmentAttempt`, add after the `status` line:

```prisma
  /// How many times the student left the exam. Client-reported; see the spec.
  awayEvents       Int           @default(0)
```

- [ ] **Step 3: Write the migration**

Create `prisma/migrations/20260827000000_attempt_away_events/migration.sql`. **Write it with LF line endings** — `core.autocrlf=true` will otherwise drift the checksum.

```sql
-- Records how often a student left an exam, for context on the result.
--
-- Hand-applied through the Supabase SQL Editor, not `prisma migrate` —
-- DIRECT_URL does not resolve from the dev machine. IF NOT EXISTS so a retry
-- after a partial failure is safe.
ALTER TABLE "AssessmentAttempt"
  ADD COLUMN IF NOT EXISTS "awayEvents" INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 4: Regenerate the Prisma client**

**Do not run a bare `npx prisma generate`** — this sandbox has no route to
`binaries.prisma.sh`, so it fails trying to download engines it already has. Point it at the
local binaries instead. Run exactly:

```bash
PRISMA_QUERY_ENGINE_LIBRARY="$(pwd)/node_modules/@prisma/engines/query_engine-windows.dll.node" \
PRISMA_SCHEMA_ENGINE_BINARY="$(pwd)/node_modules/@prisma/engines/schema-engine-windows.exe" \
PRISMA_CLI_QUERY_ENGINE_TYPE=library \
npx prisma generate
```

Expected: `Generated Prisma Client`. `awayEvents` is now on the `AssessmentAttempt` type, which
is what lets Steps 5-6 typecheck.

**Applying the SQL to the database is NOT your job.** The migration is hand-applied through the
Supabase SQL Editor by a human — `DIRECT_URL` does not resolve from this machine. Write the
migration file, regenerate the client, and note in your report that the SQL is pending manual
application. The unit suite does not touch a live database, so it passes regardless.

- [ ] **Step 5: Persist it on grade**

In `src/lib/assessment-submit.ts`, change the `submitAttempt` signature:

```typescript
export async function submitAttempt(
  studentId: string,
  attemptId: string,
  answers: SubmittedAnswer[],
  awayEvents = 0,
): Promise<SubmitAttemptResult> {
```

And add the field to the claim's `data` (inside the `db.$transaction`, currently lines 180-189), after `timeSpentSeconds`:

```typescript
        timeSpentSeconds: timing.timeSpentSeconds,
        awayEvents,
```

This is inside the compare-and-set guarded by `status: "IN_PROGRESS"`, so a replayed submission never reaches it — the first graded submission stays authoritative, with no extra code.

- [ ] **Step 6: Pass it through the route**

In `src/app/api/assessments/submit/route.ts`, change line 31 and the call below it:

```typescript
    const { attemptId, answers, awayEvents } = parsed.data;

    const outcome = await submitAttempt(studentId, attemptId, answers, awayEvents);
```

`awayEvents` is `number | undefined`; the parameter's `= 0` default covers `undefined`.

- [ ] **Step 7: Typecheck and run the suite**

Run: `npx tsc --noEmit -p tsconfig.json` then `npm test`

Expected: no type errors; 623 tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/lib/validators.ts prisma/schema.prisma prisma/migrations src/lib/assessment-submit.ts src/app/api/assessments/submit/route.ts
git commit -m "feat(exam): store the away count on the attempt"
```

---

### Task 5: Show it on high-stakes results

**Files:**
- Modify: `src/lib/attempt-results.ts` — the `select` in `buildAttemptResult` (lines 41-50) and its return object (lines 143-163)
- Modify: `src/components/assessment/results-view.tsx` — `ResultData` (lines 47-76) and the summary grid (ends around line 277)

**Interfaces:**
- Consumes: `AssessmentAttempt.awayEvents` (Task 4).
- Produces: `awayEvents: number` on the result payload `buildAttemptResult` returns.

- [ ] **Step 1: Select the column**

In `src/lib/attempt-results.ts`, inside `buildAttemptResult`'s `select`, add after `timeSpentSeconds: true,`:

```typescript
      awayEvents: true,
```

And in the returned object, after `timeSpentSeconds: attempt.timeSpentSeconds ?? 0,`:

```typescript
    awayEvents: attempt.awayEvents,
```

- [ ] **Step 2: Add it to the view's props**

In `src/components/assessment/results-view.tsx`, add to the `ResultData` type after `timeSpentSeconds: number;`:

```typescript
  /** How many times the student left the exam. Shown on high-stakes types only. */
  awayEvents?: number;
```

- [ ] **Step 3: Add the display rule and the row**

At **module scope** (next to the file's other top-level constants, not inside the component),
add:

```typescript
// High-stakes papers only. A ten-question topic quiz that flags a student for
// taking a phone call is noise, and noise is what teaches people to ignore a
// flag when it matters.
const FLAGGED_TYPES = ["MOCK_EXAM", "CBT_PRACTICE", "PAST_PAPER"];
```

Then above the component's `return`, next to where `result` is already in scope:

```typescript
  const awayEvents = result.awayEvents ?? 0;
  const showAwayEvents =
    awayEvents > 0 && FLAGGED_TYPES.includes(result.assessmentType);
```

Then, immediately **after** the closing `</div>` of the summary grid (the one containing the grade, correct-count, time and accuracy tiles, ending around line 277) and **before** the `{/* Actions */}` comment, insert:

```tsx
          {showAwayEvents && (
            <p className="mt-3 rounded-xl border border-border bg-card px-3 py-2 text-center text-xs font-medium text-muted">
              Left the exam{" "}
              {awayEvents === 1 ? "once" : `${awayEvents} times`}.
            </p>
          )}
```

The wording states the fact and draws no conclusion — it is context for whoever reads the result, not an accusation.

- [ ] **Step 4: Typecheck, lint and run the suite**

Run: `npx tsc --noEmit -p tsconfig.json`, then `npm run lint`, then `npm test`

Expected: no type errors, 0 lint errors, 623 tests pass.

- [ ] **Step 5: Verify by hand**

The tracking and display cannot be exercised by the unit suite. With the dev server running (`npm run dev`) and a student logged in:

1. Start a mock exam.
2. Switch to another tab for ~5 seconds, then come back. Repeat twice.
3. Switch away and back again within about 1 second.
4. Submit.
5. The result should read **Left the exam 2 times** — the brief switch must not be counted.
6. Start a topic quiz, switch away for 5 seconds, come back, submit. The row must **not** appear.
7. Start a mock exam, leave it for 5 seconds, come back, then refresh the page and resume. Submit. The count must survive the refresh.

- [ ] **Step 6: Commit**

```bash
git add src/lib/attempt-results.ts src/components/assessment/results-view.tsx
git commit -m "feat(exam): show the away count on high-stakes results"
```

---

## Notes for the executor

- **This work runs in the isolated worktree `.claude/worktrees/exam-focus-tracking`** on branch `feat/exam-focus-tracking`, based on `74544b8`. Another session works in the main checkout; never reach outside this worktree.
- **Baseline is green:** 609 tests pass and `npx tsc --noEmit -p tsconfig.json` exits 0. Any type error or test failure you see is yours.
- **`npx prisma generate` cannot run here** — the sandbox has no network route to `binaries.prisma.sh`, and the generated client was copied in from the main checkout. Task 4 covers what to do instead.
- **Do not add a warning modal, a counter badge, or an auto-submit** even if it looks like the obvious next step. Those were considered and rejected in the spec; the reasoning is in its Non-goals section.
- **Stage 1 (the navigation guard) is already built** — `exam-guard.ts`, `use-navigation-guard.ts`, `exam-active.ts` and the `LeaveExamDialog` in `exam-surface.tsx`. This plan does not touch any of them.
