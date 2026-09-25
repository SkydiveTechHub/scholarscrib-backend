# Lesson Note Upload — Markdown Authoring for Classroom Content

Date: 2026-08-05
Status: Draft
Builds on: `2026-08-01-lesson-engine-design.md` (block model, lint),
`2026-08-05-classroom-design.md` (the notes surface these blocks feed)

## Problem

The Classroom renders lessons well. Nobody can author them.

1. **Every lesson is machine-generated.** `src/lib/lessons.ts` builds all 150
   lessons from template strings interpolating the topic title —
   `buildLessonBlocks(title, subjectName)`. No lesson in the database was
   written by a teacher.
2. **The content gap is already documented.** The Classroom design records that
   0 of 150 lessons have authored `knowledgeChecks`, and concludes "the remedy
   is authoring content, not more code." This spec is that remedy's front door.
3. **There is no authoring path at all.** Questions have an importer
   (`scripts/import-questions.ts`, `/admin/questions/import`). Lessons have
   nothing — `src/app/admin/lessons/` is an empty directory.
4. **Editing prose in a JSON column is not authoring.** `Lesson.blocks` is the
   right storage shape, but no teacher will hand-write a `LessonBlock[]` array.

## Goal

An admin writes a topic's lesson note as a plain `.md` file, uploads it against
that topic, sees exactly how students will read it, and saves. The stored
result is structured `blocks` — indistinguishable from hand-authored JSON, and
fully usable by both the card player and the notes view.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Storage target | `Lesson.blocks` | The typed shape both surfaces consume. `content` is only a defensive fallback rendered by a deliberately minimal markdown subset. |
| Block syntax | `##` headings + `:::type` fences with `Label:` lines | Readable as ordinary markdown; parseable by a line scanner; no YAML indentation traps and no new dependency. |
| Frontmatter | Flat `key: value`, all keys optional | A notes-only file with no frontmatter must work. |
| Over-long sections | Auto-split at paragraph boundaries, warn | The 120-word cap is a card-player constraint; authors should write prose, not count words. |
| Block coverage | All seven types | Including `diagram`, with SVG inline. |
| Diagram transport | SVG inline in the fence | Keeps a lesson to one self-contained file, which bulk upload depends on. |
| Existing lesson | Overwrite behind an explicit confirm, audited | All 150 current lessons are placeholders; replacing them is the point. |
| Bulk targeting (phase 2) | `subject:` + `topic:` in frontmatter | Self-describing — a file survives being renamed or moved. |
| Schema | No migration | `prisma migrate` currently hangs (`DIRECT_URL` points at the pgbouncer pooler, not a session-mode connection). Everything below writes existing columns. |

`keyPoints` is deliberately **not** part of the format. Nothing in the notes
view or card player reads `Lesson.keyPoints`; adding syntax for a field no
surface renders would be authoring work with no student-visible result.

## The authoring format

### Frontmatter

Optional. Opens and closes with `---`. Flat `key: value` pairs only — no
nesting, no lists, no anchors. Not YAML, and not parsed by a YAML library.

```markdown
---
title: Photosynthesis
summary: How plants convert light energy into glucose.
subject: physics
topic: newtons-laws
estimatedMinutes: 25
difficulty: INTERMEDIATE
passMarkPercent: 60
practiceCount: 7
---
```

| Key | Writes to | Omitted |
|---|---|---|
| `title` | `Lesson.title` | Lesson keeps its current title |
| `summary` | `Lesson.summary` | Unchanged |
| `subject`, `topic` | Neither — routing only | Target comes from the form selectors |
| `estimatedMinutes` | `Lesson.estimatedMinutes` | Unchanged |
| `difficulty` | `Lesson.difficulty` | Unchanged |
| `passMarkPercent` | `Lesson.passMarkPercent` | Unchanged |
| `practiceCount` | `Lesson.practiceCount` | Unchanged |

Unknown keys are a warning, not an error — a typo should not cost an upload,
but it must be visible.

### Concept cards

A `## ` heading opens a concept card. Everything until the next `##` or fence
is its text.

```markdown
## What is photosynthesis?

Green plants use light energy to convert carbon dioxide and water into
glucose and oxygen. The reaction happens in the chloroplasts.
```

`### Reveal` inside a concept section maps to the block's optional `reveal`
field (the card player's tap-to-expand). A `# ` single-hash heading is treated
as the document title and used as `title` only when frontmatter omits it.

### Fenced blocks

```markdown
:::example
Problem: A 4 kg mass is pushed with a force of 20 N. Find the acceleration.
Step: Write F = ma.
Step: Substitute F = 20, m = 4.
Answer: 5 m/s²
Mode: worked
:::

:::check
Q: What is the SI unit of force?
A) Joule
B) Newton
C) Watt
Correct: B
Why: Force is measured in newtons, named after Isaac Newton.
:::

:::tip
Exam: WAEC
Read the units in the question before choosing a formula.
:::

:::mistake
Wrong: Adding forces that act in opposite directions.
Right: Subtract opposing forces, then apply F = ma.
:::

:::mnemonic
Phrase: My Very Easy Method Just Speeds Up Naming
Encoded: Mercury
Encoded: Venus
Encoded: Earth
:::

:::diagram
Title: The human eye
Caption: The path light takes to the retina.
<svg viewBox="0 0 200 120">…</svg>
Hotspot: Cornea @ 20,50 — Bends incoming light.
Hotspot: Retina @ 160,60 — Where the image forms.
:::
```

Rules the parser enforces:

- Labels are case-insensitive and match only at the start of a line. A line
  with no recognised label joins the previous field's text, so prose wraps
  naturally.
- `Step:`, `Encoded:` and `Hotspot:` repeat, in order. Every other label
  appears once; a repeat is an error naming the line number.
- `Mode:` accepts `worked | partial | solo`, defaulting to `worked`.
- `Exam:` accepts `WAEC | JAMB | NECO`; anything else is a warning and the tag
  is dropped rather than stored invalid.
- Options in a `check` are `A)` through `H)`, needing at least two. `Correct:`
  must name one of them — if it doesn't, that is an error, matching
  `lintLessonBlocks()`.
- A fence left unclosed at end of file is an error reporting where it opened.

### Block ids and `afterCard`

Ids are derived, never authored: the enclosing heading's slug plus an ordinal
(`photosynthesis-1`, `photosynthesis-2`), falling back to the block type
(`example-3`) outside any heading. Collisions get a numeric suffix, so the
duplicate-id lint cannot fire from generated ids.

A `:::check` attaches to the **immediately preceding non-check block**, which
is what an author means by placing it there and satisfies the lint's
`afterCard` requirement with no syntax. An explicit `After: <id>` line
overrides it, for the rare check that belongs to an earlier card.

**Known consequence:** ids shift when a heading is renamed or a card is
inserted. A student mid-lesson has stale ids in
`StudentProgress.checkpointData.visited`, so a few already-read cards look
unvisited. `parseCheckpointState()` already discards unknown ids, so this
degrades cosmetically rather than breaking. Not worth an id-stability mechanism
while the entire corpus is placeholder content.

### The 120-word cap

`lintLessonBlocks()` caps every non-check block at `MAX_CARD_WORDS = 120`.
Concept sections over the cap are split at paragraph boundaries into
sequential cards, the heading staying on the first. Each split is a warning
naming the heading and the resulting card count, so an author who dislikes a
break can rewrite. The notes view is unaffected — it renders consecutive
concept blocks as continuous prose.

A single paragraph over 120 words cannot be split cleanly. It becomes one
over-length card and the lint error stands: the author must break the
paragraph. Splitting mid-sentence would be worse than refusing.

## Architecture

### `src/lib/lesson-markdown.ts` — pure parser

No Prisma, no React, no `next/*` imports. One entry point:

```ts
export function parseLessonMarkdown(source: string): ParsedLesson;

export type ParsedLesson = {
  meta: LessonMeta;        // frontmatter, all fields optional
  blocks: LessonBlock[];   // exactly the lesson-engine shapes
  warnings: Issue[];       // auto-splits, unknown keys, stripped SVG
  errors: Issue[];         // malformed structure — blocks the save
};

export type Issue = { line?: number; message: string };
```

Callers run the existing `lintLessonBlocks(parsed.blocks)` and merge its
`LintIssue[]` into errors. The parser owns *syntax*; the lint owns *pedagogy*.
Keeping them separate means the lint keeps governing hand-authored blocks too.

### `sanitizeSvg()` — same module

`InteractiveDiagram` renders `block.svg` through `dangerouslySetInnerHTML`
(`src/components/lesson/interactive-diagram.tsx:31`). Uploaded SVG is therefore
executable markup on every student's page, and sanitising it at import time is
a security requirement, not hygiene.

An **allowlist**, not a blocklist:

- Elements: `svg, g, path, circle, ellipse, rect, line, polyline, polygon,
  text, tspan, defs, marker, linearGradient, radialGradient, stop, title, desc`
- Attributes: geometry (`d, x, y, cx, cy, r, rx, ry, width, height, points,
  transform, viewBox`), presentation (`fill, stroke, stroke-width,
  stroke-linecap, stroke-dasharray, opacity, font-size, font-family,
  text-anchor`), plus `id`, `class`, `aria-*`
- Removed unconditionally: `<script>`, `<foreignObject>`, `<use>`, `<image>`,
  `<style>`, every `on*` attribute, and any `href`/`xlink:href` not of the form
  `#fragment`

Every removal is a warning naming the element or attribute, so an author whose
diagram loses a feature learns why instead of wondering.

### Admin UI — `src/app/admin/lessons/`

`page.tsx` lists subjects and their topics with an authored/placeholder marker,
so the corpus's real state is visible. **A lesson is authored when
`Lesson.createdBy` is neither null nor `"system"`** — the upload sets it to the
acting admin's user id, and `seedLessons()` writes the literal `"system"` on
all 150 generated lessons (`src/lib/lessons.ts:205`). That reuses an existing
column rather than inventing a heuristic over `content`, whose text the
generator also populates.

`upload/page.tsx` (`"use client"`) is the working surface:

1. Cascading **Subject → Class/Term → Topic** selectors. When the file's
   frontmatter names a `subject` and `topic` that resolve, they pre-select and
   the admin only confirms.
2. File picker, `accept=".md,.markdown"`, read with `await file.text()` —
   the questions importer's pattern, not multipart.
3. **Preview rendered by the real `LessonNotes` component**, so what the admin
   approves is literally what students get. Errors and warnings list beside it,
   each linking to its source line.
4. **Current state of the target**, fetched alongside: existing title, block
   count, and whether it is still a placeholder.
5. Save is disabled while any error stands. Warnings never block.
6. Saving requires an explicit confirm naming the topic and what is being
   replaced — `ConfirmDialog` already exists in `src/components/admin/`.

Add `{ name: "Lessons", href: "/admin/lessons", icon: LuBookOpen }` to
`ADMIN_NAV`. The file carries an explicit warning against link-less entries;
this one has a page behind it.

### API — `POST /api/admin/lessons/import`

Follows the questions importer end to end: `requireAdmin()` → zod
`safeParse` → work → `recordAudit()` → `revalidateTag(CATALOGUE_TAG, "max")`
→ JSON. No server actions; the codebase has none.

Request body is the **raw markdown string**, not the client's parsed blocks:

```ts
{ topicId: string, markdown: string, confirm: true }
```

The server re-parses, re-sanitises and re-lints from source. The client parse
exists only to render a preview — trusting it would let a crafted request post
arbitrary blocks and unsanitised SVG straight into student pages. **The
server-side re-parse is the security boundary.**

On success it writes, in one `db.lesson.update`:

- `blocks` — the parsed array, replacing what was there
- `content` — the raw markdown
- `createdBy` — the acting admin's user id, which is what marks the lesson
  authored rather than generated
- `title`, `summary`, `estimatedMinutes`, `difficulty`, `passMarkPercent`,
  `practiceCount` — only the keys frontmatter supplied

Storing the raw markdown in `content` costs nothing (already `@db.Text`, and
the notes view prefers `blocks`) and buys round-tripping: an admin can fetch
the source back, edit it, and re-upload. That is the cheapest possible
substitute for versioning, which would need the blocked migration.

`GET /api/admin/lessons/[topicId]` returns the current lesson's title, block
count, placeholder flag and stored markdown — feeding both the pre-save
comparison and re-download.

Audit action: `lesson.import`, entity `Lesson`, summary naming the subject,
topic and block count. `AdminAudit` currently records only `question.*`
actions; this is the second family.

A topic with no `Subtopic` or `Lesson` row is not an error — the route creates
the "Core Concepts" subtopic and lesson, mirroring `seedLessons()`. New topics
must be authorable without a seed run first.

### Tests — `scripts/test-lesson-markdown.mts`

`node:test` + `tsx`, matching `test-admin-import.mts`. Against the pure parser,
so no database:

- Each of the seven block types round-trips into the right shape
- Frontmatter: full, absent, partial, unknown key warns
- Auto-split boundaries; a single over-long paragraph still errors
- Implicit `afterCard` resolution and explicit `After:` override
- Malformed input: unclosed fence, `check` with no correct option, repeated
  single-value label — each errors with a line number
- **SVG XSS payloads come out inert**: `<script>`, `onload=`, `onclick=`,
  `<foreignObject>`, `href="javascript:"`, `<use href="external">`
- A parsed lesson passes `lintLessonBlocks()` with no issues

## Phase 2 — bulk upload

Same parser, unchanged. `/admin/lessons/import` takes many `.md` files,
routing each by its frontmatter `subject` + `topic` slug pair. Files with no
frontmatter target, or a pair that resolves to no topic, are reported and
skipped.

Each file succeeds or fails **whole** — a lesson is never written with half its
blocks. The batch does not roll back on one bad file; the result is a per-file
table of imported / skipped / errored, like the questions importer's.

Phase 2 starts only after phase 1 has taken real lesson notes from a real
teacher. The format's weaknesses will show up in that first upload, and fixing
them before bulk means fixing them once.

## Out of scope

- **Rich-text editing in the browser.** The file is the source of truth.
- **Versioning and rollback.** Needs a migration, currently blocked. Re-upload
  from the stored markdown is the recovery path.
- **Media beyond inline SVG.** Images and PDFs already have `LessonResource`
  and the Cloudinary path used by avatars.
- **Authoring `Lesson.keyPoints`, `mnemonics`, `examTips` as lesson-level
  columns.** The block-level `:::mnemonic` and `:::tip` fences already reach
  both surfaces; the columns duplicate them and nothing renders `keyPoints`.
- **Generating lesson notes with an LLM.** A separate question from ingesting
  authored ones.

## Risks

| Risk | Mitigation |
|---|---|
| Uploaded SVG executes in student pages | Allowlist sanitiser, server-side, with XSS payloads in the test suite |
| Client-parsed blocks trusted by the API | Server re-parses from raw markdown; client parse is preview-only |
| Real notes are long prose, producing many thin cards | Auto-split warns visibly; revisit the cap after the first real upload rather than designing for a guess |
| Re-upload disturbs in-progress students | Stale ids are discarded by `parseCheckpointState()`; cosmetic only |
| A stray upload wipes good content | Explicit confirm showing what is being replaced, plus `AdminAudit` and the raw markdown retained in `content` |
