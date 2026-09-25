# Lesson Engine — Learning Experience Design

Date: 2026-08-01
Status: Draft
Role: Senior Learning Experience Designer

## Problem

Today a lesson is a single blob of markdown rendered on the topic page
(`src/app/(dashboard)/subjects/[subjectSlug]/[topicSlug]/page.tsx`). It is
passive: read text, scroll, leave. Nothing checks understanding, nothing adapts,
nothing schedules revision, and there is no definition of "mastered". The product
promises a closed loop — *Teach → Test → Diagnose → Plan* (PRD §7.2) — but the
teach stage stops at "page of notes".

## Goal

A lesson becomes a **guided, active-learning session** that moves a student from
*entering a topic* to *demonstrated mastery*, using retrieval, worked examples,
spaced repetition, and a grounded AI tutor — all without long blocks of text.

## Learning experience principles

| # | Principle | How the engine honours it |
|---|---|---|
| 1 | **One idea per card.** Working-memory limit → a micro card carries a single concept, ≤ ~120 words. | Content is authored as blocks, not prose paragraphs. |
| 2 | **Active before passive.** Retrieval beats re-reading. | A knowledge check follows every 2 cards; no card ends without a task or a reveal. |
| 3 | **Progressive disclosure.** Load is staged; the learner uncovers. | Worked examples reveal step-by-step; diagrams toggle labels; mnemonics reveal. |
| 4 | **I do → We do → You do.** | Worked examples ship as: fully worked → partially blanked → learner solves. |
| 5 | **Low stakes early, high stakes late.** | Knowledge checks are untimed and retryable; the exit practice is timed and graded. |
| 6 | **Visible progress.** Motivation needs signal. | A step bar shows position; checkpoints mark completion; mastery is explicit. |
| 7 | **Mistakes are content.** Misconceptions are taught, not just penalised. | Every lesson includes explicit "Wrong way → Right way" mistake cards. |
| 8 | **Spaced, not crammed.** | The engine schedules revision at 1 / 3 / 7 / 14 days after completion. |
| 9 | **Diagnosis feeds the loop.** | Mastery writes to the existing `PerformanceMetric`, which already drives weak-topic surfacing and the study plan. |

---

## The mastery journey at a glance

```
ENTER                           LEARN                          PROVE                        MASTER
──────────────────────────────────────────────────────────────────────────────────────────────────
Topic page        Orient          Learn loop        Summary      Revision (spaced)      Mastery level
   │                │          ┌─────────────┐         │              │                   written to
   │ lesson card    │  prereqs │ Micro Card  │─────────┤   practice   │   ≥70% on timed   PerformanceMetric
   │ (duration,     │  objec-  │  (concept / │  AI     │   exit (7    │   practice exit   ──────────────
   │ difficulty,    │  tives   │   diagram / │  Tutor  │   questions, │   → STRONG/COMPETENT
   │ prereqs)       │          │   worked    │  ────── │   90s each)  │   <70% → remediate
   └──▶ Start ──────┴──▶       │   example)  │         └──────┬───────┴── (rework weak cards,
                        Check  │   knowledge │                │         retry after 30 min)
                        every  │   check     │                ▼
                        2nd    └─────────────┘        next lesson / topic quiz unlock
```

**Three gates** define the journey:
1. **Orient gate** — can I start? (prerequisites met)
2. **Prove gate** — did I understand? (timed practice exit ≥ 70%)
3. **Spacing gate** — will I remember? (revision sessions auto-scheduled)

---

## Stage 0 — Entry (lesson hub on the topic page)

Each topic shows lessons as **cards**, not a scroll of notes.

| Element | Behaviour |
|---|---|
| Lesson card | Title · duration · difficulty badge · mastery badge if revisited · `Start` / `Continue` / `Retake` |
| Prerequisite chip | If unmet: lock icon + link to the prereq lesson; card is dimmed but viewable in "browse" mode |
| Progress ring | `completionPercent` from `StudentProgress` when a lesson was started |
| "Restart lesson" | Only after completion; resets checkpoint data, keeps mastery history |

Rules:
- A lesson with unmet prerequisites opens in **preview mode**: objectives are
  readable, content is locked behind a "Complete prerequisite" prompt.
- Once a lesson is COMPLETED, its card shows the computed mastery badge
  (Weak → Strong) so re-entry is a targeted revision decision, not a re-read.

---

## Stage 1 — Orient (the lesson cover)

A single, glanceable screen — no scrolling wall. Two columns on desktop, stacked
on mobile.

**Left — Why am I here?**
- **Learning objectives** as an interactive checklist (check off as read):
  - *Remember* — list the key terms.
  - *Understand* — explain the concept in your own words.
  - *Apply* — solve a JAMB/WAEC/NECO-style problem.
  - *Connect* — link it to a prior topic.
- The objectives are the same verbs used in the summary and the exit practice, so
  the student sees the contract twice.

**Right — The facts**
| Field | Source |
|---|---|
| Estimated duration | `Lesson.estimatedMinutes`, shown as a chip (`~20min`) |
| Difficulty | `Lesson.difficulty` → Basic/Intermediate/Advanced badge |
| Prerequisites | `Lesson.prerequisites` + `Topic.prerequisiteTopicId` chain |
| Exam weight | `Topic.waecWeight` / `jambWeight` chips ("High-yield for JAMB") |

**CTA row:** `Start lesson` (primary) · `Review prerequisites` (ghost, only when
locked) · `Ask the AI tutor` (text link — "not sure you're ready?").

Interaction rule: **Begin is always one click from this screen.** No forced
"check the objectives" friction; checking objectives is rewarded, not required.

---

## Stage 2 — Learn (micro learning cards)

The lesson body is a **sequence of 5–7 micro cards**, each one concept. The
student advances with `Next card`; a progress step bar shows position and the
checkpoints ahead.

### Card types

| Card type | Content | Interaction |
|---|---|---|
| **Concept** | Definition + 1 rule or example, ≤ 120 words | "Reveal the rule" or a mini-fill (blanks) |
| **Diagram** | SVG with labelled parts, hotspots | Toggle labels; click a hotspot → one-line explanation |
| **Worked example** | Problem → 3–5 steps → answer | Steps revealed one at a time ("Show next step") |
| **You-do** | Half-worked problem | Learner fills the missing step; instant check |
| **Wrong → Right** | Common misconception pair | Flip card: ❌ wrong way / ✅ right way |
| **Mnemonic** | Memory hook + the list it encodes | "Reveal" flips to the encoded list |
| **Exam tip** | Examiner behaviour, one sentence | Tagged WAEC / JAMB / NECO chip |

### Embedded components (the remaining required lesson elements)

- **Interactive diagrams** — a `Diagram` block with `nodes`, `edges`, and
  `hotspots`. Rendered as inline SVG. Mobile: hotspots become a stacked list.
- **Worked examples** — `Example` blocks with `problem`, `steps[]`, `answer`,
  `mode: "worked" \| "partial" \| "solo"`.
- **Exam tips** — `tip` blocks, one sentence, exam-type tag, always actionable
  ("JAMB often swaps 'powerhouse' for 'ribosome' — read the stem twice.").
- **Common mistakes** — `mistake` blocks, always a ❌/✅ pair.
- **Mnemonics** — `mnemonic` blocks: phrase + the ordered list it encodes.

### Knowledge checks (Stage 3) — interleaved

After every **2 cards**, a knowledge check appears inline:

- 1 question, MCQ, **untimed, retryable, no marks**.
- On a correct first try → green pulse, next card unlocks.
- On a wrong try → a one-card **"Refresh this"** remediation micro-card appears
  (mini re-explanation), then a **variant** of the question is re-asked.
- KC results are stored per check and feed the mastery score (30% weight).

> Anti-wall rule: a card that would exceed 120 words must be split into two cards.
> This is a content-authoring lint, enforced at seed time.

---

## Stage 4 — AI Tutor (available from stage 1 onward)

A slide-in drawer, contextually grounded. Contract designed now; provider wiring
is a later phase (no AI code exists in the repo today).

| Aspect | Design |
|---|---|
| Entry points | "Ask the AI tutor" (cover), drawer button (player header), post-mistake prompt ("Explain why I got this wrong") |
| Grounding | System prompt is built from the lesson's blocks + topic title + the student's KC/practice results — the tutor never invents facts outside the lesson |
| Behaviour | **Socratic by default** — answers a question with a question first, then a hint, then a short explanation if asked |
| Suggested prompts | Chips: "Explain it like I'm 12" · "Quiz me on this" · "Where do students usually trip?" |
| Guardrails | Opt-outable; typing is free; a "This is practice, not exam advice" footer line |
| API | `POST /api/lessons/[lessonId]/tutor` — `{ messages, lessonId }` → `{ reply, groundedOn }` |

---

## Stage 5 — Summary

One screen, not a paragraph. Composed of:

- **Concept map** — the lesson's diagram reused as a recap (same SVG, labels on).
- **Key points** — up to 5, one line each (existing `Lesson.keyPoints`).
- **Formulas / mnemonics** — the mnemonic cards restated as a strip.
- **"Last-minute recap"** — a 30-second read of the whole lesson, generated from
  the cards, shown collapsed by default.
- **CTA row:** `Take the practice test` (primary) · `Ask the AI tutor` (ghost).

The summary doubles as the *Orient* screen's mirror: same objectives, now as a
"✓ achieved" list.

---

## Stage 6 — Revision (spaced repetition)

The engine schedules **revision sessions**, not the student.

| Rule | Value |
|---|---|
| Schedule | 1, 3, 7, and 14 days after completion |
| Session content | 5 flip-cards (recall: card front → answer) + 3 targeted past questions from the topic bank |
| Targetting | Past questions are drawn from the student's weakest KCs and weakest topic metrics |
| Fail rule | A session < 60% resets the interval to 1 day and re-queues |
| Surface | A "Due for revision" card on the dashboard and study-plan items with `PlanItemActivity.REVISION` (already supported by the schema) |

Revision uses **retrieval**: the front of a flip-card is a blank or a stem, never
the definition itself. The answer side carries the mnemonic.

---

## Stage 7 — Practice exit (the Prove gate)

The graded end of the lesson:

- **7 questions** by default (configurable per lesson), drawn from the question
  bank tagged to the topic, **JAMB-style 90s each**, 60% pass mark default.
- Reuses `/api/assessments/generate` + the existing `QuizEngine`.
- Result is graded instantly; the attempt and wrong answers write to
  `QuestionResponse` → `PerformanceMetric` (unchanged — the loop stays intact).

**Pass (≥ pass mark):**
- Lesson → COMPLETED; revision scheduled; next lesson unlocked.
- Mastery level written to the topic's `PerformanceMetric.masteryLevel`.

**Fail (< pass mark):**
- No failure screen — a **remediation path**: the wrong questions listed, the
  cards they came from surfaced as "Revisit card 3", AI-tutor coaching prompts,
  and a **30-minute retry cooldown**.
- The lesson stays IN_PROGRESS until the pass bar is met.

---

## Stage 8 — Mastery & next steps

| State | Value | Meaning |
|---|---|---|
| Completion | `StudentProgress.status = COMPLETED` | All cards visited, KCs attempted, practice passed |
| Mastery score | `0.3 × KC accuracy + 0.7 × practice accuracy` | First-try KC correct = full, retried = half credit |
| Mastery level | `MasteryLevel` from `PerformanceMetric` | ≥85 STRONG · ≥70 COMPETENT · ≥50 DEVELOPING · else WEAK |
| Recompute | On every practice attempt; mastery = **best of the last 3 attempts** | A bad retake never erases a demonstrated pass |

After mastery, the topic page:
- Marks the lesson's card with the level badge,
- Offers **"Review weaknesses"** (the remediation list) and **"Next lesson"**,
- Unlocks the **topic quiz** CTA (already on the topic page).

---

## The 15 required components → where they live

| # | Component | Stage | Format |
|---|---|---|---|
| 1 | Learning objectives | Orient + Summary | Interactive checklist |
| 2 | Estimated duration | Cover + card | Chip |
| 3 | Difficulty | Cover + card | Badge (Basic/Intermediate/Advanced) |
| 4 | Prerequisites | Cover + hub | Locked chip + chain link |
| 5 | Micro learning cards | Learn | Concept cards, ≤120 words |
| 6 | Interactive diagrams | Learn + Summary | SVG hotspots / toggle labels |
| 7 | Worked examples | Learn | Progressive reveal (I/We/You) |
| 8 | Exam tips | Learn | Tagged one-liner chips |
| 9 | Common mistakes | Learn | ❌/✅ flip pairs |
| 10 | Mnemonics | Learn + Summary | Reveal cards |
| 11 | Knowledge checks | Learn (every 2 cards) | Untimed MCQ + remediation |
| 12 | AI tutor | All stages | Grounded Socratic drawer |
| 13 | Summary | Stage 5 | Concept map + key points + recap |
| 14 | Revision | Stage 6 | Spaced flip-cards + targeted past Qs |
| 15 | Practice questions | Stage 7 | Timed exit assessment |

---

## Schema changes

Minimal, backward-compatible. Existing `content` markdown stays as fallback/SEO;
the engine prefers `blocks`.

```prisma
model Lesson {
  // ...existing fields (content, summary, keyPoints, workedExamples, difficulty,
  //    estimatedMinutes) unchanged...

  blocks           Json?   // ordered block sequence (see authoring format)
  examTips         Json?   // [{ text, examType? }]
  mnemonics        Json?   // [{ phrase, encoded, subjectLabel? }]
  knowledgeChecks  Json?   // [{ id, question, options, answer, explanation, afterCard }]
  prerequisites    Json?   // [{ lessonTitle?, topicTitle?, reason }]
  passMarkPercent  Int     @default(60)
  practiceCount    Int     @default(7)
  revisionDays     Json?   // [1, 3, 7, 14]
}

model StudentProgress {
  // ...existing fields unchanged...
  checkpointData  Json?         // per-block state: visited / correct / attempts
  masteryScore    Float?        // last computed 0..100
  revisionDueAt   DateTime?     // next scheduled revision
}
```

Notes:
- `blocks` supersedes `workedExamples`/`keyPoints`/`summary` for new content but
  all remain writable; the lesson generator and admin import can emit either.
- No new tables. Revision sessions are derived data (schedule + query), not stored
  rows — consistent with the "versioned content pipeline" PRD value.
- `MasteryLevel` is already in the schema (`prisma/schema.prisma:109`) and written
  by `/api/assessments/submit`; the lesson engine writes to the same field.

## Routes & components

| File | Kind | Responsibility |
|---|---|---|
| `app/(dashboard)/subjects/[subjectSlug]/[topicSlug]/lessons/[lessonId]/page.tsx` | server | Fetch lesson + blocks + student state; gate prerequisites; render player |
| `components/lesson/lesson-player.tsx` | client | Stepper orchestrator: orient → cards → checks → summary → practice |
| `components/lesson/objectives-panel.tsx` | client | Objectives checklist |
| `components/lesson/micro-card.tsx` | client | Renders one block by `type` |
| `components/lesson/interactive-diagram.tsx` | client | SVG diagram + hotspots |
| `components/lesson/worked-example.tsx` | client | Progressive reveal |
| `components/lesson/knowledge-check.tsx` | client | MCQ + instant feedback + remediation card |
| `components/lesson/ai-tutor-drawer.tsx` | client | Grounded chat drawer |
| `components/lesson/mastery-summary.tsx` | client | Summary + recap + CTA row |
| `components/lesson/revision-session.tsx` | client | Flip-card deck + targeted questions |
| `lib/lesson-engine.ts` | server | Block lints, mastery math, revision scheduling |
| `api/lessons/[lessonId]/progress/route.ts` | server | PATCH block progress |
| `api/lessons/[lessonId]/tutor/route.ts` | server | AI tutor contract (later phase) |

The topic page (`page.tsx:126`) is reworked to render the lesson **hub** (Stage 0)
instead of inlined markdown; the lesson link targets the player route.

## Content authoring format

A lesson's `blocks` is an ordered array of typed objects:

```
[
  { type: "concept",   id: "c1", title, text, reveal? },
  { type: "diagram",   id: "d1", svg, nodes[], edges[], hotspots[] },
  { type: "example",   id: "e1", problem, steps[], answer, mode: "worked" },
  { type: "tip",       id: "t1", text, examType: "JAMB" },
  { type: "mistake",   id: "m1", wrong, right },
  { type: "mnemonic",  id: "n1", phrase, encoded[] },
  { type: "check",     id: "k1", question, options, answer, explanation, afterCard: "e1" }
]
```

A seed-time lint rejects: cards > 120 words, a `check` without a matching `afterCard`,
a lesson without a closing practice reference, and duplicate block ids.

## Sample lesson — Biology: "The Cell" (condensed)

Demonstrates all 15 components compactly. Full card bodies are authored by content
writers; the shapes are final.

| Block | Shape |
|---|---|
| Cover | **Objectives** (Remember: name the organelles / Understand: explain each function / Apply: compare plant & animal cells / Connect: link to "Classification") · **Duration** ~20min · **Difficulty** Basic · **Prereqs** "Introduction to Biology" |
| c1 | Concept — Plasma membrane: what's in/out; ≤ 60 words + "Reveal the rule" |
| k1 | Check — Which structure controls what enters/leaves? |
| d1 | Diagram — animal cell SVG; hotspot click → organelle one-liner |
| e1 | Worked — "A mature human red blood cell has no mitochondria. Why?" reveal steps |
| m1 | Mistake — ❌ "Ribosomes make energy" → ✅ "Ribosomes make protein; mitochondria make energy" |
| k2 | Check — plant vs animal cell difference |
| n1 | Mnemonic — "**M**ike **P**lays **R**ough **G**uitars" → Mitochonda / Plasma / Ribosome / Golgi (reveal list) |
| t1 | Tip — "JAMB swaps 'powerhouse' for 'ribosome' in options — read stems twice." |
| e2 | You-do — half-blanked plant/animal comparison table |
| Summary | Concept map (d1, labels on) + 5 key points + mnemonic strip + recap |
| Revision | Flip: "No mitochondria in mature ___" → "red blood cells" (1/3/7/14d) |
| Practice | 7 timed past JAMB cell questions |

## Accessibility & mobile

- Focus mode reuses the `QuizEngine` pattern (keyboard shortcuts, hide chrome).
- Diagrams degrade to hotspot lists under `sm:`.
- All reveals are keyboard-activatable; colour is never the only signal
  (labels/icons accompany badges).
- Each card is one focus stop; check feedback is announced via `aria-live`.

## Decisions

**Blocks over tables.** One `Json` column keeps migration tiny and matches the
content pipeline ethos; an MLC engine needs row-level control, not a CMS.

**Retryable knowledge checks, timed exit.** Low-stakes checks must not punish
curiosity; the graded gate is where timing matters (JAMB is a speed exam).

**Mastery = best of last 3.** A bad retake must not erase a demonstrated pass,
but recency still counts — standard exam-prep modelling.

**Revision is derived data.** Scheduling lives in `lib/lesson-engine.ts`, not in
rows, so it recomputes from attempts and never drifts from results.

**AI tutor is a contract now, a provider later.** No AI code exists in the repo;
the spec fixes the UX, grounding, and API so a provider (OpenAI/Anthropic/etc.)
plugs in without redesign.

**Prerequisite previews, not walls.** Locked lessons are readable as objectives +
preview; content stays gated. Locking whole pages punishes motivated students.

## Phasing

| Phase | Scope |
|---|---|
| 1 | Schema fields + authoring lint + lesson player (cards, diagrams, worked examples, checks) |
| 2 | Progress API + mastery math + practice exit wiring to `QuizEngine` |
| 3 | Summary screen + revision scheduler + dashboard "Due for revision" |
| 4 | AI tutor route + drawer (provider wiring) |
| 5 | Port existing auto-generated lessons into `blocks` via the generator |

## Verification

1. **Typecheck + build** clean after each phase.
2. Drive the app: start a locked lesson → preview mode; complete KCs with a
   deliberate wrong answer → remediation card + variant re-ask.
3. Pass the practice exit at ≥ pass mark → COMPLETED, mastery level in
   `PerformanceMetric`, revision scheduled at +1 day.
4. Fail the exit → IN_PROGRESS retained, retry cooldown active, weak cards listed.
5. Confirm a seed-lint violation (e.g., 200-word card) fails `scripts/seed-lessons.ts`.
