# Intelligent Flashcards — Cognitive Learning Design

Date: 2026-08-01
Status: Draft
Role: Cognitive Learning Expert

## Problem

The product promises a closed learning loop — *Teach → Test → Diagnose → Plan*
(PRD §7.2). The lesson engine (**docs/superpowers/specs/2026-08-01-lesson-engine-design.md**)
schedules four revision passes at 1/3/7/14 days, but that is a fixed cadence with
fixed content. It cannot answer three questions that decide exam results:

1. **When** should this *specific* fact come back, for *this* student? A fixed
   schedule ignores that some cards are already known and others are actively
   decaying.
2. **How well** is a fact actually retained? Nobody can see a retention number,
   only a binary lesson pass/fail.
3. **What** should be studied next? There is no per-card diagnosis, no leech
   detection, no high-yield triage tied to JAMB/WAEC weights.

Flashcards are the standard answer: small, atomic, retrieval-focused. But a naive
flashcard deck is also the classic failure — students grind identical decks,
self-rate inconsistently, and quit. This design makes the flashcard system
**intelligent**: an SRS scheduler per card, a real retention model, adaptive
difficulty, and recommendations that feed the existing weak-topic loop.

## Goal

A **second brain** for exam content: ten card formats, a per-card spaced
repetition engine (SM-2 structure + FSRS-style stability/difficulty/retrievability),
honest study statistics, and smart recommendations — all surfaced as a
first-class, distraction-light study surface inside the dashboard.

---

## Learning-science principles

| # | Principle | How the system honours it |
|---|---|---|
| 1 | **Retrieval beats re-reading.** | Every card is answered from memory first; the answer side is only revealed after recall. |
| 2 | **Spacing prevents forgetting.** | Per-card intervals grow exponentially; a forgetting curve (FSRS R(t)) drives the schedule, not a calendar. |
| 3 | **Desirable difficulty.** | Cards are weighted toward their hardest edge: blanks, reordering, scenario transfers — not passive re-reading. |
| 4 | **Confidence is calibrated, not trusted.** | 4-button confidence feeds the scheduler; the retention model corrects for overconfidence via repeated outcomes. |
| 5 | **One idea per card.** | Cards are ≤ ~120 words; authoring lint rejects overloaded cards. |
| 6 | **Struggle is signal, not failure.** | "Again" shrinks the interval and re-teaches; lapses are tracked and surfaced, never punished with a dead end. |
| 7 | **Interleaving beats blocking.** | A session mixes the deck's due cards rather than same-type blocks; past papers already interleave topics. |
| 8 | **Visible signal sustains effort.** | Retention %, streak, and "due today" make progress legible; the scheduler caps daily load to avoid burnout. |
| 9 | **The loop must close.** | Weak cards feed the existing `PerformanceMetric` → weak-topic → study-plan loop; completed lessons generate decks. |

---

## Card formats (10)

Every card is **typed**; the renderer, the authoring lint, and the SRS treat the
type as first-class. "AI-generated" is a **source**, not a shape — an AI card
always conforms to one of the nine shapes below.

| # | Type | Front (recall cue) | Back (answer) | Active interaction |
|---|---|---|---|---|
| 1 | **Definition** | Term | Definition + example | Reveal |
| 2 | **Formula** | Formula (KaTeX) | What each symbol means + note | Reveal |
| 3 | **Image** | Image + prompt | Caption / answer | Reveal |
| 4 | **Diagram** | Unlabelled SVG + hotspots | Labelled SVG + hotspot explanations | Toggle labels / hotspot drill |
| 5 | **Fill in the Blank** | Sentence with `___` blanks | Blanks filled + explanation | Type each blank → check |
| 6 | **Compare & Contrast** | "Compare A vs B" | Only-A / Only-B / Shared | Reveal (or tile taps) |
| 7 | **True or False** | Statement | True/False + explanation | Tap True/False → instant check |
| 8 | **Scenario** | Exam scenario + question | Answer + exam-style explanation | Reveal |
| 9 | **Process** | "Order the steps of …" | Numbered steps | Reveal (shuffled → reorder in v2) |
| 10 | **AI-generated** | *source dimension* — any of the above, generated from a lesson | generated explanation | same as its shape |

> The Fill-in-the-Blank and True/False cards are **self-graded before the
> confidence bar**: the student answers the card itself, the system tells them
> right/wrong, and only then do they rate confidence. This gives the SRS an
> *objective* outcome signal in addition to the subjective confidence rating —
> the two are combined for the retention model.

## The spaced repetition engine

A pragmatic hybrid. **SM-2** provides the battle-tested ease-factor and
interval mechanics; **FSRS** contributes the forgetting curve, stability,
difficulty, and retrievability concepts. Constants are explicit and tunable.
Pure, deterministic, and unit-testable — no database calls.

### State per card (per student)

| Field | Meaning |
|---|---|
| `state` | `NEW` · `LEARNING` · `REVIEW` · `RELEARNING` |
| `stability` | Memory strength in **days** (FSRS S). Grows on success, collapses on lapse. |
| `difficulty` | 1–10, seeded from authored `Difficulty` (BASIC=3, INTERMEDIATE=5, ADVANCED=7), evolves per outcome. |
| `easeFactor` | SM-2 EF, floor 1.3. |
| `intervalDays` | Days until next review. |
| `repetitions` | Consecutive successes in the current phase. |
| `lapses` | Total "Again" on a REVIEW card (leech detection). |
| `retention` | Predicted recall probability **R(t)** at the moment of scheduling. |
| `dueAt` | When the card is next due. |

### Ratings (confidence)

| Button | Key | Meaning |
|---|---|---|
| **Again** | 1 | I did not recall it |
| **Hard** | 2 | I recalled it, with effort |
| **Good** | 3 | I recalled it smoothly |
| **Easy** | 4 | It was too easy |

### The forgetting curve (FSRS)

```
R(t) = (1 + (19/81) · t / S) ^ -0.5
```

`t` = days since last review, `S` = stability in days. A card is due when
`R(t)` falls below the **desired retention** (default 0.90 for exams; configurable).

### Scheduling rules

**Learning phase** (NEW / after a lapse):

| Rating | Interval | Transition |
|---|---|---|
| Again | 1 min | stay / back to start of learning |
| Hard | 5 min | learning (needs 2 consecutive passes) |
| Good | 10 min | learning (needs 2 consecutive passes) |
| Easy | 1 day | graduates to REVIEW immediately |

**Graduation:** two consecutive non-"Again" ratings move the card to REVIEW;
stability seeds from the graduate rating.

**Review phase:**

| Rating | Stability growth | EF update (SM-2) |
|---|---|---|
| Again | `S → 1 day` (relearn base), lapse++, → RELEARNING | `EF -= 0.54` |
| Hard | `S *= EF · 0.8` | `EF -= 0.14` |
| Good | `S *= EF` | `EF` unchanged |
| Easy | `S *= EF · 1.3` | `EF += 0.10` |

```
EF' = clamp(EF + (0.1 − (5−q)·(0.08 + (5−q)·0.02)), 1.3, 5.0)   // SM-2, q: Again=1, Hard=3, Good=4, Easy=5
interval = round(stability) days
```

**Difficulty update (FSRS-style, per outcome):**

```
difficulty' = clamp(difficulty − 0.7·(rating − 2) + 0.02·(5 − difficulty), 1, 10)
```

- Again → +1.4 (harder), Hard → +0.7, Good → neutral, Easy → −0.7, plus 2%
  mean-reversion toward 5.

### Why this hybrid

- **FSRS stability/difficulty/R** give the product its "intelligence": a
  retention number, a per-card difficulty, and a defensible schedule.
- **SM-2 ease factor** is simple, robust, and familiar to educators; it keeps the
  system explainable and cheap to reason about.
- Both are deterministic — the same (card, rating, timestamp) always yields the
  same next state, which keeps `FlashcardReviewLog` auditable and the UI honest.

---

## Retention score & card difficulty

Two distinct numbers, both surfaced:

| Concept | Definition | Source |
|---|---|---|
| **Predicted retention** | Expected probability the card is recallable today, from R(t). | Forgetting curve |
| **Measured retention** | Observed recall rate: non-"Again" reviews ÷ reviews (weighted by the objective self-check where present). | Review log |
| **Card difficulty** | 1–10, per student, evolved from outcomes. | Difficulty model |
| **Authored difficulty** | BASIC / INTERMEDIATE / ADVANCED on the card itself. | Content |

A deck's **retention score** is the average predicted retention across its
scheduled cards — the number the dashboard shows next to "Your retention".

---

## Study statistics

| Stat | Definition |
|---|---|
| Reviews today / this week / total | From `FlashcardReviewLog` |
| Cards learned (lifetime / today) | Distinct cards that graduated to REVIEW |
| Measured retention | Success rate across reviews (30-day window) |
| Predicted retention | Average R(t) over scheduled cards |
| Average interval | Mean `intervalDays` on REVIEW cards |
| Streak | Consecutive days with ≥ 1 review |
| Time per card | Median response time from the review log |
| Activity series | Reviews per day, last 14 days (chart) |
| Difficulty mix | Count of cards per difficulty band |
| Leech cards | `lapses ≥ 4` or success rate < 35% with ≥ 8 reviews |

## Smart recommendations

Rules run server-side over the review state + the existing performance tables
(`PerformanceMetric`, `Topic.waecWeight/jambWeight`):

| # | Rule | Surfaces as |
|---|---|---|
| 1 | Due cards exist | "N cards due now" + jump-to-deck |
| 2 | Card overdue > 2× its interval | "Review overdue cards" (prioritised) |
| 3 | Predicted retention < 0.75 | "Refresher needed" list |
| 4 | Leech (lapses ≥ 4 / poor rate) | "Relearn these" — linked back to the source lesson |
| 5 | Completed lesson never made into a deck | "Turn your {lesson} into cards" |
| 6 | Weak topic (existing metric) has no cards | "Add cards for {weak topic}" |
| 7 | High-yield topic (JAMB/WAEC weight) due soon | "Study {topic} before {exam}" |

Recommendations carry a `priority` (high/medium/low), a title, a one-line
rationale, and an optional CTA. They close the loop with the existing study plan.

---

## Schema changes

New tables (global content + per-student state), backward-compatible.

```prisma
enum FlashcardType   { DEFINITION FORMULA IMAGE DIAGRAM FILL_IN_BLANK COMPARE_CONTRAST TRUE_FALSE SCENARIO PROCESS }
enum FlashcardSource { AUTHORED LESSON AI }
enum FlashcardState  { NEW LEARNING REVIEW RELEARNING }
enum ReviewRating    { AGAIN HARD GOOD EASY }

model FlashcardDeck {
  id          String  @id @default(cuid())
  title       String
  slug        String?
  description String?
  source      FlashcardSource @default(AUTHORED)
  subjectId   String?
  topicId     String?
  lessonId    String?          // generated decks are idempotent per lesson
  createdBy   String?          // author / admin id
  cards       Flashcard[]
  enrollments FlashcardEnrollment[]
  createdAt / updatedAt
}

model Flashcard {
  id         String @id @default(cuid())
  deckId     String
  cardType   FlashcardType
  prompt     String?       // short front label for lists ("Mitochondrion")
  payload    Json          // typed card body (authoring format below)
  difficulty Difficulty @default(INTERMEDIATE)
  tags       Json?
  orderIndex Int @default(0)
  reviews    FlashcardReview[]
  reviewLog  FlashcardReviewLog[]
}

model FlashcardReview {
  id, studentId, flashcardId
  state, easeFactor, stability, difficulty, intervalDays,
  repetitions, lapses, retention, dueAt, lastReviewedAt
  @@unique([studentId, flashcardId])  // one scheduling row per (student, card)
}

model FlashcardReviewLog {
  id, studentId, flashcardId
  rating ReviewRating
  objectiveCorrect Boolean?  // objective self-check result for self-grading card types
  responseTimeMs Int?
  scheduledDays  Float?
  reviewedAt DateTime @default(now())
}

model FlashcardEnrollment {
  studentId, deckId, createdAt
  @@unique([studentId, deckId])
}
```

Rationale (consistent with the lesson-engine decision):
- **Json payloads, not tables.** Card shapes vary per type; a `payload` column
  keeps the migration tiny and matches the "versioned content pipeline" ethos.
- **Global decks, per-student state.** Decks are content (like lessons); only
  review rows are per student.
- **One scheduling row per (student, card).** The SRS state is a single
  upsert; the log is append-only for statistics and audit.

## Routes & components

| File | Kind | Responsibility |
|---|---|---|
| `app/(dashboard)/flashcards/page.tsx` | server | Hub: due hero, deck grid, recommendations, generate entry |
| `app/(dashboard)/flashcards/[deckId]/page.tsx` | server | Load deck + due queue + per-card SRS state; render session |
| `app/(dashboard)/flashcards/stats/page.tsx` | server | Statistics dashboard |
| `components/flashcards/study-session.tsx` | client | Orchestrator: queue → card → rate → summary |
| `components/flashcards/flashcard-view.tsx` | client | Renders one card type (front + back, interactions) |
| `components/flashcards/rate-bar.tsx` | client | 4-button confidence + keyboard (1/2/3/4) |
| `components/flashcards/deck-list.tsx` | client | Deck grid + enroll toggle |
| `components/flashcards/recommendations.tsx` | client | Recommendation list |
| `components/flashcards/stats-dashboard.tsx` | client | Stats + recharts retention/activity charts |
| `components/flashcards/generate-deck-form.tsx` | client | Pick a completed lesson → generate a deck |
| `lib/spaced-repetition.ts` | server | Pure SRS: state transitions, R(t), difficulty, intervals |
| `lib/flashcard-analytics.ts` | server | Stats + recommendation queries |
| `lib/flashcard-content.ts` | server | Payload types, authoring lint, lesson→deck generation |
| `api/flashcards/route.ts` | server | GET decks (with due counts) · POST create deck |
| `api/flashcards/review/route.ts` | server | POST a single review (engine + log + upsert) |
| `api/flashcards/stats/route.ts` | server | GET statistics |
| `api/flashcards/recommendations/route.ts` | server | GET recommendations |
| `api/flashcards/generate/route.ts` | server | POST derive deck from a lesson (deterministic; AI provider later) |
| `api/flashcards/decks/[deckId]/enroll/route.ts` | server | POST toggle enrollment |

## Card authoring format (`payload`)

```ts
// DEFINITION
{ term: "Mitochondrion", definition: "...", example?: "...", imageUrl?: "..." }

// FORMULA
{ name: "Ohm's law", latex: "V = IR",
  variables: [{ symbol: "V", meaning: "voltage (volts)" }, ...], note?: "..." }

// IMAGE
{ imageUrl: "...", prompt: "What structure is this?", answer: "...", caption?: "..." }

// DIAGRAM
{ svg: "<svg...>", hotspots: [{ id, label, text, x?, y? }], caption?: "..." }

// FILL_IN_BLANK
{ sentence: "The powerhouse of the cell is the ___.",
  blanks: [{ id: "b1", answer: "mitochondrion" }],
  hint?: "...", explanation?: "..." }

// COMPARE_CONTRAST
{ itemA: "Plant cell", itemB: "Animal cell",
  onlyA: [...], onlyB: [...], shared: [...] }

// TRUE_FALSE
{ statement: "Mitochondria are found in all living cells.",
  answer: false, explanation?: "..." }

// SCENARIO
{ scenario: "...", question: "...", answer: "...", explanation?: "..." }

// PROCESS
{ title: "Photosynthesis", steps: ["light absorption", "..., ..."] }
```

A seed/import lint rejects: cards whose `payload` is missing required keys,
cards > 120 words, blanks whose `sentence` has fewer placeholders than `blanks`,
and `TRUE_FALSE` without a boolean answer.

## Lesson → deck generation (the "AI-generated" path)

`POST /api/flashcards/generate` converts an existing lesson's **blocks** into a
deck deterministically (idempotent per lesson, shared across students):

| Lesson block | Becomes |
|---|---|
| `concept` | Definition card |
| `check` (MCQ) | Fill-in-the-blank card (answer = correct option) or True/False |
| `mistake` | True/False ("This statement is correct: …") |
| `mnemonic` | Definition card (the phrase it encodes) |
| `example` | Scenario card |
| `tip` | Definition card (exam tip) |

The deck carries `source: LESSON` and links `lessonId` + `topicId` + `subjectId`.
A later phase can call an LLM instead; the route contract is fixed now.

## User experience

### 1. Hub (`/flashcards`)
- **Due hero** — a gradient panel: "N cards due · M new" with a single **Start
  studying** CTA that opens the deck with the most due cards.
- **Deck grid** — subject-tinted cards showing title, card count, due badge,
  progress (reviewed/total), and an enroll/star toggle.
- **Recommendations** — the smart list, priority-sorted, each with a rationale
  and CTA.
- **"Build cards from a lesson"** — a picker of the student's completed lessons.

### 2. Study session (`/flashcards/[deckId]`)
- Queue = due cards first, then new cards (capped to a daily new-card budget to
  avoid overload), interleaved by deck/topic when the deck is composite.
- Card renders its **front**; `Show answer` reveals the back (keyboard: `Space`).
- Fill-in-the-blank / true-or-false cards are **answered first** — the system
  grades the objective attempt, then shows the confidence bar.
- **Confidence bar** — Again / Hard / Good / Easy with keyboard 1–4; a response
  timer starts at card render and stops at the rating press.
- Progress: "card 7 of 25" + a thin progress bar. Session ends with a **summary**
  (reviewed, correct, predicted retention now) and "Done — see you tomorrow".

### 3. Statistics (`/flashcards/stats`)
- Stat tiles (reviews, learned, retention, streak).
- **Retention & activity charts** (recharts): predicted-retention line over the
  last 14 days, and reviews-per-day bars.
- Deck table: due / new / reviewed / retention per deck.
- Difficulty mix and leech list with "relearn" CTAs.

### Accessibility & mobile
- Keyboard-first study (1/2/3/4, Space, ←/→). All colours paired with labels.
- `aria-live` announces card answers and session results.
- Diagrams degrade to hotspot lists under `sm:`; KaTeX renders server-safe
  (display mode, no client scripts beyond the CSS).
- Reduced-motion respected (globals already honour `prefers-reduced-motion`).

---

## Decisions

**SM-2 × FSRS hybrid, not a verbatim FSRS port.** Full FSRS needs per-user
parameter training (17+ weights) and history warm-up; SM-2 alone gives no
retention or difficulty model. The hybrid delivers the product's intelligence
with transparent, deterministic math and no cold-start problem.

**Self-graded card types before confidence.** A single subjective rating is
noisy. Fill-in-the-blank and true/false give the model an objective correctness
signal; the two are both recorded so the stats are honest.

**Decks are content; reviews are state.** Mirrors lessons/`StudentProgress`.
Generated decks are idempotent per lesson, so the whole school shares one
"Cell Structure" deck instead of N duplicates.

**Daily new-card budget.** Unbounded new cards cause review-stack blow-up
(the classic SRS failure). The session caps new cards per day; the cap is
configurable per deck.

**Leech detection instead of punition.** A card is never "failed" — it is
surfaced for relearn and re-linked to its source lesson.

## Phasing

| Phase | Scope |
|---|---|
| 1 | Schema + engine (pure SRS) + authoring lint |
| 2 | Review API + study session + rate bar + card renderer |
| 3 | Hub + generate-from-lesson + enrollment |
| 4 | Stats dashboard + recommendations |
| 5 | Seed decks + dashboard wiring + AI-provider swap |

## Verification

1. `npm run lint`, `tsc`, and `next build` pass.
2. Unit-drive `lib/spaced-repetition.ts`: new → learning → graduate; lapse →
   relearning; intervals strictly increase on success; `R(t)` monotone
   decreasing; difficulty moves the right way per rating.
3. Drive the app: generate a deck from a completed lesson → study → rate →
   next due date appears in the future; log row written.
4. Answer a fill-in-the-blank wrong → objective miss recorded, then rate
   "Again" → interval 1 min, card re-queued.
5. Force a leech (4 lapses) → recommendation "Relearn these" appears.
