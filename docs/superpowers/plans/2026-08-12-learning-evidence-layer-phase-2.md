# Learning Evidence Layer — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make diagnosis wait for evidence, show students what evidence they have, record abandonment as a visible signal, and put real numbers on the Performance page.

**Architecture:** A non-decaying observation count rides alongside each channel's decayed mass in the fold and the aggregate. `gaps.ts` gates classification on `CONFIDENCE_FLOOR`. Below the floor the rails show an evidence count instead of a mastery figure. `reapStaleAttempts` emits one `QUIZ_ABANDONED` per distinct topic, consumed as a display-only reason line. The Performance page derives its counts from the ledger, and four dead `PerformanceMetric` columns are deleted.

**Tech Stack:** TypeScript, Next.js (App Router), Prisma + PostgreSQL, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-08-12-learning-evidence-layer-phase-2-design.md`
**Parent spec (read "Phase 1 as shipped"):** `docs/superpowers/specs/2026-08-11-learning-evidence-layer-design.md`

## Global Constraints

- **`decayTo` must NOT scale `observations`.** Mass decays because an old answer is weaker evidence of current ability; "you answered three questions" is a historical fact that does not fade. This is the single most important detail in this plan — get it wrong and the count silently drifts downward over weeks with no test failure.
- **No bigint literals (`0n`) anywhere under `src/`** — `tsconfig.json` targets ES2017 and rejects them (TS2737). Use `BigInt(0)`. Files under `scripts/` are covered by `tsconfig.scripts.json`, which targets ES2020, and may use literals.
- **`CONFIDENCE_FLOOR = 0.35`**, already exported from `src/engines/learning/evidence.ts`. Do not change its value.
- **Observation counts are incremented for events that contribute to a channel**, including rapid guesses (down-weighted to 0.3 mass but still answered). Never for `PRETEST_PASSED` or `QUIZ_ABANDONED`.
- **Abandonment explains, it does not rank.** `gapQueue` still orders by bottleneck score descending, then mastery ascending. Do not add a weight.
- **The database may be unreachable** (`P1001` against the Supabase pooler; no local Postgres or Docker). Author migrations offline with `prisma migrate diff`, run `prisma generate`, and **skip every step needing a live connection — say so in your report rather than faking it.** `tsc`, `eslint` and the `node:test` suites all work offline.
- **Verification commands:** `npx tsc --noEmit`, `npm run typecheck:tests`, `npm test`, `npm run lint`. All four must be clean before any commit.
- **Regression gate:** `test-learning-path-recommend.mts`, `-revision.mts`, `-plan.mts`, `-pretest.mts` and `-graph.mts` must pass with **no assertion changed**. `gaps.ts` is the only behaviour change this phase, so no expected value, test case, or assertion in those five files may move.
  Adding a newly-required type field to a fixture literal (`accObservations: 0` and friends) **is** permitted and expected — that is precisely what `npm run typecheck:tests` exists to force, and Tasks 2 and 5 instruct it. Adding a field is keeping a fixture in step with its type; changing an assertion is moving the goalposts.
  **If a suite fails, report it — never adjust an assertion to make it pass.** A failure here means the evidence layer leaked into the engine, which is the signal this gate exists to raise.
- Read `node_modules/next/dist/docs/` before touching any Next.js API — this Next.js differs from training data (see `AGENTS.md`).

---

### Task 1: Observations in the fold

**Files:**
- Modify: `src/engines/learning/fold.ts`
- Modify: `scripts/test-learning-path-evidence.mts` (append)
- Modify: `src/lib/topic-mastery-store.ts` — **one line per channel only.** Making `observations` required breaks the `ChannelStats` literals this file builds, so it must gain the field here or the build stays broken until Task 3. Set `observations: 0` with the comment in Step 7 below; Task 3 replaces the zeros with the real column.

**Interfaces:**
- Consumes: nothing new.
- Produces: `ChannelStats` gains `observations: number`. `emptyAggregate` initialises all three channels to `observations: 0`. `decayTo` leaves `observations` unscaled. `foldEvents` increments the contributing channel's `observations` by 1 per folded event.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-learning-path-evidence.mts`:

```ts
// ─── Observations ──────────────────────────────────────────

test("foldEvents: each contributing event adds one observation", () => {
  const folded = foldEvents(
    base(),
    [
      event({ correct: true }),
      event({ correct: false }),
      event({ kind: "LESSON_COMPLETED", correct: null, score: 0.8 }),
      event({ kind: "CARD_REVIEWED", correct: null, score: 0.9 }),
    ],
    now,
  );
  assert.equal(folded.acc.observations, 2);
  assert.equal(folded.lesson.observations, 1);
  assert.equal(folded.srs.observations, 1);
});

test("foldEvents: a rapid guess is one observation but only 0.3 mass", () => {
  const folded = foldEvents(base(), [event({ seconds: 1 })], now);
  assert.equal(folded.acc.observations, 1);
  close(folded.acc.mass, 0.3);
});

test("foldEvents: events carrying no channel evidence add no observations", () => {
  const folded = foldEvents(
    base(),
    [
      event({ kind: "PRETEST_PASSED", correct: null, score: null }),
      event({ kind: "QUIZ_ABANDONED", correct: null, score: null }),
    ],
    now,
  );
  assert.equal(folded.acc.observations, 0);
  assert.equal(folded.lesson.observations, 0);
  assert.equal(folded.srs.observations, 0);
});

test("decayTo: mass decays but observations do not", () => {
  const folded = foldEvents(base(), [event(), event()], now);
  const later = decayTo(folded, new Date(now.getTime() + 45 * DAY_MS));
  close(later.acc.mass, folded.acc.mass * 0.5);
  assert.equal(
    later.acc.observations,
    2,
    "observations are a historical fact and must not decay",
  );
});

test("foldEvents: an old answer still counts as one observation", () => {
  const folded = foldEvents(base(), [event({ occurredAt: daysBefore(180) })], now);
  assert.equal(folded.acc.observations, 1);
  assert.ok(folded.acc.mass < 0.1, "its mass should have decayed close to nothing");
});

test("foldEvents: observations are not double-counted at or below the cursor", () => {
  const once = foldEvents(base(), [event({ seq: 100n })], now);
  const twice = foldEvents(once, [event({ seq: 100n })], now);
  assert.equal(twice.acc.observations, once.acc.observations);
});
```

Also extend the existing invariant test so observations are covered by the replay property. Find the assertions block inside `foldEvents: incremental catch-up equals a full replay, at every split` and add, alongside the existing mass/outcome comparisons:

```ts
    assert.equal(incremental.acc.observations, full.acc.observations, message);
    assert.equal(incremental.lesson.observations, full.lesson.observations, message);
    assert.equal(incremental.srs.observations, full.srs.observations, message);
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: FAIL — `observations` is not a property of `ChannelStats`.

- [ ] **Step 3: Add the field and initialise it**

In `src/engines/learning/fold.ts`, change `ChannelStats`:

```ts
/** Decayed sufficient statistics for one evidence channel. */
export type ChannelStats = {
  outcome: number;
  mass: number;
  /**
   * Raw count of events folded into this channel. Deliberately NOT decayed:
   * mass falls because an old answer is weaker evidence of current ability,
   * but "you answered three questions" is a historical fact. This is what the
   * UI shows when confidence is too low to report a mastery figure.
   */
  observations: number;
};
```

In `emptyAggregate`, initialise all three channels:

```ts
    acc: { outcome: 0, mass: 0, observations: 0 },
    lesson: { outcome: 0, mass: 0, observations: 0 },
    srs: { outcome: 0, mass: 0, observations: 0 },
```

- [ ] **Step 4: Leave observations out of the decay**

In `src/engines/learning/fold.ts`, change `scale`:

```ts
function scale(channel: ChannelStats, factor: number): ChannelStats {
  return {
    outcome: channel.outcome * factor,
    mass: channel.mass * factor,
    // observations is carried through untouched — see ChannelStats.
    observations: channel.observations,
  };
}
```

- [ ] **Step 5: Increment in the fold**

In `foldEvents`, inside the loop, extend the contribution block:

```ts
    const contribution = contributionOf(event, now);
    if (!contribution) continue;
    const channel = channels[contribution.channel];
    channel.outcome += contribution.weight * contribution.outcome;
    channel.mass += contribution.weight;
    channel.observations += 1;
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: PASS, including the extended invariant test.

- [ ] **Step 7: Keep the build green**

Making `observations` required breaks `src/lib/topic-mastery-store.ts`, which builds `ChannelStats` literals in `loadFoldedAggregates` (around lines 48-50). The column it will eventually read does not exist until Task 3, so add the field reading zero for now:

```ts
      // observations is not yet a column on TopicMastery — Task 3 adds it and
      // replaces these zeros with row.accObservations / lessonObservations /
      // srsObservations. Zero is safe in the meantime: a stale scoringVersion
      // forces a full replay from the ledger, which recomputes the real counts.
      acc: {
        outcome: row.accWeightedOutcome,
        mass: row.accWeightedMass,
        observations: 0,
      },
      lesson: {
        outcome: row.lessonWeightedOutcome,
        mass: row.lessonWeightedMass,
        observations: 0,
      },
      srs: {
        outcome: row.srsWeightedOutcome,
        mass: row.srsWeightedMass,
        observations: 0,
      },
```

Do not touch `persistAggregates` or anything else in that file — Task 3 owns it.

Run: `npx tsc --noEmit && npm run typecheck:tests && npm run test:path`
Expected: PASS on all three. If `tsc` still complains about `ChannelStats` literals somewhere else, report it rather than guessing.

- [ ] **Step 8: Commit**

```bash
git add src/engines/learning/fold.ts scripts/test-learning-path-evidence.mts
git commit -m "feat(learning): count observations alongside decayed mass"
```

---

### Task 2: Observations on `TopicState`

**Files:**
- Modify: `src/engines/learning/mastery.ts`
- Modify: `scripts/test-learning-path-evidence.mts` (append)

**Interfaces:**
- Consumes: `ChannelStats.observations` from Task 1.
- Produces: `TopicState` gains `accObservations: number`, `lessonObservations: number`, `srsObservations: number`. `scoreAggregate` populates them from the aggregate. `assembleTopicState` sets all three to 0.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-learning-path-evidence.mts`:

```ts
test("scoreAggregate: observation counts reach TopicState", () => {
  const state = scored([
    event({ correct: true }),
    event({ correct: false }),
    event({ kind: "CARD_REVIEWED", correct: null, score: 0.9 }),
  ]);
  assert.equal(state.accObservations, 2);
  assert.equal(state.lessonObservations, 0);
  assert.equal(state.srsObservations, 1);
});

test("scoreAggregate: a topic with no evidence reports no observations", () => {
  const state = scored([]);
  assert.equal(state.accObservations, 0);
  assert.equal(state.lessonObservations, 0);
  assert.equal(state.srsObservations, 0);
});

test("scoreAggregate: observations survive decay while confidence falls", () => {
  const fresh = scored(Array.from({ length: 3 }, () => event()));
  const stale = scored(
    Array.from({ length: 3 }, () => event({ occurredAt: daysBefore(180) })),
  );
  assert.equal(stale.accObservations, fresh.accObservations);
  assert.ok(
    stale.confidence < fresh.confidence,
    "decayed evidence should be less confident despite the same count",
  );
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts`
Expected: FAIL — `accObservations` does not exist on `TopicState`.

- [ ] **Step 3: Extend `TopicState`**

In `src/engines/learning/mastery.ts`, add to the `TopicState` interface after `confidence`:

```ts
  /**
   * Raw counts of the evidence behind this topic, per channel. Unlike mastery
   * and confidence these do not decay — they answer "what have I actually
   * done?", which is what the UI shows when confidence is below the floor.
   */
  accObservations: number;
  lessonObservations: number;
  srsObservations: number;
```

- [ ] **Step 4: Populate them in `scoreAggregate`**

In the object `scoreAggregate` returns, after `confidence`:

```ts
    accObservations: aggregate.acc.observations,
    lessonObservations: aggregate.lesson.observations,
    srsObservations: aggregate.srs.observations,
```

- [ ] **Step 5: Keep `assembleTopicState` compiling**

In `assembleTopicState`, add alongside the existing `confidence: 0`:

```ts
    accObservations: 0,
    lessonObservations: 0,
    srsObservations: 0,
```

- [ ] **Step 6: Run tests and typechecks**

Run: `npx tsx --test scripts/test-learning-path-evidence.mts && npx tsc --noEmit && npm run typecheck:tests`
Expected: PASS on all three. `npm run typecheck:tests` will flag any test suite constructing a `TopicState` literal without the new fields — add `accObservations: 0, lessonObservations: 0, srsObservations: 0` to each, exactly as the existing `confidence: 0` was added.

- [ ] **Step 7: Run the regression gate**

Run: `npm run test:path`
Expected: PASS. Test files may gain the three literal fields; no assertion should change.

- [ ] **Step 8: Commit**

```bash
git add src/engines/learning/mastery.ts scripts/
git commit -m "feat(learning): expose observation counts on TopicState"
```

---

### Task 3: Persist observations

**Files:**
- Modify: `prisma/schema.prisma`
- Modify: `src/types/prisma.ts`
- Modify: `src/lib/topic-mastery-store.ts`
- Modify: `src/engines/learning/evidence.ts` (`SCORING_VERSION` 1 → 2)
- Create: `prisma/migrations/20260812000000_observation_counts/migration.sql`

**Interfaces:**
- Consumes: `ChannelStats.observations` from Task 1.
- Produces: `TopicMastery` gains `accObservations`, `lessonObservations`, `srsObservations` (`Int @default(0)`). `SCORING_VERSION` becomes 2.

- [ ] **Step 1: Add the columns**

In `prisma/schema.prisma`, in `model TopicMastery`, after the three outcome/mass pairs:

```prisma
  /// Raw event counts per channel. Never decayed — see ChannelStats in
  /// src/engines/learning/fold.ts.
  accObservations    Int @default(0)
  lessonObservations Int @default(0)
  srsObservations    Int @default(0)
```

- [ ] **Step 2: Mirror them in the hand-maintained types**

In `src/types/prisma.ts`, add to the `TopicMastery` type after `srsWeightedTotal`/`srsWeightedMass`:

```ts
  accObservations: number;
  lessonObservations: number;
  srsObservations: number;
```

- [ ] **Step 3: Author the migration offline**

The development database is unreachable, so `prisma migrate dev` cannot run. Capture the pre-edit schema and diff against it:

```bash
git show HEAD:prisma/schema.prisma > "$TMPDIR/schema-before-p2.prisma"
mkdir -p prisma/migrations/20260812000000_observation_counts
npx prisma migrate diff \
  --from-schema-datamodel "$TMPDIR/schema-before-p2.prisma" \
  --to-schema-datamodel prisma/schema.prisma \
  --script > prisma/migrations/20260812000000_observation_counts/migration.sql
npx prisma generate
```

If `$TMPDIR` is unset, use any scratch path outside the repo — never inside `prisma/`, or the next diff picks it up.

Verify before continuing: `migration.sql` contains exactly three `ADD COLUMN` statements against `TopicMastery` and touches no other table. If it touches anything else, the before-schema was captured wrongly — stop and report it. **Do not run `migrate dev`, `migrate deploy` or `db push`.**

- [ ] **Step 4: Read and write the counts in the store**

In `src/lib/topic-mastery-store.ts`, in `loadFoldedAggregates`, replace the placeholder `observations: 0` values Task 1 added with the real columns:

```ts
      acc: {
        outcome: row.accWeightedOutcome,
        mass: row.accWeightedMass,
        observations: row.accObservations,
      },
      lesson: {
        outcome: row.lessonWeightedOutcome,
        mass: row.lessonWeightedMass,
        observations: row.lessonObservations,
      },
      srs: {
        outcome: row.srsWeightedOutcome,
        mass: row.srsWeightedMass,
        observations: row.srsObservations,
      },
```

In `persistAggregates`, add the same three fields to **both** the `create` and the `update` blocks:

```ts
          accObservations: a.acc.observations,
          lessonObservations: a.lesson.observations,
          srsObservations: a.srs.observations,
```

A field present in one block and not the other means a row's values depend on whether it already existed. Both blocks must write the same field set.

- [ ] **Step 5: Bump the scoring version**

In `src/engines/learning/evidence.ts`:

```ts
export const SCORING_VERSION = 2;
```

Extend its doc comment with a line recording why:

```
 * Bumped to 2 in Phase 2, which added per-channel observation counts. Existing
 * rows have no counts and no write can backfill them; the version mismatch
 * forces a full replay from the ledger on next read, which recomputes them
 * from source.
```

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit && npm run typecheck:tests && npm test && npm run lint`
Expected: all clean. Skipped, and to be stated in your report: applying the migration and any live-database check.

- [ ] **Step 7: Commit**

```bash
git add prisma/schema.prisma prisma/migrations src/types/prisma.ts src/lib/topic-mastery-store.ts src/engines/learning/evidence.ts
git commit -m "feat(db): persist per-channel observation counts"
```

---

### Task 4: Gate diagnosis on confidence

This is the only behaviour change in this phase, and it delivers the claim the parent spec already makes.

**Files:**
- Modify: `src/engines/learning/gaps.ts`
- Create: `scripts/test-learning-path-gaps.mts`
- Modify: `package.json` (register the new suite in `test` and `test:path`)

**Interfaces:**
- Consumes: `TopicState.confidence`, `CONFIDENCE_FLOOR`.
- Produces: `classifyTopic` returns `null` for a topic with evidence whose confidence is below `CONFIDENCE_FLOOR`. `gapQueue` therefore excludes it.

- [ ] **Step 1: Write the failing test suite**

Create `scripts/test-learning-path-gaps.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildGraph,
  type GraphNode,
  type GraphEdge,
  type KnowledgeGraph,
} from "../src/engines/learning/graph";
import { emptyAggregate, foldEvents, type FoldEvent } from "../src/engines/learning/fold";
import { scoreAggregate, type TopicStateMap } from "../src/engines/learning/mastery";
import { classifyTopic, gapQueue } from "../src/engines/learning/gaps";
import { CONFIDENCE_FLOOR } from "../src/engines/learning/evidence";

const now = new Date("2026-08-12T09:00:00Z");

function node(id: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id,
    subjectId: "subj-1",
    title: `Topic ${id}`,
    slug: `topic-${id}`,
    orderIndex: 0,
    estimatedMinutes: 45,
    waecWeight: 1,
    jambWeight: 1,
    prerequisiteTopicId: null,
    ...overrides,
  };
}

function graphWith(nodes: string[], edges: GraphEdge[] = []): KnowledgeGraph {
  return buildGraph(nodes.map((id) => node(id)), edges);
}

/** A topic whose state is folded from `count` answers, all correct or all wrong. */
function stateFrom(topicId: string, count: number, correct: boolean): TopicStateMap {
  const events: FoldEvent[] = Array.from({ length: count }, (_, i) => ({
    seq: BigInt(i + 1),
    topicId,
    kind: "QUESTION_ANSWERED" as const,
    correct,
    score: null,
    difficulty: "INTERMEDIATE" as const,
    seconds: 30,
    occurredAt: now,
  }));
  const aggregate = foldEvents(emptyAggregate(topicId, "subj-1", now), events, now);
  return new Map([[topicId, scoreAggregate(aggregate, now)]]);
}

// ─── The claim Phase 1 did not deliver ─────────────────────

test("classifyTopic: one wrong answer is not enough to diagnose a weakness", () => {
  const graph = graphWith(["t"]);
  const state = stateFrom("t", 1, false);
  const topic = state.get("t");

  assert.equal(topic?.mastery, 39, "one wrong intermediate answer scores 39");
  assert.ok(
    (topic?.confidence ?? 0) < CONFIDENCE_FLOOR,
    "and carries too little confidence to act on",
  );
  assert.equal(classifyTopic(state, graph, "t"), null);
});

test("gapQueue: a thinly-evidenced topic is not listed as a gap", () => {
  const graph = graphWith(["t"]);
  assert.deepEqual(gapQueue(stateFrom("t", 1, false), graph), []);
});

test("classifyTopic: ten wrong answers still diagnose a weakness", () => {
  const graph = graphWith(["t"]);
  const state = stateFrom("t", 10, false);
  const topic = state.get("t");

  assert.equal(topic?.mastery, 24);
  assert.ok((topic?.confidence ?? 0) >= CONFIDENCE_FLOOR);
  assert.equal(classifyTopic(state, graph, "t"), "WEAK");
});

test("gapQueue: a well-evidenced weak topic is listed", () => {
  const graph = graphWith(["t"]);
  const gaps = gapQueue(stateFrom("t", 10, false), graph);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].topicId, "t");
  assert.equal(gaps[0].category, "WEAK");
});

test("classifyTopic: the gate withholds judgement, it does not invert it", () => {
  // Confidence rises with evidence; the same wrong-answer rate crosses the
  // floor and becomes diagnosable rather than flipping category.
  const graph = graphWith(["t"]);
  assert.equal(classifyTopic(stateFrom("t", 1, false), graph, "t"), null);
  assert.equal(classifyTopic(stateFrom("t", 10, false), graph, "t"), "WEAK");
});

// ─── Unchanged behaviour ───────────────────────────────────

test("classifyTopic: a topic with no evidence at all is UNTOUCHED, not null", () => {
  const graph = graphWith(["t"]);
  const state: TopicStateMap = new Map([
    ["t", scoreAggregate(emptyAggregate("t", "subj-1", now), now)],
  ]);
  assert.equal(classifyTopic(state, graph, "t"), "UNTOUCHED");
});

test("gapQueue: UNTOUCHED topics are never gaps", () => {
  const graph = graphWith(["t"]);
  const state: TopicStateMap = new Map([
    ["t", scoreAggregate(emptyAggregate("t", "subj-1", now), now)],
  ]);
  assert.deepEqual(gapQueue(state, graph), []);
});

test("gapQueue: a strong topic is not a gap", () => {
  const graph = graphWith(["t"]);
  assert.deepEqual(gapQueue(stateFrom("t", 20, true), graph), []);
});
```

- [ ] **Step 2: Register the suite**

In `package.json`, append `scripts/test-learning-path-gaps.mts` to the file list of both the `test` and the `test:path` scripts.

- [ ] **Step 3: Run to verify it fails**

Run: `npx tsx --test scripts/test-learning-path-gaps.mts`
Expected: FAIL. The "one wrong answer" tests fail because `classifyTopic` currently returns `WEAK`.

- [ ] **Step 4: Add the gate**

In `src/engines/learning/gaps.ts`, add `CONFIDENCE_FLOOR` to the imports from `./evidence` (the file already imports `examWeight`, `unmasteredDependents`, `WEAK_MASTERY` and `GAP_RETENTION` from `./recommend` — `CONFIDENCE_FLOOR` comes from `./evidence`):

```ts
import { CONFIDENCE_FLOOR } from "./evidence";
```

The gate applies to `WEAK` and `DECAYED` **only** — not to `BOTTLENECK`.

`WEAK` and `DECAYED` are claims about this student's ability: acting on them
with one answer's worth of evidence diagnoses a weakness from noise.
`BOTTLENECK` is structural — "this locked topic blocks two or more unmastered
dependents" — and its truth does not depend on how well-evidenced the student's
mastery of it is. Gating it would also suppress the category hardest exactly
where it matters, since a locked topic has had the least opportunity to
accumulate evidence.

In `classifyTopic`, replace the `WEAK` and `DECAYED` checks so both are guarded,
leaving the `BOTTLENECK` check untouched below them:

```ts
  // Enough evidence, not merely some. A topic scored from one answer has a
  // mastery figure, but acting on it would diagnose a weakness from noise.
  // Below the floor we withhold judgement rather than guess — the topic is
  // neither a gap nor untouched, it is simply not yet measured.
  //
  // BOTTLENECK is deliberately NOT gated: it asserts something about the graph
  // and about other topics' mastery, not about how well we know this one.
  const confident = topic.confidence >= CONFIDENCE_FLOOR;

  if (confident && topic.mastery < WEAK_MASTERY) return "WEAK";
  if (confident && topic.retention != null && topic.retention < GAP_RETENTION) {
    return "DECAYED";
  }
```

A thinly-evidenced topic therefore falls through to the `BOTTLENECK` check and,
failing that, returns `null`.

- [ ] **Step 5: Run to verify it passes**

Run: `npx tsx --test scripts/test-learning-path-gaps.mts`
Expected: PASS, 8 tests.

- [ ] **Step 6: Run the regression gate**

Run: `npm run test:path && npm run typecheck:tests`
Expected: PASS with `-recommend.mts`, `-revision.mts`, `-plan.mts`, `-pretest.mts` and `-graph.mts` **unedited**. If one fails, report it — do not edit the test.

- [ ] **Step 7: Commit**

```bash
git add src/engines/learning/gaps.ts scripts/test-learning-path-gaps.mts package.json
git commit -m "feat(learning): withhold diagnosis below the confidence floor"
```

---

### Task 5: The evidence-count display

**Files:**
- Create: `src/lib/evidence-display.ts`
- Create: `scripts/test-evidence-display.mts`
- Modify: `package.json` (register the new suite)
- Modify: `src/engines/learning/recommend.ts` (carry the counts on the recommendation)
- Modify: `src/engines/learning/gaps.ts` (carry the counts on the gap)
- Modify: `src/components/path/next-topics.tsx:64`, `src/components/path/gap-list.tsx:73`

**Interfaces:**
- Consumes: `TopicState.{confidence,accObservations,lessonObservations,srsObservations}`, `CONFIDENCE_FLOOR`.
- Produces: `type EvidenceCounts = { confidence: number; accObservations: number; lessonObservations: number; srsObservations: number }`; `evidenceLabel(counts: EvidenceCounts): string | null` — `null` means "confident enough, show the mastery figure". `NextTopicRecommendation` and `TopicGap` both gain the four `EvidenceCounts` fields.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-evidence-display.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { evidenceLabel } from "../src/lib/evidence-display";

const confident = { confidence: 0.8, accObservations: 0, lessonObservations: 0, srsObservations: 0 };
const thin = { confidence: 0.2, accObservations: 0, lessonObservations: 0, srsObservations: 0 };

test("evidenceLabel: above the floor there is no label — show the mastery figure", () => {
  assert.equal(evidenceLabel({ ...confident, accObservations: 20 }), null);
});

test("evidenceLabel: exactly at the floor is confident enough", () => {
  assert.equal(evidenceLabel({ ...thin, confidence: 0.35, accObservations: 4 }), null);
});

test("evidenceLabel: questions are reported by count", () => {
  assert.equal(evidenceLabel({ ...thin, accObservations: 3 }), "3 questions answered");
});

test("evidenceLabel: one question reads in the singular", () => {
  assert.equal(evidenceLabel({ ...thin, accObservations: 1 }), "1 question answered");
});

test("evidenceLabel: practice wins when several channels have evidence", () => {
  assert.equal(
    evidenceLabel({ ...thin, accObservations: 2, lessonObservations: 5, srsObservations: 9 }),
    "2 questions answered",
  );
});

test("evidenceLabel: lesson-only evidence reads as progress, not a count", () => {
  assert.equal(evidenceLabel({ ...thin, lessonObservations: 2 }), "Lesson in progress");
});

test("evidenceLabel: card reviews are reported by count, with plurals", () => {
  assert.equal(evidenceLabel({ ...thin, srsObservations: 1 }), "1 card review");
  assert.equal(evidenceLabel({ ...thin, srsObservations: 4 }), "4 card reviews");
});

test("evidenceLabel: no evidence at all has no label", () => {
  assert.equal(evidenceLabel(thin), null);
});
```

- [ ] **Step 2: Register the suite**

In `package.json`, append `scripts/test-evidence-display.mts` to both the `test` and `test:path` script file lists.

- [ ] **Step 3: Run to verify it fails**

Run: `npx tsx --test scripts/test-evidence-display.mts`
Expected: FAIL — cannot find module `../src/lib/evidence-display`.

- [ ] **Step 4: Write the helper**

Create `src/lib/evidence-display.ts`:

```ts
import { CONFIDENCE_FLOOR } from "@/engines/learning/evidence";

// What a topic shows instead of a mastery figure when too little evidence
// backs it. See docs/superpowers/specs/2026-08-12-learning-evidence-layer-phase-2-design.md

export type EvidenceCounts = {
  confidence: number;
  accObservations: number;
  lessonObservations: number;
  srsObservations: number;
};

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/**
 * The line to show in place of "N% mastery", or `null` when there is enough
 * evidence for the figure to mean something.
 *
 * Below the floor a percentage is worse than useless: it looks precise and
 * isn't. A count is honest about how much is behind it, and tells the student
 * what resolves it. Practice wins when channels are mixed because "questions
 * answered" is the model students already have.
 *
 * A topic with no evidence at all also returns null — it is untouched rather
 * than thinly measured, and its zero mastery is shown as it always was.
 */
export function evidenceLabel(counts: EvidenceCounts): string | null {
  if (counts.confidence >= CONFIDENCE_FLOOR) return null;
  if (counts.accObservations > 0) {
    return `${plural(counts.accObservations, "question")} answered`;
  }
  if (counts.lessonObservations > 0) return "Lesson in progress";
  if (counts.srsObservations > 0) {
    return plural(counts.srsObservations, "card review");
  }
  return null;
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npx tsx --test scripts/test-evidence-display.mts`
Expected: PASS, 8 tests.

- [ ] **Step 6: Carry the counts on the recommendation**

In `src/engines/learning/recommend.ts`, add to the `NextTopicRecommendation` interface after `unlocks`:

```ts
  /** Evidence behind `mastery`, for deciding whether to show it at all. */
  confidence: number;
  accObservations: number;
  lessonObservations: number;
  srsObservations: number;
```

In `toRecommendation`, populate them from the topic state it already reads:

```ts
    confidence: topic?.confidence ?? 0,
    accObservations: topic?.accObservations ?? 0,
    lessonObservations: topic?.lessonObservations ?? 0,
    srsObservations: topic?.srsObservations ?? 0,
```

There is a **third** construction site the earlier draft of this plan missed:
`revisionItemToRecommendation` in `src/engines/learning/revision.ts`, which
converts a `RevisionQueueItem` into a `NextTopicRecommendation` for the
dashboard's consolidation fallback. `RevisionQueueItem` carries no per-channel
counts, and revision items are by definition topics that were mastered and have
since decayed — so they genuinely have evidence. Add:

```ts
    // RevisionQueueItem carries no per-channel evidence counts; these items
    // already have an established mastery figure (mastered, then decayed),
    // so present as fully confident rather than thinly measured.
    confidence: 1,
    accObservations: 0,
    lessonObservations: 0,
    srsObservations: 0,
```

`confidence: 1` makes `evidenceLabel` return `null`, so these keep rendering
their mastery percentage exactly as before.

In `masteredButDecayed`, the recommendation is built inline from `topic`; add the same four fields there:

```ts
        confidence: topic.confidence,
        accObservations: topic.accObservations,
        lessonObservations: topic.lessonObservations,
        srsObservations: topic.srsObservations,
```

- [ ] **Step 7: Carry the counts on the gap**

In `src/engines/learning/gaps.ts`, add to the `TopicGap` interface after `blockedCount`:

```ts
  /** Evidence behind `mastery`, for deciding whether to show it at all. */
  confidence: number;
  accObservations: number;
  lessonObservations: number;
  srsObservations: number;
```

In `gapQueue`, populate them in the pushed object from the `topic` already in scope:

```ts
      confidence: topic.confidence,
      accObservations: topic.accObservations,
      lessonObservations: topic.lessonObservations,
      srsObservations: topic.srsObservations,
```

- [ ] **Step 8: Wire the two rails**

In `src/components/path/next-topics.tsx`, add the import:

```ts
import { evidenceLabel } from "@/lib/evidence-display";
```

Replace line 64:

```tsx
                <span className="text-xs text-muted">{item.mastery}% mastery</span>
```

with:

```tsx
                <span className="text-xs text-muted">
                  {evidenceLabel(item) ?? `${item.mastery}% mastery`}
                </span>
```

In `src/components/path/gap-list.tsx`, add the same import and replace line 73:

```tsx
                <span className="text-xs text-muted">{gap.mastery}% mastery</span>
```

with:

```tsx
                <span className="text-xs text-muted">
                  {evidenceLabel(gap) ?? `${gap.mastery}% mastery`}
                </span>
```

`evidenceLabel` takes a structural `EvidenceCounts`, and both `item` and `gap` now carry those four fields, so they can be passed directly.

- [ ] **Step 9: Verify everything**

Run: `npx tsc --noEmit && npm run typecheck:tests && npm test && npm run lint`
Expected: all clean. `npm run typecheck:tests` will flag any suite constructing a `NextTopicRecommendation` or `TopicGap` literal without the new fields — add `confidence: 0, accObservations: 0, lessonObservations: 0, srsObservations: 0` to each. Assertions must not change.

- [ ] **Step 10: Commit**

```bash
git add src/lib/evidence-display.ts scripts/test-evidence-display.mts package.json src/engines/learning/recommend.ts src/engines/learning/gaps.ts src/components/path/next-topics.tsx src/components/path/gap-list.tsx
git commit -m "feat(learning): show an evidence count when confidence is low"
```

---

### Task 6: Emit abandonment

**Files:**
- Modify: `src/lib/attempt-lifecycle.ts`
- Create: `scripts/test-attempt-abandonment.mts`
- Modify: `package.json` (register the new suite)

**Interfaces:**
- Consumes: `LearningEventKind` from `@/types/prisma`.
- Produces: `distinctTopicRefs(questions: readonly { topicId: string | null; subjectId: string }[]): Array<{ topicId: string; subjectId: string }>` — exported from `src/lib/attempt-lifecycle.ts`, pure. `reapStaleAttempts` emits one `QUIZ_ABANDONED` per distinct topic per reaped attempt.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-attempt-abandonment.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { distinctTopicRefs } from "../src/lib/attempt-lifecycle";

test("distinctTopicRefs: one entry per topic, however many questions", () => {
  const refs = distinctTopicRefs([
    { topicId: "a", subjectId: "s1" },
    { topicId: "a", subjectId: "s1" },
    { topicId: "b", subjectId: "s1" },
    { topicId: "a", subjectId: "s1" },
  ]);
  assert.equal(refs.length, 2);
  assert.deepEqual(refs.map((r) => r.topicId).sort(), ["a", "b"]);
});

test("distinctTopicRefs: a 40-question paper over 12 topics yields 12", () => {
  const questions = Array.from({ length: 40 }, (_, i) => ({
    topicId: `t${i % 12}`,
    subjectId: "s1",
  }));
  assert.equal(distinctTopicRefs(questions).length, 12);
});

test("distinctTopicRefs: questions with no topic are dropped", () => {
  const refs = distinctTopicRefs([
    { topicId: null, subjectId: "s1" },
    { topicId: "a", subjectId: "s1" },
    { topicId: null, subjectId: "s2" },
  ]);
  assert.deepEqual(refs, [{ topicId: "a", subjectId: "s1" }]);
});

test("distinctTopicRefs: an empty paper yields nothing", () => {
  assert.deepEqual(distinctTopicRefs([]), []);
});

test("distinctTopicRefs: the first subject seen for a topic wins", () => {
  const refs = distinctTopicRefs([
    { topicId: "a", subjectId: "s1" },
    { topicId: "a", subjectId: "s2" },
  ]);
  assert.deepEqual(refs, [{ topicId: "a", subjectId: "s1" }]);
});
```

- [ ] **Step 2: Register the suite**

In `package.json`, append `scripts/test-attempt-abandonment.mts` to both the `test` and `test:path` script file lists.

- [ ] **Step 3: Run to verify it fails**

Run: `npx tsx --test scripts/test-attempt-abandonment.mts`
Expected: FAIL — `distinctTopicRefs` is not exported.

- [ ] **Step 4: Write the pure helper**

In `src/lib/attempt-lifecycle.ts`, add above `reapStaleAttempts`:

```ts
/**
 * The distinct topics a paper covers, for recording abandonment.
 *
 * One event per topic, not per question: a 40-question mock spanning 12 topics
 * means the student abandoned 12 topics once, not 40 times. Without the dedup
 * "started 3 times" would read as 120.
 */
export function distinctTopicRefs(
  questions: readonly { topicId: string | null; subjectId: string }[],
): Array<{ topicId: string; subjectId: string }> {
  const seen = new Map<string, string>();
  for (const question of questions) {
    if (!question.topicId) continue;
    if (!seen.has(question.topicId)) seen.set(question.topicId, question.subjectId);
  }
  return [...seen].map(([topicId, subjectId]) => ({ topicId, subjectId }));
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npx tsx --test scripts/test-attempt-abandonment.mts`
Expected: PASS, 5 tests.

- [ ] **Step 6: Emit on reap**

In `src/lib/attempt-lifecycle.ts`, extend `reapStaleAttempts`.

**Leave the first query exactly as it is.** It selects only `id`, `startedAt`
and `assessment.timeLimitMinutes` for up to 100 candidates, and that is
deliberate: `reapStaleAttempts` runs before **every** quiz generation
(`assessment-generation.ts:93`, `jamb-cbt-generation.ts:132`) and almost always
finds nothing to reap. Pulling each candidate's questions there would join every
question of up to 100 attempts — a JAMB CBT paper alone has 180 — on a hot path,
to discover there is nothing to do.

Keep the existing `expired` computation (a list of ids) unchanged too. Then,
**after** the `if (expired.length === 0) return 0;` early return, fetch the
questions for only the handful that actually expired:

```ts
  // Second query, and only once something has actually expired: the first query
  // stays lightweight because it runs on every quiz generation and usually
  // finds nothing. Here `expired` is typically zero or one attempt.
  const expiredAttempts = await db.assessmentAttempt.findMany({
    where: { id: { in: expired } },
    select: {
      id: true,
      startedAt: true,
      assessment: {
        select: {
          questions: {
            select: { question: { select: { topicId: true, subjectId: true } } },
          },
        },
      },
    },
  });
```

Then replace the bare `updateMany` with a transaction that also records the abandonment:

```ts
  // The status change and its ledger events commit together — the attempt row
  // is the domain row these events describe.
  //
  // `occurredAt` is the attempt's startedAt, not now: this reaper runs
  // opportunistically when the student next generates a quiz, so an attempt
  // abandoned on Monday may not be noticed until Thursday. The ledger records
  // when the student engaged, not when we found out.
  const events = expiredAttempts.flatMap((attempt) =>
    distinctTopicRefs(
      attempt.assessment.questions.map((aq) => aq.question),
    ).map((ref) => ({
      studentId,
      subjectId: ref.subjectId,
      topicId: ref.topicId,
      kind: "QUIZ_ABANDONED" as const,
      sourceId: attempt.id,
      occurredAt: attempt.startedAt,
    })),
  );

  const [result] = await db.$transaction([
    db.assessmentAttempt.updateMany({
      where: { id: { in: expired }, status: "IN_PROGRESS" },
      data: { status: "TIMED_OUT" },
    }),
    ...(events.length > 0 ? [db.learningEvent.createMany({ data: events })] : []),
  ]);
  return result.count;
```

- [ ] **Step 7: Keep the reaper non-blocking**

`reapStaleAttempts` is called before quiz generation (`assessment-generation.ts:93`, `jamb-cbt-generation.ts:132`). A failure here must not stop a student starting a quiz. Wrap the body's transaction so a throw is logged and reported as zero reaped:

```ts
  try {
    const [result] = await db.$transaction([ /* ...as above... */ ]);
    return result.count;
  } catch (error) {
    console.error("Reaping stale attempts failed:", error);
    return 0;
  }
```

- [ ] **Step 8: Verify**

Run: `npx tsc --noEmit && npm run typecheck:tests && npm test && npm run lint`
Expected: all clean. Skipped, and to be stated in your report: any live check that events are actually written.

- [ ] **Step 9: Commit**

```bash
git add src/lib/attempt-lifecycle.ts scripts/test-attempt-abandonment.mts package.json
git commit -m "feat(learning): record quiz abandonment per distinct topic"
```

---

### Task 7: Surface abandonment on the gap list

**Files:**
- Modify: `src/engines/learning/gaps.ts`
- Modify: `src/lib/dashboard.ts`
- Modify: `src/components/path/gap-list.tsx`
- Modify: `scripts/test-learning-path-gaps.mts` (append)

**Interfaces:**
- Consumes: `gapQueue` from Task 4, `TopicGap` from Task 5.
- Produces: `gapQueue(state, graph, pretestPassed?, abandonedByTopic?: ReadonlyMap<string, number>)` — a fourth optional parameter. `TopicGap` gains `abandonedCount: number`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-learning-path-gaps.mts`:

```ts
test("gapQueue: abandonment counts reach the gap", () => {
  const graph = graphWith(["t"]);
  const gaps = gapQueue(
    stateFrom("t", 10, false),
    graph,
    new Set(),
    new Map([["t", 3]]),
  );
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].abandonedCount, 3);
});

test("gapQueue: a topic never abandoned reports zero, not undefined", () => {
  const graph = graphWith(["t"]);
  const gaps = gapQueue(stateFrom("t", 10, false), graph);
  assert.equal(gaps[0].abandonedCount, 0);
});

test("gapQueue: abandonment does not change the ranking", () => {
  // `b` has the lower mastery, so it ranks first on the existing rule. Heavy
  // abandonment on `a` must not move it.
  const graph = graphWith(["a", "b"]);
  const state = new Map([
    ...stateFrom("a", 10, false),
    ...stateFrom("b", 20, false),
  ]);
  const ranked = gapQueue(state, graph, new Set(), new Map([["a", 99]]));
  assert.deepEqual(
    ranked.map((g) => g.topicId),
    gapQueue(state, graph).map((g) => g.topicId),
    "ranking must be identical with and without abandonment data",
  );
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx tsx --test scripts/test-learning-path-gaps.mts`
Expected: FAIL — `gapQueue` takes three parameters and `abandonedCount` does not exist.

- [ ] **Step 3: Extend `gapQueue`**

In `src/engines/learning/gaps.ts`, add to the `TopicGap` interface:

```ts
  /**
   * How many times this topic appeared in a quiz the student started and never
   * finished. Explains a gap; deliberately does not rank it.
   */
  abandonedCount: number;
```

Change the signature and populate the field:

```ts
export function gapQueue(
  state: TopicStateMap,
  graph: KnowledgeGraph,
  pretestPassed: ReadonlySet<string> = new Set(),
  abandonedByTopic: ReadonlyMap<string, number> = new Map(),
): TopicGap[] {
```

and in the pushed object:

```ts
      abandonedCount: abandonedByTopic.get(topicId) ?? 0,
```

The sort comparator is unchanged: bottleneck score descending, then mastery ascending.

- [ ] **Step 4: Load the counts on the dashboard**

In `src/lib/dashboard.ts`, inside `loadLearningPath`, after the `computePathState` call, add:

```ts
  // Display-only, so it is loaded here rather than folded into the aggregate:
  // rare, and read on one surface. Same pattern as `pretestPassed`.
  //
  // Decoration, not diagnosis: if this fails the gap list loses its reason
  // lines, which is a far better outcome than failing the dashboard.
  let abandonedByTopic = new Map<string, number>();
  try {
    const abandonedRows = await db.learningEvent.groupBy({
      by: ["topicId"],
      where: { studentId: userId, kind: "QUIZ_ABANDONED", topicId: { not: null } },
      _count: { _all: true },
    });
    abandonedByTopic = new Map(
      abandonedRows
        .filter((row): row is typeof row & { topicId: string } => row.topicId !== null)
        .map((row) => [row.topicId, row._count._all]),
    );
  } catch (error) {
    console.error("Loading abandonment counts failed:", error);
  }
```

Change the `gapQueue` call to pass it:

```ts
  return {
    subjects,
    learningPicks,
    gaps: gapQueue(state, graph, pretestPassed, abandonedByTopic),
    revision,
  };
```

- [ ] **Step 5: Render the reason line**

In `src/components/path/gap-list.tsx`, inside the `<div className="mt-1 flex flex-wrap ...">` block, after the mastery/evidence span, add:

```tsx
                {gap.abandonedCount > 0 && (
                  <span className="text-xs text-muted">
                    Started {gap.abandonedCount}{" "}
                    {gap.abandonedCount === 1 ? "time" : "times"} without finishing
                  </span>
                )}
```

- [ ] **Step 6: Verify**

Run: `npx tsx --test scripts/test-learning-path-gaps.mts && npx tsc --noEmit && npm run typecheck:tests && npm test && npm run lint`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/engines/learning/gaps.ts src/lib/dashboard.ts src/components/path/gap-list.tsx scripts/test-learning-path-gaps.mts
git commit -m "feat(learning): explain gaps with abandonment counts"
```

---

### Task 8: Real numbers on the Performance page

**Files:**
- Modify: `src/lib/performance.ts`
- Modify: `prisma/schema.prisma`
- Modify: `src/types/prisma.ts`
- Create: `prisma/migrations/20260812010000_drop_dead_performance_columns/migration.sql`

**Interfaces:**
- Consumes: the `LearningEvent` ledger.
- Produces: `getPerformanceData` returns real `subjectMetrics`. `PerformanceMetric` loses `totalAttempted`, `totalCorrect`, `accuracy` and `averageTimePerQuestion`.

- [ ] **Step 1: Confirm nothing else reads the four columns**

Run:

```bash
grep -rn "totalAttempted\|totalCorrect\|averageTimePerQuestion" src --include=*.ts --include=*.tsx
grep -rn "\.accuracy\|accuracy:" src --include=*.ts --include=*.tsx | grep -v "performance.ts"
```

Expected: hits only in `src/lib/performance.ts`, `src/app/(dashboard)/performance/page.tsx` (which reads the service's output, not the columns) and `src/types/prisma.ts`. **If anything else reads them, stop and report it** — a stale grep is how the `pretestPassedAt` mirror drift survived unnoticed.

- [ ] **Step 2: Derive the counts from the ledger**

In `src/lib/performance.ts`, replace the `db.performanceMetric.findMany({...})` entry in the `db.$transaction([...])` array with a ledger aggregation. Because `groupBy` cannot conditionally count in one pass, run two groupings and join them:

```ts
    db.learningEvent.groupBy({
      by: ["subjectId"],
      where: { studentId: userId, kind: "QUESTION_ANSWERED" },
      _count: { _all: true },
    }),
    db.learningEvent.groupBy({
      by: ["subjectId"],
      where: { studentId: userId, kind: "QUESTION_ANSWERED", correct: true },
      _count: { _all: true },
    }),
```

The transaction now returns three results rather than two, so change the destructure from

```ts
  const [attempts, subjectMetrics] = await db.$transaction([
```

to

```ts
  const [attempts, attemptedRows, correctRows] = await db.$transaction([
```

keeping the existing `assessmentAttempt.findMany` as the first entry.

The groupings return `subjectId` only, so the subject's name, slug and code need a lookup. After the transaction:

```ts
  const correctBySubject = new Map(
    correctRows.map((row) => [row.subjectId, row._count._all]),
  );
  const subjectIds = attemptedRows.map((row) => row.subjectId);
  const subjects = await db.subject.findMany({
    where: { id: { in: subjectIds } },
    select: { id: true, name: true, slug: true, code: true },
  });
  const subjectById = new Map(subjects.map((s) => [s.id, s]));

  const subjectMetrics = attemptedRows
    .flatMap((row) => {
      const subject = subjectById.get(row.subjectId);
      if (!subject) return [];
      const totalAttempted = row._count._all;
      const totalCorrect = correctBySubject.get(row.subjectId) ?? 0;
      return [{
        subjectName: subject.name,
        subjectSlug: subject.slug,
        subjectCode: subject.code,
        totalAttempted,
        totalCorrect,
        accuracy: totalAttempted > 0 ? (totalCorrect / totalAttempted) * 100 : 0,
      }];
    })
    .sort((a, b) => b.accuracy - a.accuracy);
```

The old query ordered by `accuracy` descending; the sort above preserves that. Return `subjectMetrics` directly instead of mapping the old rows.

- [ ] **Step 3: Drop the dead columns**

In `prisma/schema.prisma`, delete these four lines from `model PerformanceMetric`:

```prisma
  totalAttempted         Int          @default(0)
  totalCorrect           Int          @default(0)
  accuracy               Float        @default(0)
  averageTimePerQuestion Float?
```

Keep `masteryLevel`, `lastUpdated` and `pretestPassedAt` — `src/lib/achievements.ts:178` counts `masteryLevel: "STRONG"`, `src/lib/flashcard-analytics.ts:414` selects on `masteryLevel`, and `src/lib/learning-path.ts:196` reads `pretestPassedAt`.

Delete the same four fields from the `PerformanceMetric` type in `src/types/prisma.ts`.

- [ ] **Step 4: Author the migration offline**

```bash
git show HEAD:prisma/schema.prisma > "$TMPDIR/schema-before-p2b.prisma"
mkdir -p prisma/migrations/20260812010000_drop_dead_performance_columns
npx prisma migrate diff \
  --from-schema-datamodel "$TMPDIR/schema-before-p2b.prisma" \
  --to-schema-datamodel prisma/schema.prisma \
  --script > prisma/migrations/20260812010000_drop_dead_performance_columns/migration.sql
npx prisma generate
```

Verify: the SQL contains exactly four `DROP COLUMN` statements against `PerformanceMetric` and touches no other table. Stop and report if it touches anything else. **Do not apply it.**

- [ ] **Step 5: Verify**

Run: `npx tsc --noEmit && npm run typecheck:tests && npm test && npm run lint`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/lib/performance.ts prisma/schema.prisma prisma/migrations src/types/prisma.ts
git commit -m "feat(performance): derive subject metrics from the ledger"
```

---

## Verification still outstanding

The database has been unreachable throughout Phase 1 and this plan, so two migrations are now authored and unapplied. Phase 1's list lives in the parent spec under "Verification still outstanding"; run it first, then:

1. `npx prisma migrate deploy` — applies `20260812000000_observation_counts` and `20260812010000_drop_dead_performance_columns` after Phase 1's. Confirm both apply cleanly.
2. Answer one question correctly. The "Keep learning" rail must read **"1 question answered"**, not a percentage, and the topic must **not** appear under "Tighten your gaps".
3. Answer ten incorrectly on another topic. It must now appear as a gap with a mastery figure.
4. Abandon a quiz, then generate another to trigger the reaper. Confirm one `QUIZ_ABANDONED` per distinct topic in the abandoned paper, timestamped at the attempt's `startedAt`, and that the gap list shows "Started 1 time without finishing".
5. Load the Performance page as a student with history. Confirm real per-subject counts rather than zeros.

## Out of scope

Phase 3: snapshots, velocity, the exam-readiness forecast, difficulty-targeted question selection, and the automatic cursor-reset reconciliation pass.

Also deliberately not fixed here, and documented in the spec's §6: `PerformanceMetric.masteryLevel` is written only on lesson completion and pretest pass, so `achievements.ts` and `flashcard-analytics.ts` read a level that ignores quiz performance entirely. Fixing it means deriving `masteryLevel` from `computeTopicState` at those call sites, which pulls two unrelated features into this phase. It belongs in its own change.
