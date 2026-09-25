# Learning Evidence Layer — Tracked Activity, Hardened Scoring, Forecasting

Date: 2026-08-11
Status: Phase 1 implemented (see "Phase 1 as shipped" below); Phases 2-3 not started
Role: Senior Learning Experience Designer

## Phase 1 as shipped — read this before trusting the rest of this document

Phase 1 landed in `docs/superpowers/plans/2026-08-11-learning-evidence-layer-phase-1.md`.
The sections below describe the **full three-phase design**. Several of their
headline claims are Phase 2 or 3 behaviour and are **not** in the code today.

### What this document claims that Phase 1 does NOT deliver

- **"One wrong answer no longer diagnoses a weakness."** `CONFIDENCE_FLOOR`
  exists in `src/engines/learning/evidence.ts` but has **zero call sites**.
  `classifyTopic` in `src/engines/learning/gaps.ts` still gates on the old
  "any evidence at all" check, so one wrong answer still classifies a topic
  `WEAK` — at mastery 39 rather than 0, but still `WEAK`. Confidence gating is
  Phase 2.
- **The "still measuring" UI state.** Not built. No surface reads `confidence`.
- **Everything in "Velocity and forecast" and "Difficulty targeting".** Phase 3.
  No snapshots table exists.
- **Quiz abandonment and lesson-block granularity.** Phase 2. `QUIZ_ABANDONED`
  is in the enum and the fold, with no emitter.
- **The `PerformanceMetric` projection.** Phase 2. The Performance page's
  subject breakdown still renders zeros.

### Known limitations of Phase 1

- **No reconciliation for skipped sequences.** `LearningEvent.seq` is
  `BIGSERIAL` — allocated at insert, visible at commit — so a slow-committing
  transaction can be skipped forever by a read that advances a topic's cursor
  past it. The daily cursor-reset-and-replay that mitigates this is Phase 3.
  Until then, **bumping `SCORING_VERSION` is the repair lever**: it forces a
  full per-topic replay from the ledger on the next read.
- **`saveLessonProgress` has no emitter.** `src/lib/lesson-progress.ts` writes
  `StudentProgress.masteryScore`, which the old engine counted as lesson
  evidence and the new one ignores. Dormant — no client posts `masteryScore`
  today — but wiring one without also emitting `LESSON_BLOCK_COMPLETED` would
  silently lose that evidence. There is a tripwire comment at the write site.
- **The lesson channel counts restatements.** `LESSON_COMPLETED` carries
  `bestOfLastThree`, itself an aggregate, so a genuine re-pass emits a
  restatement of a running best that the fold treats as a fresh observation.
  The practice and SRS channels emit one event per new observation. Revisit if
  the lesson channel over-weights repeat learners.
- **The engine test suites are not type-checked.** `tsconfig.json` excludes
  `scripts/`, and several suites construct `TopicState` literals omitting the
  now-required `confidence` field. Harmless at runtime; invisible drift.

### Verification still outstanding

**No query in Phase 1 has ever executed.** The development database was
unreachable throughout implementation (`P1001`), so the migration was authored
offline with `prisma migrate diff` and has **not been applied**. Coverage is 482
passing pure-function tests; nothing exercises Prisma.

Before this reaches real traffic, run all of:

1. `npx prisma migrate deploy` — applies
   `20260811000000_learning_evidence_layer`. Confirm it applies cleanly and
   drops only the three dead `PerformanceMetric` columns.
2. Complete a quiz. Confirm one `QUESTION_ANSWERED` row per answer, each
   carrying `difficulty`, `correct` and `sourceId`.
3. Complete a lesson, review a flashcard, pass a readiness pretest. Confirm one
   `LESSON_COMPLETED`, one `CARD_REVIEWED`, and both `PRETEST_PASSED` **and**
   one `QUESTION_ANSWERED` per pretest answer.
4. Refresh the lesson-result page several times. Confirm **exactly one**
   `LESSON_COMPLETED` event survives — this guards the append-only inflation
   bug fixed in `5b07b0e`.
5. **The end-to-end check, highest value of all:** as a student with no
   history, answer one question correctly. The dashboard must show that topic
   at mastery **56**, not 100, and it must **not** appear under "Tighten your
   gaps". Then confirm `TopicMastery.cursorSeq` equals the ledger's max `seq`,
   and that reloading the dashboard changes neither the numbers nor the cursor.

Step 5 exercises the migration, an emitter, the store, the fold and the
change-gated persist in one pass. It is the difference between "the tests pass"
and "this works".

## Problem

The Learning Path Engine (`docs/superpowers/specs/2026-08-02-learning-path-engine-design.md`)
ranks topics correctly given a mastery number. The mastery number is the
problem. Everything downstream — the dashboard's "Next for you" and "Tighten
your gaps" rails, the gap queue, the revision queue, the study plan — inherits
whatever `computeTopicState` (`src/engines/learning/mastery.ts:108`) produces,
and that function reads thin data naively.

Eight concrete defects, all verified in the current tree:

1. **Accuracy is a lifetime unweighted mean.** Every response a student has ever
   given on a topic counts equally. A student who went 2/10 in January and 9/10
   last week sits near 55% indefinitely. There is no recency weighting anywhere
   in the evidence layer.
2. **No sample-size correction.** One correct answer yields `acc = 100`, hence
   composite mastery 100, hence the topic is "done" and vanishes from both
   rails. One wrong answer yields mastery 0 and a `WEAK` gap. A single click
   moves the student's entire recommendation set.
3. **`Question.difficulty` is never read.** A grep across `src/engines` returns
   no matches. An EASY and a HARD question are identical evidence.
4. **`timeSpentSeconds` is never read.** It is recorded on both
   `AssessmentAttempt` and `QuestionResponse` and consumed nowhere. Rapid
   guessing is invisible to the model.
5. **`lastStudy` is the max of any evidence timestamp** (`mastery.ts:234`),
   including `StudentProgress.lastAccessedAt`. Opening a lesson and closing it
   resets the retention clock, so `R(t)` reports "fresh" when nothing was
   learned.
6. **Only `status: COMPLETED` attempts count.** A student who abandons a quiz
   after getting five wrong leaves no evidence at all. The strongest struggle
   signal in the product is discarded.
7. **`FlashcardReviewLog` is unused by topic mastery.** Per-review ratings and
   lapses exist; the engine reads only current `stability` on REVIEW and
   RELEARNING cards.
8. **`PerformanceMetric` is a mostly-dead table.** Only `masteryLevel`,
   `lastUpdated` and `pretestPassedAt` are ever written
   (`src/lib/topic-practice-result.ts:148`, `src/lib/pretest.ts:202`). The
   columns `masteryScore`, `lastStudiedAt`, `revisionDueAt`, `totalAttempted`,
   `totalCorrect`, `accuracy` and `averageTimePerQuestion` are declared and
   never populated.

Defect 8 has a user-visible consequence today: `src/lib/performance.ts:133`
reads `totalAttempted`, `totalCorrect` and `accuracy` off `PerformanceMetric`
and orders by `accuracy`, so **the Performance page's subject breakdown renders
zeros**.

Defect 8 also has a structural consequence. Everything is recomputed on read
and nothing is persisted, so there is **no time series**: no mastery history, no
velocity, no distinction between improving and plateaued. Nothing in the current
design can be predictive in any meaningful sense. It is a snapshot with a
retention curve attached.

## Goal

Rebuild the evidence layer beneath the Learning Path Engine so that:

- every meaningful student action is **captured** as a durable event;
- mastery is **scored** from that stream in a way that respects recency,
  sample size, question difficulty and engagement;
- the confidence behind every number is **explicit**, so "weak" and "we don't
  know yet" stop being the same state;
- mastery is **persisted over time**, enabling a real exam-readiness forecast
  against the student's study-plan target date;
- and question selection can **target difficulty** to the student's current
  ability.

The knowledge graph, availability gating, `recommendNext`, `gapQueue`,
`revisionQueue` and the study plan are **not** in scope. They consume a
`TopicStateMap` and will continue to, unchanged.

## Constraints

- **No background worker.** Plain Next.js route handlers and server actions over
  Prisma. No cron, no queue. Anything persisted is written inline on a request
  or lazily on read.
- **Pre-launch.** No live student data. Schema is free to change; no backfill
  and no dual-read period is required.
- **The engine is pure-function tested.** `scripts/test-learning-path-*.mts` run
  `node:test` over in-memory state with no database. New code must preserve
  this.

## Architecture

Four layers, each with one job, replacing the three `findMany` calls currently
inlined in `computeTopicState`.

### 1. Capture — `LearningEvent`

Append-only. Every write path emits alongside its existing write. This is the
system of record for *what the student did*.

```prisma
enum LearningEventKind {
  QUESTION_ANSWERED
  QUIZ_ABANDONED
  LESSON_BLOCK_COMPLETED
  LESSON_COMPLETED
  CARD_REVIEWED
  PRETEST_PASSED
}

model LearningEvent {
  seq        BigInt            @id @default(autoincrement())
  studentId  String
  student    User              @relation(fields: [studentId], references: [id], onDelete: Cascade)
  subjectId  String
  topicId    String?
  kind       LearningEventKind
  correct    Boolean?
  score      Float?      // 0..1 for non-binary outcomes (lesson mastery, SRS retention)
  difficulty Difficulty? // authored label captured at answer time
  seconds    Int?
  sourceId   String?     // questionId / lessonId / flashcardId — audit and dedup
  occurredAt DateTime    @default(now())

  @@index([studentId, topicId, seq])
  @@index([studentId, seq])
}
```

Emitting sites:

| Site | Event |
|---|---|
| `src/app/api/assessments/submit/route.ts` | one `QUESTION_ANSWERED` per response |
| `src/app/api/assessments/attempts/[attemptId]/route.ts` | `QUIZ_ABANDONED` — **no write path exists**; this route is `GET`-only today, so phase 2 must add one |
| `src/app/api/lessons/[lessonId]/progress/route.ts` | `LESSON_BLOCK_COMPLETED` |
| `src/lib/topic-practice-result.ts` | `LESSON_COMPLETED` |
| `src/app/api/flashcards/review/route.ts` | `CARD_REVIEWED` |
| `src/lib/pretest.ts` | `PRETEST_PASSED` |

### 2. Aggregate — `TopicMastery`

Holds **decayed sufficient statistics**, not a mastery score. Three
weighted sum/total pairs, one per evidence channel.

```prisma
model TopicMastery {
  studentId String
  student   User   @relation(fields: [studentId], references: [id], onDelete: Cascade)
  subjectId String
  topicId   String

  accWeightedOutcome Float @default(0)  // Σ wᵢ·oᵢ   practice
  accWeightedMass    Float @default(0)  // Σ wᵢ
  lessonWeightedOutcome Float @default(0)
  lessonWeightedMass    Float @default(0)
  srsWeightedOutcome    Float @default(0)
  srsWeightedMass       Float @default(0)

  decayAnchor   DateTime  // the t the stored sums are decayed to
  cursorSeq     BigInt    @default(0)
  lastEffortAt  DateTime? // advanced only by genuine-effort events
  scoringVersion Int      @default(1)
  updatedAt     DateTime  @updatedAt

  @@id([studentId, topicId])
  @@index([studentId, subjectId])
}
```

This works because exponential decay carries forward in closed form. With
`wᵢ = 2^(−(t − tᵢ)/H)`:

```
S(t₂) = S(t₁) · 2^(−(t₂ − t₁)/H)  +   Σ   2^(−(t₂ − tᵢ)/H)
                                   events in (t₁, t₂]
```

So a read multiplies the stored sums by one decay factor and folds in only
events newer than `cursorSeq` — typically zero to a handful of rows — and the
result is *exact*, identical to a full replay.

The cursor is what makes this an optimisation rather than a second source of
truth. A missed write-path update self-heals on the next read. A double write
cannot double-count, because folding is keyed on sequence position rather than
on incrementing a counter. Resetting every cursor to zero replays the ledger
from scratch.

### 3. Derive — pure functions

```ts
foldEvents(aggregate: TopicAggregate | null, events: LearningEvent[], now: Date): TopicAggregate
scoreTopic(aggregate: TopicAggregate, now: Date): TopicState
```

Neither touches Prisma. `computeTopicState` is rewritten to load aggregates plus
newer events, fold, score, and return the **same `TopicStateMap`** that
availability, `recommendNext`, `gapQueue` and `revisionQueue` already consume.
`TopicState` gains one field — `confidence: number` — and is otherwise
unchanged.

### 4. History — `TopicMasterySnapshot`

```prisma
model TopicMasterySnapshot {
  studentId  String
  student    User     @relation(fields: [studentId], references: [id], onDelete: Cascade)
  topicId    String
  day        DateTime @db.Date   // Africa/Lagos day boundary
  mastery    Float
  confidence Float

  @@id([studentId, topicId, day])
  @@index([studentId, day])
}
```

Written lazily, at most once per student per day, on the first read of that day.
That same pass performs a **full cursor reset and replay** for the student and
refreshes `PerformanceMetric` from the derived state — which is what finally
puts real numbers on the Performance page.

## Scoring

Two decay mechanisms already coexist and a third must not be introduced by
accident. **Retention** `R(t)` is a forgetting model — *will they recall it now*.
**Recency weight** is epistemic — *how much does an old answer tell me about
current ability*. They are correlated but distinct, so recency uses a
deliberately long half-life to avoid double-counting what `R(t)` already models.

### Constants

| Constant | Value | Meaning |
|---|---|---|
| `H` | 45 days | recency half-life |
| `α` | 4 | prior strength, in questions |
| `PRIOR` | 0.45 | prior belief for an unevidenced topic |
| `RAPID_SECONDS` | 3 | below this, a response is a rapid guess |
| `RAPID_WEIGHT` | 0.3 | multiplier applied to a rapid guess |
| `CONFIDENCE_FLOOR` | 0.35 | below this, mastery is not reported or diagnosed |

All live in one module and are covered by `scoringVersion`.

### Per-response outcome

Difficulty adjusts the *outcome*, not the denominator, so the asymmetry is right
in both directions: an easy question cannot prove mastery, and missing a hard
one is not damning.

| | BASIC | INTERMEDIATE | ADVANCED |
|---|---|---|---|
| correct | 0.85 | 1.00 | 1.00 |
| wrong | 0.00 | 0.15 | 0.35 |

The project's `Difficulty` enum is `BASIC | INTERMEDIATE | ADVANCED`
(`prisma/schema.prisma:54`), not the EASY/MEDIUM/HARD wording used in early
drafts of this document. A response with no difficulty label is treated as
INTERMEDIATE.

### Per-response weight

```
wᵢ = 2^(−age_days / H)                     recency
wᵢ *= RAPID_WEIGHT   if seconds < RAPID_SECONDS
```

Rapid guesses are down-weighted rather than dropped. Dropping them would leave a
student who speed-clicked twenty questions looking untouched.

### Channel score and confidence

```
W    = Σ wᵢ
acc  = (Σ wᵢ·oᵢ + α·PRIOR) / (W + α)
conf = W / (W + α)
```

Worked cases:

| Evidence | Mastery | Confidence |
|---|---|---|
| 1 correct (intermediate) | 56 | 0.20 |
| 1 wrong (intermediate) | 39 | 0.20 |
| 10/10 intermediate | 84 | 0.71 |
| 0/10 intermediate | 24 | 0.71 |

One correct answer no longer completes a topic: 56 is below `TARGET` 70, so the
topic stays in "Keep learning". One wrong answer no longer diagnoses a weakness:
39 is below `WEAK_MASTERY` 50 and would classify as `WEAK` on mastery alone, but
its confidence of 0.20 is under the 0.35 floor, so the gap queue withholds
judgement. Ten wrong answers land at 24 with confidence 0.71 — firmly `WEAK`,
and now with the evidence to say so.

Note the floors in the outcome table lift the bottom of the range: a student who
gets everything wrong scores 24 rather than 0, because a wrong answer on a
medium question is treated as weak evidence of ability rather than proof of its
absence. The `WEAK_MASTERY` threshold of 50 is unaffected by this shift.

### Composite

The existing 0.45 / 0.35 / 0.20 channel reweighting is kept, with each channel's
weight additionally multiplied by that channel's own confidence, then
renormalised over the channels that have any evidence at all:

```
effective_weightᶜ = base_weightᶜ · confᶜ
mastery = 100 · Σ (effective_weightᶜ · accᶜ) / Σ effective_weightᶜ
```

A topic with heavy practice evidence and one flaky flashcard leans on the
practice automatically.

Overall topic confidence is **not** an average of the channel confidences —
averaging would let an empty channel drag down a well-evidenced topic. It is
computed from the combined evidence mass across channels:

```
conf = (Wacc + Wlesson + Wsrs) / (Wacc + Wlesson + Wsrs + α)
```

so evidence from different channels accumulates rather than competing.

### Effort timestamp

`lastEffortAt` advances on `QUESTION_ANSWERED` (non-rapid),
`LESSON_BLOCK_COMPLETED`, `LESSON_COMPLETED` and `CARD_REVIEWED`. It never
advances on lesson access. `R(t)` is computed from it.

### Confidence gating

- `confidence < CONFIDENCE_FLOOR` — the UI shows *still measuring* rather than a
  mastery figure.
- `gapQueue` refuses to classify `WEAK` or `DECAYED` below the floor. The
  existing `hasEvidence` check in `classifyTopic` (`src/engines/learning/gaps.ts:82`)
  becomes an *enough*-evidence check.

### Velocity and forecast (phase 3)

Velocity is an OLS slope over the last 28 daily snapshots, in mastery points per
day. Forecast per topic:

```
daysToTarget = (TARGET − mastery) / velocity        TARGET = 70
```

compared against the days remaining to `StudyPlan.targetDate`. A topic is *at
risk* when `velocity ≤ 0` or `daysToTarget` exceeds the remaining days. Fewer
than 7 snapshots reports "still measuring" rather than a fabricated projection.

### Difficulty targeting (phase 3)

Inverting the outcome table gives expected success by band: roughly `m + 0.15`
on BASIC, `m` on INTERMEDIATE, `m − 0.15` on ADVANCED, where `m` is
mastery/100. Question selection prefers the band whose expected success is
closest to 0.75.

## Error handling

**Event emission has two tiers, under one rule: the ledger is never the sole
record of anything the product needs.**

- Events duplicating a domain row (`QUESTION_ANSWERED` beside `QuestionResponse`,
  `LESSON_COMPLETED` beside `StudentProgress`) emit inside the existing
  transaction. Atomicity is free, because if the event rolls back so does what
  it described.
- Events with no domain row (`QUIZ_ABANDONED`) emit best-effort outside the
  transaction; failures are logged and swallowed. A ledger hiccup must never
  fail a quiz submission. Losing an abandonment event is a rounding error.

**Aggregate drift.** Postgres sequence values can commit out of order, so a
straggler below the cursor can be skipped. The daily snapshot pass resets the
cursor and replays from scratch, bounding worst-case drift to 24 hours on one
student's one topic. A manual recompute entry point covers support cases.

**Scoring changes.** `TopicMastery.scoringVersion` is compared against the
current constants module on read; a mismatch forces a full replay. This is what
makes the constants tunable rather than frozen.

**Missing aggregate row** is not a special case: absent row means cursor 0 means
full fold. The read path has exactly one branch.

**Null `topicId`.** Events are still recorded at subject level. They contribute
nothing topic-level, but they are not silently discarded.

**Day boundaries.** Snapshots key on the Africa/Lagos day. A `@db.Date` on UTC
boundaries would roll over at 1am local time, smearing evening study into the
next day's snapshot and corrupting the velocity slope.

**Snapshot writes never break a render.** Wrapped in try/catch; the
`@@id([studentId, topicId, day])` composite makes a concurrent write from a
second open tab a no-op rather than a duplicate.

**Ledger growth.** No pruning in phase 1. At `H = 45` days, events older than
roughly 270 days carry under 0.2% weight, so compaction is available later at
the same seam the replay already uses.

## Testing

`foldEvents` and `scoreTopic` take no Prisma client, so every claim below is
testable without a database, matching the existing `scripts/test-*.mts`
`node:test` pattern.

**The central invariant** — incremental catch-up must equal full replay:

```
fold(∅, allEvents, t₂)  ≡  fold(fold(∅, before(t₁), t₁), after(t₁), t₂)
```

Exercised property-style over generated event sequences with randomised split
points, asserted to float epsilon. This is the test that justifies the
architecture; a decay carry-forward bug would hide nowhere else.

New `scripts/test-learning-path-evidence.mts` additionally covers:

- the four worked scoring cases as literal assertions (56 / 39 / 84 / 24);
- confidence monotonic in evidence mass;
- rapid-guess down-weighting;
- `lastEffortAt` advancing on effort events and not on lesson access;
- one wrong answer producing no `WEAK` gap;
- the difficulty outcome table in all six cells.

Phase 3 adds velocity over synthetic snapshot series with a known slope, and the
under-7-snapshots "still measuring" path.

**The regression signal that matters:** `test-learning-path-state.mts` is
reframed to feed events instead of rows, but the other five suites —
`-graph`, `-recommend`, `-revision`, `-plan` and `-pretest` — must pass
**unchanged** in phase 1. If they do not, the blast radius escaped the evidence
layer. (Gap classification has no suite of its own today; the confidence gate
added in phase 2 is covered by the new evidence suite.)

## Phases

Each phase is independently shippable and independently testable.

### Phase 1 — Ledger, aggregate, hardened scoring

Add `LearningEvent` and `TopicMastery`. Emit from the six write paths. Rewrite
`computeTopicState` to fold aggregate plus newer events. Drop the dead
`PerformanceMetric` columns `masteryScore`, `lastStudiedAt` and `revisionDueAt`.

**No UI change and no downstream engine change.** Both dashboard rails consume
the same `TopicStateMap` they do today, built from better numbers, and
immediately point at better topics.

### Phase 2 — Widen capture, surface confidence

Quiz abandonment, lesson-block granularity, rapid-guess flagging, `lastEffortAt`.
`confidence` joins `TopicState`; gap classification gates on it; the UI gains a
*still measuring* state. The daily projection into `PerformanceMetric` lands
here, replacing the zeros on the Performance page.

### Phase 3 — History and prediction

`TopicMasterySnapshot` with the lazy once-per-day write and full replay.
Velocity, the exam-readiness forecast against `StudyPlan.targetDate`, and
difficulty-targeted question selection.

## Decisions taken

- **Append-only ledger** over extending existing tables — one place to add a
  signal, and replay after a scoring change.
- **Hardened weighted average** over Elo or Bayesian knowledge tracing —
  explainable, no calibration data required, preserves the existing reason
  strings and pure-function tests. Elo remains available later; the ledger is
  the substrate it would need.
- **Aggregate with cursor** over fold-on-read — read cost stays flat as the
  ledger grows, without becoming a second source of truth.
- **Per-topic trend is computed but not surfaced** on the rails. Snapshots exist
  because the forecast needs velocity, not to put an "improving" badge on every
  card.
