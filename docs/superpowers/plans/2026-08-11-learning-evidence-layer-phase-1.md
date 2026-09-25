# Learning Evidence Layer — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Learning Path Engine's evidence layer with an append-only event ledger and a decayed running aggregate, so per-topic mastery respects recency, sample size, question difficulty and engagement — without changing any UI or any downstream engine.

**Architecture:** Every write path appends a `LearningEvent`. A `TopicMastery` row per (student, topic) holds decayed sufficient statistics — weighted outcome sums and evidence mass per channel — plus a `cursorSeq` marking how far into the ledger it has folded. Reads decay the stored sums forward in closed form and fold in only events past the cursor, which is exact. `computeTopicState` keeps its signature and still returns a `TopicStateMap`, so availability, `recommendNext`, `gapQueue`, `revisionQueue` and both dashboard rails are untouched.

**Tech Stack:** TypeScript, Next.js (App Router), Prisma + PostgreSQL, `node:test` via `tsx` for pure-function tests.

**Spec:** `docs/superpowers/specs/2026-08-11-learning-evidence-layer-design.md`

## Global Constraints

- **Phase 1 changes no UI and no downstream engine *behaviour*.** Do not change scoring, ranking, gating or classification logic in `src/engines/learning/{availability,recommend,gaps,revision,graph}.ts`. The single permitted exception is mechanical: widening a Prisma-client parameter type union in `availability.ts` so it names the tables the evidence layer reads (Task 8, Step 2). No function body in those files may change. If a behavioural change seems necessary, stop — the design is wrong. The regression gate below is the proof that behaviour held.
- **Pure functions take no Prisma client.** Everything in `src/engines/learning/evidence.ts` and `fold.ts`, and `scoreAggregate` in `mastery.ts`, must be testable with no database.
- **Database access lives in `src/lib` services**, per commit `563b226` ("move every database read and write behind a service in src/lib").
- **Scoring constants, copied verbatim from the spec:** `RECENCY_HALF_LIFE_DAYS = 45`, `PRIOR_STRENGTH = 4`, `PRIOR_OUTCOME = 0.45`, `RAPID_SECONDS = 3`, `RAPID_WEIGHT = 0.3`, `CONFIDENCE_FLOOR = 0.35`, `SCORING_VERSION = 1`.
- **Difficulty enum is `BASIC | INTERMEDIATE | ADVANCED`** (`prisma/schema.prisma:54`). Not EASY/MEDIUM/HARD.
- **Outcome table:** BASIC `{correct 0.85, wrong 0.0}`, INTERMEDIATE `{correct 1.0, wrong 0.15}`, ADVANCED `{correct 1.0, wrong 0.35}`. A null difficulty is treated as INTERMEDIATE.
- **The regression gate:** after Task 8, `npm run test:path` must pass with `test-learning-path-graph/-recommend/-revision/-plan/-pretest.mts` **unmodified**. Only `-state.mts` may be edited.
- Read `node_modules/next/dist/docs/` before touching any Next.js API — this Next.js differs from training data (see `AGENTS.md`).
- **The development database is unreachable** (`P1001` against the Supabase
  pooler), and there is no local Postgres. Every step that needs a live
  connection is deferred to a single catch-up pass, tracked in the ledger:
  applying the migration (`migrate deploy`), and the hands-on verification in
  Tasks 4, 5, 6 and 8 that uses Prisma Studio or the dev server. **Skip those
  steps and say so in your report — do not fake them, and do not report a
  verification you did not perform.** Everything else proceeds normally:
  `prisma migrate diff`, `prisma generate`, `tsc --noEmit` and the whole
  `node:test` suite all work offline, and every task is still reviewed.

---

### Task 1: Scoring primitives

The pure per-response maths: outcome by difficulty, recency weight, rapid-guess penalty, and the shrunk channel score. No aggregation and no events yet — just the functions everything else composes.

**Files:**
- Create: `src/engines/learning/evidence.ts`
- Create: `scripts/test-learning-path-evidence.mts`
- Modify: `package.json` (register the new test file in `test` and `test:path`)

**Interfaces:**
- Consumes: `Difficulty` from `@/types/prisma`.
- Produces: `SCORING_VERSION`, `RECENCY_HALF_LIFE_DAYS`, `PRIOR_STRENGTH`, `PRIOR_OUTCOME`, `RAPID_SECONDS`, `RAPID_WEIGHT`, `CONFIDENCE_FLOOR` (all `number`); `responseOutcome(correct: boolean, difficulty: Difficulty | null): number`; `recencyWeight(ageDays: number): number`; `ageInDays(from: Date, to: Date): number`; `isRapidGuess(seconds: number | null): boolean`; `responseWeight(ageDays: number, seconds: number | null): number`; `channelScore(weightedOutcome: number, mass: number): { score: number; confidence: number }`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-learning-path-evidence.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ageInDays,
  channelScore,
  isRapidGuess,
  recencyWeight,
  responseOutcome,
  responseWeight,
  PRIOR_STRENGTH,
  RECENCY_HALF_LIFE_DAYS,
} from "../src/engines/learning/evidence";

const DAY_MS = 86_400_000;
const now = new Date("2026-08-01T09:00:00Z");

function close(actual: number, expected: number, epsilon = 1e-9) {
  assert.ok(
    Math.abs(actual - expected) < epsilon,
    `expected ${expected}, got ${actual}`,
  );
}

// ─── Outcome by difficulty ─────────────────────────────────

test("responseOutcome: all six cells of the difficulty table", () => {
  assert.equal(responseOutcome(true, "BASIC"), 0.85);
  assert.equal(responseOutcome(false, "BASIC"), 0);
  assert.equal(responseOutcome(true, "INTERMEDIATE"), 1);
  assert.equal(responseOutcome(false, "INTERMEDIATE"), 0.15);
  assert.equal(responseOutcome(true, "ADVANCED"), 1);
  assert.equal(responseOutcome(false, "ADVANCED"), 0.35);
});

test("responseOutcome: an unlabelled question is treated as INTERMEDIATE", () => {
  assert.equal(responseOutcome(true, null), responseOutcome(true, "INTERMEDIATE"));
  assert.equal(responseOutcome(false, null), responseOutcome(false, "INTERMEDIATE"));
});

test("responseOutcome: a correct BASIC answer cannot prove mastery", () => {
  assert.ok(responseOutcome(true, "BASIC") < responseOutcome(true, "ADVANCED"));
});

test("responseOutcome: a wrong ADVANCED answer is not damning", () => {
  assert.ok(responseOutcome(false, "ADVANCED") > responseOutcome(false, "BASIC"));
});

// ─── Recency weight ────────────────────────────────────────

test("recencyWeight: halves every half-life", () => {
  close(recencyWeight(0), 1);
  close(recencyWeight(RECENCY_HALF_LIFE_DAYS), 0.5);
  close(recencyWeight(RECENCY_HALF_LIFE_DAYS * 2), 0.25);
});

test("recencyWeight: a future age clamps to full weight, never above", () => {
  close(recencyWeight(-10), 1);
});

test("ageInDays: measures forward and clamps backwards to zero", () => {
  close(ageInDays(new Date(now.getTime() - 3 * DAY_MS), now), 3);
  close(ageInDays(new Date(now.getTime() + 3 * DAY_MS), now), 0);
});

// ─── Rapid guessing ────────────────────────────────────────

test("isRapidGuess: under three seconds, and unknown timing is not a guess", () => {
  assert.equal(isRapidGuess(2), true);
  assert.equal(isRapidGuess(3), false);
  assert.equal(isRapidGuess(30), false);
  assert.equal(isRapidGuess(null), false);
});

test("responseWeight: a rapid guess is down-weighted, not dropped", () => {
  const considered = responseWeight(0, 30);
  const guessed = responseWeight(0, 1);
  close(guessed, considered * 0.3);
  assert.ok(guessed > 0, "a rapid guess must still carry some weight");
});

// ─── Shrunk channel score ──────────────────────────────────

test("channelScore: the four worked cases from the spec", () => {
  close(channelScore(1.0, 1).score, 0.56);        // 1 correct intermediate  → 56
  close(channelScore(0.15, 1).score, 0.39);       // 1 wrong intermediate    → 39
  close(channelScore(10, 10).score, 11.8 / 14);   // 10/10 intermediate      → 84
  close(channelScore(1.5, 10).score, 3.3 / 14);   // 0/10 intermediate       → 24
});

test("channelScore: no evidence returns the prior exactly", () => {
  close(channelScore(0, 0).score, 0.45);
  close(channelScore(0, 0).confidence, 0);
});

test("channelScore: confidence is the data's share against the prior", () => {
  close(channelScore(1, 1).confidence, 1 / (1 + PRIOR_STRENGTH));
  close(channelScore(10, 10).confidence, 10 / (10 + PRIOR_STRENGTH));
});

test("channelScore: confidence rises monotonically with evidence mass", () => {
  let previous = -1;
  for (const mass of [0, 1, 2, 5, 10, 50, 200]) {
    const { confidence } = channelScore(mass, mass);
    assert.ok(confidence > previous, `confidence fell at mass ${mass}`);
    assert.ok(confidence < 1, "confidence must never reach certainty");
    previous = confidence;
  }
});

test("channelScore: a single answer cannot reach the extremes", () => {
  assert.ok(channelScore(1, 1).score < 0.7, "one correct answer must not master a topic");
  assert.ok(channelScore(0, 1).score > 0.3, "one wrong answer must not zero a topic");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: FAIL — cannot find module `../src/engines/learning/evidence`.

- [ ] **Step 3: Write the implementation**

Create `src/engines/learning/evidence.ts`:

```ts
import type { Difficulty } from "@/types/prisma";

// Learning Evidence Layer — scoring primitives.
// See docs/superpowers/specs/2026-08-11-learning-evidence-layer-design.md

const DAY_MS = 86_400_000;

/**
 * Bumped whenever any constant below changes. A TopicMastery row carrying an
 * older version is replayed from the ledger rather than trusted.
 */
export const SCORING_VERSION = 1;

/**
 * Recency half-life. Deliberately long: this models how much an old answer
 * tells us about *current ability*, which is a different question from the
 * forgetting curve R(t) in mastery.ts. A short half-life here would
 * double-count forgetting.
 */
export const RECENCY_HALF_LIFE_DAYS = 45;

/** Prior strength, in questions. Four questions' worth of prior belief. */
export const PRIOR_STRENGTH = 4;

/** Prior belief for a topic with no evidence — slightly below neutral. */
export const PRIOR_OUTCOME = 0.45;

/** Below this, an answer was not read. */
export const RAPID_SECONDS = 3;

/**
 * Rapid guesses are down-weighted rather than dropped. Dropping them would
 * leave a student who speed-clicked twenty questions looking untouched.
 */
export const RAPID_WEIGHT = 0.3;

/** Below this confidence, mastery is not reported and not diagnosed. */
export const CONFIDENCE_FLOOR = 0.35;

/**
 * Difficulty adjusts the *outcome*, not the denominator, so the asymmetry
 * runs both ways: an easy question cannot prove mastery, and missing a hard
 * one is not proof of absence.
 */
const OUTCOME_BY_DIFFICULTY: Record<Difficulty, { correct: number; wrong: number }> = {
  BASIC: { correct: 0.85, wrong: 0.0 },
  INTERMEDIATE: { correct: 1.0, wrong: 0.15 },
  ADVANCED: { correct: 1.0, wrong: 0.35 },
};

/** The 0..1 outcome a single response contributes. */
export function responseOutcome(
  correct: boolean,
  difficulty: Difficulty | null,
): number {
  const band = OUTCOME_BY_DIFFICULTY[difficulty ?? "INTERMEDIATE"];
  return correct ? band.correct : band.wrong;
}

/** Whole days between two instants, clamped at zero against clock skew. */
export function ageInDays(from: Date, to: Date): number {
  return Math.max(0, (to.getTime() - from.getTime()) / DAY_MS);
}

/** 2^(-age/H) — the multiplicative form is what makes the fold incremental. */
export function recencyWeight(ageDays: number): number {
  return Math.pow(2, -Math.max(0, ageDays) / RECENCY_HALF_LIFE_DAYS);
}

/** Unknown timing is not evidence of guessing. */
export function isRapidGuess(seconds: number | null): boolean {
  return seconds !== null && seconds < RAPID_SECONDS;
}

export function responseWeight(ageDays: number, seconds: number | null): number {
  return recencyWeight(ageDays) * (isRapidGuess(seconds) ? RAPID_WEIGHT : 1);
}

/**
 * Bayesian shrinkage toward the prior. `score` is the channel's 0..1 estimate;
 * `confidence` is the share of that estimate coming from data rather than the
 * prior, which is the same quantity read from the other side.
 */
export function channelScore(
  weightedOutcome: number,
  mass: number,
): { score: number; confidence: number } {
  return {
    score: (weightedOutcome + PRIOR_STRENGTH * PRIOR_OUTCOME) / (mass + PRIOR_STRENGTH),
    confidence: mass / (mass + PRIOR_STRENGTH),
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: PASS, 14 tests.

- [ ] **Step 5: Register the test file in both npm scripts**

In `package.json`, append `scripts/test-learning-path-evidence.mts` to the end of the `test` script's file list, and to the `test:path` script's file list.

- [ ] **Step 6: Run the full suite**

Run: `npm run test:path`
Expected: PASS. The new file runs alongside the existing six.

- [ ] **Step 7: Commit**

```bash
git add src/engines/learning/evidence.ts scripts/test-learning-path-evidence.mts package.json
git commit -m "feat(learning): add evidence scoring primitives"
```

---

### Task 2: The event fold

The heart of the design. A `TopicAggregate` holds decayed sufficient statistics; `foldEvents` carries them forward and folds in events past the cursor. The test that matters is the invariant: incremental catch-up must equal a full replay.

**Files:**
- Create: `src/engines/learning/fold.ts`
- Modify: `scripts/test-learning-path-evidence.mts` (append a fold section)

**Interfaces:**
- Consumes: `ageInDays`, `recencyWeight`, `responseOutcome`, `responseWeight`, `isRapidGuess` from `./evidence`; `Difficulty` from `@/types/prisma`.
- Produces: `type LearningEventKind`; `type FoldEvent = { seq: bigint; topicId: string; kind: LearningEventKind; correct: boolean | null; score: number | null; difficulty: Difficulty | null; seconds: number | null; occurredAt: Date }`; `type ChannelStats = { outcome: number; mass: number }`; `type TopicAggregate = { topicId: string; subjectId: string; acc: ChannelStats; lesson: ChannelStats; srs: ChannelStats; decayAnchor: Date; cursorSeq: bigint; lastEffortAt: Date | null }`; `emptyAggregate(topicId: string, subjectId: string, at: Date): TopicAggregate`; `decayTo(aggregate: TopicAggregate, to: Date): TopicAggregate`; `foldEvents(base: TopicAggregate, events: readonly FoldEvent[], now: Date): TopicAggregate`.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test-learning-path-evidence.mts`:

```ts
import {
  emptyAggregate,
  foldEvents,
  decayTo,
  type FoldEvent,
  type TopicAggregate,
} from "../src/engines/learning/fold";

let seqCounter = 0n;

function event(overrides: Partial<FoldEvent> = {}): FoldEvent {
  seqCounter += 1n;
  return {
    seq: seqCounter,
    topicId: "t1",
    kind: "QUESTION_ANSWERED",
    correct: true,
    score: null,
    difficulty: "INTERMEDIATE",
    seconds: 30,
    occurredAt: now,
    ...overrides,
  };
}

function daysBefore(days: number, from: Date = now): Date {
  return new Date(from.getTime() - days * DAY_MS);
}

const base = () => emptyAggregate("t1", "subj-1", now);

// ─── Channel routing ───────────────────────────────────────

test("foldEvents: an answered question lands in the practice channel", () => {
  const folded = foldEvents(base(), [event({ correct: true })], now);
  close(folded.acc.mass, 1);
  close(folded.acc.outcome, 1);
  close(folded.lesson.mass, 0);
  close(folded.srs.mass, 0);
});

test("foldEvents: lesson events land in the lesson channel, cards in the SRS channel", () => {
  const folded = foldEvents(
    base(),
    [
      event({ kind: "LESSON_COMPLETED", correct: null, score: 0.8 }),
      event({ kind: "LESSON_BLOCK_COMPLETED", correct: null, score: 0.6 }),
      event({ kind: "CARD_REVIEWED", correct: null, score: 0.85 }),
    ],
    now,
  );
  close(folded.lesson.mass, 2);
  close(folded.lesson.outcome, 1.4);
  close(folded.srs.mass, 1);
  close(folded.srs.outcome, 0.85);
  close(folded.acc.mass, 0);
});

test("foldEvents: events carrying no channel evidence contribute no mass", () => {
  const folded = foldEvents(
    base(),
    [
      event({ kind: "PRETEST_PASSED", correct: null, score: null }),
      event({ kind: "QUIZ_ABANDONED", correct: null, score: null }),
    ],
    now,
  );
  close(folded.acc.mass, 0);
  close(folded.lesson.mass, 0);
  close(folded.srs.mass, 0);
});

test("foldEvents: an out-of-range lesson score is clamped to 0..1", () => {
  const folded = foldEvents(
    base(),
    [event({ kind: "LESSON_COMPLETED", correct: null, score: 1.4 })],
    now,
  );
  close(folded.lesson.outcome, 1);
});

// ─── Decay ─────────────────────────────────────────────────

test("foldEvents: an older answer carries less weight than a fresh one", () => {
  const fresh = foldEvents(base(), [event({ occurredAt: now })], now);
  const old = foldEvents(base(), [event({ occurredAt: daysBefore(45) })], now);
  close(old.acc.mass, fresh.acc.mass * 0.5);
});

test("decayTo: carrying forward a half-life halves both sums", () => {
  const folded = foldEvents(base(), [event()], now);
  const later = decayTo(folded, new Date(now.getTime() + 45 * DAY_MS));
  close(later.acc.mass, folded.acc.mass * 0.5);
  close(later.acc.outcome, folded.acc.outcome * 0.5);
});

test("decayTo: carrying backwards is a no-op, never an amplification", () => {
  const folded = foldEvents(base(), [event()], now);
  const earlier = decayTo(folded, daysBefore(10));
  close(earlier.acc.mass, folded.acc.mass);
});

// ─── The cursor ────────────────────────────────────────────

test("foldEvents: events at or below the cursor are already folded", () => {
  const once = foldEvents(base(), [event({ seq: 100n })], now);
  const twice = foldEvents(once, [{ ...event({ seq: 100n }) }], now);
  close(twice.acc.mass, once.acc.mass);
});

test("foldEvents: the cursor advances to the highest sequence seen", () => {
  const folded = foldEvents(base(), [event({ seq: 7n }), event({ seq: 41n })], now);
  assert.equal(folded.cursorSeq, 41n);
});

test("foldEvents: out-of-order input folds the same as sorted input", () => {
  const a = event({ seq: 10n, occurredAt: daysBefore(5) });
  const b = event({ seq: 20n, occurredAt: daysBefore(1) });
  const sorted = foldEvents(base(), [a, b], now);
  const shuffled = foldEvents(base(), [b, a], now);
  close(shuffled.acc.mass, sorted.acc.mass);
  close(shuffled.acc.outcome, sorted.acc.outcome);
  assert.equal(shuffled.cursorSeq, sorted.cursorSeq);
});

// ─── Effort timestamp ──────────────────────────────────────

test("foldEvents: lastEffortAt tracks the latest genuine-effort event", () => {
  const folded = foldEvents(
    base(),
    [
      event({ occurredAt: daysBefore(10) }),
      event({ kind: "CARD_REVIEWED", correct: null, score: 0.9, occurredAt: daysBefore(2) }),
      event({ occurredAt: daysBefore(30) }),
    ],
    now,
  );
  assert.equal(folded.lastEffortAt?.getTime(), daysBefore(2).getTime());
});

test("foldEvents: a rapid guess is not effort and does not reset the clock", () => {
  const folded = foldEvents(
    base(),
    [
      event({ occurredAt: daysBefore(10), seconds: 30 }),
      event({ occurredAt: daysBefore(1), seconds: 1 }),
    ],
    now,
  );
  assert.equal(folded.lastEffortAt?.getTime(), daysBefore(10).getTime());
});

test("foldEvents: an abandoned quiz is not effort", () => {
  const folded = foldEvents(
    base(),
    [event({ kind: "QUIZ_ABANDONED", correct: null, score: null })],
    now,
  );
  assert.equal(folded.lastEffortAt, null);
});

// ─── The central invariant ─────────────────────────────────

test("foldEvents: incremental catch-up equals a full replay, at every split", () => {
  const kinds = [
    { kind: "QUESTION_ANSWERED" as const, correct: true, score: null },
    { kind: "QUESTION_ANSWERED" as const, correct: false, score: null },
    { kind: "LESSON_COMPLETED" as const, correct: null, score: 0.7 },
    { kind: "CARD_REVIEWED" as const, correct: null, score: 0.9 },
  ];
  const difficulties = ["BASIC", "INTERMEDIATE", "ADVANCED", null] as const;

  // A deterministic pseudo-random sequence — reproducible on failure.
  let rng = 12345;
  const next = () => (rng = (rng * 1103515245 + 12345) % 2147483648) / 2147483648;

  for (let trial = 0; trial < 50; trial += 1) {
    const count = 5 + Math.floor(next() * 20);
    const events: FoldEvent[] = [];
    for (let i = 0; i < count; i += 1) {
      const shape = kinds[Math.floor(next() * kinds.length)];
      events.push({
        seq: BigInt(i + 1),
        topicId: "t1",
        kind: shape.kind,
        correct: shape.correct,
        score: shape.score,
        difficulty: difficulties[Math.floor(next() * difficulties.length)],
        seconds: next() < 0.2 ? 1 : 30,
        // 120 days back up to 10 days back, ascending with i.
        occurredAt: daysBefore(120 - i * (110 / count)),
      });
    }

    const t1 = daysBefore(5);
    const t2 = now;
    const splitAt = 1 + Math.floor(next() * (events.length - 1));
    const before = events.slice(0, splitAt);
    const after = events.slice(splitAt);

    const full = foldEvents(emptyAggregate("t1", "subj-1", t1), events, t2);
    const incremental = foldEvents(
      foldEvents(emptyAggregate("t1", "subj-1", t1), before, t1),
      after,
      t2,
    );

    const message = `trial ${trial}, split ${splitAt}`;
    close(incremental.acc.mass, full.acc.mass, 1e-9);
    close(incremental.acc.outcome, full.acc.outcome, 1e-9);
    close(incremental.lesson.mass, full.lesson.mass, 1e-9);
    close(incremental.lesson.outcome, full.lesson.outcome, 1e-9);
    close(incremental.srs.mass, full.srs.mass, 1e-9);
    close(incremental.srs.outcome, full.srs.outcome, 1e-9);
    assert.equal(incremental.cursorSeq, full.cursorSeq, message);
    assert.equal(
      incremental.lastEffortAt?.getTime() ?? null,
      full.lastEffortAt?.getTime() ?? null,
      message,
    );
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: FAIL — cannot find module `../src/engines/learning/fold`.

- [ ] **Step 3: Write the implementation**

Create `src/engines/learning/fold.ts`:

```ts
import type { Difficulty } from "@/types/prisma";
import {
  ageInDays,
  isRapidGuess,
  recencyWeight,
  responseOutcome,
  responseWeight,
} from "./evidence";

// Learning Evidence Layer — the ledger fold.
// See docs/superpowers/specs/2026-08-11-learning-evidence-layer-design.md

export type LearningEventKind =
  | "QUESTION_ANSWERED"
  | "QUIZ_ABANDONED"
  | "LESSON_BLOCK_COMPLETED"
  | "LESSON_COMPLETED"
  | "CARD_REVIEWED"
  | "PRETEST_PASSED";

/** One ledger row, narrowed to what the fold reads. */
export type FoldEvent = {
  seq: bigint;
  topicId: string;
  kind: LearningEventKind;
  correct: boolean | null;
  score: number | null;
  difficulty: Difficulty | null;
  seconds: number | null;
  occurredAt: Date;
};

/** Decayed sufficient statistics for one evidence channel. */
export type ChannelStats = { outcome: number; mass: number };

export type TopicAggregate = {
  topicId: string;
  subjectId: string;
  acc: ChannelStats;
  lesson: ChannelStats;
  srs: ChannelStats;
  /** The instant the stored sums are decayed to. */
  decayAnchor: Date;
  /** Highest ledger sequence already folded in. */
  cursorSeq: bigint;
  /** Latest genuine-effort event — drives the retention curve. */
  lastEffortAt: Date | null;
};

const EFFORT_KINDS: ReadonlySet<LearningEventKind> = new Set([
  "QUESTION_ANSWERED",
  "LESSON_BLOCK_COMPLETED",
  "LESSON_COMPLETED",
  "CARD_REVIEWED",
  "PRETEST_PASSED",
]);

export function emptyAggregate(
  topicId: string,
  subjectId: string,
  at: Date,
): TopicAggregate {
  return {
    topicId,
    subjectId,
    acc: { outcome: 0, mass: 0 },
    lesson: { outcome: 0, mass: 0 },
    srs: { outcome: 0, mass: 0 },
    decayAnchor: at,
    // BigInt(0), not 0n: tsconfig targets ES2017, which rejects bigint
    // literals (TS2737). The call form is equivalent and portable.
    cursorSeq: BigInt(0),
    lastEffortAt: null,
  };
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function scale(channel: ChannelStats, factor: number): ChannelStats {
  return { outcome: channel.outcome * factor, mass: channel.mass * factor };
}

/**
 * Carries the stored sums forward to `to`.
 *
 * Exact, not approximate: because wᵢ = 2^(−(t − tᵢ)/H) is multiplicative in t,
 *   S(t₂) = S(t₁) · 2^(−(t₂ − t₁)/H)
 * so a single factor moves the whole aggregate. This is the property the
 * running aggregate is built on.
 */
export function decayTo(aggregate: TopicAggregate, to: Date): TopicAggregate {
  const factor = recencyWeight(ageInDays(aggregate.decayAnchor, to));
  return {
    ...aggregate,
    acc: scale(aggregate.acc, factor),
    lesson: scale(aggregate.lesson, factor),
    srs: scale(aggregate.srs, factor),
    decayAnchor: to,
  };
}

type Contribution = {
  channel: "acc" | "lesson" | "srs";
  weight: number;
  outcome: number;
};

function contributionOf(event: FoldEvent, now: Date): Contribution | null {
  const age = ageInDays(event.occurredAt, now);
  switch (event.kind) {
    case "QUESTION_ANSWERED":
      if (event.correct === null) return null;
      return {
        channel: "acc",
        weight: responseWeight(age, event.seconds),
        outcome: responseOutcome(event.correct, event.difficulty),
      };
    case "LESSON_COMPLETED":
    case "LESSON_BLOCK_COMPLETED":
      if (event.score === null) return null;
      return { channel: "lesson", weight: recencyWeight(age), outcome: clamp01(event.score) };
    case "CARD_REVIEWED":
      if (event.score === null) return null;
      return { channel: "srs", weight: recencyWeight(age), outcome: clamp01(event.score) };
    default:
      // QUIZ_ABANDONED and PRETEST_PASSED are recorded but carry no channel
      // evidence — they say something about engagement and unlocking, not
      // about how well the topic is known.
      return null;
  }
}

/** A rapid guess is a click, not study — it must not reset the retention clock. */
function isEffort(event: FoldEvent): boolean {
  if (!EFFORT_KINDS.has(event.kind)) return false;
  if (event.kind === "QUESTION_ANSWERED" && isRapidGuess(event.seconds)) return false;
  return true;
}

/**
 * Folds ledger events into an aggregate, carrying the existing sums forward to
 * `now` first. Events at or below `base.cursorSeq` are skipped, which makes the
 * fold idempotent and order-independent: replaying the same batch changes
 * nothing, and a dropped write is picked up by the next read.
 */
export function foldEvents(
  base: TopicAggregate,
  events: readonly FoldEvent[],
  now: Date,
): TopicAggregate {
  const carried = decayTo(base, now);
  const channels = {
    acc: { ...carried.acc },
    lesson: { ...carried.lesson },
    srs: { ...carried.srs },
  };
  let cursorSeq = carried.cursorSeq;
  let lastEffortAt = carried.lastEffortAt;

  for (const event of events) {
    // Compare against the ORIGINAL cursor, not the running one, so unsorted
    // input cannot cause an event to be skipped.
    if (event.seq <= base.cursorSeq) continue;
    if (event.seq > cursorSeq) cursorSeq = event.seq;

    if (isEffort(event) && (!lastEffortAt || event.occurredAt > lastEffortAt)) {
      lastEffortAt = event.occurredAt;
    }

    const contribution = contributionOf(event, now);
    if (!contribution) continue;
    const channel = channels[contribution.channel];
    channel.outcome += contribution.weight * contribution.outcome;
    channel.mass += contribution.weight;
  }

  return { ...carried, ...channels, cursorSeq, lastEffortAt };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: PASS. The invariant test runs 50 trials at randomised split points.

- [ ] **Step 5: Commit**

```bash
git add src/engines/learning/fold.ts scripts/test-learning-path-evidence.mts
git commit -m "feat(learning): fold ledger events into a decayed aggregate"
```

---

### Task 3: Aggregate to TopicState

Turns an aggregate into the `TopicState` the rest of the engine already consumes, adding one field: `confidence`. This is the seam that keeps the blast radius contained.

**Files:**
- Modify: `src/engines/learning/mastery.ts` (add `confidence` to `TopicState`, add `scoreAggregate`)
- Modify: `scripts/test-learning-path-evidence.mts` (append a scoring section)

**Interfaces:**
- Consumes: `TopicAggregate`, `ChannelStats` from `./fold`; `channelScore`, `PRIOR_STRENGTH` from `./evidence`; `masteryLevelFromScore` from `@/lib/lesson-engine`.
- Produces: `scoreAggregate(aggregate: TopicAggregate, now: Date): TopicState`, exported from `./mastery`. `TopicState` gains `confidence: number`.

**Why this lives in `mastery.ts` rather than its own module:** it needs
`ACC_WEIGHT`, `stabilityForLevel`, `topicRetention` and `TopicState` from
`mastery.ts`, and `mastery.ts` needs `scoreAggregate` for `computeTopicState` in
Task 8. Splitting them creates a module cycle in which a `const` export can
initialise as `undefined`. `mastery.ts` also shrinks overall in Task 8, so it
does not grow unwieldy.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test-learning-path-evidence.mts`:

```ts
import { scoreAggregate } from "../src/engines/learning/mastery";

function scored(events: FoldEvent[], at: Date = now) {
  return scoreAggregate(foldEvents(emptyAggregate("t1", "subj-1", at), events, at), at);
}

// ─── Aggregate → TopicState ────────────────────────────────

test("scoreAggregate: the four worked cases from the spec", () => {
  assert.equal(scored([event({ correct: true })]).mastery, 56);
  assert.equal(scored([event({ correct: false })]).mastery, 39);

  const ten = (correct: boolean) =>
    Array.from({ length: 10 }, () => event({ correct }));
  assert.equal(scored(ten(true)).mastery, 84);
  assert.equal(scored(ten(false)).mastery, 24);
});

test("scoreAggregate: one correct answer leaves the topic below TARGET", () => {
  assert.ok(scored([event({ correct: true })]).mastery < 70);
});

test("scoreAggregate: ten wrong answers are firmly weak, with the evidence to say so", () => {
  const state = scored(Array.from({ length: 10 }, () => event({ correct: false })));
  assert.ok(state.mastery < 50);
  assert.ok(state.confidence > 0.35);
});

test("scoreAggregate: one wrong answer is below the confidence floor", () => {
  assert.ok(scored([event({ correct: false })]).confidence < 0.35);
});

test("scoreAggregate: a single channel scores exactly its own shrunk value", () => {
  // With one channel present the confidence weighting cancels in the
  // renormalisation, so mastery is the channel score unmodified.
  const state = scored([event({ correct: true })]);
  close(state.mastery / 100, channelScore(1, 1).score, 0.005);
});

test("scoreAggregate: no evidence yields zero mastery and zero confidence", () => {
  const state = scored([]);
  assert.equal(state.mastery, 0);
  assert.equal(state.confidence, 0);
  assert.equal(state.acc, null);
  assert.equal(state.lessonM, null);
  assert.equal(state.srs, null);
});

test("scoreAggregate: channels with no evidence stay null so hasEvidence still works", () => {
  const state = scored([event({ correct: true })]);
  assert.ok(state.acc !== null);
  assert.equal(state.lessonM, null);
  assert.equal(state.srs, null);
});

test("scoreAggregate: confidence accumulates across channels rather than averaging", () => {
  const practiceOnly = scored(Array.from({ length: 4 }, () => event({ correct: true })));
  const both = scored([
    ...Array.from({ length: 4 }, () => event({ correct: true })),
    event({ kind: "CARD_REVIEWED", correct: null, score: 0.9 }),
  ]);
  assert.ok(
    both.confidence > practiceOnly.confidence,
    "adding a second channel must raise confidence, not dilute it",
  );
});

test("scoreAggregate: the better-evidenced channel dominates the composite", () => {
  const state = scored([
    ...Array.from({ length: 20 }, () => event({ correct: true })),
    event({ kind: "CARD_REVIEWED", correct: null, score: 0.0 }),
  ]);
  // Twenty correct answers against one bad card: practice must win.
  assert.ok(state.mastery > 70, `expected practice to dominate, got ${state.mastery}`);
});

test("scoreAggregate: retention derives from lastEffortAt, not from lesson access", () => {
  const state = scored([event({ occurredAt: daysBefore(1) })]);
  assert.ok(state.retention !== null);
  assert.equal(state.lastStudy?.getTime(), daysBefore(1).getTime());

  const untouched = scored([event({ kind: "QUIZ_ABANDONED", correct: null, score: null })]);
  assert.equal(untouched.retention, null);
  assert.equal(untouched.lastStudy, null);
});

test("scoreAggregate: mastery maps onto the existing level bands", () => {
  const strong = scored(Array.from({ length: 60 }, () => event({ correct: true })));
  assert.equal(strong.level, "STRONG");
  assert.equal(strong.stability, 60);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: FAIL — `scoreAggregate` is not exported from `mastery`.

- [ ] **Step 3: Add `confidence` to `TopicState`**

In `src/engines/learning/mastery.ts`, add to the `TopicState` interface, after `retention`:

```ts
  /**
   * How much of `mastery` comes from data rather than the prior (0..1).
   * Below CONFIDENCE_FLOOR the number is not worth showing or diagnosing.
   */
  confidence: number;
```

In `assembleTopicState`, add `confidence: 0` to the returned object so the legacy path still type-checks. That function survives only for the tests that already cover it; `computeTopicState` stops calling it in Task 8.

- [ ] **Step 4: Write the implementation**

Add these imports to the top of `src/engines/learning/mastery.ts`:

```ts
import { channelScore, PRIOR_STRENGTH } from "./evidence";
import type { TopicAggregate, ChannelStats } from "./fold";
```

Then append the function to the same file:

```ts
/**
 * Composite mastery from the three channels.
 *
 * Each channel's base weight is multiplied by that channel's own confidence,
 * then renormalised over the channels that have any evidence at all — so a
 * topic with heavy practice and one flaky flashcard leans on the practice
 * automatically. With a single channel present the confidence factor cancels,
 * and mastery is exactly that channel's shrunk score.
 */
export function scoreAggregate(
  aggregate: TopicAggregate,
  now: Date,
): TopicState {
  const acc = channelScore(aggregate.acc.outcome, aggregate.acc.mass);
  const lesson = channelScore(aggregate.lesson.outcome, aggregate.lesson.mass);
  const srs = channelScore(aggregate.srs.outcome, aggregate.srs.mass);

  const present: Array<[number, number]> = [];
  const consider = (
    stats: ChannelStats,
    baseWeight: number,
    scored: { score: number; confidence: number },
  ) => {
    if (stats.mass > 0) present.push([baseWeight * scored.confidence, scored.score]);
  };
  consider(aggregate.acc, ACC_WEIGHT, acc);
  consider(aggregate.lesson, LESSON_WEIGHT, lesson);
  consider(aggregate.srs, SRS_WEIGHT, srs);

  const weightSum = present.reduce((sum, [weight]) => sum + weight, 0);
  const composite =
    weightSum > 0
      ? present.reduce((sum, [weight, score]) => sum + (weight / weightSum) * score, 0)
      : 0;
  const mastery = Math.min(100, Math.max(0, Math.round(composite * 100)));

  // Confidence is NOT an average of the channel confidences — averaging would
  // let an empty channel drag down a well-evidenced topic. Evidence from
  // different channels accumulates.
  const totalMass = aggregate.acc.mass + aggregate.lesson.mass + aggregate.srs.mass;
  const confidence = totalMass / (totalMass + PRIOR_STRENGTH);

  const level = masteryLevelFromScore(mastery);
  const stability = stabilityForLevel(level);

  return {
    topicId: aggregate.topicId,
    acc: aggregate.acc.mass > 0 ? acc.score * 100 : null,
    lessonM: aggregate.lesson.mass > 0 ? lesson.score * 100 : null,
    srs: aggregate.srs.mass > 0 ? srs.score : null,
    lastStudy: aggregate.lastEffortAt,
    mastery,
    level,
    stability,
    retention: topicRetention(aggregate.lastEffortAt, stability, now),
    confidence,
  };
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: PASS.

- [ ] **Step 6: Verify nothing downstream broke**

Run: `npm run test:path && npx tsc --noEmit`
Expected: PASS on both. The only type change so far is an added `TopicState` field.

- [ ] **Step 7: Commit**

```bash
git add src/engines/learning/mastery.ts scripts/test-learning-path-evidence.mts
git commit -m "feat(learning): score a topic aggregate into TopicState with confidence"
```

---

### Task 4: Schema — ledger and aggregate

**Files:**
- Modify: `prisma/schema.prisma`
- Modify: `src/types/prisma.ts`

**Interfaces:**
- Produces: Prisma models `LearningEvent` and `TopicMastery`, enum `LearningEventKind`, and matching hand-written types in `src/types/prisma.ts`.

- [ ] **Step 1: Add the enum**

In `prisma/schema.prisma`, after `enum EdgeKind` (around line 148):

```prisma
enum LearningEventKind {
  QUESTION_ANSWERED
  QUIZ_ABANDONED
  LESSON_BLOCK_COMPLETED
  LESSON_COMPLETED
  CARD_REVIEWED
  PRETEST_PASSED
}
```

- [ ] **Step 2: Add the models**

In `prisma/schema.prisma`, after the `PerformanceMetric` model:

```prisma
// ─── Learning Evidence Layer ──────────────────────────────
// See docs/superpowers/specs/2026-08-11-learning-evidence-layer-design.md

/// Append-only record of what the student did. Never updated, never deleted
/// except by cascade. TopicMastery is a fold of this table.
model LearningEvent {
  seq        BigInt            @id @default(autoincrement())
  studentId  String
  student    User              @relation(fields: [studentId], references: [id], onDelete: Cascade)
  subjectId  String
  topicId    String?
  kind       LearningEventKind
  correct    Boolean?
  /// 0..1 outcome for non-binary evidence (lesson mastery, review grade).
  score      Float?
  /// The authored difficulty label at the moment the question was answered.
  difficulty Difficulty?
  seconds    Int?
  /// questionId / lessonId / flashcardId — for audit, not for logic.
  sourceId   String?
  occurredAt DateTime          @default(now())

  @@index([studentId, topicId, seq])
  @@index([studentId, seq])
}

/// Decayed sufficient statistics per (student, topic) — a cache of the fold
/// over LearningEvent, not a second source of truth. Reset cursorSeq to 0 to
/// force a full replay.
model TopicMastery {
  studentId String
  student   User   @relation(fields: [studentId], references: [id], onDelete: Cascade)
  subjectId String
  topicId   String
  topic     Topic  @relation(fields: [topicId], references: [id], onDelete: Cascade)

  accWeightedOutcome    Float @default(0)
  accWeightedMass       Float @default(0)
  lessonWeightedOutcome Float @default(0)
  lessonWeightedMass    Float @default(0)
  srsWeightedOutcome    Float @default(0)
  srsWeightedMass       Float @default(0)

  /// The instant the stored sums are decayed to.
  decayAnchor    DateTime
  /// Highest LearningEvent.seq already folded in.
  cursorSeq      BigInt    @default(0)
  /// Latest genuine-effort event. Lesson *access* never advances this.
  lastEffortAt   DateTime?
  scoringVersion Int       @default(1)
  updatedAt      DateTime  @updatedAt

  @@id([studentId, topicId])
  @@index([studentId, subjectId])
}
```

- [ ] **Step 3: Add the back-relations on User and Topic**

In `model User`, add:

```prisma
  learningEvents LearningEvent[]
  topicMastery   TopicMastery[]
```

In `model Topic`, add:

```prisma
  topicMastery TopicMastery[]
```

- [ ] **Step 4: Drop the dead Learning-Path columns**

In `model PerformanceMetric`, delete these three lines. They have never been written by any code path, and the aggregate replaces them:

```prisma
  masteryScore  Float?    // composite 0..100 (practice + lesson + SRS)
  lastStudiedAt DateTime? // latest evidence timestamp, drives retention decay
  revisionDueAt DateTime? // next merged revision due date
```

Keep `pretestPassedAt` — `computePathState` reads it (`src/lib/learning-path.ts:196`).
Keep `totalAttempted`, `totalCorrect`, `accuracy`, `averageTimePerQuestion` — they are also unwritten today, but `src/lib/performance.ts:133` reads them and Phase 2 populates them.

- [ ] **Step 5: Author the migration offline, and regenerate the client**

The development database is unreachable (`P1001` against the Supabase pooler),
so `prisma migrate dev` cannot run — it needs a live connection and a shadow
database. Author the migration from the schema files instead. `prisma migrate
diff` and `prisma generate` both work fully offline.

First capture the pre-edit schema to diff against (run this from the repo root;
it reads the committed version, so do it even after you have edited the file):

```bash
git show HEAD:prisma/schema.prisma > "$TMPDIR/schema-before.prisma"
```

If `$TMPDIR` is unset, use any scratch path outside the repo — do not write it
into `prisma/`, or the next diff will pick it up.

Then generate the delta as SQL and place it as a migration:

```bash
mkdir -p prisma/migrations/20260811000000_learning_evidence_layer
npx prisma migrate diff \
  --from-schema-datamodel "$TMPDIR/schema-before.prisma" \
  --to-schema-datamodel prisma/schema.prisma \
  --script > prisma/migrations/20260811000000_learning_evidence_layer/migration.sql
npx prisma generate
```

Verify before moving on:
- `migration.sql` is non-empty and contains `CREATE TYPE "LearningEventKind"`,
  `CREATE TABLE "LearningEvent"`, `CREATE TABLE "TopicMastery"`, and three
  `DROP COLUMN` statements against `PerformanceMetric`.
- It contains nothing else. If it drops or recreates an unrelated table, the
  before-schema was captured wrongly — stop and report it.
- `npx prisma generate` succeeds and reports the client written.

Do **not** run `prisma migrate dev`, `migrate deploy`, or `db push`. Applying
this migration is deferred to a catch-up pass once the database is reachable.

- [ ] **Step 6: Mirror the types**

In `src/types/prisma.ts`, add to the enums section:

```ts
export type LearningEventKind =
  | "QUESTION_ANSWERED"
  | "QUIZ_ABANDONED"
  | "LESSON_BLOCK_COMPLETED"
  | "LESSON_COMPLETED"
  | "CARD_REVIEWED"
  | "PRETEST_PASSED";
```

Add to the models section:

```ts
export type LearningEvent = {
  seq: bigint;
  studentId: string;
  subjectId: string;
  topicId: string | null;
  kind: LearningEventKind;
  correct: boolean | null;
  score: number | null;
  difficulty: Difficulty | null;
  seconds: number | null;
  sourceId: string | null;
  occurredAt: Date;
};

export type TopicMastery = {
  studentId: string;
  subjectId: string;
  topicId: string;
  accWeightedOutcome: number;
  accWeightedMass: number;
  lessonWeightedOutcome: number;
  lessonWeightedMass: number;
  srsWeightedOutcome: number;
  srsWeightedMass: number;
  decayAnchor: Date;
  cursorSeq: bigint;
  lastEffortAt: Date | null;
  scoringVersion: number;
  updatedAt: Date;
};
```

Remove `masteryScore`, `lastStudiedAt` and `revisionDueAt` from the existing `PerformanceMetric` type.

- [ ] **Step 7: Verify the build**

Run: `npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add prisma/schema.prisma prisma/migrations src/types/prisma.ts
git commit -m "feat(db): add the learning event ledger and topic mastery aggregate"
```

---

### Task 5: Emit service and the practice write path

The highest-volume emitter. `QUESTION_ANSWERED` events are written in the same transaction that records the responses, so they commit together or not at all.

**Files:**
- Create: `src/lib/learning-events.ts`
- Modify: `src/lib/assessment-submit.ts:60` (select `difficulty`), `:163-184` (emit in the transaction)

**Interfaces:**
- Consumes: `LearningEventKind`, `Difficulty` from `@/types/prisma`.
- Produces: `type NewLearningEvent = { studentId: string; subjectId: string; topicId: string | null; kind: LearningEventKind; correct?: boolean | null; score?: number | null; difficulty?: Difficulty | null; seconds?: number | null; sourceId?: string | null; occurredAt?: Date }`; `emitLearningEvents(client: LearningEventWriter, events: readonly NewLearningEvent[]): Promise<void>`; `emitLearningEventsSafely(client, events): Promise<void>`.

- [ ] **Step 1: Write the emit service**

Create `src/lib/learning-events.ts`:

```ts
import type { PrismaClient } from "@prisma/client";
import type { Difficulty, LearningEventKind } from "@/types/prisma";

// Learning Evidence Layer — writing to the ledger.
// See docs/superpowers/specs/2026-08-11-learning-evidence-layer-design.md

export type NewLearningEvent = {
  studentId: string;
  subjectId: string;
  topicId: string | null;
  kind: LearningEventKind;
  correct?: boolean | null;
  score?: number | null;
  difficulty?: Difficulty | null;
  seconds?: number | null;
  sourceId?: string | null;
  occurredAt?: Date;
};

/** Accepts either the client or a transaction handle. */
export type LearningEventWriter = Pick<PrismaClient, "learningEvent">;

/**
 * Appends to the ledger.
 *
 * Call this inside the transaction that writes the domain row the event
 * describes, so the two commit together. For events with no domain row of
 * their own, use `emitLearningEventsSafely` instead.
 */
export async function emitLearningEvents(
  client: LearningEventWriter,
  events: readonly NewLearningEvent[],
): Promise<void> {
  if (events.length === 0) return;
  await client.learningEvent.createMany({ data: [...events] });
}

/**
 * Best-effort append for signals with no domain row — an abandoned quiz, a
 * dwell measurement. Losing one is a rounding error in the aggregate; failing
 * the student's request over it is not acceptable.
 */
export async function emitLearningEventsSafely(
  client: LearningEventWriter,
  events: readonly NewLearningEvent[],
): Promise<void> {
  try {
    await emitLearningEvents(client, events);
  } catch (error) {
    console.error("Learning event emit failed:", error);
  }
}
```

- [ ] **Step 2: Select the difficulty on submit**

In `src/lib/assessment-submit.ts`, inside the `questions.select.question.select` block (line 60), add `difficulty: true` after `topicId: true`:

```ts
                select: {
                  id: true,
                  correctAnswer: true,
                  marks: true,
                  topicId: true,
                  subjectId: true,
                  difficulty: true,
                  subject: { select: { code: true, name: true } },
                },
```

- [ ] **Step 3: Collect the events while grading**

In `src/lib/assessment-submit.ts`, add an import at the top:

```ts
import { emitLearningEvents, type NewLearningEvent } from "./learning-events";
```

Declare the accumulator beside `responseData` (after line 92):

```ts
  const learningEvents: NewLearningEvent[] = [];
```

Inside the `for (const answer of answers)` loop, after the `responseData.push({...})` call, add:

```ts
    learningEvents.push({
      studentId,
      subjectId: question.subjectId,
      topicId: question.topicId,
      kind: "QUESTION_ANSWERED",
      correct: isCorrect,
      difficulty: question.difficulty,
      seconds: answer.timeSpentSeconds || null,
      sourceId: question.id,
    });
```

- [ ] **Step 4: Emit inside the claim transaction**

In the `db.$transaction` callback, after the `tx.questionResponse.createMany({...})` call and before `return true`:

```ts
    // In-transaction: the ledger row and the response it describes commit
    // together or not at all. The compare-and-set above means only the winning
    // submission reaches here, so a double-tap cannot double-count.
    await emitLearningEvents(tx, learningEvents);
```

- [ ] **Step 5: Verify the build and the existing suite**

Run: `npx tsc --noEmit && npm test`
Expected: PASS.

- [ ] **Step 6: Verify events are actually written**

Start the dev server, sign in, complete any quiz, then:

```bash
npx prisma studio
```

Open the `LearningEvent` table. Expected: one `QUESTION_ANSWERED` row per answered question, each with a populated `difficulty`, `correct`, and `sourceId`.

- [ ] **Step 7: Commit**

```bash
git add src/lib/learning-events.ts src/lib/assessment-submit.ts
git commit -m "feat(learning): emit question-answered events on submit"
```

---

### Task 6: The remaining emitters

Lesson completion, flashcard reviews and pretest passes. Each goes in the transaction that already writes its domain row.

**Files:**
- Modify: `src/lib/topic-practice-result.ts:148` (lesson completion)
- Modify: `src/lib/flashcards.ts:254-326` (`recordFlashcardReview`)
- Modify: `src/lib/pretest.ts:202`

**Interfaces:**
- Consumes: `emitLearningEvents`, `NewLearningEvent` from `./learning-events`.
- Produces: nothing new. `LESSON_COMPLETED` carries `score = masteryScore / 100`; `CARD_REVIEWED` carries `score` from the review-grade table below; `PRETEST_PASSED` carries no score.

- [ ] **Step 1: Emit on lesson completion**

In `src/lib/topic-practice-result.ts`, add the import:

```ts
import { emitLearningEvents } from "./learning-events";
```

The completion branch already runs a `db.$transaction([...])` array containing the `studentProgress.upsert` and the `performanceMetric.upsert` (line 148). Add a third entry to that array:

```ts
      db.learningEvent.createMany({
        data: [
          {
            studentId: userId,
            subjectId,
            topicId,
            kind: "LESSON_COMPLETED" as const,
            score: bestMastery / 100,
            sourceId: lessonId,
          },
        ],
      }),
```

Note: this uses `createMany` directly rather than `emitLearningEvents` because the surrounding call is Prisma's *array* transaction form, which takes promises rather than a callback. The helper is for callback transactions.

- [ ] **Step 2: Map review grades to outcomes**

In `src/lib/flashcards.ts`, add near the top of the file:

```ts
/**
 * A review grade as a 0..1 outcome for the SRS evidence channel. AGAIN is a
 * genuine failure to recall; EASY is effortless recall.
 */
const REVIEW_OUTCOME: Record<ReviewRating, number> = {
  AGAIN: 0,
  HARD: 0.5,
  GOOD: 0.85,
  EASY: 1,
};
```

If `ReviewRating` is not already imported in this file, add it to the existing `@/types/prisma` import.

- [ ] **Step 3: Resolve the flashcard's topic**

In `recordFlashcardReview`, extend the `flashcard` select (line 270) so the deck's topic is available:

```ts
    select: {
      id: true,
      difficulty: true,
      deck: {
        select: {
          topicId: true,
          topic: { select: { subjectId: true } },
          lesson: {
            select: {
              subtopic: {
                select: { topic: { select: { id: true, subjectId: true } } },
              },
            },
          },
        },
      },
    },
```

- [ ] **Step 4: Emit the review event**

In `recordFlashcardReview`, immediately before the `db.$transaction([...])` call (line 309):

```ts
  // A deck hangs off either a topic directly or a lesson's subtopic. Cards
  // with neither still record a review; they just carry no topic evidence.
  const deckTopic = flashcard.deck.lesson?.subtopic.topic ?? null;
  const topicId = flashcard.deck.topicId ?? deckTopic?.id ?? null;
  const subjectId = flashcard.deck.topic?.subjectId ?? deckTopic?.subjectId ?? null;
```

Add a third entry to the transaction array:

```ts
    ...(topicId && subjectId
      ? [
          db.learningEvent.createMany({
            data: [
              {
                studentId,
                subjectId,
                topicId,
                kind: "CARD_REVIEWED" as const,
                score: REVIEW_OUTCOME[rating],
                seconds: responseTimeMs ? Math.round(responseTimeMs / 1000) : null,
                sourceId: flashcardId,
                occurredAt: now,
              },
            ],
          }),
        ]
      : []),
```

The `const [review] = await db.$transaction([...])` destructure still takes the first element, so adding entries at the end is safe.

- [ ] **Step 5: Emit the pretest's answers as evidence**

`src/lib/pretest.ts` has its **own** grading path — `gradePretest` writes
`QuestionResponse` rows directly and marks the attempt `COMPLETED`, without
going through `assessment-submit.ts`. Because the old `computeTopicState`
counted every response on a COMPLETED attempt, pretest answers **already count
as practice evidence today**. Emitting only `PRETEST_PASSED` here would silently
stop them counting — a behaviour regression no test would catch. So the answers
are emitted too.

Add the import:

```ts
import { type NewLearningEvent } from "./learning-events";
```

In `gradePretest`, declare an accumulator beside `responseData`:

```ts
  const learningEvents: NewLearningEvent[] = [];
```

Inside the `for (const answer of answers)` loop, after the `responseData.push({...})` call, add:

```ts
    learningEvents.push({
      studentId,
      subjectId: question.subjectId,
      topicId: question.topicId,
      kind: "QUESTION_ANSWERED",
      correct: isCorrect,
      difficulty: question.difficulty,
      seconds: answer.timeSpentSeconds || null,
      sourceId: question.id,
    });
```

`question` here is the full `Question` record (the query uses
`include: { question: true }`), so `subjectId`, `topicId` and `difficulty` are
all in scope.

Then add a third entry to the existing `db.$transaction([...])` array, after the
`assessmentAttempt.update`:

```ts
    ...(learningEvents.length > 0
      ? [db.learningEvent.createMany({ data: learningEvents })]
      : []),
```

- [ ] **Step 6: Emit on pretest pass**

In the same file, the pass branch upserts `performanceMetric` inside
`if (passed) { ... }`. The unlock and its ledger event must commit together —
two sequential bare awaits would let a crash between them leave a topic marked
pretest-passed with no corresponding event. Both writes are plain promises, so
the array transaction form applies. Replace the lone `performanceMetric.upsert`
await with:

```ts
    await db.$transaction([
      db.performanceMetric.upsert({
        where: {
          studentId_subjectId_topicId: {
            studentId,
            subjectId: topic.subjectId,
            topicId: topic.id,
          },
        },
        create: {
          studentId,
          subjectId: topic.subjectId,
          topicId: topic.id,
          pretestPassedAt: new Date(),
        },
        update: { pretestPassedAt: new Date() },
      }),
      db.learningEvent.createMany({
        data: [
          {
            studentId,
            subjectId: topic.subjectId,
            topicId: topic.id,
            kind: "PRETEST_PASSED",
            sourceId: topic.id,
          },
        ],
      }),
    ]);
```

Keep the surrounding `recorded = true;` assignment exactly as it is.

Use `topic.subjectId` and `topic.id` — that is what the surrounding code uses;
there are no bare `subjectId` / `topicId` variables in this scope.

- [ ] **Step 7: Verify the build and the suite**

Run: `npx tsc --noEmit && npm test`
Expected: PASS.

- [ ] **Step 8: Verify each emitter by hand** (SKIPPED — database unreachable; see Global Constraints)

Start the dev server and, checking `LearningEvent` in `npx prisma studio` after each:
1. Complete a lesson → one `LESSON_COMPLETED` row with `score` between 0 and 1.
2. Review a flashcard → one `CARD_REVIEWED` row with `score` matching the grade pressed.
3. Pass a readiness pretest → one `PRETEST_PASSED` row.

- [ ] **Step 9: Commit**

```bash
git add src/lib/topic-practice-result.ts src/lib/flashcards.ts src/lib/pretest.ts
git commit -m "feat(learning): emit lesson, review and pretest events"
```

---

### Task 7: The aggregate store

Loads aggregates, loads only the events past each topic's cursor, and persists the folded result. All Prisma access for the evidence layer lives here.

**Files:**
- Create: `src/lib/topic-mastery-store.ts`

**Interfaces:**
- Consumes: `TopicAggregate`, `FoldEvent`, `emptyAggregate`, `foldEvents` from `@/engines/learning/fold`; `SCORING_VERSION` from `@/engines/learning/evidence`.
- Produces: `type MasteryStoreClient = Pick<PrismaClient, "topicMastery" | "learningEvent">`; `loadFoldedAggregates(client: MasteryStoreClient, studentId: string, topics: ReadonlyMap<string, string>, now: Date): Promise<{ aggregates: Map<string, TopicAggregate>; changed: Set<string> }>`; `persistAggregates(client: MasteryStoreClient, studentId: string, aggregates: Iterable<TopicAggregate>): Promise<void>`. The `topics` map is topicId → subjectId. `changed` holds only the topics that folded at least one new event — Task 8 persists those and skips the rest.

- [ ] **Step 1: Write the store**

Create `src/lib/topic-mastery-store.ts`:

```ts
import type { PrismaClient } from "@prisma/client";
import { SCORING_VERSION } from "@/engines/learning/evidence";
import {
  emptyAggregate,
  foldEvents,
  type FoldEvent,
  type TopicAggregate,
} from "@/engines/learning/fold";

// Learning Evidence Layer — reading and persisting the aggregate.
// See docs/superpowers/specs/2026-08-11-learning-evidence-layer-design.md

export type MasteryStoreClient = Pick<PrismaClient, "topicMastery" | "learningEvent">;

/**
 * Current aggregates for `topics` (topicId → subjectId), each carried forward
 * to `now` and caught up with any ledger events past its cursor.
 *
 * A missing row is not a special case: it becomes an empty aggregate at
 * cursor 0, so the fold replays that topic's whole ledger. The same path
 * handles a stale `scoringVersion` — the cursor is forced back to 0 and the
 * topic replays under the new constants.
 */
export async function loadFoldedAggregates(
  client: MasteryStoreClient,
  studentId: string,
  topics: ReadonlyMap<string, string>,
  now: Date,
): Promise<{ aggregates: Map<string, TopicAggregate>; changed: Set<string> }> {
  const topicIds = [...topics.keys()];
  if (topicIds.length === 0) return new Map();

  const rows = await client.topicMastery.findMany({
    where: { studentId, topicId: { in: topicIds } },
  });

  const aggregates = new Map<string, TopicAggregate>();
  for (const topicId of topicIds) {
    const subjectId = topics.get(topicId) as string;
    const row = rows.find((candidate) => candidate.topicId === topicId);
    if (!row || row.scoringVersion !== SCORING_VERSION) {
      aggregates.set(topicId, emptyAggregate(topicId, subjectId, now));
      continue;
    }
    aggregates.set(topicId, {
      topicId,
      subjectId,
      acc: { outcome: row.accWeightedOutcome, mass: row.accWeightedMass },
      lesson: { outcome: row.lessonWeightedOutcome, mass: row.lessonWeightedMass },
      srs: { outcome: row.srsWeightedOutcome, mass: row.srsWeightedMass },
      decayAnchor: row.decayAnchor,
      cursorSeq: row.cursorSeq,
      lastEffortAt: row.lastEffortAt,
    });
  }

  // One clause per topic, each bounded by THAT topic's own cursor.
  //
  // The obvious shortcut — take the lowest cursor across all topics and fetch
  // everything above it — collapses to a full history scan in practice. A topic
  // the student has never touched has no TopicMastery row, so it enters the map
  // with cursor 0, and every student has untouched topics in their subjects.
  // The bound would therefore be 0 on essentially every request, re-reading the
  // entire ledger each time and defeating the point of keeping an aggregate.
  //
  // Each clause is an index range scan on (studentId, topicId, seq), which the
  // schema declares, and a topic that is fully caught up matches no rows at all.
  // BigInt(0), not 0n: tsconfig targets ES2017, which rejects bigint literals
  // (TS2737). The call form is equivalent and portable.
  const cursorClauses = topicIds.map((topicId) => ({
    topicId,
    seq: { gt: aggregates.get(topicId)?.cursorSeq ?? BigInt(0) },
  }));

  const events = await client.learningEvent.findMany({
    where: {
      studentId,
      OR: cursorClauses,
    },
    orderBy: { seq: "asc" },
    select: {
      seq: true,
      topicId: true,
      kind: true,
      correct: true,
      score: true,
      difficulty: true,
      seconds: true,
      occurredAt: true,
    },
  });

  const byTopic = new Map<string, FoldEvent[]>();
  for (const event of events) {
    if (!event.topicId) continue;
    const bucket = byTopic.get(event.topicId);
    const folded = { ...event, topicId: event.topicId } as FoldEvent;
    if (bucket) bucket.push(folded);
    else byTopic.set(event.topicId, [folded]);
  }

  // `changed` carries the topics that actually folded a new event. Everything
  // else is byte-identical to what is already stored apart from its decay
  // anchor, and re-anchoring buys nothing: decayTo is exact from ANY anchor, so
  // a stale one produces the same numbers on the next read. Without this the
  // read path would issue N upserts on every dashboard render, classroom page
  // and lesson access check — writes that carry no new information, awaited in
  // front of the user.
  const folded = new Map<string, TopicAggregate>();
  const changed = new Set<string>();
  for (const [topicId, aggregate] of aggregates) {
    const next = foldEvents(aggregate, byTopic.get(topicId) ?? [], now);
    folded.set(topicId, next);
    if (next.cursorSeq !== aggregate.cursorSeq) changed.add(topicId);
  }
  return { aggregates: folded, changed };
}

/**
 * Writes the folded aggregates back. Best-effort by design: the aggregate is a
 * cache of the ledger, so a failed write costs one recomputation on the next
 * read, never correctness. It must not fail the page that triggered it.
 */
export async function persistAggregates(
  client: MasteryStoreClient,
  studentId: string,
  aggregates: Iterable<TopicAggregate>,
): Promise<void> {
  const writes = [...aggregates]
    // Nothing folded and nothing stored — no row worth creating.
    .filter((a) => a.cursorSeq > BigInt(0))
    .map((a) =>
      client.topicMastery.upsert({
        where: { studentId_topicId: { studentId, topicId: a.topicId } },
        create: {
          studentId,
          subjectId: a.subjectId,
          topicId: a.topicId,
          accWeightedOutcome: a.acc.outcome,
          accWeightedMass: a.acc.mass,
          lessonWeightedOutcome: a.lesson.outcome,
          lessonWeightedMass: a.lesson.mass,
          srsWeightedOutcome: a.srs.outcome,
          srsWeightedMass: a.srs.mass,
          decayAnchor: a.decayAnchor,
          cursorSeq: a.cursorSeq,
          lastEffortAt: a.lastEffortAt,
          scoringVersion: SCORING_VERSION,
        },
        update: {
          accWeightedOutcome: a.acc.outcome,
          accWeightedMass: a.acc.mass,
          lessonWeightedOutcome: a.lesson.outcome,
          lessonWeightedMass: a.lesson.mass,
          srsWeightedOutcome: a.srs.outcome,
          srsWeightedMass: a.srs.mass,
          decayAnchor: a.decayAnchor,
          cursorSeq: a.cursorSeq,
          lastEffortAt: a.lastEffortAt,
          scoringVersion: SCORING_VERSION,
        },
      }),
    );

  if (writes.length === 0) return;
  try {
    await Promise.all(writes);
  } catch (error) {
    console.error("Topic mastery persist failed:", error);
  }
}
```

- [ ] **Step 2: Verify the build**

Run: `npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/lib/topic-mastery-store.ts
git commit -m "feat(learning): add the topic mastery aggregate store"
```

---

### Task 8: Rewire `computeTopicState`

The switchover. `computeTopicState` keeps its exact signature, so all four call sites are untouched, but it now folds the ledger instead of averaging raw rows.

**Files:**
- Modify: `src/engines/learning/mastery.ts:108-252` (replace `computeTopicState`)
- Modify: `src/lib/learning-path.ts:180-194` (widen the accepted client type)
- Modify: `src/engines/learning/availability.ts:20-33, 191-217` (widen the accepted client type)
- **Not** `src/lib/classroom-data.ts` — verified: line 144 calls
  `computeTopicState(db, userId, graph)` with the full `db` client, which
  structurally satisfies any `Pick<PrismaClient, …>` signature. No change is
  needed there. Do not edit it.
- **No test file changes.** All six `scripts/test-learning-path-*.mts` suites must pass unedited — that is this task's regression gate.

**Interfaces:**
- Consumes: `loadFoldedAggregates`, `persistAggregates`, `type MasteryStoreClient` from `@/lib/topic-mastery-store`; `scoreAggregate` from this same file (added in Task 3).
- Produces: `computeTopicState(prisma, studentId, graph, now?)` — same name, same return type `Promise<TopicStateMap>`. The `prisma` parameter type changes from `Pick<PrismaClient, "questionResponse" | "studentProgress" | "flashcardReview">` to `MasteryStoreClient`.

- [ ] **Step 1: Replace the body of `computeTopicState`**

In `src/engines/learning/mastery.ts`, replace the entire `computeTopicState` function (lines 108-252) with:

```ts
/**
 * Derives the state layer for every node in the graph by folding the learning
 * event ledger.
 *
 * The per-topic aggregate carries the decayed sufficient statistics forward in
 * closed form, so only events past its cursor are read — usually none. The
 * folded result is written back opportunistically; that write is a cache
 * refresh, not a source of truth, so its failure costs a recomputation and
 * nothing else.
 */
export async function computeTopicState(
  prisma: MasteryStoreClient,
  studentId: string,
  graph: KnowledgeGraph,
  now = new Date(),
): Promise<TopicStateMap> {
  if (graph.nodes.size === 0) return new Map();

  const topics = new Map<string, string>();
  for (const [topicId, node] of graph.nodes) {
    topics.set(topicId, node.subjectId);
  }

  const { aggregates, changed } = await loadFoldedAggregates(
    prisma,
    studentId,
    topics,
    now,
  );

  const state: TopicStateMap = new Map();
  for (const [topicId, aggregate] of aggregates) {
    state.set(topicId, scoreAggregate(aggregate, now));
  }

  // Only topics that folded a new event are worth writing back. A read that
  // changed nothing writes nothing — otherwise every dashboard render, every
  // classroom page and every lesson access check would await N upserts of
  // identical numbers.
  if (changed.size > 0) {
    await persistAggregates(
      prisma,
      studentId,
      [...changed].map((topicId) => aggregates.get(topicId) as TopicAggregate),
    );
  }

  return state;
}
```

Update the imports at the top of `mastery.ts`: remove the now-unused `PrismaClient` import if nothing else uses it, and add:

```ts
import {
  loadFoldedAggregates,
  persistAggregates,
  type MasteryStoreClient,
} from "@/lib/topic-mastery-store";
```

`scoreAggregate` is already in this file from Task 3, so it needs no import.
Note the direction of dependency: `mastery.ts` → `topic-mastery-store.ts` →
`fold.ts` → `evidence.ts`. The store must never import `mastery.ts`, or the
cycle Task 3 avoided comes back.

`assembleTopicState`, `compositeMastery`, `topicRetention`, `stabilityForLevel` and `STABILITY_BY_LEVEL` all stay — they are still exported and still tested.

- [ ] **Step 2: Widen the client types at the call sites**

In `src/lib/learning-path.ts`, change the `computePathState` prisma parameter type to:

```ts
  prisma: Pick<
    PrismaClient,
    | "topic"
    | "topicEdge"
    | "topicMastery"
    | "learningEvent"
    | "performanceMetric"
  >,
```

In `src/engines/learning/availability.ts` there are two unions to change, and
they are **not** changed the same way. Each function's union must name exactly
the delegates it still touches, directly or through what it calls.

`TopicReadyCheck` (around line 20) — it only calls `loadPretestPassed`
(`performanceMetric`), `loadGraph` (`topic`, `topicEdge`) and
`computeTopicState` (now `topicMastery`, `learningEvent`). Drop all three old
members:

```ts
  prisma: Pick<
    PrismaClient,
    | "topic"
    | "topicEdge"
    | "topicMastery"
    | "learningEvent"
    | "performanceMetric"
  >;
```

`computeLessonAccess` (around line 236) — this one **still calls
`prisma.studentProgress.findMany` directly** at line 266, to collect the
student's completed lesson ids. Keep `"studentProgress"`; drop only
`"questionResponse"` and `"flashcardReview"`:

```ts
  prisma: Pick<
    PrismaClient,
    | "topic"
    | "topicEdge"
    | "topicMastery"
    | "learningEvent"
    | "studentProgress"
    | "lesson"
    | "performanceMetric"
  >,
```

Removing `"studentProgress"` from the second union is a compile error, not a
tidy-up. If you find yourself deleting a member, check for a direct
`prisma.<delegate>.` call in that function first.

- [ ] **Step 3: Verify the build**

Run: `npx tsc --noEmit`
Expected: PASS, with no edit to `src/lib/classroom-data.ts` — it passes the full `db` client, which satisfies any narrowed signature. If it does error, report that as a concern rather than editing it; it would mean the parameter types diverged from what this plan assumed.

- [ ] **Step 4: Run the regression gate — change no test file**

Run: `npm run test:path`
Expected: PASS, **with every one of the six suites unedited**.

`scripts/test-learning-path-state.mts` needs no change either. It never calls
`computeTopicState` — verified, it contains no reference to it and nothing
async. It exercises `compositeMastery`, `assembleTopicState`, `topicRetention`,
`isAvailable`, `prereqStatuses`, `lessonUnlockState` and
`resolvePrerequisiteEntries`, all of which this task leaves untouched. Its
`stateFor` helper builds state through `assembleTopicState`, which still exists
and still maps an accuracy figure straight to that mastery — so the availability
tests keep getting the exact boundary values they assert on (`GATE - 1`, `GATE`,
25, 35, 90).

**Do not "modernise" that helper to drive events through the fold.** Shrinkage
makes mastery coarse — successive correct answers score 56, 63, 69, 73 — so a
fold-driven helper cannot produce 59 or 60, and the gate-boundary tests would
break. `assembleTopicState` is the right tool for constructing a topic at an
exact mastery in a test; the fold is the right tool for deriving mastery from
real evidence. They are different jobs.

**If any suite fails, do not edit the test.** A failure here means the evidence
layer leaked into the engine — that is the signal this gate exists to raise.
Stop and report it.

- [ ] **Step 5: Run the whole suite and lint**

Run: `npm test && npm run lint`
Expected: PASS.

- [ ] **Step 6: Verify end to end against a real database** (SKIPPED — database unreachable; see Global Constraints. Record it in the catch-up list, do not fake it.)

Start the dev server and sign in as a student with no history:

1. Load `/dashboard`. Expected: the learning-path sections stay hidden — `hasActivity` is still false.
2. Answer one question correctly in a topic quiz and submit.
3. Load `/dashboard` again. Expected: "Next for you" shows the topic with mastery **56**, not 100, and it does **not** appear under "Tighten your gaps".
4. Check `TopicMastery` in `npx prisma studio`. Expected: one row for that topic with `cursorSeq` matching the ledger's highest `seq`, `accWeightedMass` near 1, and `scoringVersion` 1.
5. Reload `/dashboard`. Expected: `cursorSeq` unchanged and mastery unchanged — the second read folded nothing and did not double-count.

- [ ] **Step 7: Commit**

```bash
git add src/engines/learning/mastery.ts src/lib/learning-path.ts src/engines/learning/availability.ts
git commit -m "feat(learning): derive topic state by folding the event ledger"
```

---

## Out of scope for this plan

Phases 2 and 3 of the spec get their own plans, written after Phase 1 lands:

- **Phase 2** — quiz abandonment (needs a write path that does not exist yet; `src/app/api/assessments/attempts/[attemptId]/route.ts` is `GET`-only, and `reapStaleAttempts` in `src/lib/attempt-lifecycle.ts:39` is the natural hook), lesson-block granularity, surfacing `confidence` in the UI, gating `gapQueue` on `CONFIDENCE_FLOOR`, and the daily projection into `PerformanceMetric` that fixes the zeros on the Performance page.
- **Phase 3** — `TopicMasterySnapshot`, the lazy once-per-day write with full replay, velocity, the exam-readiness forecast, and difficulty-targeted question selection.

They are deferred rather than written now because both build directly on the `foldEvents` / `scoreAggregate` interfaces, and planning their steps before those exist in real code would produce guesses about names and shapes rather than instructions.
