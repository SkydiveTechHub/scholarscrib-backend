# Phase 2 fix wave — whole-branch review findings

Base commit: `130c6cf`. Source: the Phase 2 whole-branch review (0 Critical, 7
Important, 3 Minor), plus three user rulings taken 2026-08-17.

The review's verdict was NEEDS FIXES, but nothing it found was Critical: no data
loss, no crash path, no incorrect persisted value. The engine arithmetic
reproduces the spec's worked values exactly. Every finding below is a seam
between tasks — which is precisely what the per-task reviews could not see.

## Rulings this plan implements

1. **DECAYED is gated on observations, not confidence.** Confidence decays with
   age (45-day half-life) and so does retention, so the two conditions chased
   each other and DECAYED was reachable only inside a bounded age window — a
   3-answer topic could never be DECAYED at all, and a 10-answer topic stopped
   being DECAYED after ~99 days. Raw observation counts do not decay, so a topic
   the student once knew stays flagged as faded however long ago it was learned.
   WEAK keeps the confidence gate.
2. **Abandonment alone can qualify a topic as a gap.** Previously
   `abandonedCount` could only render on a topic that independently qualified,
   so the commonest case — open a mock on an unpractised topic, bail — produced
   a correct ledger row and zero user-visible output.
3. **A `QuestionResponse → LearningEvent` backfill is written.** The ledger has
   never been written; without a backfill the Performance page reads empty above
   a populated weak-topics list drawn from the same history.

## Decisions taken while planning (flag if wrong)

- **`OBSERVATION_FLOOR = 3`.** Chosen to match the existing confidence floor
  rather than to introduce a second, unrelated notion of "enough". The review
  computed that `CONFIDENCE_FLOOR = 0.35` is crossed at mass 2.1538, i.e. the
  third fresh non-rapid answer. So for fresh evidence the two gates agree
  exactly; they diverge only as evidence ages, which is the entire point.
- **`ABANDONED_FLOOR = 2`.** One abandonment is a misclick, a phone call, a
  closed laptop. Two is a pattern. This is the one number here with no prior
  art in the spec, so it is called out for review rather than buried.
- **ABANDONED ranks below the evidenced categories.** A topic with no evidence
  has `mastery` 0 and `bottleneckScore` 0, so under the existing comparator
  (score desc, then mastery asc) it would sort *ahead* of every other score-0
  gap — crowding out topics we have actually measured. ABANDONED therefore
  sorts into its own tier at the bottom. This does not change the ordering of
  WEAK/DECAYED/BOTTLENECK relative to each other, so the spec's "abandonment
  explains, it does not rank" still holds for everything it applied to before.

---

## Task 1: Make the revision rail honest

**Files:** `src/engines/learning/revision.ts`, `scripts/test-learning-path-revision.mts`

The `confidence: 1` sentinel at `revision.ts:265-271` is justified by a comment
that is factually wrong: it claims revision items "already have an established
mastery figure (mastered, then decayed)", but `revisionQueue` applies **no
mastery threshold** — only `lastStudy != null` and `isRevisionDue`. Contrast
`masteredButDecayed` in `recommend.ts:190`, which does gate on
`mastery >= TARGET` and is safe.

Consequence, probed by the reviewer against the real engine: one wrong answer 20
days old renders `1 question answered` in the gap list and `40% mastery` on the
rail, in the same dashboard render.

- [ ] **Step 1** — Add a failing test to `test-learning-path-revision.mts`: build
  a thinly-evidenced due topic (`confidence` below `CONFIDENCE_FLOOR`, one
  observation), push it through `revisionQueue` → `revisionItemToRecommendation`,
  and assert the resulting recommendation's `confidence` and `accObservations`
  are the topic's real values, not `1` and `0`.
- [ ] **Step 2** — Add `confidence`, `accObservations`, `lessonObservations`,
  `srsObservations` to `RevisionQueueItem`, populated from `topic` inside
  `revisionQueue`'s loop.
- [ ] **Step 3** — `revisionItemToRecommendation` passes them through. Delete the
  sentinel and its comment.
- [ ] **Step 4** — `mkState` in the revision suite currently hardcodes
  `confidence: 0` with a comment saying nothing reads it. That is no longer
  true; give it a parameter defaulting to a confident value so existing
  assertions are unaffected.

## Task 2: Gate DECAYED on observations

**Files:** `src/engines/learning/evidence.ts`, `src/engines/learning/gaps.ts`,
`scripts/test-learning-path-gaps.mts`

- [ ] **Step 1** — Add to `evidence.ts`, beside `CONFIDENCE_FLOOR`:

```ts
/**
 * Minimum raw observations before a topic can be diagnosed DECAYED.
 *
 * Confidence decays with age; observation counts do not. DECAYED asks "did
 * this student once know it and lose it?", which is a question about how much
 * evidence was ever gathered, not about how fresh that evidence is. Gating it
 * on confidence made the category chase its own tail: retention and confidence
 * fall together, so a topic became DECAYED and then silently stopped being
 * DECAYED while getting staler.
 *
 * 3 matches CONFIDENCE_FLOOR for fresh evidence — mass crosses 0.35 on the
 * third non-rapid answer — so the two gates agree at t=0 and diverge only with
 * age, which is the intent.
 */
export const OBSERVATION_FLOOR = 3;
```

- [ ] **Step 2** — In `classifyTopic` (`gaps.ts:105-114`), keep `confident` for
  WEAK and introduce a separate `measured` for DECAYED:

```ts
  const confident = topic.confidence >= CONFIDENCE_FLOOR;
  const observations =
    topic.accObservations + topic.lessonObservations + topic.srsObservations;
  const measured = observations >= OBSERVATION_FLOOR;

  if (confident && topic.mastery < WEAK_MASTERY) return "WEAK";
  if (measured && topic.retention != null && topic.retention < GAP_RETENTION) {
    return "DECAYED";
  }
```

  Update the block comment above it: it currently explains one gate, and there
  are now two with different jobs.

- [ ] **Step 3** — Tests. These are the assertions the review found missing
  entirely, so they matter more than the code change:
  - a 2-observation faded topic is **not** DECAYED;
  - a 3-observation faded topic **is**;
  - **an aged fixture**: a topic with 10 observations, retention below
    `GAP_RETENTION`, and confidence *below* the floor is still DECAYED. This is
    the regression that motivated the whole task — it must fail before Step 2.

## Task 3: Wire the third surface

**Files:** `src/components/path/graph-view.tsx`, `src/lib/classroom-data.ts`

Spec §3 names three surfaces (lines 33 and 149); the plan built two.
`graph-view.tsx:232-233` renders `` `${node.mastery}% mastery` `` unconditionally.

- [ ] **Step 1** — Add `confidence: number` and the three observation counts to
  `GraphViewNode` (`graph-view.tsx:19-27`).
- [ ] **Step 2** — Populate them in `classroom-data.ts`'s node loop (~line 160)
  from `topicState`, defaulting to `0` when there is no state — a topic with no
  state has no evidence, so a zero confidence is the honest value.
- [ ] **Step 3** — In `graph-view.tsx:232-233`, route both branches through
  `evidenceLabel` exactly as `gap-list.tsx:75` does:
  `evidenceLabel(node) ?? \`${node.mastery}% mastery\``, and for the revision
  branch `` `Revision due · ${evidenceLabel(node) ?? `${node.mastery}%`}` ``.
- [ ] **Step 4** — No test suite covers this component. Do not invent one here;
  note it in the report as the third surface with no coverage.

## Task 4: Let abandonment qualify a topic as a gap

**Files:** `src/engines/learning/gaps.ts`, `src/lib/dashboard.ts`,
`src/components/path/gap-list.tsx`, `scripts/test-learning-path-gaps.mts`

`QUIZ_ABANDONED` contributes no channel evidence (`fold.ts:142-147`) and is not
in `EFFORT_KINDS`, so an abandoned-but-never-answered topic fails the
`hasEvidence` guard at `gaps.ts:93-96` and never reaches the categories.

- [ ] **Step 1** — Add `ABANDONED` to `GapCategory` and `ABANDONED_FLOOR = 2` to
  `evidence.ts`, with the "one is a misclick, two is a pattern" rationale.
- [ ] **Step 2** — `classifyTopic` takes a fifth parameter
  `abandonedByTopic: ReadonlyMap<string, number> = new Map()`. Inside the
  `!hasEvidence` branch, before the `UNTOUCHED` return:

```ts
  if (!hasEvidence) {
    if ((abandonedByTopic.get(topicId) ?? 0) >= ABANDONED_FLOOR) return "ABANDONED";
    return isAvailable(topicId, state, graph, pretestPassed) ? "UNTOUCHED" : null;
  }
```

  A topic with evidence keeps its existing classification — abandonment only
  rescues the no-evidence case.
- [ ] **Step 3** — `gapQueue` passes `abandonedByTopic` into `classifyTopic` and
  admits `ABANDONED` to the queue. Sort: ABANDONED into its own bottom tier —
  add a leading comparator key so `ABANDONED` sorts after everything else, then
  the existing score-desc / mastery-asc within each tier, unchanged.
- [ ] **Step 4** — `gap-list.tsx` needs a label, icon and variant for the new
  category. Copy: **"Started without finishing"**. The existing
  `abandonedCount` reason line already renders beneath it.
- [ ] **Step 5** — Tests: 1 abandonment on an unevidenced topic is not a gap;
  2 is, and is classified ABANDONED; an evidenced weak topic with 5
  abandonments is still WEAK, not ABANDONED; and the ranking test — an
  ABANDONED topic sorts below a WEAK one even though its mastery is lower.

## Task 5: Stop concurrent reaps double-counting

**Files:** `src/lib/attempt-lifecycle.ts`, `scripts/test-attempt-abandonment.mts`

`attempt-lifecycle.ts:124-130`: the `updateMany` is idempotent (guarded by
`status: "IN_PROGRESS"`), the `createMany` is not, and `LearningEvent` has no
unique constraint. Two overlapping generate requests both insert full event
sets.

Prefer the interactive-transaction fix over a unique constraint: a partial
unique index is not expressible in the Prisma schema, and the DB is unreachable
so a fourth migration cannot be validated.

- [ ] **Step 1** — Replace the batch transaction with an interactive one that
  transitions **one attempt at a time** and emits that attempt's events only
  when its own update reports `count === 1`. `expired` is typically 0–1
  entries, so the loop is not a hot path.
- [ ] **Step 2** — Keep the whole body inside the existing `try` — the global
  constraint is that nothing here may block quiz generation. This is the defect
  Task 6 shipped and had to fix; do not reintroduce it.
- [ ] **Step 3** — The suite currently tests only `distinctTopicRefs`. Extract
  whatever is needed to assert that a second reap of the same attempt emits
  nothing, without requiring a database.

## Task 6: Test the gate that shipped

**Files:** `scripts/test-learning-path-recommend.mts`

`mkState:66` hardcodes `confidence: 0.8`, so all five `classifyTopic` tests and
both `gapQueue` ranking tests **would still pass if the confidence gate were
deleted entirely**. They were adjusted to accommodate the gate, not to exercise
it.

- [ ] **Step 1** — Give `mkState` a `confidence` parameter defaulting to `0.8`,
  and observation parameters defaulting to a measured value, so existing
  assertions are untouched.
- [ ] **Step 2** — Add: sub-floor WEAK is withheld; sub-floor BOTTLENECK is
  **not** withheld (the user's ruling, currently unpinned by any assertion).

## Task 7: Backfill the ledger

**Files:** `scripts/backfill-learning-events.ts` (new)

- [ ] **Step 1** — Replay `QuestionResponse` rows into `LearningEvent` as
  `QUESTION_ANSWERED`, carrying `studentId`, `subjectId`, `topicId`, `correct`,
  the authored `difficulty`, `seconds`, `sourceId = questionId`, and
  `occurredAt` from the response's own timestamp — **not** `now()`, or the
  decay maths will treat years-old history as fresh.
- [ ] **Step 2** — Idempotent and resumable: batch with a cursor, and skip
  students who already have `QUESTION_ANSWERED` events. It will first run
  against a database nobody has been able to reach, so it must be safe to run
  twice.
- [ ] **Step 3** — Dry-run mode printing counts per student and per subject
  without writing. Default to dry-run; require an explicit flag to write.
- [ ] **Step 4** — After backfilling, `TopicMastery.cursorSeq` must be reset to
  0 for affected students so the next read replays the full ledger. Do this in
  the same script.
- [ ] **Step 5** — Do **not** run it. The database is unreachable and this is
  the one script here that writes.

## Task 8: Reconcile the documents and the leftovers

**Files:** the Phase 2 spec, `src/engines/learning/mastery.ts`,
`src/app/(dashboard)/performance/page.tsx`, `prisma/schema.prisma`

- [ ] **Step 1** — Spec §Testing line 278 still says DECAYED and BOTTLENECK "are
  gated the same way", contradicting §2 after the ruling. Fix it, and record
  the new observation gate.
- [ ] **Step 2** — Spec §2's justification for exempting BOTTLENECK describes a
  *zero*-evidence topic, which `gaps.ts:93-96` blocks anyway; the exemption only
  ever rescues `0 < confidence < 0.35`. The ruling stands, its stated reason
  does not. Rewrite the rationale to match what the code does.
- [ ] **Step 3** — `assembleTopicState` (`mastery.ts:109-130`) hardcodes
  `confidence: 0` and zero observations, making any state it builds permanently
  undiagnosable. Mark it test-only in a comment, or make it honest.
- [ ] **Step 4** — `performance/page.tsx:117` hardcodes the plural and now
  renders "1 questions · 1 correct" with real data. Fix. Note in the report —
  do not fix without a ruling — that `:99`/`:126` derive a grade letter and a
  full-width progress bar from single-answer accuracy with no denominator, and
  `:29-32` averages per-subject accuracy unweighted.
- [ ] **Step 5** — `schema.prisma:640` `scoringVersion @default(1)` is inert but
  stale. Leave the default alone (changing it needs a migration for no gain);
  add a comment pointing at `SCORING_VERSION`.

---

## Out of scope

Phase 3 still owns: snapshots, velocity, the exam-readiness forecast,
difficulty-targeted question selection, and the automatic cursor-reset
reconciliation pass.

The `LearningEvent` index gap the review flagged — the two Performance
`groupBy`s filter `studentId + kind` and group by `subjectId`, and neither
existing index covers `kind` or `subjectId` — is deliberately left alone. It
needs `EXPLAIN ANALYZE` against real data to size, and the database has never
been reachable. Recorded in the verification list instead.

## Verification still outstanding

Unchanged from the Phase 2 plan, plus:

6. Confirm a topic with 10 answers and retention below `GAP_RETENTION` is still
   DECAYED a year later.
7. Abandon a quiz twice on a never-practised topic; confirm it enters the gap
   list as "Started without finishing" and does not outrank measured gaps.
8. Run the backfill in dry-run, check the counts, then for real; confirm the
   Performance page and the weak-topics list agree afterwards.
9. `EXPLAIN ANALYZE` both Performance `groupBy`s against a seeded ledger.
