# Learning Path Engine — Knowledge-Graph Design

Date: 2026-08-02
Status: Draft
Role: Senior Learning Experience Designer

## Problem

Today topics are **isolated lessons**. The schema models prerequisites as a
single scalar chain (`Topic.prerequisiteTopicId`, `prisma/schema.prisma:305`),
the topic page gates on "any lesson completed under the prereq topic"
(`hasCompletedAnyLessonInTopic`, `src/lib/lesson-engine.ts:351`), and the study
plan is a round-robin over activity types with no dependency awareness
(`src/app/api/study-plan/route.ts:112`).

That means the product cannot answer five questions that decide exam results:

1. **When can I start this?** — a linear chain forces one ordering; a real
   syllabus has *many* prerequisites (e.g., "Vectors" gates both "Statics" and
   "Projectile Motion", each of which gates others).
2. **What should I do next?** — nothing ranks the dozen available topics by
   payoff; students pick by habit, not by yield.
3. **Where are my gaps?** — weaknesses are listed per topic, but nobody knows a
   weak topic also *blocks* three downstream topics (the graph multiplier).
4. **When must I revise?** — fixed 1/3/7/14-day passes (lesson engine) and the
   flashcard SRS schedule exist separately; nothing merges them into one
   mastery-driven revision queue.
5. **What should tomorrow's plan contain?** — the plan must schedule *lesson →
   practice → spaced revision* per topic in dependency order, not shuffle
   activity types.

## Goal

A **Learning Path Engine** that treats topics as nodes in a **knowledge graph
(DAG)**, derives each student's per-topic mastery and retention, gates unlocks
progressively, and produces ranked *next-topic*, *learning-gap*, and *revision*
recommendations plus a **graph-aware personalized study plan** — closing the
PRD's loop *Teach → Test → Diagnose → Plan* (§7.2) at topic granularity.

## Learning-science principles

| # | Principle | How the engine honours it |
|---|---|---|
| 1 | **Prerequisites are a graph, not a queue.** | Many-to-many edges with a strength (how much prereq mastery unlocks how much), validated as a DAG at seed time. |
| 2 | **You earn the next step.** | A topic unlocks only when its prereq edges' mastery gates are met; lessons unlock progressively *within* a topic. |
| 3 | **Fix the bottleneck, not the symptom.** | Gap triage ranks weak topics by how many downstream topics they gate (weighted by exam weight) — the highest-leverage fix wins. |
| 4 | **Mastery is composite and evidence-based.** | Practice accuracy + lesson mastery + flashcard retention combine; missing evidence reweights, never zeroes. |
| 5 | **Retention decays; revision follows it.** | The SRS forgetting curve (already in `lib/spaced-repetition.ts`) drives a topic-level retention number and a due-for-revision queue. |
| 6 | **Don't force known material.** | A "readiness pretest" lets a student self-certify a topic (≥80% on 5 questions) instead of grinding lessons they know. |
| 7 | **Recommend by payoff, explain the payoff.** | Next-topic ranking mixes exam weight, downstream leverage, decay, readiness, and freshness — with a one-line human reason. |
| 8 | **The plan is derived, not guessed.** | The scheduler runs on the graph (topological order) and re-runs on demand; it never drifts from mastery. |
| 9 | **Closed loop at topic granularity.** | Every lesson, quiz, and flashcard review writes mastery; the same numbers that unlock topics also schedule revision and re-order the plan. |

---

## The knowledge graph model

```
Node: Topic (existing)                       Edge: TopicEdge (new)
  id, subjectId, curriculumLevelId,            id
  title, slug, orderIndex,                     prereqTopicId ──► Topic (gated node)
  estimatedMinutes,                            topicId       ──► Topic (learnt node)
  waecWeight, jambWeight,                      kind: PREREQUISITE | STRONG_RELATED | RELATED
  prerequisiteTopicId (legacy)                 strength: 0..1   // mastery fraction required
                                               rationale: "Statics assumes vector resolution"

Subject ──► Topic (node) ──► Subtopic ──► Lesson (leaf, existing progressive gates:
            │                                   Lesson.prerequisites + orderIndex)
            └─ TopicEdge ─► Topic
```

Graph invariants (enforced by a seed-time lint, `lintKnowledgeGraph`):

- The graph is a **DAG** — a topological sort succeeds; cycles are reported with
  the cycle path, not silently dropped.
- No duplicate `(prereqTopicId, topicId)` pairs; `strength ∈ (0, 1]`.
- Every edge references existing topics in the same subject (cross-subject edges
  are allowed only for CORE subjects — e.g. Maths → Physics).
- `Topic.prerequisiteTopicId` rows are **migrated into edges** on import; the
  engine reads edges and falls back to the scalar only when no edges exist.

---

## Algorithms

### A. Per-topic mastery & retention (the state layer)

```
function topicState(studentId, graph):
  for each topic t:
    acc    = metric(t).accuracy            // 0..100, practice evidence
    lessonM = avg(StudentProgress.lesson.masteryScore)  // best-of-last-3 per lesson
    srs    = avgPredictedRetention(cards in t's enrolled decks)   // R(t) from SRS

    present = [(0.45, acc), (0.35, lessonM), (0.20, srs)] where component != null
    wSum = Σ w;  w_i = w_i / wSum          // reweight missing evidence

    mastery[t]    = round(Σ w_i * component_i, 0..100)
    level[t]      = masteryLevelFromScore(mastery[t])   // 85 STRONG / 70 COMPETENT /
                                                        // 50 DEVELOPING / else WEAK (existing)
    lastStudy[t]  = max(lastLessonAt, lastAttemptAt, lastReviewAt)
    stability[t]  = { WEAK:5, DEVELOPING:14, COMPETENT:30, STRONG:60 } days
    retention[t]  = R(daysSince(lastStudy[t]), stability[t])
                  = (1 + (19/81) * days / stability) ^ -0.5   // FSRS curve, shared with SRS
```

Two numbers per topic, both surfaced: **mastery** (have you got it) and
**retention** (will you still have it on exam day).

### B. Progressive unlock (the availability gate)

```
TARGET        = 70     // topic is "done" at mastery ≥ 70
GATE          = 60     // default mastery a strength-1.0 prereq requires
PRETEST_PASS  = 80     // readiness pretest pass mark (%)

function isAvailable(t):
  for (p → t, strength) in incomingEdges(t) where kind == PREREQUISITE:
    need = GATE * strength
    if mastery[p] < need and not pretestPassed(p):   return false
  return true

function lessonUnlockState(lesson, topicState):
  topicReady = isAvailable(topic)
  lessonPrereqsMet = every entry in Lesson.prerequisites is
                     (a COMPLETED lesson) or (topic mastery ≥ COMPETENT)
  priorDone = all lessons with lower orderIndex in the subtopic are COMPLETED
  return topicReady && lessonPrereqsMet && priorDone
```

Notes:
- `pretestPassed(p)` is earned once: pass a 5-question readiness pretest ≥ 80%.
- The old `hasCompletedAnyLessonInTopic` gate is superseded by `mastery[p] ≥ GATE`.
- "Preview, never a wall" is preserved: a locked lesson still shows objectives
  and a "Complete prerequisites" prompt (lesson-engine design, Stage 0).

### C. Next-topic recommendation

```
function recommendNext(state, graph, k = 3):
  candidates = { t : isAvailable(t) and mastery[t] < TARGET }
  if candidates empty: return consolidation picks from the revision queue

  for t in candidates:
    urgency   = (TARGET - mastery[t]) / TARGET            // 0..1
    leverage  = Σ_{d dependent, mastery[d] < TARGET} examWeight(d) / Σ examWeight
    decay     = max(0, 0.85 - retention[t])               // 0..1
    readiness = 1 if all incoming prereqs STRONG else 0.5
    freshness = 0.5 if (today - lastStudy[t]) < 1 day else 1

    score[t] = 0.30·urgency + 0.30·leverage + 0.20·decay
             + 0.10·readiness + 0.10·freshness

  return top-k with reasons, e.g.:
    "High-yield for JAMB"          (urgency — big weight, low mastery)
    "Unlocks 3 topics"             (leverage)
    "Fading — revise while fresh"  (decay)
```

### D. Learning-gap detection

```
classify(t):
  if mastery[t] < 50            → WEAK
  else if retention[t] < 0.80   → DECAYED
  else if isAvailable(t) and never studied → UNTOUCHED
  else if not isAvailable(t) and gates ≥ 2 unmastered dependents → BOTTLENECK

bottleneckScore(t) = Σ_{d ∈ descendants(t)} examWeight(d)   // the graph multiplier

gapQueue = sort(topics in WEAK ∪ DECAYED ∪ BOTTLENECK)
           by (bottleneckScore desc, mastery asc)
```

The dashboard's "Topics to improve" is re-ranked by `bottleneckScore`: fixing a
weak *foundation* topic pays for several downstream topics at once.

### E. Revision recommendation (mastery-driven)

```
revisionDue(t) =
     retention[t] < 0.85                          // decayed below target
  OR min(StudentProgress.revisionDueAt) ≤ today   // fixed [1,3,7,14] cadence
  OR dueSrsCards(t) > 0                           // flashcard SRS queue

priority(t) = (0.85 - retention[t]) · examWeight(t) · (1 + blockedCount(t)/maxBlocked)

revisionSession(t) = due flashcard reviews + 3 targeted past questions
                     from the student's weakest KCs (reuses lesson-engine
                     Stage 6 revision design)
```

The SRS scheduler (`lib/spaced-repetition.ts`) keeps working per card; this
engine aggregates its output into a per-topic queue and merges it with the
fixed-cadence passes — one surface, not two.

### F. Personalized study plan (graph-aware scheduler)

Reworks `/api/study-plan` from round-robin to a topological scheduler:

```
generatePlan(subjects, targetDate, dailyMinutes, state):
  G       = combined DAG of chosen subjects
  order   = topologicalSort(G)                      // Kahn; tie-break by
                                                    // urgency+leverage desc
  # 1. Reserve the revision runway: last 20% of days (min 14, max 21)
  #    hold mock exams + revision-only items.
  # 2. Learnable topics = mastery < TARGET.
  # 3. Per topic, allocate sessions:
  #    lessons   = ceil(estimatedMinutes · (1 + weakBonus) / sessionMinutes)
  #    practice  = 1, or 2 if mastery < 50
  #    revision  = schedules at +1 / +3 / +7 / +14 days after each lesson block
  # 4. Forwards greedy schedule over learnable days, per day:
  #    available = [t in order : isAvailable(t) and not fully scheduled]
  #    pick = argmax(urgency(t) + leverage(t))       // interleave subjects
  #    fill day budget; carry overflow to next day
  # 5. Emit StudyPlanItem rows (LESSON/PRACTICE/REVISION/PAST_QUESTIONS/MOCK_EXAM)
  #    with notes = the one-line reason ("Vectors unblocks Statics + Projectile")
```

The planner runs on demand (regenerate button), is deterministic given the same
state, and degrades gracefully when a subject has no edges (falls back to the
current round-robin so no subject regresses).

---

## Schema changes

Backward-compatible; the legacy scalar stays writable so old imports keep working.

```prisma
enum EdgeKind { PREREQUISITE STRONG_RELATED RELATED }

model TopicEdge {
  id            String   @id @default(cuid())
  prereqTopicId String
  prereqTopic   Topic    @relation("GraphPrereq", fields: [prereqTopicId], references: [id], onDelete: Cascade)
  topicId       String
  topic         Topic    @relation("GraphDependent", fields: [topicId], references: [id], onDelete: Cascade)
  kind          EdgeKind @default(PREREQUISITE)
  strength      Float    @default(1)   // fraction of GATE the prereq must reach
  rationale     String?                // author-facing "why" shown in the UI
  @@unique([prereqTopicId, topicId])
  @@index([topicId])
}

model Topic {
  // ...existing fields unchanged, incl. prerequisiteTopicId (legacy)...
  prereqEdges  TopicEdge[] @relation("GraphDependent")  // incoming
  dependentEdges TopicEdge[] @relation("GraphPrereq")   // outgoing
}

model PerformanceMetric {
  // ...existing fields unchanged...
  masteryScore  Float?      // composite 0..100 (algorithm A)
  lastStudiedAt DateTime?   // max evidence timestamp for retention decay
  revisionDueAt DateTime?   // next merged revision due date
}
```

Rationale: one join table gives the graph; `masteryScore`/`lastStudiedAt` give
the decay math a home; everything else (unlock, recommend, gaps, queue) is
**derived state** computed from these plus the existing tables — consistent with
the lesson-engine decision ("revision is derived data, not rows").

## Routes & components

| File | Kind | Responsibility |
|---|---|---|
| `src/engines/learning/graph.ts` | server | Load nodes+edges, DAG lint, legacy-scalar migration |
| `src/engines/learning/mastery.ts` | server | Composite mastery, retention curve, topic state |
| `src/engines/learning/availability.ts` | server | `isAvailable`, lesson unlock, readiness pretest |
| `src/engines/learning/recommend.ts` | server | Next-topic ranking + reasons |
| `src/engines/learning/gaps.ts` | server | Classification, bottleneck scoring |
| `src/engines/learning/revision.ts` | server | Merged revision queue + priority |
| `src/engines/planner/plan.ts` | server | Topological scheduler → `StudyPlanItem[]` |
| `src/lib/learning-path.ts` | server | Facade: one `computePathState(studentId, subjectId?)` call |
| `api/learning-path/next/route.ts` | server | GET next topics |
| `api/learning-path/gaps/route.ts` | server | GET gap queue |
| `api/learning-path/revision/route.ts` | server | GET revision queue |
| `api/learning-path/topics/[topicId]/pretest/route.ts` | server | POST readiness pretest (grade + record) |
| `api/study-plan/route.ts` | server | REWORKED: POST delegates to `planner/plan.ts` |
| `app/(dashboard)/subjects/[subjectSlug]/page.tsx` | server | Rework: graph view (list/graph toggle) + "Next in path" |
| `app/(dashboard)/subjects/[subjectSlug]/[topicSlug]/page.tsx` | server | Prereq chips per edge, per-lesson unlock, mastery ring |
| `app/(dashboard)/dashboard/page.tsx` | server | Add "Next for you" rail |
| `components/path/graph-view.tsx` | client | DAG render: nodes coloured by state, edge arrows |
| `components/path/next-topics.tsx` | client | Recommended rail with reasons |
| `components/path/gap-list.tsx` | client | Ranked gaps with "unlocks N topics" labels |
| `components/path/revision-queue.tsx` | client | Due list + study-now |
| `components/path/pretest-dialog.tsx` | client | Readiness pretest modal |

## User experience

### Stage 0 — Subject page as a knowledge graph
A **graph view** (toggle beside today's term-grouped list) renders topics as
nodes and prerequisites as arrows. Node states carry the signal:

| State | Node | Colour / marker |
|---|---|---|
| LOCKED | grey, lock icon | prereq unmet; hover shows the missing prereq |
| READY | blue | available, not started |
| STARTED | amber | in progress |
| DECAYED | orange, pulse | due for revision |
| MASTERED | green, ✓ | mastery ≥ 70 |

The **recommended next** node glows with a "Next" tag. A header reads
"12 of 45 mastered · 3 ready · 2 due for revision".

### Stage 1 — Topic hub (progressive unlock)
- **Prereq chips** per incoming edge: `Forces ✓` / `Vectors — needs 60% mastery`,
  each linking to the prereq topic.
- **Lesson list** with per-lesson lock state; the first unlocked lesson is
  highlighted "Start here".
- A **"Not sure you know this?" → Take readiness pretest** link: 5 questions,
  ≥80% passes, marks the topic pretest-passed (skips the lesson grind).
- **Mastery ring + retention %** and a **"Due for revision"** badge when decayed.

### Stage 2 — Dashboard "Next for you" rail
Three compact queues, each with a one-tap CTA:

1. **Recommended next** — up to 3 topics, each with its reason line.
2. **Learning gaps** — ranked by bottleneck score, labelled
   "Weak and blocks 3 topics" / "Fading fast".
3. **Revision queue** — merged due items (SRS + fixed cadence), count badge.

### Stage 3 — Study plan timeline
The reworked plan lists days with graph-aware items and a `notes` reason on each
(`Vectors unblocks Statics + Projectile`). The final 2–3 weeks are a visible
revision runway: mocks first, then per-topic revision in priority order.
"Regenerate" is preserved.

### Stage 4 — The loop closes
Every lesson completion, quiz attempt, and flashcard review refreshes the
composite mastery; the next read of any queue reflects it. No manual "update"
step anywhere.

## Decisions

**Edge table over scalar prereq.** A real syllabus is a DAG, not a chain; the
legacy scalar is migrated and kept as a fallback so no subject regresses before
edges are authored.

**Composite mastery with reweighting.** Practice, lessons, and SRS each capture
different evidence; a topic with only quiz data must not score 0 on lessons.

**Derived unlock/queue, stored only the facts.** Unlocks, recommendations, and
revision dues recompute from `masteryScore`/`lastStudiedAt`/`revisionDueAt`;
the planner persists only `StudyPlanItem` rows. Nothing drifts.

**Bottleneck-first gap strategy.** The distinctive insight: a weak foundation
topic is worth more than its own marks because it gates downstream topics.
`bottleneckScore` = weighted descendant count drives both the gap list and the
recommendation's `leverage` term.

**Pretest self-certification.** Forces nothing; a ≥80% readiness pass lets a
student skip material they demonstrably know, keeping motivation high.

**Greedy topological scheduler.** Deterministic, O(V·E), and good enough for a
45-topic subject; no integer programming. Falls back to today's round-robin when
a subject has no edges.

## Phasing

| Phase | Scope |
|---|---|
| 1 | `TopicEdge` + `PerformanceMetric` fields; graph load + DAG lint; migrate `prerequisiteTopicId` → edges |
| 2 | Composite mastery + retention + `isAvailable`; rework topic hub gating |
| 3 | `recommendNext` + gap detection; dashboard "Next for you" rail |
| 4 | Merged revision queue (SRS + fixed cadence) + revision-queue UI |
| 5 | Rework `/api/study-plan` on `planner/plan.ts`; timeline reasons + runway |
| 6 | Graph view on subject page + readiness pretest + content seed (edges for all subjects) |

## Verification

1. `npm run lint`, `tsc`, and `next build` pass.
2. Unit-drive `graph.ts`: a cycle is reported with its path; duplicate edges
   rejected; legacy scalar migrates to an edge.
3. Unit-drive `mastery.ts`: reweighting when a component is null; retention is
   monotone decreasing and hits the due threshold on schedule.
4. Unit-drive `recommend.ts` + `gaps.ts`: a weak topic that gates 3 topics ranks
   above an equal-weak isolated topic.
5. Unit-drive `planner/plan.ts`: the emitted session order is a valid topological
   order of the graph; revision offsets are +1/+3/+7/+14; mock runs fall in the
   final runway.
6. Drive the app: master a prereq topic → dependent unlocks; force decay →
   revision queue populates; regenerate plan → order respects dependencies.
