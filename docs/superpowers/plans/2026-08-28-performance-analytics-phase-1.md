# Performance Analytics — Phase 1 (Subject Lens) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Performance page's wrong-count weakness ranking with a real subject lens — a per-subject verdict, a five-group topic breakdown driven by the existing mastery engine, and a difficulty/pacing/rapid-guess profile.

**Architecture:** A new `src/engines/analytics/` of pure, database-free functions layered over the existing learning engines (`classifyTopic`, `computeTopicState`, `gapQueue`'s ordering rule), plus a `src/lib/analytics/` loader that assembles them from Prisma. `/performance` becomes a tab shell; the existing page stays as Overview and loses only its wrong-count block. No schema change, no migration.

**Tech Stack:** Next.js 16 (App Router, React 19, server components by default), Prisma 6, TypeScript, Tailwind v4, `node --test` via `tsx` for unit tests.

**Spec:** `docs/superpowers/specs/2026-08-28-performance-analytics-design.md`

## Global Constraints

- **Next.js in this repo is not the Next.js you know.** Before writing any page, layout, or route file, read the relevant guide in `node_modules/next/dist/docs/`. Heed deprecation notices. (`AGENTS.md`)
- **`params` and `searchParams` are Promises** and must be awaited. Existing example: `src/app/(dashboard)/classroom/page.tsx:16-23`.
- **Server components by default.** Add `"use client"` only where an interaction genuinely needs it. Collapsible sections in this plan use native `<details>/<summary>`, which needs no client JS.
- **Never read `PerformanceMetric.masteryLevel`.** It is stale — written only by `topic-practice-result.ts` and `pretest.ts`, never by answering questions. Mastery comes from `TopicMastery` via `computeTopicState`, always.
- **Never invent an evidence convention.** Below the floors, use `evidenceLabel()` from `src/lib/evidence-display.ts`. Floors come from `src/engines/learning/evidence.ts` (`CONFIDENCE_FLOOR`, `OBSERVATION_FLOOR`); thresholds come from `src/engines/learning/recommend.ts` (`WEAK_MASTERY = 50`) and `src/engines/learning/availability.ts` (`TARGET = 70`). Import them; do not re-declare their values.
- **Grades are `getGrade` letters** (A/B/C/D/F) from `src/lib/performance.ts`. No WAEC 1–9 numerals anywhere.
- **Mobile first.** No tables below `sm:`; card stacks that become grids. No hover-only information. Long lists inside `<details>`, with Needs work open by default.
- **Dates crossing the server → client boundary are ISO strings**, never `Date` instances. (Convention stated in `src/lib/evidence-display.ts:12-22`.)
- **Every new test script must be appended to the `test` script in `package.json`.**
- Commit after every task. Run `npx tsc --noEmit -p tsconfig.json` before any commit that touches `.tsx`.

---

## File Structure

**Created — pure engine (no Prisma import allowed in any of these):**

| File | Responsibility |
|---|---|
| `src/engines/analytics/insight.ts` | The `Insight` type, severity ranking, and `selectInsights` |
| `src/engines/analytics/topic-groups.ts` | Five-group presentation of `GapCategory` |
| `src/engines/analytics/profile.ts` | Accuracy by difficulty, pacing, rapid-guess rate |
| `src/engines/analytics/subject-insights.ts` | Turns groups + profile into `Insight[]` |

**Created — data layer:**

| File | Responsibility |
|---|---|
| `src/lib/analytics/subject-view.ts` | Assembles `SubjectPerformance` from Prisma + the engines |

**Created — UI:**

| File | Responsibility |
|---|---|
| `src/app/(dashboard)/performance/layout.tsx` | Page header + tab rail shared by every performance route |
| `src/app/(dashboard)/performance/subjects/page.tsx` | Subject lens route |
| `src/app/(dashboard)/performance/subjects/loading.tsx` | Skeleton |
| `src/components/performance/performance-tabs.tsx` | Client: scrollable pill tabs, active from `usePathname` |
| `src/components/performance/subject-chips.tsx` | Subject selector, weakest-first |
| `src/components/performance/verdict-band.tsx` | The one-sentence verdict + four figures |
| `src/components/performance/insight-list.tsx` | Renders `Insight[]` |
| `src/components/performance/topic-group-list.tsx` | One collapsible group of topic rows |
| `src/components/performance/profile-band.tsx` | Difficulty / pacing / rapid-guess |

**Created — tests:**
`scripts/test-analytics-insight.mts`, `scripts/test-analytics-topic-groups.mts`, `scripts/test-analytics-profile.mts`, `scripts/test-analytics-subject-insights.mts`

**Modified:**
`src/app/(dashboard)/performance/page.tsx` (drop the header — now in layout — and the wrong-count block), `src/lib/performance.ts` (delete `loadWeakTopics` and its types), `package.json` (test script).

---

### Task 0: Remove the dead analytics type scaffolding

**Files:**
- Delete: `src/types/analytics.ts`
- Modify: `src/types/index.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. This task frees the names `SubjectPerformance`, `TopicPerformance`, `ExamReadiness`, `GradePrediction` and `TrendDataPoint` for the real implementations in Tasks 2–5 and in Phases 2–3.

> **Why this runs first.** `src/types/analytics.ts` declares `SubjectPerformance`, `TopicPerformance`, `DashboardStats`, `RecentActivity`, `ExamReadiness`, `GradePrediction` and `TrendDataPoint` — aspirational shapes for exactly this feature, written before it was designed. Nothing imports any of them; they reach the app only through `export *` in `src/types/index.ts`. Left in place, the codebase would carry two exported types called `SubjectPerformance` — one real and one that never was — and a future reader would have no way to tell which is live. Deleting them is cheaper before Task 5 introduces the real one than after.

- [ ] **Step 1: Confirm nothing imports them**

Run:

```bash
grep -rn "types/analytics\|SubjectPerformance\|TopicPerformance\|DashboardStats\|RecentActivity\|ExamReadiness\|GradePrediction\|TrendDataPoint" src scripts --include=*.ts --include=*.tsx | grep -v "^src/types/analytics.ts"
```

Expected: only the `export * from "./analytics"` line in `src/types/index.ts`. If any other file appears, **stop and report it** — do not delete. These names are plausible enough that a real consumer may have appeared since this plan was written.

- [ ] **Step 2: Delete the file and its re-export**

```bash
git rm src/types/analytics.ts
```

Then remove the `export * from "./analytics";` line from `src/types/index.ts`.

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors. Any error here means Step 1's grep missed a consumer — restore the file and report.

- [ ] **Step 4: Run the suite**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/types/index.ts
git commit -m "chore(types): drop unused analytics type scaffolding"
```

---

### Task 1: Insight core

**Files:**
- Create: `src/engines/analytics/insight.ts`
- Test: `scripts/test-analytics-insight.mts`
- Modify: `package.json` (test script)

**Interfaces:**
- Consumes: nothing.
- Produces: `InsightSeverity`, `InsightKind`, `Insight`, `SEVERITY_RANK`, `selectInsights(insights: readonly Insight[], limit?: number): Insight[]`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-analytics-insight.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  selectInsights,
  type Insight,
} from "../src/engines/analytics/insight";

function insight(
  kind: Insight["kind"],
  severity: Insight["severity"],
  headline = kind,
): Insight {
  return { kind, severity, headline };
}

test("orders by severity, most severe first", () => {
  const out = selectInsights(
    [
      insight("SUBJECT_STRENGTH", "WIN"),
      insight("PACING_SLOW", "WARNING"),
      insight("RAPID_GUESSING", "CRITICAL"),
    ],
    10,
  );
  assert.deepEqual(out.map((i) => i.severity), ["CRITICAL", "WARNING", "WIN"]);
});

test("is stable within a severity", () => {
  const out = selectInsights(
    [
      insight("WEAK_TOPIC", "WARNING", "first"),
      insight("DECAYED_TOPIC", "WARNING", "second"),
    ],
    10,
  );
  assert.deepEqual(out.map((i) => i.headline), ["first", "second"]);
});

test("caps at the limit", () => {
  const out = selectInsights(
    [
      insight("RAPID_GUESSING", "CRITICAL"),
      insight("WEAK_TOPIC", "WARNING"),
      insight("DECAYED_TOPIC", "WARNING"),
      insight("STALE_TOPIC", "INFO"),
    ],
    2,
  );
  assert.equal(out.length, 2);
  assert.deepEqual(out.map((i) => i.severity), ["CRITICAL", "WARNING"]);
});

test("keeps at most one WIN", () => {
  const out = selectInsights(
    [
      insight("SUBJECT_STRENGTH", "WIN", "win one"),
      insight("IMPROVING", "WIN", "win two"),
    ],
    10,
  );
  assert.deepEqual(out.map((i) => i.headline), ["win one"]);
});

test("returns an empty list for no insights", () => {
  assert.deepEqual(selectInsights([], 3), []);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-analytics-insight.mts`
Expected: FAIL — cannot find module `../src/engines/analytics/insight`.

- [ ] **Step 3: Write the implementation**

Create `src/engines/analytics/insight.ts`:

```ts
// The finding every analytics view emits alongside its display data.
//
// Today these render as sentences. Later a recommendation engine consumes the
// same array and turns it into StudyPlanItems — which is why the engines emit
// structured findings rather than formatted strings, and why `headline` is a
// whole sentence rather than a fragment a caller has to assemble.
// See docs/superpowers/specs/2026-08-28-performance-analytics-design.md §9.

export type InsightSeverity = "CRITICAL" | "WARNING" | "INFO" | "WIN";

export type InsightKind =
  | "UNTOUCHED_SUBJECT"
  | "LOW_COVERAGE"
  | "WEAK_TOPIC"
  | "DECAYED_TOPIC"
  | "STALE_TOPIC"
  | "BOTTLENECK_TOPIC"
  | "RAPID_GUESSING"
  | "PACING_SLOW"
  | "PACING_RUSHED"
  | "DIFFICULTY_DRIFT"
  | "IMPROVING"
  | "PLATEAU"
  | "SLIPPING"
  | "INSUFFICIENT_EVIDENCE"
  | "LOW_CONSISTENCY"
  | "SUBJECT_STRENGTH"
  | "EXAM_RULE_VIOLATION"
  | "COURSE_REQUIREMENT_RISK";

export type Insight = {
  kind: InsightKind;
  severity: InsightSeverity;
  subjectId?: string;
  topicId?: string;
  /** One plain sentence. This is the text that renders — no assembly by callers. */
  headline: string;
  detail?: string;
  action?: { label: string; href: string };
};

export const SEVERITY_RANK: Record<InsightSeverity, number> = {
  CRITICAL: 0,
  WARNING: 1,
  INFO: 2,
  WIN: 3,
};

/**
 * The insights worth showing, most severe first.
 *
 * At most one WIN survives. A page that congratulates a student twice while
 * they still have gaps reads as noise, and the WIN exists only so the section
 * is not an unbroken list of failings.
 *
 * The sort is stable, so producers control the order within a severity and can
 * express "this weak topic matters more than that one" by emitting it first.
 */
export function selectInsights(
  insights: readonly Insight[],
  limit = 3,
): Insight[] {
  let winsKept = 0;
  return [...insights]
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity])
    .filter((insight) => {
      if (insight.severity !== "WIN") return true;
      winsKept += 1;
      return winsKept === 1;
    })
    .slice(0, limit);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-analytics-insight.mts`
Expected: PASS, 5 tests.

> `Array.prototype.sort` is specified as stable in modern V8, which is what makes the "stable within a severity" test meaningful rather than incidental.

- [ ] **Step 5: Register the test**

In `package.json`, append ` scripts/test-analytics-insight.mts` to the end of the `test` script value.

- [ ] **Step 6: Run the whole suite**

Run: `npm test`
Expected: PASS, including the new file.

- [ ] **Step 7: Commit**

```bash
git add src/engines/analytics/insight.ts scripts/test-analytics-insight.mts package.json
git commit -m "feat(analytics): add Insight type and selection"
```

---

### Task 2: Topic grouping

**Files:**
- Create: `src/engines/analytics/topic-groups.ts`
- Test: `scripts/test-analytics-topic-groups.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `classifyTopic` (`src/engines/learning/gaps.ts`), `bottleneckScore` (same file), `TopicStateMap`/`TopicState` (`src/engines/learning/mastery.ts`), `KnowledgeGraph` (`src/engines/learning/graph.ts`), `OBSERVATION_FLOOR` (`src/engines/learning/evidence.ts`), `WEAK_MASTERY` (`src/engines/learning/recommend.ts`), `TARGET` (`src/engines/learning/availability.ts`).
- Produces: `TopicGroupKey`, `TopicRow`, `TopicGroups`, `STALE_RETENTION`, `groupTopics(state, graph, pretestPassed?, abandonedByTopic?): TopicGroups`.

> **The rule this task encodes** (spec §5): the five groups are *total* over every `GapCategory` and every `null` return from `classifyTopic`. `classifyTopic`'s gating reasoning is not re-litigated — the only thing added is splitting its `null`, which the gap queue does not need and a performance view cannot do without. In particular, a topic above the observation floor with mastery below `WEAK_MASTERY` but confidence too low for `classifyTopic` to call it WEAK lands in **Coming along**, never Needs work: routing it into Needs work would overturn a withholding the mastery engine made on purpose.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-analytics-topic-groups.mts`:

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
import { groupTopics } from "../src/engines/analytics/topic-groups";
import { TARGET } from "../src/engines/learning/availability";

const now = new Date("2026-08-28T09:00:00Z");
const DAY_MS = 86_400_000;

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

function graphWith(ids: string[], edges: GraphEdge[] = []): KnowledgeGraph {
  return buildGraph(ids.map((id) => node(id)), edges);
}

/** Folds `count` answers for one topic, `ageDays` before `now`. */
function stateFor(
  entries: { topicId: string; count: number; correct: boolean; ageDays?: number }[],
): TopicStateMap {
  const state: TopicStateMap = new Map();
  for (const entry of entries) {
    const occurredAt = new Date(now.getTime() - (entry.ageDays ?? 0) * DAY_MS);
    const events: FoldEvent[] = Array.from({ length: entry.count }, (_, i) => ({
      seq: BigInt(i + 1),
      topicId: entry.topicId,
      kind: "QUESTION_ANSWERED" as const,
      correct: entry.correct,
      score: null,
      difficulty: "INTERMEDIATE" as const,
      seconds: 30,
      occurredAt,
    }));
    const aggregate = foldEvents(
      emptyAggregate(entry.topicId, "subj-1", occurredAt),
      events,
      now,
    );
    state.set(entry.topicId, scoreAggregate(aggregate, now));
  }
  return state;
}

test("a topic with no evidence is Unproven, not weak", () => {
  const graph = graphWith(["t1"]);
  const groups = groupTopics(new Map(), graph, new Set(), new Map(), now);
  assert.deepEqual(groups.UNPROVEN.map((r) => r.topicId), ["t1"]);
  assert.equal(groups.NEEDS_WORK.length, 0);
});

test("a topic below the observation floor is Unproven, not Solid", () => {
  const graph = graphWith(["t1"]);
  const state = stateFor([{ topicId: "t1", count: 2, correct: true }]);
  const groups = groupTopics(state, graph, new Set(), new Map(), now);
  assert.deepEqual(groups.UNPROVEN.map((r) => r.topicId), ["t1"]);
  assert.equal(groups.SOLID.length, 0);
});

test("a well-evidenced strong topic is Solid", () => {
  const graph = graphWith(["t1"]);
  const state = stateFor([{ topicId: "t1", count: 30, correct: true }]);
  const groups = groupTopics(state, graph, new Set(), new Map(), now);
  assert.deepEqual(groups.SOLID.map((r) => r.topicId), ["t1"]);
  assert.ok((state.get("t1")?.mastery ?? 0) >= TARGET);
});

test("a well-evidenced failing topic needs work", () => {
  const graph = graphWith(["t1"]);
  const state = stateFor([{ topicId: "t1", count: 30, correct: false }]);
  const groups = groupTopics(state, graph, new Set(), new Map(), now);
  assert.deepEqual(groups.NEEDS_WORK.map((r) => r.topicId), ["t1"]);
});

test("an abandoned topic is Unproven, not Needs work", () => {
  const graph = graphWith(["t1"]);
  const groups = groupTopics(
    new Map(),
    graph,
    new Set(),
    new Map([["t1", 5]]),
    now,
  );
  assert.deepEqual(groups.UNPROVEN.map((r) => r.topicId), ["t1"]);
  assert.equal(groups.UNPROVEN[0].category, "ABANDONED");
  assert.equal(groups.NEEDS_WORK.length, 0);
});

test("every graph topic lands in exactly one group", () => {
  const graph = graphWith(["t1", "t2", "t3", "t4"]);
  const state = stateFor([
    { topicId: "t1", count: 30, correct: true },
    { topicId: "t2", count: 30, correct: false },
    { topicId: "t3", count: 2, correct: true },
    { topicId: "t4", count: 20, correct: true, ageDays: 400 },
  ]);
  const groups = groupTopics(state, graph, new Set(), new Map(), now);
  const all = [
    ...groups.NEEDS_WORK,
    ...groups.NEEDS_REVISION,
    ...groups.UNPROVEN,
    ...groups.COMING_ALONG,
    ...groups.SOLID,
  ];
  assert.equal(all.length, 4);
  assert.equal(new Set(all.map((r) => r.topicId)).size, 4);
});

test("Needs work is ordered by leverage then mastery, matching the gap queue", () => {
  // t-hub has two dependents, so it carries leverage; t-leaf has none.
  const nodes = ["t-hub", "t-leaf", "t-dep1", "t-dep2"];
  const edges: GraphEdge[] = [
    { id: "e1", from: "t-hub", to: "t-dep1", kind: "PREREQUISITE", strength: 1, rationale: null },
    { id: "e2", from: "t-hub", to: "t-dep2", kind: "PREREQUISITE", strength: 1, rationale: null },
  ];
  const graph = graphWith(nodes, edges);
  const state = stateFor([
    { topicId: "t-hub", count: 30, correct: false },
    { topicId: "t-leaf", count: 30, correct: false },
    { topicId: "t-dep1", count: 30, correct: false },
    { topicId: "t-dep2", count: 30, correct: false },
  ]);
  const groups = groupTopics(state, graph, new Set(), new Map(), now);
  assert.equal(groups.NEEDS_WORK[0].topicId, "t-hub");
});

test("a stale Solid topic is flagged", () => {
  const graph = graphWith(["t1"]);
  const state = stateFor([{ topicId: "t1", count: 40, correct: true, ageDays: 60 }]);
  const groups = groupTopics(state, graph, new Set(), new Map(), now);
  const row = [...groups.SOLID, ...groups.NEEDS_REVISION, ...groups.COMING_ALONG].find(
    (r) => r.topicId === "t1",
  );
  assert.ok(row, "topic should be grouped");
  assert.ok(
    row.stale || row.group === "NEEDS_REVISION",
    "aged strong evidence should read as stale or as needing revision",
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-analytics-topic-groups.mts`
Expected: FAIL — cannot find module `../src/engines/analytics/topic-groups`.

- [ ] **Step 3: Write the implementation**

Create `src/engines/analytics/topic-groups.ts`:

```ts
import type { KnowledgeGraph } from "../learning/graph";
import type { TopicStateMap } from "../learning/mastery";
import { classifyTopic, bottleneckScore } from "../learning/gaps";
import type { GapCategory } from "../learning/gaps";
import { OBSERVATION_FLOOR } from "../learning/evidence";
import { TARGET } from "../learning/availability";

// The subject view's presentation of GapCategory.
// See docs/superpowers/specs/2026-08-28-performance-analytics-design.md §5.
//
// This is deliberately a thin layer over `classifyTopic` rather than a second
// classifier. The gating reasoning there — WEAK on confidence because it asks
// how well we know the topic now, DECAYED on raw observations because it asks
// whether the student once knew it — is not repeated or overridden here.
//
// What it adds is the split of `classifyTopic`'s `null`, which conflates two
// situations a gap queue does not care about and a performance view cannot
// confuse: a topic that is fine, and a topic we cannot yet judge.

export type TopicGroupKey =
  | "NEEDS_WORK"
  | "NEEDS_REVISION"
  | "UNPROVEN"
  | "COMING_ALONG"
  | "SOLID";

/**
 * Retention below which a Solid topic is flagged stale.
 *
 * Looser than GAP_RETENTION (0.8, the DECAYED threshold) on purpose: this is a
 * nudge to revise, not a diagnosis of decay, and it fires earlier so the nudge
 * arrives before the decay does.
 */
export const STALE_RETENTION = 0.9;

export type TopicRow = {
  topicId: string;
  subjectId: string;
  title: string;
  slug: string;
  group: TopicGroupKey;
  /** The underlying category, or null when `classifyTopic` withheld judgement. */
  category: GapCategory | null;
  mastery: number;
  retention: number | null;
  confidence: number;
  observations: number;
  bottleneckScore: number;
  /** ISO string — this shape crosses the server -> client boundary. */
  lastStudy: string | null;
  /** SOLID only: retention has slipped but not far enough to be DECAYED. */
  stale: boolean;
};

export type TopicGroups = Record<TopicGroupKey, TopicRow[]>;

function emptyGroups(): TopicGroups {
  return {
    NEEDS_WORK: [],
    NEEDS_REVISION: [],
    UNPROVEN: [],
    COMING_ALONG: [],
    SOLID: [],
  };
}

/**
 * Every topic in the graph, sorted into five groups.
 *
 * The graph, not the state map, is the population: a topic with no evidence
 * must still appear, because in a performance view an untouched topic is a
 * finding rather than an absence.
 */
export function groupTopics(
  state: TopicStateMap,
  graph: KnowledgeGraph,
  pretestPassed: ReadonlySet<string> = new Set(),
  abandonedByTopic: ReadonlyMap<string, number> = new Map(),
  now: Date = new Date(),
): TopicGroups {
  void now; // classifyTopic scores against the already-decayed state.
  const groups = emptyGroups();

  for (const [topicId, node] of graph.nodes) {
    const topic = state.get(topicId);
    const category = classifyTopic(
      state,
      graph,
      topicId,
      pretestPassed,
      abandonedByTopic,
    );
    const observations = topic
      ? topic.accObservations + topic.lessonObservations + topic.srsObservations
      : 0;
    const mastery = topic?.mastery ?? 0;
    const retention = topic?.retention ?? null;

    // Total over every category and over null. No unhandled case.
    let group: TopicGroupKey;
    if (category === "WEAK" || category === "BOTTLENECK") {
      group = "NEEDS_WORK";
    } else if (category === "DECAYED") {
      group = "NEEDS_REVISION";
    } else if (category === "UNTOUCHED" || category === "ABANDONED") {
      group = "UNPROVEN";
    } else if (observations < OBSERVATION_FLOOR) {
      // Includes the `classifyTopic` -> null case for a topic outside the
      // graph's available frontier: too little evidence either way.
      group = "UNPROVEN";
    } else if (mastery >= TARGET) {
      group = "SOLID";
    } else {
      // Bounded by TARGET alone. A topic under WEAK_MASTERY whose confidence
      // is too low for classifyTopic to call it WEAK belongs here, not in
      // NEEDS_WORK — see the module comment.
      group = "COMING_ALONG";
    }

    groups[group].push({
      topicId,
      subjectId: node.subjectId,
      title: node.title,
      slug: node.slug,
      group,
      category,
      mastery,
      retention,
      confidence: topic?.confidence ?? 0,
      observations,
      bottleneckScore: bottleneckScore(graph, topicId),
      lastStudy: topic?.lastStudy?.toISOString() ?? null,
      stale:
        group === "SOLID" && retention !== null && retention < STALE_RETENTION,
    });
  }

  // Needs work follows gapQueue's comparator exactly, so this page and the
  // learning path agree about which fix matters most.
  groups.NEEDS_WORK.sort(
    (a, b) => b.bottleneckScore - a.bottleneckScore || a.mastery - b.mastery,
  );
  // Weakest memory first — the thing most likely to be gone by the exam.
  groups.NEEDS_REVISION.sort((a, b) => (a.retention ?? 0) - (b.retention ?? 0));
  // Closest to target first: the quickest wins.
  groups.COMING_ALONG.sort((a, b) => b.mastery - a.mastery);
  // Stale first, then strongest.
  groups.SOLID.sort(
    (a, b) => Number(b.stale) - Number(a.stale) || b.mastery - a.mastery,
  );
  // Curriculum order — the sequence a student would actually study them in.
  const orderIndex = (id: string) => graph.nodes.get(id)?.orderIndex ?? 0;
  groups.UNPROVEN.sort((a, b) => orderIndex(a.topicId) - orderIndex(b.topicId));

  return groups;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-analytics-topic-groups.mts`
Expected: PASS, 8 tests.

If "a well-evidenced strong topic is Solid" fails because mastery lands just under `TARGET`, raise the fixture's `count`, not the threshold — `TARGET` is the learning path's own constant and this view must agree with it.

- [ ] **Step 5: Register the test and run the suite**

Append ` scripts/test-analytics-topic-groups.mts` to the `test` script in `package.json`, then run `npm test`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/engines/analytics/topic-groups.ts scripts/test-analytics-topic-groups.mts package.json
git commit -m "feat(analytics): group topics into five performance groups"
```

---

### Task 3: Difficulty, pacing and rapid-guess profile

**Files:**
- Create: `src/engines/analytics/profile.ts`
- Test: `scripts/test-analytics-profile.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `isRapidGuess` (`src/engines/learning/evidence.ts`), `Difficulty` from `@/types/prisma` — **not** from `@prisma/client`. The learning engines all import it from the hand-written mirror (`fold.ts:1`, `evidence.ts:1`); matching them keeps the engine layer free of a Prisma import.
- Produces: `AnswerSample`, `DifficultyBandKey`, `DifficultyBand`, `PacingVerdict`, `Pacing`, `Profile`, `PROFILE_MIN_ANSWERS`, `RUSHED_RATIO`, `SLOW_RATIO`, `buildProfile(samples, expectedSeconds): Profile`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-analytics-profile.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildProfile,
  PROFILE_MIN_ANSWERS,
  type AnswerSample,
} from "../src/engines/analytics/profile";

function samples(
  count: number,
  overrides: Partial<AnswerSample> = {},
): AnswerSample[] {
  return Array.from({ length: count }, () => ({
    difficulty: "INTERMEDIATE" as const,
    correct: true,
    seconds: 60,
    ...overrides,
  }));
}

test("refuses a profile below the minimum sample", () => {
  const profile = buildProfile(samples(PROFILE_MIN_ANSWERS - 1), 60);
  assert.equal(profile.status, "insufficient");
  if (profile.status !== "insufficient") return;
  assert.equal(profile.answered, PROFILE_MIN_ANSWERS - 1);
  assert.equal(profile.needed, PROFILE_MIN_ANSWERS);
});

test("reports accuracy per difficulty band", () => {
  const profile = buildProfile(
    [
      ...samples(10, { difficulty: "BASIC", correct: true }),
      ...samples(10, { difficulty: "ADVANCED", correct: false }),
    ],
    60,
  );
  assert.equal(profile.status, "ok");
  if (profile.status !== "ok") return;
  const basic = profile.bands.find((b) => b.difficulty === "BASIC");
  const advanced = profile.bands.find((b) => b.difficulty === "ADVANCED");
  assert.equal(basic?.accuracy, 100);
  assert.equal(advanced?.accuracy, 0);
});

test("keeps unlabelled answers as their own band rather than dropping them", () => {
  const profile = buildProfile(
    [
      ...samples(10, { difficulty: null, correct: true }),
      ...samples(10, { difficulty: "BASIC", correct: true }),
    ],
    60,
  );
  assert.equal(profile.status, "ok");
  if (profile.status !== "ok") return;
  const unlabelled = profile.bands.find((b) => b.difficulty === "UNLABELLED");
  assert.equal(unlabelled?.answered, 10);
  assert.equal(profile.answered, 20);
});

test("omits a band with no answers instead of dividing by zero", () => {
  const profile = buildProfile(samples(20, { difficulty: "BASIC" }), 60);
  assert.equal(profile.status, "ok");
  if (profile.status !== "ok") return;
  assert.ok(profile.bands.every((b) => b.answered > 0));
  assert.ok(profile.bands.every((b) => Number.isFinite(b.accuracy)));
});

test("calls a fast student rushed and a slow one slow", () => {
  const rushed = buildProfile(samples(20, { seconds: 20 }), 60);
  const slow = buildProfile(samples(20, { seconds: 120 }), 60);
  const onPace = buildProfile(samples(20, { seconds: 60 }), 60);
  assert.equal(rushed.status === "ok" && rushed.pacing?.verdict, "RUSHED");
  assert.equal(slow.status === "ok" && slow.pacing?.verdict, "SLOW");
  assert.equal(onPace.status === "ok" && onPace.pacing?.verdict, "ON_PACE");
});

test("reports no pacing when there is no authored estimate", () => {
  const profile = buildProfile(samples(20), null);
  assert.equal(profile.status, "ok");
  if (profile.status !== "ok") return;
  assert.equal(profile.pacing, null);
});

test("measures the rapid-guess rate", () => {
  const profile = buildProfile(
    [...samples(5, { seconds: 1 }), ...samples(15, { seconds: 60 })],
    60,
  );
  assert.equal(profile.status, "ok");
  if (profile.status !== "ok") return;
  assert.equal(profile.rapidGuessRate, 25);
});

test("survives answers with no recorded time", () => {
  const profile = buildProfile(samples(20, { seconds: null }), 60);
  assert.equal(profile.status, "ok");
  if (profile.status !== "ok") return;
  assert.equal(profile.pacing, null);
  assert.equal(profile.rapidGuessRate, 0);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-analytics-profile.mts`
Expected: FAIL — cannot find module `../src/engines/analytics/profile`.

- [ ] **Step 3: Write the implementation**

Create `src/engines/analytics/profile.ts`:

```ts
import type { Difficulty } from "@/types/prisma";
import { isRapidGuess } from "../learning/evidence";

// How a student answers, as distinct from how well.
// See docs/superpowers/specs/2026-08-28-performance-analytics-design.md §4.

export type AnswerSample = {
  difficulty: Difficulty | null;
  correct: boolean;
  seconds: number | null;
};

export type DifficultyBandKey = Difficulty | "UNLABELLED";

export type DifficultyBand = {
  difficulty: DifficultyBandKey;
  answered: number;
  /** Percentage, 0..100. */
  accuracy: number;
};

export type PacingVerdict = "RUSHED" | "ON_PACE" | "SLOW";

export type Pacing = {
  meanSeconds: number;
  expectedSeconds: number;
  /** meanSeconds / expectedSeconds. */
  ratio: number;
  verdict: PacingVerdict;
};

export type Profile =
  | { status: "insufficient"; answered: number; needed: number }
  | {
      status: "ok";
      answered: number;
      bands: DifficultyBand[];
      /** Percentage of answers that were rapid guesses, 0..100. */
      rapidGuessRate: number;
      /** Null when nothing can be said: no timings, or no authored estimate. */
      pacing: Pacing | null;
    };

/**
 * Below this, a profile is noise. Twenty answers is roughly one practice
 * session — enough that each difficulty band has a chance of being non-empty,
 * and low enough that a student sees the band after a single sitting.
 */
export const PROFILE_MIN_ANSWERS = 20;

export const RUSHED_RATIO = 0.6;
export const SLOW_RATIO = 1.3;

const BAND_ORDER: DifficultyBandKey[] = [
  "BASIC",
  "INTERMEDIATE",
  "ADVANCED",
  "UNLABELLED",
];

/**
 * `expectedSeconds` is the subject's authored mean `timeEstimateSeconds`, or
 * null when the subject has no authored estimates.
 *
 * It is a subject-level mean rather than a per-question join because
 * `LearningEvent.sourceId` is documented as being for audit rather than logic
 * and carries no index — a coarser figure is the honest cost of not adding an
 * index to serve a display metric.
 */
export function buildProfile(
  samples: readonly AnswerSample[],
  expectedSeconds: number | null,
): Profile {
  const answered = samples.length;
  if (answered < PROFILE_MIN_ANSWERS) {
    return { status: "insufficient", answered, needed: PROFILE_MIN_ANSWERS };
  }

  const bands: DifficultyBand[] = [];
  for (const key of BAND_ORDER) {
    const inBand = samples.filter(
      (s) => (s.difficulty ?? "UNLABELLED") === key,
    );
    // An empty band is omitted rather than reported as 0% — a band nobody has
    // answered is not a band the student is failing.
    if (inBand.length === 0) continue;
    const correct = inBand.filter((s) => s.correct).length;
    bands.push({
      difficulty: key,
      answered: inBand.length,
      accuracy: (correct / inBand.length) * 100,
    });
  }

  const timed = samples.filter(
    (s): s is AnswerSample & { seconds: number } => s.seconds !== null,
  );
  const rapid = timed.filter((s) => isRapidGuess(s.seconds)).length;
  const rapidGuessRate = (rapid / answered) * 100;

  let pacing: Pacing | null = null;
  if (timed.length > 0 && expectedSeconds !== null && expectedSeconds > 0) {
    const meanSeconds =
      timed.reduce((sum, s) => sum + s.seconds, 0) / timed.length;
    const ratio = meanSeconds / expectedSeconds;
    pacing = {
      meanSeconds,
      expectedSeconds,
      ratio,
      verdict:
        ratio < RUSHED_RATIO ? "RUSHED" : ratio > SLOW_RATIO ? "SLOW" : "ON_PACE",
    };
  }

  return { status: "ok", answered, bands, rapidGuessRate, pacing };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-analytics-profile.mts`
Expected: PASS, 8 tests.

If "measures the rapid-guess rate" fails, check `RAPID_SECONDS` in `src/engines/learning/evidence.ts` — the fixture uses `seconds: 1` to sit under it. Adjust the fixture, never the constant.

- [ ] **Step 5: Register the test and run the suite**

Append ` scripts/test-analytics-profile.mts` to the `test` script in `package.json`, then run `npm test`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/engines/analytics/profile.ts scripts/test-analytics-profile.mts package.json
git commit -m "feat(analytics): add difficulty, pacing and rapid-guess profile"
```

---

### Task 4: Subject insights

**Files:**
- Create: `src/engines/analytics/subject-insights.ts`
- Test: `scripts/test-analytics-subject-insights.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `Insight` (Task 1), `TopicGroups`/`TopicRow` (Task 2), `Profile` (Task 3).
- Produces: `RAPID_GUESS_ALARM`, `subjectInsights(input: { subjectId: string; subjectSlug: string; groups: TopicGroups; profile: Profile }): Insight[]`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-analytics-subject-insights.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { subjectInsights } from "../src/engines/analytics/subject-insights";
import type { TopicGroups, TopicRow } from "../src/engines/analytics/topic-groups";
import type { Profile } from "../src/engines/analytics/profile";

function row(topicId: string, overrides: Partial<TopicRow> = {}): TopicRow {
  return {
    topicId,
    subjectId: "subj-1",
    title: `Topic ${topicId}`,
    slug: `topic-${topicId}`,
    group: "NEEDS_WORK",
    category: "WEAK",
    mastery: 30,
    retention: 0.9,
    confidence: 0.8,
    observations: 20,
    bottleneckScore: 0,
    lastStudy: null,
    stale: false,
    ...overrides,
  };
}

function groups(overrides: Partial<TopicGroups> = {}): TopicGroups {
  return {
    NEEDS_WORK: [],
    NEEDS_REVISION: [],
    UNPROVEN: [],
    COMING_ALONG: [],
    SOLID: [],
    ...overrides,
  };
}

const okProfile: Profile = {
  status: "ok",
  answered: 50,
  bands: [{ difficulty: "INTERMEDIATE", answered: 50, accuracy: 70 }],
  rapidGuessRate: 0,
  pacing: { meanSeconds: 60, expectedSeconds: 60, ratio: 1, verdict: "ON_PACE" },
};

const input = (over: Partial<Parameters<typeof subjectInsights>[0]> = {}) => ({
  subjectId: "subj-1",
  subjectSlug: "physics",
  groups: groups(),
  profile: okProfile,
  ...over,
});

test("names the weak topics", () => {
  const out = subjectInsights(
    input({ groups: groups({ NEEDS_WORK: [row("t1"), row("t2"), row("t3")] }) }),
  );
  const weak = out.filter((i) => i.kind === "WEAK_TOPIC");
  assert.equal(weak.length, 2, "at most the top two weak topics are named");
  assert.ok(weak[0].headline.includes("Topic t1"));
  assert.equal(weak[0].topicId, "t1");
});

test("a bottleneck outranks a plain weakness", () => {
  const out = subjectInsights(
    input({
      groups: groups({
        NEEDS_WORK: [row("t1", { category: "BOTTLENECK", bottleneckScore: 9 })],
      }),
    }),
  );
  const bottleneck = out.find((i) => i.kind === "BOTTLENECK_TOPIC");
  assert.ok(bottleneck);
  assert.equal(bottleneck.severity, "CRITICAL");
});

test("rapid guessing is critical", () => {
  const out = subjectInsights(
    input({ profile: { ...okProfile, rapidGuessRate: 40 } }),
  );
  const guessing = out.find((i) => i.kind === "RAPID_GUESSING");
  assert.ok(guessing);
  assert.equal(guessing.severity, "CRITICAL");
});

test("pacing verdicts produce their own insights", () => {
  const slow = subjectInsights(
    input({
      profile: {
        ...okProfile,
        pacing: { meanSeconds: 120, expectedSeconds: 60, ratio: 2, verdict: "SLOW" },
      },
    }),
  );
  assert.ok(slow.some((i) => i.kind === "PACING_SLOW"));

  const rushed = subjectInsights(
    input({
      profile: {
        ...okProfile,
        pacing: { meanSeconds: 20, expectedSeconds: 60, ratio: 0.33, verdict: "RUSHED" },
      },
    }),
  );
  assert.ok(rushed.some((i) => i.kind === "PACING_RUSHED"));
});

test("untouched topics are reported as unknowns, not weaknesses", () => {
  const out = subjectInsights(
    input({
      groups: groups({
        UNPROVEN: [row("t1", { group: "UNPROVEN", category: "UNTOUCHED" })],
      }),
    }),
  );
  const unknown = out.find((i) => i.kind === "INSUFFICIENT_EVIDENCE");
  assert.ok(unknown);
  assert.equal(unknown.severity, "INFO");
  assert.ok(!out.some((i) => i.kind === "WEAK_TOPIC"));
});

test("a stale solid topic is flagged", () => {
  const out = subjectInsights(
    input({
      groups: groups({
        SOLID: [row("t1", { group: "SOLID", category: null, mastery: 85, stale: true })],
      }),
    }),
  );
  assert.ok(out.some((i) => i.kind === "STALE_TOPIC"));
});

test("a subject with no gaps earns a win", () => {
  const out = subjectInsights(
    input({
      groups: groups({
        SOLID: [row("t1", { group: "SOLID", category: null, mastery: 90 })],
      }),
    }),
  );
  const win = out.find((i) => i.kind === "SUBJECT_STRENGTH");
  assert.ok(win);
  assert.equal(win.severity, "WIN");
});

test("an insufficient profile produces no pacing or guessing claims", () => {
  const out = subjectInsights(
    input({ profile: { status: "insufficient", answered: 4, needed: 20 } }),
  );
  assert.ok(!out.some((i) => i.kind === "PACING_SLOW" || i.kind === "RAPID_GUESSING"));
});

test("every insight carries a non-empty headline", () => {
  const out = subjectInsights(
    input({
      groups: groups({
        NEEDS_WORK: [row("t1")],
        NEEDS_REVISION: [row("t2", { group: "NEEDS_REVISION", category: "DECAYED" })],
        UNPROVEN: [row("t3", { group: "UNPROVEN", category: "UNTOUCHED" })],
      }),
      profile: { ...okProfile, rapidGuessRate: 40 },
    }),
  );
  assert.ok(out.length > 0);
  assert.ok(out.every((i) => i.headline.trim().length > 0));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-analytics-subject-insights.mts`
Expected: FAIL — cannot find module `../src/engines/analytics/subject-insights`.

- [ ] **Step 3: Write the implementation**

Create `src/engines/analytics/subject-insights.ts`:

```ts
import type { Insight } from "./insight";
import type { TopicGroups } from "./topic-groups";
import type { Profile } from "./profile";

// Findings the subject view emits. Pure: groups and profile in, insights out.
// See docs/superpowers/specs/2026-08-28-performance-analytics-design.md §9.

/** Above this share of rapid guesses, the student's own numbers are unreliable. */
export const RAPID_GUESS_ALARM = 20;

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

export function subjectInsights(input: {
  subjectId: string;
  subjectSlug: string;
  groups: TopicGroups;
  profile: Profile;
}): Insight[] {
  const { subjectId, subjectSlug, groups, profile } = input;
  const insights: Insight[] = [];

  // Rapid guessing first: it undermines every other number on the page, so it
  // is stated before the numbers it undermines.
  if (profile.status === "ok" && profile.rapidGuessRate >= RAPID_GUESS_ALARM) {
    insights.push({
      kind: "RAPID_GUESSING",
      severity: "CRITICAL",
      subjectId,
      headline: `${Math.round(profile.rapidGuessRate)}% of your answers here were clicked too fast to have been read.`,
      detail:
        "Those answers still count toward your mastery, so the figures on this page are flattering you. Slow down and the numbers start telling the truth.",
    });
  }

  // Needs work is already ordered by leverage, so the first two are the two
  // that matter most. Naming more than two turns advice into a backlog.
  for (const topic of groups.NEEDS_WORK.slice(0, 2)) {
    const isBottleneck = topic.category === "BOTTLENECK";
    insights.push({
      kind: isBottleneck ? "BOTTLENECK_TOPIC" : "WEAK_TOPIC",
      severity: isBottleneck ? "CRITICAL" : "WARNING",
      subjectId,
      topicId: topic.topicId,
      headline: isBottleneck
        ? `${topic.title} is holding up other topics you need.`
        : `${topic.title} is your weakest topic here, at ${Math.round(topic.mastery)}% mastery.`,
      action: {
        label: "Practise this topic",
        href: `/practice/past-questions?topic=${topic.slug}`,
      },
    });
  }

  if (groups.NEEDS_REVISION.length > 0) {
    const first = groups.NEEDS_REVISION[0];
    insights.push({
      kind: "DECAYED_TOPIC",
      severity: "WARNING",
      subjectId,
      topicId: first.topicId,
      headline: `You knew ${first.title} and it has faded — ${plural(groups.NEEDS_REVISION.length, "topic")} here need revision.`,
      action: {
        label: "Revise",
        href: `/classroom/${subjectSlug}/${first.slug}`,
      },
    });
  }

  const stale = groups.SOLID.filter((topic) => topic.stale);
  if (stale.length > 0) {
    insights.push({
      kind: "STALE_TOPIC",
      severity: "INFO",
      subjectId,
      topicId: stale[0].topicId,
      headline: `${plural(stale.length, "strong topic")} here haven't been touched in a while.`,
      detail: "Still solid, but worth a quick review before the exam.",
    });
  }

  if (groups.UNPROVEN.length > 0) {
    insights.push({
      kind: "INSUFFICIENT_EVIDENCE",
      severity: "INFO",
      subjectId,
      headline: `${plural(groups.UNPROVEN.length, "topic")} here you haven't proven yet — not weaknesses, unknowns.`,
      action: { label: "Open the classroom", href: `/classroom/${subjectSlug}` },
    });
  }

  if (profile.status === "ok" && profile.pacing) {
    if (profile.pacing.verdict === "SLOW") {
      insights.push({
        kind: "PACING_SLOW",
        severity: "WARNING",
        subjectId,
        headline: `You're taking about ${Math.round(profile.pacing.ratio * 100)}% of the expected time per question here.`,
        detail:
          "Accuracy without speed still fails a timed paper. Practise against the clock.",
      });
    } else if (profile.pacing.verdict === "RUSHED") {
      insights.push({
        kind: "PACING_RUSHED",
        severity: "WARNING",
        subjectId,
        headline: `You're answering well under the expected time per question here.`,
        detail: "Going faster than the paper requires usually costs marks it needn't.",
      });
    }
  }

  // The win comes last so it never crowds out a finding, and selectInsights
  // keeps at most one of them anyway.
  if (
    groups.NEEDS_WORK.length === 0 &&
    groups.NEEDS_REVISION.length === 0 &&
    groups.SOLID.length > 0
  ) {
    insights.push({
      kind: "SUBJECT_STRENGTH",
      severity: "WIN",
      subjectId,
      headline: `No gaps here — ${plural(groups.SOLID.length, "topic")} at target and nothing weak.`,
    });
  }

  return insights;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-analytics-subject-insights.mts`
Expected: PASS, 9 tests.

- [ ] **Step 5: Register the test and run the suite**

Append ` scripts/test-analytics-subject-insights.mts` to the `test` script in `package.json`, then run `npm test`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/engines/analytics/subject-insights.ts scripts/test-analytics-subject-insights.mts package.json
git commit -m "feat(analytics): derive subject insights from groups and profile"
```

---

### Task 5: The subject view loader

**Files:**
- Create: `src/lib/analytics/subject-view.ts`

**Interfaces:**
- Consumes: `computePathState` (`src/lib/learning-path.ts`), `groupTopics` (Task 2), `buildProfile` (Task 3), `subjectInsights` (Task 4), `getGrade` (`src/lib/performance.ts`), `db` (`src/lib/db.ts`).
- Produces: `SubjectChoice`, `SubjectVerdict`, `SubjectPerformance`, `getSubjectChoices(userId): Promise<SubjectChoice[]>`, `getSubjectPerformance(userId, subjectSlug, now?): Promise<SubjectPerformance | null>`.

> No unit test: this function is a Prisma assembly with no branching logic of its own — every rule it applies is already tested in Tasks 1–4. It is verified by `tsc` and by the manual check in Task 7. Do not add a mock-Prisma test; the repo does not have that pattern and it would test the mock.

- [ ] **Step 1: Write the loader**

Create `src/lib/analytics/subject-view.ts`:

```ts
import { db } from "../db";
import { getGrade } from "../performance";
import { computePathState } from "../learning-path";
import { groupTopics, type TopicGroups } from "@/engines/analytics/topic-groups";
import { buildProfile, type AnswerSample, type Profile } from "@/engines/analytics/profile";
import { subjectInsights } from "@/engines/analytics/subject-insights";
import { selectInsights, type Insight } from "@/engines/analytics/insight";

// Assembles the subject lens. Every rule here lives in the engines; this file
// only fetches and hands over.
// See docs/superpowers/specs/2026-08-28-performance-analytics-design.md §4.

export type SubjectChoice = {
  id: string;
  name: string;
  slug: string;
  code: string;
  /** Accuracy across all recorded answers, 0..100, or null with no answers. */
  accuracy: number | null;
  answered: number;
};

export type SubjectVerdict = {
  accuracy: number | null;
  grade: string | null;
  answered: number;
  correct: number;
  topicsCovered: number;
  topicsInScope: number;
  secondsSpent: number;
};

export type SubjectPerformance = {
  subject: { id: string; name: string; slug: string; code: string };
  verdict: SubjectVerdict;
  groups: TopicGroups;
  profile: Profile;
  insights: Insight[];
};

/**
 * Subjects the student has any evidence in, weakest first — the ordering is
 * itself advice, so the chip they most need is the one nearest the thumb.
 */
export async function getSubjectChoices(userId: string): Promise<SubjectChoice[]> {
  const [attempted, correct] = await db.$transaction([
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
  ]);

  const correctBySubject = new Map(
    correct.map((row) => [row.subjectId, row._count._all]),
  );
  const subjects = await db.subject.findMany({
    where: { id: { in: attempted.map((row) => row.subjectId) } },
    select: { id: true, name: true, slug: true, code: true },
  });
  const byId = new Map(subjects.map((subject) => [subject.id, subject]));

  return attempted
    .flatMap((row) => {
      const subject = byId.get(row.subjectId);
      if (!subject) return [];
      const answered = row._count._all;
      const right = correctBySubject.get(row.subjectId) ?? 0;
      return [
        {
          ...subject,
          answered,
          accuracy: answered > 0 ? (right / answered) * 100 : null,
        },
      ];
    })
    .sort((a, b) => (a.accuracy ?? 0) - (b.accuracy ?? 0));
}

export async function getSubjectPerformance(
  userId: string,
  subjectSlug: string,
  now = new Date(),
): Promise<SubjectPerformance | null> {
  const subject = await db.subject.findFirst({
    where: { slug: subjectSlug },
    select: { id: true, name: true, slug: true, code: true },
  });
  if (!subject) return null;

  const { graph, state, pretestPassed } = await computePathState(
    db,
    userId,
    [subject.id],
    now,
  );

  // Decoration, not diagnosis: a failure here costs the Unproven group its
  // "you started this and left" reason line, which is a far better outcome
  // than failing the page. Same pattern as lib/dashboard.ts.
  let abandonedByTopic = new Map<string, number>();
  try {
    const rows = await db.learningEvent.groupBy({
      by: ["topicId"],
      where: {
        studentId: userId,
        subjectId: subject.id,
        kind: "QUIZ_ABANDONED",
        topicId: { not: null },
      },
      _count: { _all: true },
    });
    abandonedByTopic = new Map(
      rows
        .filter((row): row is typeof row & { topicId: string } => row.topicId !== null)
        .map((row) => [row.topicId, row._count._all]),
    );
  } catch (error) {
    console.error("Loading abandonment counts failed:", error);
  }

  const groups = groupTopics(state, graph, pretestPassed, abandonedByTopic, now);

  const answers = await db.learningEvent.findMany({
    where: {
      studentId: userId,
      subjectId: subject.id,
      kind: "QUESTION_ANSWERED",
    },
    select: { difficulty: true, correct: true, seconds: true },
  });
  const samples: AnswerSample[] = answers.map((row) => ({
    difficulty: row.difficulty,
    correct: row.correct === true,
    seconds: row.seconds,
  }));

  const estimate = await db.question.aggregate({
    where: { subjectId: subject.id },
    _avg: { timeEstimateSeconds: true },
  });
  const profile = buildProfile(samples, estimate._avg.timeEstimateSeconds ?? null);

  const answered = samples.length;
  const correctCount = samples.filter((sample) => sample.correct).length;
  const accuracy = answered > 0 ? (correctCount / answered) * 100 : null;
  const covered = [
    ...groups.NEEDS_WORK,
    ...groups.NEEDS_REVISION,
    ...groups.COMING_ALONG,
    ...groups.SOLID,
  ].length;

  return {
    subject,
    verdict: {
      accuracy,
      grade: accuracy === null ? null : getGrade(accuracy),
      answered,
      correct: correctCount,
      topicsCovered: covered,
      topicsInScope: graph.nodes.size,
      secondsSpent: samples.reduce((sum, s) => sum + (s.seconds ?? 0), 0),
    },
    groups,
    profile,
    insights: selectInsights(
      subjectInsights({
        subjectId: subject.id,
        subjectSlug: subject.slug,
        groups,
        profile,
      }),
      4,
    ),
  };
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

If `tsc` reports errors about the Prisma client's shape (missing models or fields that plainly exist in `prisma/schema.prisma`), the generated client is stale — stop the dev server, run `npx prisma generate`, and retry. A dev server holding the query engine DLL makes `prisma generate` fail with `EPERM`, and the stale client shows up as bogus type errors.

- [ ] **Step 3: Commit**

```bash
git add src/lib/analytics/subject-view.ts
git commit -m "feat(analytics): load subject performance from the evidence layer"
```

---

### Task 6: Performance shell and tabs

**Files:**
- Create: `src/app/(dashboard)/performance/layout.tsx`
- Create: `src/components/performance/performance-tabs.tsx`
- Modify: `src/app/(dashboard)/performance/page.tsx` (remove its `PageHeader`)

**Interfaces:**
- Consumes: `PageHeader` (`src/components/ui/page-header.tsx`).
- Produces: `PerformanceTabs` (no props).

> Only two tabs exist in Phase 1 — Overview and By subject. Do not add Exams or Progress links: a tab that leads to a 404 is worse than a tab that arrives later.

- [ ] **Step 1: Read the routing docs**

Read the layouts and pages guide under `node_modules/next/dist/docs/`. This repo's Next.js differs from what you may remember; confirm the layout signature and that nested layouts do not re-render on segment navigation before writing the file.

- [ ] **Step 2: Write the tab component**

Create `src/components/performance/performance-tabs.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/performance", label: "Overview" },
  { href: "/performance/subjects", label: "By subject" },
];

/**
 * A scrollable pill rail, not a desktop tab bar: at 360px a wrapping tab bar
 * breaks into two ragged lines and the active tab moves under the thumb.
 */
export function PerformanceTabs() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Performance views"
      className="-mx-4 mb-6 overflow-x-auto px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      <ul className="flex w-max gap-2">
        {TABS.map((tab) => {
          const active =
            tab.href === "/performance"
              ? pathname === "/performance"
              : pathname.startsWith(tab.href);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={`block whitespace-nowrap rounded-full px-4 py-2 text-sm font-semibold transition-colors ${
                  active
                    ? "bg-primary text-primary-foreground"
                    : "bg-secondary text-muted hover:text-foreground"
                }`}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
```

- [ ] **Step 3: Write the layout**

Create `src/app/(dashboard)/performance/layout.tsx`:

```tsx
import type { ReactNode } from "react";
import { PageHeader } from "@/components/ui/page-header";
import { PerformanceTabs } from "@/components/performance/performance-tabs";

export default function PerformanceLayout({ children }: { children: ReactNode }) {
  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Performance"
        description="Track your progress, see your grades, and identify topics that need more attention."
      />
      <PerformanceTabs />
      {children}
    </div>
  );
}
```

- [ ] **Step 4: Remove the duplicated header from Overview**

In `src/app/(dashboard)/performance/page.tsx`, delete the `<PageHeader ... />` element and the now-unused `PageHeader` import, and change the outermost `<div className="animate-fade-in">` wrapper to a fragment `<>...</>` — the layout owns both now.

- [ ] **Step 5: Typecheck and look at it**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

Run `npm run dev`, open `/performance`, and confirm: one header (not two), two pills, Overview highlighted. Narrow the window to 360px and confirm the pills scroll rather than wrap.

- [ ] **Step 6: Commit**

```bash
git add "src/app/(dashboard)/performance/layout.tsx" src/components/performance/performance-tabs.tsx "src/app/(dashboard)/performance/page.tsx"
git commit -m "feat(performance): add tab shell to the performance section"
```

---

### Task 7: The subject view

**Files:**
- Create: `src/app/(dashboard)/performance/subjects/page.tsx`
- Create: `src/app/(dashboard)/performance/subjects/loading.tsx`
- Create: `src/components/performance/subject-chips.tsx`
- Create: `src/components/performance/verdict-band.tsx`
- Create: `src/components/performance/insight-list.tsx`
- Create: `src/components/performance/topic-group-list.tsx`
- Create: `src/components/performance/profile-band.tsx`

**Interfaces:**
- Consumes: `getSubjectChoices`, `getSubjectPerformance`, `SubjectPerformance`, `SubjectChoice` (Task 5); `TopicRow`, `TopicGroupKey` (Task 2); `Profile` (Task 3); `Insight` (Task 1); `evidenceLabel` (`src/lib/evidence-display.ts`); `Badge`, `Progress`, `EmptyState`, `buttonClass`, `PageSkeleton` (existing `src/components/ui/*`).
- Produces: the route; no exports consumed by later tasks.

- [ ] **Step 1: Write the presentational components**

Create `src/components/performance/insight-list.tsx`:

```tsx
import Link from "next/link";
import type { Insight } from "@/engines/analytics/insight";

const TONE: Record<Insight["severity"], string> = {
  CRITICAL: "border-danger/30 bg-danger-soft",
  WARNING: "border-warning/30 bg-warning-soft",
  INFO: "border-border bg-secondary/40",
  WIN: "border-success/30 bg-success-soft",
};

export function InsightList({ insights }: { insights: Insight[] }) {
  if (insights.length === 0) return null;

  return (
    <ul className="mt-6 space-y-2.5">
      {insights.map((insight, index) => (
        <li
          key={`${insight.kind}-${insight.topicId ?? index}`}
          className={`rounded-2xl border p-4 ${TONE[insight.severity]}`}
        >
          <p className="text-sm font-semibold text-foreground">{insight.headline}</p>
          {insight.detail && (
            <p className="mt-1 text-xs leading-relaxed text-muted">{insight.detail}</p>
          )}
          {insight.action && (
            <Link
              href={insight.action.href}
              className="mt-2 inline-block text-xs font-bold text-primary hover:underline"
            >
              {insight.action.label} →
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}
```

If `danger`/`success`/`warning` soft tokens are not all present in the Tailwind theme, check `src/app/globals.css` for the token names actually defined and use those — the existing performance page uses `bg-success-soft`, `bg-warning-soft`, `bg-primary-soft` and `text-tone-blue-ink`, so follow whichever of those exist rather than inventing new tokens.

Create `src/components/performance/verdict-band.tsx`:

```tsx
import { Badge } from "@/components/ui/badge";
import type { SubjectVerdict } from "@/lib/analytics/subject-view";

function gradeVariant(grade: string): "green" | "blue" | "amber" | "orange" | "red" {
  switch (grade) {
    case "A": return "green";
    case "B": return "blue";
    case "C": return "amber";
    case "D": return "orange";
    default: return "red";
  }
}

export function VerdictBand({
  subjectName,
  verdict,
}: {
  subjectName: string;
  verdict: SubjectVerdict;
}) {
  const hours = verdict.secondsSpent / 3600;
  const figures = [
    {
      label: "Accuracy",
      value: verdict.accuracy === null ? "—" : `${Math.round(verdict.accuracy)}%`,
    },
    { label: "Questions", value: String(verdict.answered) },
    {
      label: "Topics covered",
      value: `${verdict.topicsCovered}/${verdict.topicsInScope}`,
    },
    {
      label: "Time",
      value: hours >= 1 ? `${hours.toFixed(1)}h` : `${Math.round(verdict.secondsSpent / 60)}m`,
    },
  ];

  return (
    <section className="card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-prose text-sm font-semibold leading-relaxed text-foreground">
          {verdict.accuracy === null
            ? `You haven't answered any ${subjectName} questions yet.`
            : `You're at ${Math.round(verdict.accuracy)}% accuracy in ${subjectName} across ${verdict.answered} ${verdict.answered === 1 ? "question" : "questions"} — a ${verdict.grade}.`}
        </p>
        {verdict.grade && (
          <Badge variant={gradeVariant(verdict.grade)}>{verdict.grade}</Badge>
        )}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {figures.map((figure) => (
          <div key={figure.label} className="rounded-xl bg-secondary/40 p-3">
            <dt className="text-xs font-semibold text-muted">{figure.label}</dt>
            <dd className="mt-0.5 text-lg font-bold tracking-tight text-foreground">
              {figure.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
```

Create `src/components/performance/topic-group-list.tsx`:

```tsx
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { evidenceLabel } from "@/lib/evidence-display";
import type { TopicRow } from "@/engines/analytics/topic-groups";

/**
 * A card stack that becomes a grid from sm: up. Never a table — a horizontally
 * scrolling table is not an acceptable phone experience.
 *
 * <details> rather than a client component: collapsing needs no JavaScript, so
 * this stays a server component and ships none.
 */
export function TopicGroupList({
  title,
  blurb,
  rows,
  subjectSlug,
  defaultOpen = false,
}: {
  title: string;
  blurb: string;
  rows: TopicRow[];
  subjectSlug: string;
  defaultOpen?: boolean;
}) {
  if (rows.length === 0) return null;

  return (
    <details open={defaultOpen} className="card mt-4 overflow-hidden">
      <summary className="cursor-pointer list-none p-5">
        <span className="flex items-center justify-between gap-3">
          <span>
            <span className="block text-sm font-bold text-foreground">{title}</span>
            <span className="mt-0.5 block text-xs text-muted">{blurb}</span>
          </span>
          <Badge variant="blue">{rows.length}</Badge>
        </span>
      </summary>

      <ul className="grid grid-cols-1 gap-2 border-t border-border bg-secondary/20 p-4 sm:grid-cols-2">
        {rows.map((row) => {
          const fallback = evidenceLabel({
            confidence: row.confidence,
            accObservations: row.observations,
            lessonObservations: 0,
            srsObservations: 0,
            lastStudy: row.lastStudy,
          });
          return (
            <li key={row.topicId}>
              <Link
                href={`/classroom/${subjectSlug}/${row.slug}`}
                className="block rounded-xl border border-border bg-card p-3 transition-colors hover:border-primary/30"
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-semibold text-foreground">
                    {row.title}
                  </span>
                  {row.stale && <Badge variant="amber">Stale</Badge>}
                </span>
                {fallback ? (
                  <span className="mt-1.5 block text-xs text-muted">{fallback}</span>
                ) : (
                  <>
                    <span className="mt-1.5 block text-xs text-muted">
                      {Math.round(row.mastery)}% mastery · {row.observations} answered
                    </span>
                    <span className="mt-1.5 block">
                      <Progress value={Math.round(row.mastery)} tone="auto" />
                    </span>
                  </>
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
```

Create `src/components/performance/profile-band.tsx`:

```tsx
import { Progress } from "@/components/ui/progress";
import type { Profile } from "@/engines/analytics/profile";

const BAND_LABEL: Record<string, string> = {
  BASIC: "Basic",
  INTERMEDIATE: "Intermediate",
  ADVANCED: "Advanced",
  UNLABELLED: "Unlabelled",
};

const PACING_COPY = {
  RUSHED: "You're answering faster than these questions are meant to take.",
  ON_PACE: "You're answering at about the expected pace.",
  SLOW: "You're taking longer than these questions are meant to take.",
} as const;

export function ProfileBand({ profile }: { profile: Profile }) {
  if (profile.status === "insufficient") {
    return (
      <div className="card mt-4 p-5">
        <p className="text-sm text-muted">
          Answer {profile.needed - profile.answered} more{" "}
          {profile.needed - profile.answered === 1 ? "question" : "questions"} here and
          you'll see how you do by difficulty, and whether your pace would survive a
          timed paper.
        </p>
      </div>
    );
  }

  return (
    <div className="card mt-4 space-y-4 p-5">
      <div>
        <p className="section-label mb-3">Accuracy by difficulty</p>
        <ul className="space-y-2.5">
          {profile.bands.map((band) => (
            <li key={band.difficulty}>
              <span className="mb-1 flex items-center justify-between text-xs">
                <span className="font-semibold text-foreground">
                  {BAND_LABEL[band.difficulty] ?? band.difficulty}
                </span>
                <span className="text-muted">
                  {Math.round(band.accuracy)}% of {band.answered}
                </span>
              </span>
              <Progress value={Math.round(band.accuracy)} tone="auto" />
            </li>
          ))}
        </ul>
      </div>

      {profile.pacing && (
        <p className="text-xs leading-relaxed text-muted">
          {PACING_COPY[profile.pacing.verdict]} You average{" "}
          {Math.round(profile.pacing.meanSeconds)}s against an expected{" "}
          {Math.round(profile.pacing.expectedSeconds)}s.
        </p>
      )}

      {profile.rapidGuessRate > 0 && (
        <p className="text-xs leading-relaxed text-muted">
          {Math.round(profile.rapidGuessRate)}% of your answers were too fast to have
          been read.
        </p>
      )}
    </div>
  );
}
```

Create `src/components/performance/subject-chips.tsx`:

```tsx
import Link from "next/link";
import type { SubjectChoice } from "@/lib/analytics/subject-view";

/** Weakest-first, so the chip a student most needs sits nearest the thumb. */
export function SubjectChips({
  subjects,
  activeSlug,
}: {
  subjects: SubjectChoice[];
  activeSlug: string;
}) {
  return (
    <div className="-mx-4 mb-5 overflow-x-auto px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      <div className="flex w-max gap-2">
        {subjects.map((subject) => {
          const active = subject.slug === activeSlug;
          return (
            <Link
              key={subject.id}
              href={`/performance/subjects?subject=${subject.slug}`}
              aria-current={active ? "page" : undefined}
              className={`whitespace-nowrap rounded-full px-3.5 py-2 text-xs font-bold transition-colors ${
                active
                  ? "bg-primary text-primary-foreground"
                  : "bg-secondary text-muted hover:text-foreground"
              }`}
            >
              {subject.code}
              {subject.accuracy !== null && (
                <span className="ml-1.5 font-semibold opacity-70">
                  {Math.round(subject.accuracy)}%
                </span>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Write the route**

Create `src/app/(dashboard)/performance/subjects/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import Link from "next/link";
import { LuTarget, LuChevronRight } from "react-icons/lu";
import { auth } from "@/lib/auth";
import { getSubjectChoices, getSubjectPerformance } from "@/lib/analytics/subject-view";
import { EmptyState } from "@/components/ui/empty-state";
import { buttonClass } from "@/components/ui/button";
import { SubjectChips } from "@/components/performance/subject-chips";
import { VerdictBand } from "@/components/performance/verdict-band";
import { InsightList } from "@/components/performance/insight-list";
import { TopicGroupList } from "@/components/performance/topic-group-list";
import { ProfileBand } from "@/components/performance/profile-band";

export default async function SubjectPerformancePage({
  searchParams,
}: {
  searchParams: Promise<{ subject?: string }>;
}) {
  const session = await auth();
  if (!session?.user?.id) redirect("/login");

  const { subject: requested } = await searchParams;
  const choices = await getSubjectChoices(session.user.id);

  if (choices.length === 0) {
    return (
      <EmptyState
        tone="primary"
        icon={<LuTarget className="h-6 w-6" />}
        title="Nothing to analyse yet"
        description="Answer some questions and this page will show you which topics are weak, which have faded, and which you've never proven."
        action={
          <Link href="/practice/past-questions" className={buttonClass("primary", "lg")}>
            Start Practicing
            <LuChevronRight className="h-4 w-4" />
          </Link>
        }
      />
    );
  }

  // Weakest-first ordering makes choices[0] the subject that most needs looking at.
  const activeSlug =
    choices.find((choice) => choice.slug === requested)?.slug ?? choices[0].slug;
  const data = await getSubjectPerformance(session.user.id, activeSlug);
  if (!data) redirect("/performance/subjects");

  return (
    <>
      <SubjectChips subjects={choices} activeSlug={activeSlug} />

      <VerdictBand subjectName={data.subject.name} verdict={data.verdict} />
      <InsightList insights={data.insights} />

      <h2 className="section-label mt-8 mb-1">Topics</h2>
      <TopicGroupList
        title="Needs work"
        blurb="Measured, and weak. These are your real weaknesses."
        rows={data.groups.NEEDS_WORK}
        subjectSlug={data.subject.slug}
        defaultOpen
      />
      <TopicGroupList
        title="Needs revision"
        blurb="You knew these and they've faded."
        rows={data.groups.NEEDS_REVISION}
        subjectSlug={data.subject.slug}
      />
      <TopicGroupList
        title="Coming along"
        blurb="Real progress, not finished yet."
        rows={data.groups.COMING_ALONG}
        subjectSlug={data.subject.slug}
      />
      <TopicGroupList
        title="Unproven"
        blurb="Not weaknesses — unknowns. You haven't answered enough here to say."
        rows={data.groups.UNPROVEN}
        subjectSlug={data.subject.slug}
      />
      <TopicGroupList
        title="Solid"
        blurb="Strong. Anything marked stale is worth a quick review."
        rows={data.groups.SOLID}
        subjectSlug={data.subject.slug}
      />

      <h2 className="section-label mt-8 mb-1">How you answer</h2>
      <ProfileBand profile={data.profile} />
    </>
  );
}
```

Create `src/app/(dashboard)/performance/subjects/loading.tsx`:

```tsx
import { PageSkeleton } from "@/components/ui/page-skeleton";

export default function Loading() {
  return <PageSkeleton />;
}
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors. If `buttonClass`, `EmptyState`, `Badge` or `Progress` props do not match, read the component file and match its real signature — the calls above follow `src/app/(dashboard)/performance/page.tsx`, so a mismatch means that page has diverged.

- [ ] **Step 4: Verify it in the running app**

Run `npm run dev` and open `/performance/subjects` as a student with practice history.

Confirm:
- Chips are weakest-first; clicking one changes the subject and the URL.
- Needs work is open; the other four groups are collapsed.
- A topic with fewer than three observations shows an evidence sentence, not a percentage.
- At 360px nothing scrolls horizontally except the chip rails.
- A student with no history sees the empty state, not a crash.

- [ ] **Step 5: Commit**

```bash
git add "src/app/(dashboard)/performance/subjects" src/components/performance
git commit -m "feat(performance): add the subject lens"
```

---

### Task 8: Retire the wrong-count weakness ranking

**Files:**
- Modify: `src/app/(dashboard)/performance/page.tsx`
- Modify: `src/lib/performance.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `PerformanceData` loses `subjectWeakTopics`; `PerformanceWeakTopic` and `PerformanceSubjectWeakTopics` are deleted.

> This is the deletion the whole phase is for. Ranking topics by raw wrong-count measures how much a student practised, so the topics they worked hardest on rise to the top of their weakness list — the subject lens now answers this properly, and leaving both would put two contradictory weakness lists one tab apart.

- [ ] **Step 1: Confirm nothing else consumes the weak-topic data**

Run:

```bash
grep -rn "subjectWeakTopics\|PerformanceWeakTopic\|PerformanceSubjectWeakTopics\|loadWeakTopics" src scripts
```

Expected: hits only in `src/lib/performance.ts` and `src/app/(dashboard)/performance/page.tsx`. If anything else appears, stop and report it rather than deleting — a stale grep is how past mirror-drift survived unnoticed in this codebase.

- [ ] **Step 2: Delete the producer**

In `src/lib/performance.ts`: delete the `PerformanceWeakTopic` type, the `PerformanceSubjectWeakTopics` type, the whole `loadWeakTopics` function, the `subjectWeakTopics` field from `PerformanceData`, and the `subjectWeakTopics: await loadWeakTopics(userId)` line from the returned object.

- [ ] **Step 3: Delete the consumer and point at the new view**

In `src/app/(dashboard)/performance/page.tsx`: delete the `weakEntry` lookup and the entire `{weakEntry && weakEntry.topics.length > 0 && (...)}` block inside the subject card, and drop the now-unused `LuTriangleAlert` import.

Change the subject card's link target from `/classroom/${metric.subjectSlug}` to `/performance/subjects?subject=${metric.subjectSlug}` so the Overview's subject rows lead into the lens that now owns topic detail.

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors. An error here means Step 1's grep missed a consumer.

- [ ] **Step 5: Run the suite**

Run: `npm test`
Expected: PASS.

- [ ] **Step 6: Verify in the running app**

Open `/performance`. Confirm the subject cards no longer carry a "Topics to improve" strip, and that clicking one lands on that subject's lens.

- [ ] **Step 7: Commit**

```bash
git add src/lib/performance.ts "src/app/(dashboard)/performance/page.tsx"
git commit -m "refactor(performance): drop the wrong-count weakness ranking"
```

---

## Phase 1 done

At this point `/performance` has a working subject lens, the misleading wrong-count ranking is gone, and no schema has changed. Phases 2 (progress lens) and 3 (exam lens) get their own plan documents, written against the same spec when this one lands.
