# Natural Lesson-Note Format — Uploading Teachers' Notes Unedited

Date: 2026-08-06
Status: Draft
Builds on: `2026-08-05-lesson-note-upload-design.md` (the `:::` fence dialect,
the parser, the upload form), `2026-08-01-lesson-engine-design.md` (block model,
lint)

## Problem

The lesson-note upload spec ended with a prediction:

> Phase 2 starts only after phase 1 has taken real lesson notes from a real
> teacher. The format's weaknesses will show up in that first upload, and
> fixing them before bulk means fixing them once.

This is that upload. `02_Measurement_and_Units.md` is a real SSS1 Physics
lesson note written in ordinary teacher's markdown, and it does not use the
`:::` dialect anywhere. Run through `validateLessonMarkdown()` it produces:

```
meta:   { title: "Physics Lesson Note: Measurement and Units" }
blocks: 11 concept cards, nothing else
ERROR:  A lesson should include at least one knowledge check.
WARN:   Line 91: "Quiz (10 Questions)" is longer than 120 words, split into 2 cards
```

The error disables **Save lesson**, so the file cannot be uploaded at all.

Four distinct failures, in descending severity:

1. **The quiz is inert.** The file ends with ten numbered questions, options
   `a)`–`d)`, the correct one marked `✔`. The parser sees prose. No
   `CheckBlock` is produced, `lintLessonBlocks()` fails on "at least one
   knowledge check", and the save is blocked. The pedagogical heart of the
   note — the part that makes it a lesson rather than a handout — is the part
   that is thrown away.
2. **Worked examples are flat.** Three `**Example N:** … **Solution:** …`
   groups collapse into one 98-word concept card instead of three
   `ExampleBlock`s with steps and answers.
3. **Tables render as pipe soup.** `src/components/lesson/markdown.tsx`
   supports `## headings`, `- bullets` and `**bold**` only. The file's three
   tables reach the student as one run-on paragraph:
   `| Fundamental Quantity | SI Unit | Symbol | |---|---|---| | Length | metre | m | …`
4. **Header boilerplate becomes content.** `# Physics Lesson Note: Measurement
   and Units` is stored as the lesson title, prefix and all, and the
   `**Class:** SSS1 | **Term:** First Term | …` line below it becomes a stray
   untitled 18-word card at the top of the lesson.

Two non-problems, measured rather than assumed:

- **The 120-word cap is fine.** Every section in the file scores under it —
  the largest is 116 words. The one split warning fires only because ten quiz
  questions are crammed into a single card, and disappears once they become
  checks. No cap change, no table-aware word counting.
- **`afterCard` ordering is fine.** It is read by `lintLessonBlocks()` only;
  the player renders blocks in array order. Seven checks resolving to the same
  preceding card is valid and renders as an end-of-lesson quiz, which is what
  the author wrote.

## Goal

A teacher's `.md` file uploads **with no editing**. The natural conventions
above are recognised directly by the parser, so this file — and the rest of the
corpus written in the same house style — needs no conversion step, no
rewriting, and no second dialect for authors to learn.

## Governing principle: additive

The `:::` fence dialect is **unchanged**. Every file that parses today parses
identically after this change, and the existing fence test suite must pass
untouched. Natural conventions are recognised only where they are unambiguous,
and every recogniser fails *back to prose* rather than erroring when its shape
does not match. A file that uses neither convention still becomes concept cards
exactly as it does now.

This matters because the two formats will coexist indefinitely: fences remain
the only way to author `tip`, `mistake`, `mnemonic` and `diagram` blocks, and
nothing here replaces them.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Where conversion lives | In the parser | A separate converter step means every file is touched by hand once, forever. Recognition in the parser is written once. |
| Short-answer quiz questions | `ConceptBlock` with `reveal` | The card player already has tap-to-expand. No new block type, no free-text grading, and the question stays a question. |
| Renderer | Extend `markdown.tsx` by hand | Keeps escaping correct *by construction* — no `dangerouslySetInnerHTML`, no dependency in the student render path. Uploaded notes are untrusted input; this matches the posture `sanitizeSvg()` already sets. |
| Header metadata | Parsed, displayed, not stored | There is no column for Class/Term, and adding one needs the migration that is still blocked. Showing it in the preview is what the admin actually needs. |
| Word cap | Unchanged at 120 | Measured against the real file; not a problem. |
| `lesson-markdown.ts` | Split into a directory | 1083 lines plus ~250 incoming. `index.ts` re-exports, so no import path changes. |
| Ambiguity | Fail back to prose | A half-recognised quiz that silently drops a question is worse than one that stays readable text. |

## The natural format

### Document header

```markdown
# Physics Lesson Note: Measurement and Units
**Class:** SSS1 | **Term:** First Term | **Curriculum Reference:** NERDC …
```

**Title.** An `# ` heading is the document title, as today. New: a leading
`<Words> Lesson Note:` / `Lesson Note:` prefix is stripped, case-insensitively,
giving `Measurement and Units`. Only that exact boilerplate — a title
containing a colon for any other reason keeps it.

**Info line.** The first non-blank line after the H1 is read as metadata when
**every** `|`-separated segment matches `**Key:** value`. It is captured into
`meta.docInfo: Record<string, string>` and emitted as no block.

Both restrictions are load-bearing. Requiring *all* segments to match means an
ordinary sentence containing one bold run stays prose; requiring the line to
directly follow the H1 means a similar line deeper in the body stays prose too.
When either test fails the line is prose, exactly as today.

`docInfo` is rendered in the upload form's preview panel so the admin can
confirm they are uploading the SSS1 First Term note. It is written to no
column.

### Quiz sections

A `## ` heading matching `/^quiz\b/i` — so `## Quiz`, `## Quiz (10 Questions)`,
`## Quiz Time` all qualify — puts the scanner in quiz mode until the next `# `
or `## ` heading, the next `:::` fence, or end of file.

The heading itself emits no block; a quiz is its questions. Prose appearing
*before* the first numbered item (a rubric line, say) becomes one concept card
titled with the heading, so an instruction to students is never dropped.

In quiz mode, a line matching `^\d+\.\s+` opens a question. What follows
decides the block:

**Multiple choice → `CheckBlock`**

```markdown
2. Which of these is a fundamental quantity?
   a) Speed
   b) Area
   c) Mass ✔
   d) Density
```

Options match `^\s*([a-h])\)\s*(.*)$` and are upper-cased to the `A)`–`H)` keys
`CheckBlock.options` expects. The option carrying an answer marker — `✔`
(U+2714), `✓` (U+2713), `✅` (U+2705), or a trailing `*` / `**` — becomes
`answer`; the marker is stripped from the stored option text. `afterCard`
resolves through the existing "previous non-check block" rule, unchanged.

**Short answer → `ConceptBlock` with `reveal`**

```markdown
8. Convert 3,000 g to kilograms. *(Short answer: 3 kg)*
```

becomes `{ type: "concept", text: "Convert 3,000 g to kilograms.", reveal: "3 kg" }`.
The trailing span matches `\*\((?:short answer|answer):\s*(.+)\)\*` and is
removed from the question text. The capture is **greedy**, so an answer
containing its own parentheses or semicolons — question 10's runs to two
clauses — is taken whole to the final `)`, not truncated at the first. The student reads the question, thinks, taps to
reveal — which is what the parenthetical means on paper.

**Errors.** Both are errors rather than warnings, because both mean a question
the author wrote would silently vanish from the lesson:

| Shape | Message |
|---|---|
| Question with options, none marked | `Question 4 has no correct option — mark it with ✔.` |
| Question with two or more marked | `Question 4 marks 3 correct options — a check needs exactly one.` (count is the actual number) |
| Question with one option | `Question 4 has only one option — a check needs at least two.` |
| Question with neither options nor short answer | `Question 4 has no options and no "(Short answer: …)".` |

Every message carries the source line number, so the upload form links to it.

Unlabelled continuation lines append to whichever question or option opened
last — the same rule `readFence()` already uses, so a wrapped question or a
long option works without ceremony.

**Ids.** Checks take `check-1`, `check-2`, … and short-answer concepts take
`short-answer-1`, … from the existing id factory. Type-based rather than
heading-based ids keep lint messages legible ("check-4 has no correct answer"
beats "quiz-10-questions-4"), and the factory already guarantees uniqueness.

### Worked examples

A `## ` heading matching `/^worked examples?\b/i` puts the scanner in example
mode, ending at the next `##`, the next `:::` fence, or EOF.

```markdown
**Example 2:** A trip from Ibadan to Lagos takes 2 hours 30 minutes. Convert this to seconds.
**Solution:**
2 hours = 2 × 3,600 s = 7,200 s
30 minutes = 30 × 60 s = 1,800 s
Total = 7,200 + 1,800 = **9,000 seconds**
```

`**Example N:**` opens one; the rest of that line is `problem`. `**Solution:**`
opens the working. Both tolerate the colon inside or outside the bold run
(`**Example 2:**` and `**Example 2**:`), since that distinction is invisible
once rendered and no author will be consistent about it. Then:

- **The last non-blank line of the working is the answer**, and where that line
  contains a bolded span (`/\*\*(.+?)\*\*\s*$/`), only the bolded text is
  stored as `answer`. All preceding lines become `steps`, in order.
- A working of one line is that answer with no steps.
- `mode` is always `worked` — the natural format has no way to express
  `partial` or `solo`, and inferring one would be invention. Authors who want
  them still have `:::example`.

This rule was checked against all three examples in the source file:
`**5,000 m**`, `**9,000 seconds**` and `**50 kg**` each extract correctly, with
the arithmetic above them becoming steps.

An `**Example N:**` with no `**Solution:**` is an error naming the example's
line. `ExampleBlock` requires an answer, and guessing which line is the answer
without the marker would be guessing.

### Horizontal rules

A line of `---`, `***` or `___` alone is dropped by the parser rather than
buffered. Today it survives into card text as literal `---` and inflates the
word count. This is separate from frontmatter, which is parsed before the body
scan and unaffected.

## Renderer — `src/components/lesson/markdown.tsx`

Four cases, no new dependency, and the existing guarantee holds: every string
goes through `escapeHtml`, nothing reaches `dangerouslySetInnerHTML`.

**Pipe tables.** A block whose second line matches `/^\|[\s:|-]+\|$/` and whose
lines all start and end with `|` renders as `<table>`; cells split on `|` with
the leading and trailing empties dropped, and each cell's text runs through
`renderInline` so `**bold**` inside a cell still works. A block that looks
table-ish but fails the delimiter test **falls back to a paragraph** — the same
fail-safe posture as the SVG sanitiser, so a malformed table degrades to
readable text instead of a broken grid. Column alignment (`:---:`) is not
supported; nothing in the corpus uses it.

**Ordered lists.** A block whose lines all match `/^\s*\d+\.\s+/` renders as
`<ol>`, a sibling of the existing `- ` case. This fixes the six-item Learning
Objectives list.

**Italics.** `renderInline`'s split regex becomes
`/(\*\*[^*]+\*\*|\*[^*]+\*)/g` — bold first so it is never eaten by the italic
alternative. Fixes the italicised book titles under Recommended Resources.

**Rules.** A block that is only `---` / `***` / `___` renders nothing. The
parser already strips these from new uploads; this covers lesson `content`
already in the database.

The block-classification decision is extracted as a pure function
(`classifyBlock(text): "table" | "ol" | "ul" | "heading" | "rule" | "p"`) so it
is testable under `node:test` with the rest of the suite, without a React
renderer.

## Architecture — `src/lib/lesson-markdown/`

`src/lib/lesson-markdown.ts` (1083 lines) becomes a directory. The move is
mechanical; **no import path changes**, because every current importer names
the module, not the file:

- `src/lib/admin-lesson.ts`
- `src/components/admin/lesson-upload-form.tsx`
- `src/app/api/admin/lessons/import/route.ts`
- `scripts/test-lesson-markdown.mts`, `scripts/test-admin-lesson.mts`

Both spellings in use — `@/lib/lesson-markdown` and
`../src/lib/lesson-markdown` — resolve to `index.ts`. The old file must be
**deleted** in the same commit: leaving it beside the directory makes
resolution ambiguous, and TypeScript would prefer the file.

| Module | Holds | Roughly |
|---|---|---|
| `types.ts` | `Issue`, `LessonMeta`, `LessonDifficulty`, `ParsedLesson` | 40 |
| `svg-sanitiser.ts` | `sanitizeSvg`, `stripHostileOnce`, allowlists | 400, moved verbatim |
| `frontmatter.ts` | `parseFrontmatter` | 70 |
| `fences.ts` | `FENCE_TYPES`, `readFence`, `buildFenceBlock`, `parseHotspot` | 320 |
| `natural.ts` | `parseDocHeader`, `parseQuizSection`, `parseWorkedExamples` | 250, new |
| `index.ts` | the line scanner, `emitConcept`, `slugify`, id factory, public API, re-exports | 250 |

`types.ts` exists to keep the dependency graph acyclic: `fences.ts` and
`natural.ts` both need `Issue`, and both are imported by `index.ts`.

`svg-sanitiser.ts` moves **without edits**. Its correctness argument — the
linear-time hostile-element strip, the fail-closed second pass, the
copy-through invariant — is dense, load-bearing, and hard-won across three
commits. Rewriting it while moving it would put the whole thing back up for
review for no benefit.

The scanner in `index.ts` gains one dispatch point: on a `## ` heading, test
the title against the quiz and worked-example patterns before falling through
to the existing concept-section path. Each recogniser consumes its own lines
and returns blocks plus issues, so the scanner stays a scanner.

## Tests — `scripts/test-lesson-markdown.mts`

The real file is committed as `scripts/fixtures/measurement-and-units.md` — a
new directory; there is no existing fixture convention. It is the anchor case:
if it regresses, the feature has failed at the only thing it was built for.

**Fixture:** parses to **zero errors**, and to an asserted block sequence —
concept cards for §§1–5, three `ExampleBlock`s, seven `CheckBlock`s, three
short-answer concepts with `reveal` set, and no stray header card. Title is
`Measurement and Units`. `docInfo.Class === "SSS1"`.

**Quiz:** each answer marker variant (`✔`, `✓`, `✅`, trailing `*`); marker
stripped from option text; options upper-cased; `afterCard` resolves to the
preceding concept; short answer → `reveal`; and one case per error row in the
table above, each asserting the line number.

**Worked examples:** three examples from the fixture with steps and answers
split as specified; bolded-span extraction; single-line working → answer with
no steps; missing `**Solution:**` errors at the right line.

**Header:** prefix stripped; `docInfo` captured; no block emitted for the info
line; a bold-containing sentence that is *not* an info line stays prose; an
info-shaped line deeper in the body stays prose.

**Renderer:** `classifyBlock` over tables, malformed tables (→ `p`), ordered
lists, unordered lists, rules and plain paragraphs.

**Regression:** the entire existing suite — all seven fence types, frontmatter,
auto-split, `afterCard`, malformed input, and every SVG XSS payload — passes
unchanged. This is the guardrail on "additive".

## Out of scope

- **Authored `## Learning Objectives` feeding the orient screen.** There is no
  `objectives` column on `Lesson`, and `ObjectivesPanel` derives its own from
  the topic title. The section stays a concept card, which renders correctly
  once ordered lists land. Wiring it up needs a migration.
- **Storing Class, Term or Curriculum Reference.** Same blocked migration.
- **Raising or table-adjusting the 120-word cap.** Measured; unnecessary.
- **Column alignment in tables, nested lists, block quotes, inline code,
  images, links.** None appear in the corpus. Add them when a real file needs
  them, on the evidence of that file.
- **Bulk upload.** Still phase 2 of the previous spec. This change is what
  makes it worth building — but it is a separate one.
- **A `partial` / `solo` example mode in the natural format.** No natural
  syntax expresses it; `:::example` still does.

## Risks

| Risk | Mitigation |
|---|---|
| A recogniser fires on prose that merely looks like a quiz or example | Every recogniser is gated on an explicit `##` heading, and falls back to prose when its inner shape does not match. Tests cover the near-miss cases. |
| The split silently changes parser behaviour | `svg-sanitiser.ts` moves verbatim; the full existing suite must pass with no edits. Any test change during the split is a red flag, not a fixup. |
| Both `lesson-markdown.ts` and `lesson-markdown/` exist after the split | The old file is deleted in the same commit; TypeScript resolution prefers the file, so leaving it would silently keep the old code live. |
| A question's answer marker is lost to encoding (`✔` mangled on save) | Four markers accepted, including plain trailing `*`; an unmarked question is a hard error naming the line, never a silent drop. |
| Table rendering breaks on a malformed table | Falls back to paragraph — degrades to readable text, never a broken grid. |
| The house style differs across teachers | This spec is written from one real file. The recognisers are gated and additive, so a second style adds a recogniser rather than reopening the format. |
