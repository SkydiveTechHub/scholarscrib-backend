# Classroom — Subject & Topic Experience Design

Date: 2026-08-05
Status: Approved
Supersedes: the navigation and topic-page portions of
`2026-08-01-lesson-engine-design.md` (the engine itself is unchanged)

## Problem

The Subjects section works but reads as a directory rather than a place to
study.

1. **The name undersells it.** "Subjects" describes a taxonomy. What the section
   actually holds is teaching content — notes, examples, checks, practice.
2. **There is a page of one.** Every topic has exactly one lesson (verified:
   150 topics, 150 subtopics, 150 lessons, no exceptions), yet the topic page
   renders a *list* of lessons that the student must click through to reach the
   content. One click, zero information.
3. **The syllabus is a long scroll.** A subject stacks nine groups (3 classes ×
   3 terms). An SS3 student opening Physics scrolls past two years of SS1 and
   SS2 material to reach anything relevant.
4. **Practice is a separate errand.** Nothing on a subject or topic page leads
   into the exam practice that the rest of the app is built around.
5. **Resources are invisible.** 43 subject resources exist; no page inside the
   section surfaces them.

## Goal

A student opens **Classroom**, picks a subject, immediately sees the part of the
syllabus they are actually studying, opens a topic, and reads it — then tests,
drills, or revises it without leaving.

## Reconciling with the Lesson Engine

`2026-08-01-lesson-engine-design.md` deliberately replaced "a page of notes …
passive: read text, scroll, leave" with a paced card player. This design makes a
notes view the **default** for a topic, which reads like a reversal. It is not,
and the distinction matters:

| Need | Surface | Why |
|---|---|---|
| Learning a topic for the first time | Card player (`/study`) | One idea per card, retrieval, mastery gate — the engine's principles, unchanged |
| Revising a topic already met | Notes | A student hunting one formula before an exam cannot click through fourteen cards to find it |

The engine's principles govern **first learning**. They were never a good fit
for **revision**, which is scanning and re-reading by nature. Both surfaces
render the same authored `blocks`; neither duplicates content. The player keeps
its progress tracking and mastery gate, and remains the only route to
"completed".

## Scope

**In:** rename and re-route; subject-page class/term browsing; topic page as
notes; topic action bar; resources with fallback; practice deep-link; redirects.

**Out:** authoring `knowledgeChecks` content; changing the card player itself;
changing the flashcard or SRS engines; any schema migration.

## Routes

```
/classroom                                subject list
/classroom/[subject]                      topic browser, grouped by class/term
/classroom/[subject]/[topic]              topic page — notes-first
/classroom/[subject]/[topic]/study        paced card player
/classroom/[subject]/[topic]/quiz         quick self-check, untimed
/classroom/[subject]/[topic]/practice     timed past-question practice
```

`lessonId` leaves the URL space entirely. Because a topic has exactly one
lesson, `/study` and `/practice` resolve it server-side. The
`/subjects/[s]/[t]/lessons/[lessonId]/…` branch is removed.

### Redirects

`/subjects/*` → `/classroom/*`, permanent (308), preserving path and query.
Implemented in `next.config.ts` `redirects()` so it costs no request-time work.

Existing internal links are updated at the source; the redirect exists for
bookmarks, browser history and anything already shared.

## Subject page — topic browser

**Class control.** A segmented `SS1 | SS2 | SS3` control, sticky on scroll,
identical on desktop and mobile. It is the "mobile filter" — one component, two
paddings, nothing bespoke to maintain.

**Default selection.** The student's own `classLevel` from the session. This is
the single highest-value decision on this page: an SS2 student reaches SS2
Physics in zero taps. Fallback order when `classLevel` is absent or not a senior
class: first class with any topics, else `SS1`.

**Term sections.** Within the selected class, three sections (1st/2nd/3rd term),
each headed with a completion count (`4/7 done`) derived from
`StudentProgress`. Terms with no topics render a muted empty row rather than
vanishing, so the syllabus shape stays legible.

**Graph toggle.** The existing `CurriculumViewToggle` / `GraphView` is retained
and sits beside the class control. The map view is genuinely useful for
prerequisites and is already built.

**Practice CTA.** Deep-links to the scoped mock picker:
`/practice/mock-exam?subjectId=…&fromClass=SS2&fromTerm=FIRST&toClass=SS2&toTerm=THIRD`

This defaults to the whole selected class year, which the picker already
describes as "all of SS2".

**The link deliberately carries no `examType`.** The picker lists subjects *per
board*, so a subject cannot be resolved before a board is chosen. The picker
therefore still opens at the board step, holding the incoming subject and scope
in reserve and applying them as soon as a board is picked. If the chosen board
has no questions for that subject, the pre-fill is dropped and the picker
behaves as if opened cold — the student is never left staring at a subject that
board cannot offer.

## Topic page — notes-first

```
Classroom / Physics / SS2 · 2nd term
Motion in a Straight Line                          mastery ●●●○○
──────────────────────────────────────────────────────────────
[Study step by step] [Quick quiz] [Flashcards] [Practice]   sticky
──────────────────────────────────────────────────────────────
  … lesson blocks rendered continuously …
──────────────────────────────────────────────────────────────
More resources                        (omitted when nothing to show)
──────────────────────────────────────────────────────────────
← Previous topic                                  Next topic →
```

The mastery indicator in the header is the existing per-topic mastery from the
learning-path engine (`computeTopicState`), not a new measure. It is read-only
here; nothing on the notes page changes it.

### Block rendering

The same authored `blocks` the player consumes, presented continuously:

| Block type | Notes rendering |
|---|---|
| `concept` | Heading + prose |
| `example` | Worked-example callout (reuses `worked-example.tsx`) |
| `diagram` | Figure (reuses `interactive-diagram.tsx`) |
| `tip`, `mistake`, `mnemonic` | Tinted callout, one tone each |
| `check` | **Omitted** — a knowledge check belongs to the player, where an answer is graded and recorded |

Lessons with no `blocks` fall back to the `content` markdown field. All 150
current lessons have `blocks`, so this path is defensive rather than expected.

### Action bar

Four actions, sticky once the header scrolls away:

- **Study step by step** → `/study`, the card player, unchanged
- **Quick quiz** → `/quiz`, short and untimed, drawn from the question bank
- **Flashcards** → if a deck already exists for this topic's lesson, opens it;
  otherwise generates one via the existing lesson-scoped generation, then opens
  it. The button label reflects which will happen ("Flashcards" vs "Build
  flashcards"), so a click never silently creates something.
- **Practice** → `/practice`, timed past questions, the existing practice exit

**Known thinness:** no lesson has authored `knowledgeChecks` (verified: 0 of
150). Quick quiz and Practice therefore differ by framing — short/untimed versus
long/timed — rather than by source. This is acceptable for launch; the remedy is
authoring content, not more code.

### Resources

Topic resources come from `LessonResource` (already 1:1 with a topic via the
single lesson; no schema change). When a topic has none — true for all 150
today, as `LessonResource` holds 0 rows — the section falls back to the
subject's resources under an explicit heading, "More Physics resources", so the
UI never implies a specificity it does not have. When both are empty the section
is omitted entirely.

### Topic navigation

Previous/next within the same term, by `orderIndex`. At a term boundary the link
carries to the adjacent term, and at a class boundary it stops.

## Components

**New**

| Component | Responsibility |
|---|---|
| `lesson-notes.tsx` | Renders `LessonBlock[]` as continuous notes |
| `class-term-browser.tsx` | Class control + term sections + progress counts |
| `topic-action-bar.tsx` | The four actions, sticky behaviour |
| `topic-resources.tsx` | Lesson resources with subject fallback |

**Reused unchanged:** `markdown`, `worked-example`, `interactive-diagram`,
`micro-card`, `lesson-player`, `practice-exit`, `view-toggle`, `graph-view`,
and the scoped mock-exam flow.

**Modified:** `mock-exam-picker.tsx` gains query-param hydration so the practice
CTA can pre-fill it. It currently has no deep-link support; this is a real change
to working code, not a free win.

## Data

No schema migration. This is deliberate — `prisma migrate` currently hangs
because `DIRECT_URL` points at the pgbouncer pooler rather than a session-mode
connection, so a design needing a migration would be blocked on unrelated
infrastructure.

Subject page loads subject + topics + curriculum levels + the student's progress
in one query. Topic page loads topic + its lesson + progress + lesson resources
in one, with the subject-resource fallback fetched only when the first is empty.

## Testing

Pure logic, in the existing `node:test` + `tsx` harness:

- **Block → notes**: `check` blocks excluded; every other type mapped; empty
  `blocks` falls back to `content`; block order preserved.
- **Resource fallback**: lesson resources preferred; subject resources used only
  when lesson resources are empty; section omitted when both are empty.
- **Class default**: session `classLevel` honoured; absent, junior, or unknown
  values fall back to the first class with topics, else `SS1`.
- **Topic navigation**: previous/next within a term; crossing a term boundary;
  stopping at a class boundary.

Rendering and routing are verified by the existing typecheck, lint and build.

## Risks

| Risk | Mitigation |
|---|---|
| Redirects miss a link shape and 404 a bookmark | Wildcard `/subjects/:path*` rule; verified against every route in the tree |
| Deep-link params make the mock picker's state harder to reason about | Params hydrate initial state only; the picker's own flow is otherwise untouched |
| Notes view tempts students to skip the player, weakening mastery tracking | Player is the only route to "completed"; the notes header shows mastery, making the gap visible |
| Long lessons make a single scrolling page unwieldy | Blocks carry headings; a future in-page contents rail is possible without changing the data |

## Non-goals

Search across topics; per-topic resource authoring UI; changing how mastery is
calculated; offline access; any change to the flashcard or SRS engines.
