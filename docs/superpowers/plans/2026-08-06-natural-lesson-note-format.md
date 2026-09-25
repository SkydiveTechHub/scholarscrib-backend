# Natural Lesson-Note Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/fixtures/measurement-and-units.md` — a real teacher's lesson note using no `:::` fences — upload with zero errors and zero hand-editing.

**Architecture:** `src/lib/lesson-markdown.ts` (1083 lines) becomes a directory of focused modules, then gains three *recognisers* for natural markdown conventions: a document header, a `## Quiz` section of numbered questions, and a `## Worked Examples` section. Each is gated on an explicit `##` heading and falls back to prose when its inner shape does not match, so the existing fence dialect is untouched. Separately, the student-facing renderer learns tables, ordered lists and italics.

**Tech Stack:** TypeScript, Next.js (App Router), React 19, `node:test` + `tsx` for tests, no new dependencies.

## Global Constraints

- **No new dependencies.** The renderer stays hand-rolled; uploaded notes are untrusted input and escaping must remain correct by construction.
- **No `dangerouslySetInnerHTML`** anywhere in this work. The only existing use is `InteractiveDiagram`, fed by `sanitizeSvg()`, and it is not touched.
- **No schema migration.** `prisma migrate` currently hangs (`DIRECT_URL` points at the pgbouncer pooler). Every change writes existing columns or nothing.
- **Additive only.** The full existing test suite in `scripts/test-lesson-markdown.mts` (817 lines) must pass **unedited** at every commit. A test that needs changing is a signal you have altered existing behaviour — stop and reconsider, do not edit the test.
- **Import paths must not change.** Consumers import `@/lib/lesson-markdown` or `../src/lib/lesson-markdown`; both must keep resolving.
- **Run the suite with:** `npm test` (runs all 20 test files) or, for this feature alone, `node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts`
- **Every parse error and warning carries a source line number** where one is knowable. The upload form renders `Line N: message`.
- **Answer markers accepted:** `✔` (U+2714), `✓` (U+2713), `✅` (U+2705), `☑` (U+2611), or a trailing `*` / `**`.
- **`MAX_CARD_WORDS` stays 120.** Do not change it.

## Deviations from the spec

Two, both found while writing this plan. Both are refinements, not reversals.

1. **`ids.ts` is a seventh module** the spec's table does not list. `parseHotspot` (in `fences.ts`) and the scanner (in `index.ts`) both need `slugify`, so leaving it in `index.ts` creates a cycle. This serves the spec's own stated reason for `types.ts` — "keep the dependency graph acyclic".

2. **`segmentMarkdown()` replaces the spec's `classifyBlock()`.** `classifyBlock` classifies a whole blank-line-delimited block as one kind, and that is too coarse for the exact case it was meant to fix. The Learning Objectives section is:

   ```
   By the end of this lesson, students should be able to:
   1. Define measurement and physical quantities
   2. Distinguish between fundamental and derived quantities
   ```

   One block, no blank lines, whose first line is not a list item — so `classifyBlock` returns `"p"` and the numbered list still renders as a run-on paragraph. `segmentMarkdown()` splits a block into runs of homogeneous lines instead, yielding a paragraph followed by an `<ol>`. Same testability (pure function, `node:test`), correct on the real file.

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `src/lib/lesson-markdown/types.ts` | Shared types: `Issue`, `LessonMeta`, `LessonDifficulty`, `ParsedLesson`. Imports nothing local — the graph's root. |
| `src/lib/lesson-markdown/ids.ts` | `slugify`, `makeIdFactory`. |
| `src/lib/lesson-markdown/svg-sanitiser.ts` | `sanitizeSvg` and its allowlists. **Moved verbatim.** |
| `src/lib/lesson-markdown/frontmatter.ts` | `parseFrontmatter`. |
| `src/lib/lesson-markdown/fences.ts` | `:::` dialect: `readFence`, `buildFenceBlock`, `parseHotspot`. |
| `src/lib/lesson-markdown/natural.ts` | **New.** `parseDocHeader`, `parseQuizSection`, `parseWorkedExamples`, `stripAnswerMarker`. |
| `src/lib/lesson-markdown/index.ts` | The line scanner, `emitConcept`, the public API, and re-exports. |
| `src/lib/markdown-segments.ts` | `segmentMarkdown()` — pure, testable, no React. |
| `scripts/test-markdown-segments.mts` | Tests for the above. |

**Modified**

| File | Change |
|---|---|
| `src/components/lesson/markdown.tsx` | Renders `segmentMarkdown()` output; gains table, `<ol>` and italic cases. |
| `src/components/admin/lesson-upload-form.tsx` | Shows `meta.docInfo` in the preview column. |
| `scripts/test-lesson-markdown.mts` | **Appended to only.** Never edit an existing test. |
| `package.json` | Add `scripts/test-markdown-segments.mts` to the `test` script. |

**Deleted**

| File | Why |
|---|---|
| `src/lib/lesson-markdown.ts` | Replaced by the directory. Must go in the same commit — TypeScript resolves `lesson-markdown.ts` in preference to `lesson-markdown/index.ts`, so leaving it keeps the old code silently live. |

---

### Task 1: Split `lesson-markdown.ts` into a directory

Pure refactor. Zero behaviour change. The existing suite is the test.

**Files:**
- Create: `src/lib/lesson-markdown/{types,ids,svg-sanitiser,frontmatter,fences,natural,index}.ts`
- Delete: `src/lib/lesson-markdown.ts`
- Test: `scripts/test-lesson-markdown.mts` (existing, unedited)

**Interfaces:**
- Consumes: nothing.
- Produces: the module boundaries every later task builds on. Public API from `index.ts` is unchanged: `parseLessonMarkdown(source: string): ParsedLesson`, `validateLessonMarkdown(source: string): ParsedLesson`, `sanitizeSvg(svg: string): { svg: string; warnings: Issue[] }`, `slugify(text: string): string`, and the types `Issue`, `LessonDifficulty`, `LessonMeta`, `ParsedLesson`.

- [ ] **Step 1: Capture the current behaviour as a baseline**

Before moving anything, record that the suite passes:

```bash
cd /c/Users/user/Desktop/scholarscrib
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | tail -5
```

Expected: all tests pass. Write the pass count down — it must be identical at Step 8.

- [ ] **Step 2: Create `types.ts`**

Move lines 25–58 of the current file (`Issue`, `LessonDifficulty`, `DIFFICULTIES`, `LessonMeta`, `TEXT_KEYS`, `NUMBER_KEYS`) plus `ParsedLesson`. `natural.ts` will add `docInfo` to `LessonMeta` in Task 2; leave it out for now.

```ts
import type { LessonBlock } from "@/lib/lesson-engine";

export type Issue = { line?: number; message: string };

export type LessonDifficulty = "BASIC" | "INTERMEDIATE" | "ADVANCED";

export const DIFFICULTIES: readonly LessonDifficulty[] = [
  "BASIC",
  "INTERMEDIATE",
  "ADVANCED",
];

export type LessonMeta = {
  title?: string;
  summary?: string;
  subject?: string;
  topic?: string;
  estimatedMinutes?: number;
  difficulty?: LessonDifficulty;
  passMarkPercent?: number;
  practiceCount?: number;
};

export type ParsedLesson = {
  meta: LessonMeta;
  blocks: LessonBlock[];
  warnings: Issue[];
  errors: Issue[];
};

export const TEXT_KEYS = ["title", "summary", "subject", "topic"] as const;
export const NUMBER_KEYS = [
  "estimatedMinutes",
  "passMarkPercent",
  "practiceCount",
] as const;
```

- [ ] **Step 3: Create `ids.ts`**

Move `slugify` (lines 60–68) and `makeIdFactory` (70–83) verbatim. Export both.

```ts
export function slugify(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "block"
  );
}

/** Mints ids as `<slug>-<n>`, bumping n until the id is unused. */
export function makeIdFactory() {
  const used = new Set<string>();
  return function nextId(slug: string): string {
    let n = 1;
    let id = `${slug}-${n}`;
    while (used.has(id)) {
      n += 1;
      id = `${slug}-${n}`;
    }
    used.add(id);
    return id;
  };
}
```

- [ ] **Step 4: Create `svg-sanitiser.ts` — move lines 302–711 VERBATIM**

Move `SVG_ELEMENTS`, `SVG_VOID_HOSTILE`, `SVG_ATTRS`, `HostileToken`, `isWordChar`, `wordRunEnd`, `stripHostileOnce`, `sanitizeSvg` — **including every comment**. Those comments carry the linear-time and fail-closed correctness arguments; they are the most valuable text in the file.

Add at the top:

```ts
import type { Issue } from "./types";
```

Change nothing else. Do not reformat. Do not "improve" anything. If your diff shows any change inside a function body, you have made a mistake.

- [ ] **Step 5: Create `frontmatter.ts`, `fences.ts`, `natural.ts`**

`frontmatter.ts` — move lines 85–153 (`Frontmatter` type and `parseFrontmatter`), importing `DIFFICULTIES, NUMBER_KEYS, TEXT_KEYS` and types from `./types`. Export `parseFrontmatter` and the `Frontmatter` type.

`fences.ts` — move lines 155–300 and 713–875: `FENCE_TYPES`, `FenceType`, `FenceFields`, `SINGLE_LABELS`, `SCALAR_LABELS`, `readFence`, `FenceContext`, `parseHotspot`, `buildFenceBlock`. Imports:

```ts
import type { Issue } from "./types";
import { slugify } from "./ids";
import { sanitizeSvg } from "./svg-sanitiser";
```

Export `FENCE_TYPES`, `FenceType`, `readFence`, `buildFenceBlock`, `FenceContext`.

`natural.ts` — create as a stub with only a comment; Task 2 fills it:

```ts
// Recognisers for natural teacher's-markdown conventions — a document header,
// a numbered quiz, and worked examples. Each is gated on an explicit heading
// by the scanner in ./index.ts, and each falls back to prose rather than
// erroring when its inner shape does not match.
// See docs/superpowers/specs/2026-08-06-natural-lesson-note-format-design.md
export {};
```

- [ ] **Step 6: Create `index.ts`**

Move lines 877–1083 (`Section`, `emitConcept`, `parseLessonMarkdown`, `validateLessonMarkdown`) plus the module header comment from lines 19–23. Then re-export the public surface:

```ts
export { slugify } from "./ids";
export { sanitizeSvg } from "./svg-sanitiser";
export type {
  Issue,
  LessonDifficulty,
  LessonMeta,
  ParsedLesson,
} from "./types";
```

Imports it needs:

```ts
import type { LessonBlock, ConceptBlock } from "@/lib/lesson-engine";
import { MAX_CARD_WORDS, blockWordCount, lintLessonBlocks, wordCount } from "@/lib/lesson-engine";
import type { Issue, LessonMeta, ParsedLesson } from "./types";
import { makeIdFactory, slugify } from "./ids";
import { parseFrontmatter } from "./frontmatter";
import { FENCE_TYPES, buildFenceBlock, readFence, type FenceType } from "./fences";
```

Note the old file imported `EXAM_TYPES` and the block types for fence building — those move to `fences.ts` and are no longer needed here.

- [ ] **Step 7: Delete the old file**

```bash
cd /c/Users/user/Desktop/scholarscrib && rm src/lib/lesson-markdown.ts
```

This is not optional and cannot wait for a later commit. While both exist, `@/lib/lesson-markdown` resolves to the **old file**, so the suite would be testing code you are about to abandon.

- [ ] **Step 8: Run the full suite and the type checker**

```bash
cd /c/Users/user/Desktop/scholarscrib
npx tsc --noEmit
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts scripts/test-admin-lesson.mts 2>&1 | tail -10
```

Expected: `tsc` clean, and the **same** pass count as Step 1. If a test now fails, you changed behaviour during the move — revert and redo the offending module, do not patch the test.

- [ ] **Step 9: Commit**

```bash
git add -A src/lib/lesson-markdown.ts src/lib/lesson-markdown/
git commit -m "refactor(lessons): split lesson-markdown into focused modules

1083 lines about to take another 250. index.ts re-exports the whole public
surface, so no import path changes; svg-sanitiser.ts moves verbatim, comments
and all, because its linear-time and fail-closed arguments are the most
load-bearing text in the file.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Document header and horizontal rules

**Files:**
- Modify: `src/lib/lesson-markdown/natural.ts`, `src/lib/lesson-markdown/types.ts`, `src/lib/lesson-markdown/index.ts`
- Test: `scripts/test-lesson-markdown.mts` (append)

**Interfaces:**
- Consumes: `Issue`, `LessonMeta` from `./types`.
- Produces:
  - `stripLessonNotePrefix(title: string): string`
  - `parseInfoLine(line: string): Record<string, string> | null` — `null` when the line is not an info line.
  - `isHorizontalRule(line: string): boolean`
  - `LessonMeta.docInfo?: Record<string, string>`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-lesson-markdown.mts`:

```ts
test("a Lesson Note: prefix is stripped from the document title", () => {
  const result = parseLessonMarkdown(
    "# Physics Lesson Note: Measurement and Units\n\n## Intro\n\nText.",
  );
  assert.equal(result.meta.title, "Measurement and Units");
});

test("a title whose colon is not lesson-note boilerplate is left alone", () => {
  const result = parseLessonMarkdown("# Osmosis: A Closer Look\n\n## Intro\n\nText.");
  assert.equal(result.meta.title, "Osmosis: A Closer Look");
});

test("the info line under the H1 becomes docInfo, not a block", () => {
  const result = parseLessonMarkdown(
    [
      "# Physics Lesson Note: Measurement and Units",
      "**Class:** SSS1 | **Term:** First Term | **Curriculum Reference:** NERDC",
      "",
      "## Intro",
      "",
      "Text.",
    ].join("\n"),
  );
  assert.deepEqual(result.meta.docInfo, {
    Class: "SSS1",
    Term: "First Term",
    "Curriculum Reference": "NERDC",
  });
  assert.equal(result.blocks.length, 1);
  assert.equal((result.blocks[0] as ConceptBlock).title, "Intro");
});

test("a sentence with one bold run under the H1 stays prose", () => {
  const result = parseLessonMarkdown(
    ["# Title", "This lesson is **important** for WAEC.", "", "## Intro", "", "Text."].join("\n"),
  );
  assert.equal(result.meta.docInfo, undefined);
  assert.equal(result.blocks.length, 2);
  assert.equal((result.blocks[0] as ConceptBlock).text, "This lesson is **important** for WAEC.");
});

test("an info-shaped line deeper in the body stays prose", () => {
  const result = parseLessonMarkdown(
    ["# Title", "", "## Intro", "", "**Note:** read this | **Also:** and this"].join("\n"),
  );
  assert.equal(result.meta.docInfo, undefined);
  assert.equal(result.blocks.length, 1);
  assert.match((result.blocks[0] as ConceptBlock).text, /\*\*Note:\*\* read this/);
});

test("horizontal rules are dropped from card text", () => {
  const result = parseLessonMarkdown("## A\n\nFirst.\n\n---\n\nSecond.");
  const block = result.blocks[0] as ConceptBlock;
  assert.ok(!block.text.includes("---"), `rule leaked into text: ${block.text}`);
  assert.match(block.text, /First\./);
  assert.match(block.text, /Second\./);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /c/Users/user/Desktop/scholarscrib
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | grep -E "^not ok|# fail"
```

Expected: 6 failures — `docInfo` is not a property of `LessonMeta` (tsx will surface a type error or `undefined`), titles keep their prefix, rules leak into text.

- [ ] **Step 3: Add `docInfo` to `LessonMeta`**

In `src/lib/lesson-markdown/types.ts`, add one field:

```ts
export type LessonMeta = {
  title?: string;
  summary?: string;
  subject?: string;
  topic?: string;
  estimatedMinutes?: number;
  difficulty?: LessonDifficulty;
  passMarkPercent?: number;
  practiceCount?: number;
  /**
   * `**Class:** SSS1 | **Term:** First Term` captured from under the H1.
   * Displayed in the upload preview so the admin can confirm which note they
   * are uploading. Written to no column — none exists, and adding one needs
   * the migration that is still blocked.
   */
  docInfo?: Record<string, string>;
};
```

- [ ] **Step 4: Implement the three helpers in `natural.ts`**

```ts
/**
 * Strips the `<Subject> Lesson Note:` boilerplate teachers put in front of the
 * real title. Anchored and specific: a title containing a colon for any other
 * reason ("Osmosis: A Closer Look") keeps it.
 */
export function stripLessonNotePrefix(title: string): string {
  return title.replace(/^[A-Za-z][A-Za-z\s]*?\blesson\s+notes?\s*:\s*/i, "").trim() || title.trim();
}

/**
 * `**Class:** SSS1 | **Term:** First Term` → `{ Class: "SSS1", Term: "First Term" }`.
 *
 * Returns null unless EVERY `|`-separated segment is a `**Key:** value` pair.
 * That is what keeps an ordinary sentence containing one bold run — "This
 * lesson is **important** for WAEC." — from being swallowed as metadata.
 */
export function parseInfoLine(line: string): Record<string, string> | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith("**")) return null;

  const segments = trimmed.split("|").map((s) => s.trim()).filter(Boolean);
  if (segments.length === 0) return null;

  const info: Record<string, string> = {};
  for (const segment of segments) {
    const match = /^\*\*([^*]+?)\s*:?\s*\*\*\s*:?\s*(.*)$/.exec(segment);
    if (!match) return null;
    const key = match[1].trim();
    const value = match[2].trim();
    if (!key || !value) return null;
    info[key] = value;
  }
  return info;
}

/** `---`, `***` or `___` alone on a line. */
export function isHorizontalRule(line: string): boolean {
  return /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line);
}
```

- [ ] **Step 5: Wire them into the scanner**

In `src/lib/lesson-markdown/index.ts`, import them:

```ts
import { isHorizontalRule, parseInfoLine, stripLessonNotePrefix } from "./natural";
```

Add a tracker beside the existing `let inReveal = false;`:

```ts
// Set on the line after an H1, cleared by any other content. The info line is
// only metadata directly under the title; the same shape deeper in the body is
// ordinary prose.
let expectInfoLine = false;
```

Replace the H1 branch (currently lines 996–1001 of the pre-split file):

```ts
    const h1 = /^#\s+(.*)$/.exec(line);
    if (h1) {
      flush();
      if (!meta.title) meta.title = stripLessonNotePrefix(h1[1].trim());
      expectInfoLine = true;
      continue;
    }
```

Immediately after that branch, before the `h2` branch, add:

```ts
    if (expectInfoLine) {
      if (!line.trim()) continue; // blank lines between H1 and info line are fine
      const info = parseInfoLine(line);
      expectInfoLine = false;
      if (info) {
        meta.docInfo = { ...meta.docInfo, ...info };
        continue;
      }
      // Not an info line — fall through and treat it as ordinary content.
    }
```

Then, just before the final `buffer.push(line)` at the end of the loop body, add:

```ts
    if (isHorizontalRule(line)) continue;
```

Finally, clear the flag in the `h2` branch so an H2 immediately after the H1 does not leave it armed. Change the `h2` branch to:

```ts
    const h2 = /^##\s+(.*)$/.exec(line);
    if (h2) {
      flush();
      expectInfoLine = false;
      section = { title: h2[1].trim(), text: "", line: lineNo };
      continue;
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /c/Users/user/Desktop/scholarscrib
npx tsc --noEmit
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | tail -8
```

Expected: all pass, including every pre-existing test.

- [ ] **Step 7: Commit**

```bash
git add src/lib/lesson-markdown/ scripts/test-lesson-markdown.mts
git commit -m "feat(lessons): read the lesson-note header instead of carding it

'# Physics Lesson Note: X' now titles the lesson X, and the
'**Class:** SSS1 | **Term:** ...' line under it becomes meta.docInfo rather
than a stray 18-word card at the top of every lesson.

Both recognisers are deliberately narrow. The info line is only read directly
under the H1, and only when every |-separated segment is a **Key:** value pair
-- so 'This lesson is **important** for WAEC.' stays prose, which a laxer test
would have eaten.

Horizontal rules are dropped rather than buffered; they were reaching cards as
literal '---' and counting toward the 120-word cap.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Quiz recogniser

The task that unblocks the upload. Turns the file's ten questions into 7 `CheckBlock`s and 3 reveal cards, removing the `at least one knowledge check` error.

**Files:**
- Modify: `src/lib/lesson-markdown/natural.ts`, `src/lib/lesson-markdown/index.ts`
- Test: `scripts/test-lesson-markdown.mts` (append)

**Interfaces:**
- Consumes: `Issue` from `./types`; `LessonBlock`, `CheckBlock`, `ConceptBlock` from `@/lib/lesson-engine`.
- Produces:
  - `stripAnswerMarker(text: string): { text: string; marked: boolean }`
  - `isQuizHeading(title: string): boolean`
  - `parseQuizSection(args: SectionArgs): SectionResult`
  - `type SectionArgs = { lines: string[]; startLine: number; heading: string; nextId: (slug: string) => string; previousNonCheckId: string | null; errors: Issue[]; warnings: Issue[] }`
  - `type SectionResult = { blocks: LessonBlock[]; consumed: number }` — `consumed` is how many entries of `lines` the recogniser used.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-lesson-markdown.mts`:

```ts
const QUIZ_LESSON = [
  "## Overview",
  "",
  "Measurement compares a quantity with a standard.",
  "",
  "## Quiz (10 Questions)",
  "",
  "1. Measurement involves comparing a quantity with a:",
  "   a) friend's estimate",
  "   b) known standard (unit) ✔",
  "   c) random guess",
  "",
  "2. The SI unit of length is the:",
  "   a) kilometre",
  "   b) metre ✓",
  "",
  "3. Convert 3,000 g to kilograms. *(Short answer: 3 kg)*",
].join("\n");

test("quiz questions with options become check blocks", () => {
  const result = parseLessonMarkdown(QUIZ_LESSON);
  assert.deepEqual(result.errors, []);
  const checks = result.blocks.filter((b) => b.type === "check") as CheckBlock[];
  assert.equal(checks.length, 2);

  assert.equal(checks[0].question, "Measurement involves comparing a quantity with a:");
  assert.deepEqual(checks[0].options, {
    A: "friend's estimate",
    B: "known standard (unit)",
    C: "random guess",
  });
  assert.equal(checks[0].answer, "B");
  assert.equal(checks[0].afterCard, "overview-1");
  assert.equal(checks[1].answer, "B");
});

test("the answer marker is stripped from the stored option text", () => {
  const result = parseLessonMarkdown(QUIZ_LESSON);
  const check = result.blocks.find((b) => b.type === "check") as CheckBlock;
  assert.equal(check.options.B, "known standard (unit)");
  assert.ok(!JSON.stringify(check.options).includes("✔"));
});

test("every accepted answer marker works", () => {
  for (const marker of ["✔", "✓", "✅", "☑", "*", "**"]) {
    const result = parseLessonMarkdown(
      ["## A", "", "Text.", "", "## Quiz", "", "1. Q?", `   a) wrong`, `   b) right ${marker}`].join("\n"),
    );
    assert.deepEqual(result.errors, [], `marker ${marker} produced errors`);
    const check = result.blocks.find((b) => b.type === "check") as CheckBlock;
    assert.equal(check.answer, "B", `marker ${marker} did not mark option B`);
    assert.equal(check.options.B, "right", `marker ${marker} was not stripped`);
  }
});

test("a short-answer question becomes a concept card with a reveal", () => {
  const result = parseLessonMarkdown(QUIZ_LESSON);
  const reveal = result.blocks.find(
    (b) => b.type === "concept" && (b as ConceptBlock).reveal,
  ) as ConceptBlock;
  assert.equal(reveal.text, "Convert 3,000 g to kilograms.");
  assert.equal(reveal.reveal, "3 kg");
  assert.equal(reveal.id, "short-answer-1");
});

test("a short answer containing punctuation is captured whole", () => {
  const result = parseLessonMarkdown(
    [
      "## A",
      "",
      "Text.",
      "",
      "## Quiz",
      "",
      "1. Explain the difference. *(Short answer: Fundamental cannot be broken down, e.g., mass; derived are combinations, e.g., speed = distance/time)*",
    ].join("\n"),
  );
  const reveal = result.blocks.find(
    (b) => b.type === "concept" && (b as ConceptBlock).reveal,
  ) as ConceptBlock;
  assert.equal(
    reveal.reveal,
    "Fundamental cannot be broken down, e.g., mass; derived are combinations, e.g., speed = distance/time",
  );
});

test("a quiz question with no marked option is an error naming the line", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "Text.", "", "## Quiz", "", "1. Q?", "   a) one", "   b) two"].join("\n"),
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /Question 1 has no correct option/);
  assert.equal(result.errors[0].line, 7);
});

test("a quiz question with two marked options is an error", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "Text.", "", "## Quiz", "", "1. Q?", "   a) one ✔", "   b) two ✔"].join("\n"),
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /Question 1 marks 2 correct options/);
});

test("a quiz question with one option is an error", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "Text.", "", "## Quiz", "", "1. Q?", "   a) only ✔"].join("\n"),
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /Question 1 has only one option/);
});

test("a quiz question with neither options nor a short answer is an error", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "Text.", "", "## Quiz", "", "1. Just a sentence."].join("\n"),
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /has no options and no/);
});

test("prose above the first quiz question is kept as a titled card", () => {
  const result = parseLessonMarkdown(
    [
      "## A", "", "Text.", "",
      "## Quiz", "", "Answer all questions in 10 minutes.", "",
      "1. Q?", "   a) one", "   b) two ✔",
    ].join("\n"),
  );
  assert.deepEqual(result.errors, []);
  const rubric = result.blocks.find(
    (b) => b.type === "concept" && (b as ConceptBlock).title === "Quiz",
  ) as ConceptBlock;
  assert.equal(rubric.text, "Answer all questions in 10 minutes.");
});

test("a quiz section ends at the next heading", () => {
  const result = parseLessonMarkdown(
    [
      "## A", "", "Text.", "",
      "## Quiz", "", "1. Q?", "   a) one", "   b) two ✔", "",
      "## Resources", "", "A book.",
    ].join("\n"),
  );
  assert.deepEqual(result.errors, []);
  const last = result.blocks[result.blocks.length - 1] as ConceptBlock;
  assert.equal(last.title, "Resources");
  assert.equal(last.text, "A book.");
});

test("a quiz lesson passes the authoring lint", () => {
  const result = validateLessonMarkdown(QUIZ_LESSON);
  assert.deepEqual(result.errors, []);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /c/Users/user/Desktop/scholarscrib
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | grep -cE "^not ok"
```

Expected: 12 failures. Every quiz test fails — questions are still prose, so `checks.length` is 0.

- [ ] **Step 3: Implement the recogniser in `natural.ts`**

Add these imports at the top of `natural.ts`:

```ts
import type { LessonBlock, CheckBlock, ConceptBlock } from "@/lib/lesson-engine";
import type { Issue } from "./types";
```

Then:

```ts
export type SectionArgs = {
  /** Body lines *after* the heading, from the heading's next line onward. */
  lines: string[];
  /** 1-based source line number of `lines[0]`. */
  startLine: number;
  heading: string;
  nextId: (slug: string) => string;
  previousNonCheckId: string | null;
  errors: Issue[];
  warnings: Issue[];
};

export type SectionResult = { blocks: LessonBlock[]; consumed: number };

/** `## Quiz`, `## Quiz (10 Questions)`, `## Quiz Time`. */
export function isQuizHeading(title: string): boolean {
  return /^quiz\b/i.test(title.trim());
}

/**
 * Removes a correct-answer marker from the end of an option.
 *
 * Four glyphs plus a bare trailing `*`, because a marker that fails to survive
 * a copy-paste or an encoding round-trip would turn a good question into a
 * hard error. `marked` is false when no marker was present — trailing
 * whitespace alone never counts, since the marker group is not optional.
 */
export function stripAnswerMarker(text: string): { text: string; marked: boolean } {
  const stripped = text.replace(/\s*(?:[✔✓✅☑]|\*{1,2})\s*$/u, "");
  return { text: stripped.trim(), marked: stripped !== text };
}

/** A line that closes any natural section: a heading of level 1-2, or a fence. */
function isSectionTerminator(line: string): boolean {
  return /^#{1,2}\s/.test(line) || /^:::/.test(line.trim());
}

const SHORT_ANSWER_RE = /\*\((?:short answer|answer)\s*:\s*(.+)\)\*/i;

type RawQuestion = {
  label: string;
  line: number;
  stem: string[];
  options: Array<{ key: string; text: string[] }>;
};

export function parseQuizSection(args: SectionArgs): SectionResult {
  const { lines, startLine, heading, nextId, previousNonCheckId, errors } = args;

  let consumed = 0;
  while (consumed < lines.length && !isSectionTerminator(lines[consumed])) consumed += 1;
  const body = lines.slice(0, consumed);

  const preamble: string[] = [];
  const questions: RawQuestion[] = [];

  for (let i = 0; i < body.length; i++) {
    const raw = body[i];
    const lineNo = startLine + i;
    if (isHorizontalRule(raw)) continue;

    const question = /^\s*(\d+)[.)]\s+(.*)$/.exec(raw);
    if (question) {
      questions.push({
        label: question[1],
        line: lineNo,
        stem: [question[2].trim()],
        options: [],
      });
      continue;
    }

    const current = questions[questions.length - 1];
    const option = current ? /^\s*([A-Ha-h])[.)]\s+(.*)$/.exec(raw) : null;
    if (option && current) {
      current.options.push({ key: option[1].toUpperCase(), text: [option[2].trim()] });
      continue;
    }

    if (!raw.trim()) continue;

    // Unlabelled: continue whatever opened last, so wrapped questions and long
    // options work without ceremony — the same rule readFence() uses.
    if (!current) {
      preamble.push(raw.trim());
    } else if (current.options.length > 0) {
      current.options[current.options.length - 1].text.push(raw.trim());
    } else {
      current.stem.push(raw.trim());
    }
  }

  const blocks: LessonBlock[] = [];

  // Prose above the first question is a rubric, not decoration. Keep it.
  if (preamble.length > 0) {
    const rubric: ConceptBlock = {
      type: "concept",
      id: nextId(slugify(heading)),
      title: heading,
      text: preamble.join("\n"),
    };
    blocks.push(rubric);
  }

  // A check must follow a non-check block. Prefer a rubric card we just made,
  // else the last card before the quiz.
  let lastNonCheckId =
    blocks.length > 0 ? blocks[blocks.length - 1].id : previousNonCheckId;

  for (const question of questions) {
    const stem = question.stem.join(" ").trim();

    if (question.options.length === 0) {
      const shortAnswer = SHORT_ANSWER_RE.exec(stem);
      if (!shortAnswer) {
        errors.push({
          line: question.line,
          message: `Question ${question.label} has no options and no "(Short answer: …)".`,
        });
        continue;
      }
      const block: ConceptBlock = {
        type: "concept",
        id: nextId("short-answer"),
        text: stem.replace(SHORT_ANSWER_RE, "").trim(),
        reveal: shortAnswer[1].trim(),
      };
      blocks.push(block);
      lastNonCheckId = block.id;
      continue;
    }

    if (question.options.length < 2) {
      errors.push({
        line: question.line,
        message: `Question ${question.label} has only one option — a check needs at least two.`,
      });
      continue;
    }

    const options: Record<string, string> = {};
    const marked: string[] = [];
    for (const option of question.options) {
      const { text, marked: isAnswer } = stripAnswerMarker(option.text.join(" ").trim());
      options[option.key] = text;
      if (isAnswer) marked.push(option.key);
    }

    if (marked.length === 0) {
      errors.push({
        line: question.line,
        message: `Question ${question.label} has no correct option — mark it with ✔.`,
      });
      continue;
    }
    if (marked.length > 1) {
      errors.push({
        line: question.line,
        message: `Question ${question.label} marks ${marked.length} correct options — a check needs exactly one.`,
      });
      continue;
    }
    if (!lastNonCheckId) {
      errors.push({
        line: question.line,
        message: `Question ${question.label} cannot be the lesson's first block — put the quiz after a card.`,
      });
      continue;
    }

    const check: CheckBlock = {
      type: "check",
      id: nextId("check"),
      question: stem,
      options,
      answer: marked[0],
      explanation: "",
      afterCard: lastNonCheckId,
    };
    blocks.push(check);
  }

  return { blocks, consumed };
}
```

Add `slugify` to the imports at the top of `natural.ts`:

```ts
import { slugify } from "./ids";
```

- [ ] **Step 4: Dispatch to it from the scanner**

In `src/lib/lesson-markdown/index.ts`, extend the import:

```ts
import {
  isHorizontalRule,
  isQuizHeading,
  parseInfoLine,
  parseQuizSection,
  stripLessonNotePrefix,
} from "./natural";
```

Replace the `h2` branch written in Task 2 with:

```ts
    const h2 = /^##\s+(.*)$/.exec(line);
    if (h2) {
      flush();
      expectInfoLine = false;
      const title = h2[1].trim();

      if (isQuizHeading(title)) {
        const result = parseQuizSection({
          lines: front.bodyLines.slice(i + 1),
          startLine: lineNo + 1,
          heading: title,
          nextId,
          previousNonCheckId:
            [...blocks].reverse().find((b) => b.type !== "check")?.id ?? null,
          errors,
          warnings,
        });
        blocks.push(...result.blocks);
        i += result.consumed;
        continue;
      }

      section = { title, text: "", line: lineNo };
      continue;
    }
```

`i += result.consumed` leaves `i` on the last consumed line; the loop's own `i++` then advances to the terminator, which is re-read as a normal line. That is why `parseQuizSection` stops *before* the terminator rather than eating it.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /c/Users/user/Desktop/scholarscrib
npx tsc --noEmit
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | tail -8
```

Expected: all pass, including every pre-existing test.

- [ ] **Step 6: Commit**

```bash
git add src/lib/lesson-markdown/ scripts/test-lesson-markdown.mts
git commit -m "feat(lessons): read a numbered quiz as knowledge checks

A '## Quiz' heading now puts the scanner in quiz mode: numbered questions with
a) b) c) options become CheckBlocks, the option carrying a check mark becomes
the answer, and '*(Short answer: X)*' questions become concept cards with X in
the reveal.

This is what was blocking the first real upload. A teacher's note ended with
ten questions the parser read as prose, so lintLessonBlocks() failed on 'at
least one knowledge check' and Save stayed disabled -- the pedagogical heart of
the note was the one part thrown away.

A malformed question errors rather than warns. The alternative is a question
the author wrote vanishing silently from the lesson, which is worse than an
upload that stops and says which line to fix.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Worked-examples recogniser

**Files:**
- Modify: `src/lib/lesson-markdown/natural.ts`, `src/lib/lesson-markdown/index.ts`
- Test: `scripts/test-lesson-markdown.mts` (append)

**Interfaces:**
- Consumes: `SectionArgs`, `SectionResult`, `isSectionTerminator` from Task 3.
- Produces:
  - `isWorkedExamplesHeading(title: string): boolean`
  - `parseWorkedExamples(args: SectionArgs): SectionResult`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-lesson-markdown.mts`:

```ts
const EXAMPLES_LESSON = [
  "## Overview",
  "",
  "Units matter.",
  "",
  "## Worked Examples",
  "",
  "**Example 1:** Convert 5 km to metres.",
  "**Solution:**",
  "1 km = 1,000 m",
  "5 km = 5 × 1,000 = **5,000 m**",
  "",
  "**Example 2:** A trip takes 2 hours 30 minutes. Convert to seconds.",
  "**Solution:**",
  "2 hours = 2 × 3,600 s = 7,200 s",
  "30 minutes = 30 × 60 s = 1,800 s",
  "Total = 7,200 + 1,800 = **9,000 seconds**",
].join("\n");

test("worked examples become example blocks with steps and answers", () => {
  const result = parseLessonMarkdown(EXAMPLES_LESSON);
  assert.deepEqual(result.errors, []);
  const examples = result.blocks.filter((b) => b.type === "example") as ExampleBlock[];
  assert.equal(examples.length, 2);

  assert.equal(examples[0].problem, "Convert 5 km to metres.");
  assert.deepEqual(examples[0].steps, ["1 km = 1,000 m"]);
  assert.equal(examples[0].answer, "5,000 m");
  assert.equal(examples[0].mode, "worked");

  assert.equal(examples[1].steps.length, 2);
  assert.equal(examples[1].answer, "9,000 seconds");
});

test("an unbolded final line becomes the whole answer", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "T.", "", "## Worked Examples", "", "**Example 1:** Q?", "**Solution:**", "step one", "the answer is 4"].join("\n"),
  );
  const example = result.blocks.find((b) => b.type === "example") as ExampleBlock;
  assert.deepEqual(example.steps, ["step one"]);
  assert.equal(example.answer, "the answer is 4");
});

test("a one-line solution is the answer with no steps", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "T.", "", "## Worked Examples", "", "**Example 1:** Q?", "**Solution:**", "**42**"].join("\n"),
  );
  const example = result.blocks.find((b) => b.type === "example") as ExampleBlock;
  assert.deepEqual(example.steps, []);
  assert.equal(example.answer, "42");
});

test("the colon may sit inside or outside the bold run", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "T.", "", "## Worked Examples", "", "**Example 1**: Q?", "**Solution**:", "**7**"].join("\n"),
  );
  assert.deepEqual(result.errors, []);
  const example = result.blocks.find((b) => b.type === "example") as ExampleBlock;
  assert.equal(example.problem, "Q?");
  assert.equal(example.answer, "7");
});

test("an example with no Solution: is an error naming its line", () => {
  const result = parseLessonMarkdown(
    ["## A", "", "T.", "", "## Worked Examples", "", "**Example 1:** Q?", "some prose"].join("\n"),
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /Example 1 has no \*\*Solution:\*\*/);
  assert.equal(result.errors[0].line, 7);
});

test("a worked-examples section ends at the next heading", () => {
  const result = parseLessonMarkdown(EXAMPLES_LESSON + "\n\n## After\n\nMore text.");
  const last = result.blocks[result.blocks.length - 1] as ConceptBlock;
  assert.equal(last.title, "After");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /c/Users/user/Desktop/scholarscrib
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | grep -cE "^not ok"
```

Expected: 6 failures — examples are still one concept card, so `examples.length` is 0.

- [ ] **Step 3: Implement in `natural.ts`**

Add `ExampleBlock` to the type import at the top:

```ts
import type { LessonBlock, CheckBlock, ConceptBlock, ExampleBlock } from "@/lib/lesson-engine";
```

Then:

```ts
/** `## Worked Examples`, `## Worked Example`. */
export function isWorkedExamplesHeading(title: string): boolean {
  return /^worked\s+examples?\b/i.test(title.trim());
}

// The colon may sit inside or outside the bold run -- `**Example 2:**` and
// `**Example 2**:` render identically, and no author is consistent about it.
const EXAMPLE_OPEN_RE = /^\s*\*\*\s*Example\s*([\w.]+?)\s*:?\s*\*\*\s*:?\s*(.*)$/i;
const SOLUTION_OPEN_RE = /^\s*\*\*\s*Solutions?\s*:?\s*\*\*\s*:?\s*(.*)$/i;
const TRAILING_BOLD_RE = /\*\*(.+?)\*\*\s*$/;

type RawExample = { label: string; line: number; problem: string; working: string[]; hasSolution: boolean };

export function parseWorkedExamples(args: SectionArgs): SectionResult {
  const { lines, startLine, nextId, errors } = args;

  let consumed = 0;
  while (consumed < lines.length && !isSectionTerminator(lines[consumed])) consumed += 1;
  const body = lines.slice(0, consumed);

  const examples: RawExample[] = [];

  for (let i = 0; i < body.length; i++) {
    const raw = body[i];
    const lineNo = startLine + i;
    if (isHorizontalRule(raw) || !raw.trim()) continue;

    const open = EXAMPLE_OPEN_RE.exec(raw);
    if (open) {
      examples.push({
        label: open[1],
        line: lineNo,
        problem: open[2].trim(),
        working: [],
        hasSolution: false,
      });
      continue;
    }

    const current = examples[examples.length - 1];
    if (!current) continue; // prose before the first example: nothing to attach it to

    const solution = SOLUTION_OPEN_RE.exec(raw);
    if (solution) {
      current.hasSolution = true;
      if (solution[1].trim()) current.working.push(solution[1].trim());
      continue;
    }

    if (current.hasSolution) current.working.push(raw.trim());
    else current.problem = `${current.problem} ${raw.trim()}`.trim();
  }

  const blocks: LessonBlock[] = [];

  for (const example of examples) {
    if (!example.hasSolution || example.working.length === 0) {
      errors.push({
        line: example.line,
        message: `Example ${example.label} has no **Solution:** — an example needs an answer.`,
      });
      continue;
    }

    // The last line of the working is the answer; where it ends in a bolded
    // span, only that span is the answer. Checked against every example in
    // scripts/fixtures/measurement-and-units.md.
    const steps = example.working.slice(0, -1);
    const lastLine = example.working[example.working.length - 1];
    const bold = TRAILING_BOLD_RE.exec(lastLine);

    const block: ExampleBlock = {
      type: "example",
      id: nextId("example"),
      problem: example.problem,
      steps,
      answer: (bold ? bold[1] : lastLine).trim(),
      // The natural format has no way to express partial or solo, and
      // inferring one would be invention. :::example still offers them.
      mode: "worked",
    };
    blocks.push(block);
  }

  return { blocks, consumed };
}
```

- [ ] **Step 4: Dispatch to it from the scanner**

In `index.ts`, extend the import from `./natural` with `isWorkedExamplesHeading` and `parseWorkedExamples`, then add a second branch inside the `h2` handler, directly after the quiz branch:

```ts
      if (isWorkedExamplesHeading(title)) {
        const result = parseWorkedExamples({
          lines: front.bodyLines.slice(i + 1),
          startLine: lineNo + 1,
          heading: title,
          nextId,
          previousNonCheckId: null,
          errors,
          warnings,
        });
        blocks.push(...result.blocks);
        i += result.consumed;
        continue;
      }
```

`previousNonCheckId` is unused by this recogniser — examples are not checks and need no `afterCard` — but `SectionArgs` is shared, so pass `null`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /c/Users/user/Desktop/scholarscrib
npx tsc --noEmit
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | tail -8
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/lib/lesson-markdown/ scripts/test-lesson-markdown.mts
git commit -m "feat(lessons): read '**Example N:** / **Solution:**' as example blocks

Three worked examples were collapsing into one 98-word concept card. They now
become ExampleBlocks with their arithmetic as steps.

The answer rule: the last line of the working is the answer, and where it ends
in a bolded span only that span is stored -- so '5 km = 5 x 1,000 = **5,000 m**'
yields '5,000 m', not the whole equation. Verified against all three examples in
the fixture.

An example with no **Solution:** errors rather than guessing which line is the
answer.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Render tables, ordered lists and italics

**Files:**
- Create: `src/lib/markdown-segments.ts`, `scripts/test-markdown-segments.mts`
- Modify: `src/components/lesson/markdown.tsx`, `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `segmentMarkdown(content: string): Segment[]` where

  ```ts
  export type Segment =
    | { kind: "heading"; text: string }
    | { kind: "p"; text: string }
    | { kind: "ul"; items: string[] }
    | { kind: "ol"; items: string[] }
    | { kind: "table"; header: string[]; rows: string[][] }
    | { kind: "rule" };
  ```

- [ ] **Step 1: Write the failing tests**

Create `scripts/test-markdown-segments.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { segmentMarkdown } from "../src/lib/markdown-segments";

test("a pipe table becomes a table segment", () => {
  const segments = segmentMarkdown(
    ["| Quantity | Unit |", "|---|---|", "| Length | metre |", "| Mass | kilogram |"].join("\n"),
  );
  assert.equal(segments.length, 1);
  assert.equal(segments[0].kind, "table");
  const table = segments[0] as Extract<ReturnType<typeof segmentMarkdown>[number], { kind: "table" }>;
  assert.deepEqual(table.header, ["Quantity", "Unit"]);
  assert.deepEqual(table.rows, [["Length", "metre"], ["Mass", "kilogram"]]);
});

test("a table without a delimiter row falls back to a paragraph", () => {
  const segments = segmentMarkdown("| Quantity | Unit |\n| Length | metre |");
  assert.equal(segments.length, 1);
  assert.equal(segments[0].kind, "p");
});

test("a lead-in line followed by numbered items splits into a paragraph and a list", () => {
  const segments = segmentMarkdown(
    [
      "By the end of this lesson, students should be able to:",
      "1. Define measurement",
      "2. Distinguish quantities",
    ].join("\n"),
  );
  assert.equal(segments.length, 2);
  assert.equal(segments[0].kind, "p");
  assert.equal((segments[0] as { kind: "p"; text: string }).text, "By the end of this lesson, students should be able to:");
  assert.equal(segments[1].kind, "ol");
  assert.deepEqual((segments[1] as { kind: "ol"; items: string[] }).items, [
    "Define measurement",
    "Distinguish quantities",
  ]);
});

test("bulleted lists still work", () => {
  const segments = segmentMarkdown("- one\n- two");
  assert.equal(segments[0].kind, "ul");
  assert.deepEqual((segments[0] as { kind: "ul"; items: string[] }).items, ["one", "two"]);
});

test("a horizontal rule becomes a rule segment", () => {
  const segments = segmentMarkdown("Text.\n\n---\n\nMore.");
  assert.deepEqual(segments.map((s) => s.kind), ["p", "rule", "p"]);
});

test("headings are recognised", () => {
  const segments = segmentMarkdown("## Heading\n\nText.");
  assert.equal(segments[0].kind, "heading");
  assert.equal((segments[0] as { kind: "heading"; text: string }).text, "Heading");
});

test("a plain paragraph keeps its internal line breaks as one segment", () => {
  const segments = segmentMarkdown("One line\nand another.");
  assert.equal(segments.length, 1);
  assert.equal(segments[0].kind, "p");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /c/Users/user/Desktop/scholarscrib
node --import tsx --test --test-force-exit scripts/test-markdown-segments.mts 2>&1 | tail -5
```

Expected: failure to resolve `../src/lib/markdown-segments`.

- [ ] **Step 3: Implement `src/lib/markdown-segments.ts`**

```ts
// Splits the lesson markdown subset into renderable segments. Pure and
// React-free so it can be tested under node:test.
//
// Blocks are separated by blank lines, but a single block may mix kinds --
// a lead-in sentence followed by a numbered list is one block and two
// segments -- so each block is further split into runs of like lines.

export type Segment =
  | { kind: "heading"; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][] }
  | { kind: "rule" };

const RULE_RE = /^\s*(-{3,}|\*{3,}|_{3,})\s*$/;
const UL_RE = /^\s*[-*]\s+(.*)$/;
const OL_RE = /^\s*\d+[.)]\s+(.*)$/;
const DELIMITER_RE = /^\s*\|[\s:|-]+\|\s*$/;

function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 2;
}

function cells(line: string): string[] {
  return line
    .trim()
    .slice(1, -1)
    .split("|")
    .map((cell) => cell.trim());
}

/**
 * A table needs a header, a `|---|` delimiter and at least the shape of a
 * body. Anything that fails those tests falls back to a paragraph rather
 * than rendering a broken grid -- the same fail-safe posture sanitizeSvg
 * takes, for the same reason: this input is authored by hand and untrusted.
 */
function readTable(lines: string[]): Segment | null {
  if (lines.length < 2) return null;
  if (!isTableRow(lines[0]) || !DELIMITER_RE.test(lines[1])) return null;
  if (!lines.every(isTableRow)) return null;
  return {
    kind: "table",
    header: cells(lines[0]),
    rows: lines.slice(2).map(cells),
  };
}

type LineKind = "rule" | "ul" | "ol" | "table" | "text";

function lineKind(line: string): LineKind {
  if (RULE_RE.test(line)) return "rule";
  if (UL_RE.test(line)) return "ul";
  if (OL_RE.test(line)) return "ol";
  if (isTableRow(line)) return "table";
  return "text";
}

export function segmentMarkdown(content: string): Segment[] {
  const segments: Segment[] = [];

  for (const block of content.split(/\n{2,}/)) {
    const lines = block.split("\n").filter((line) => line.trim());
    if (lines.length === 0) continue;

    // A rule is `---`, but `- item` is a bullet; RULE_RE is checked first in
    // lineKind, so ordering here is already correct.
    let run: string[] = [];
    let runKind: LineKind | null = null;

    const flushRun = () => {
      if (run.length === 0 || runKind === null) return;
      switch (runKind) {
        case "rule":
          run.forEach(() => segments.push({ kind: "rule" }));
          break;
        case "ul":
          segments.push({
            kind: "ul",
            items: run.map((line) => (UL_RE.exec(line) as RegExpExecArray)[1]),
          });
          break;
        case "ol":
          segments.push({
            kind: "ol",
            items: run.map((line) => (OL_RE.exec(line) as RegExpExecArray)[1]),
          });
          break;
        case "table": {
          const table = readTable(run);
          // Fail safe: an unparseable table is readable text, not a broken grid.
          segments.push(table ?? { kind: "p", text: run.join("\n") });
          break;
        }
        default: {
          const text = run.join("\n");
          const heading = /^#{2,4}\s+(.*)$/.exec(text.trim());
          if (heading && run.length === 1) segments.push({ kind: "heading", text: heading[1] });
          else segments.push({ kind: "p", text });
        }
      }
      run = [];
      runKind = null;
    };

    for (const line of lines) {
      const kind = lineKind(line);
      if (kind !== runKind) {
        flushRun();
        runKind = kind;
      }
      run.push(line);
    }
    flushRun();
  }

  return segments;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /c/Users/user/Desktop/scholarscrib
node --import tsx --test --test-force-exit scripts/test-markdown-segments.mts 2>&1 | tail -5
```

Expected: all 7 pass.

- [ ] **Step 5: Rewrite `markdown.tsx` on top of it**

Replace the whole of `src/components/lesson/markdown.tsx`:

```tsx
import { Fragment, type ReactNode } from "react";
import { segmentMarkdown } from "@/lib/markdown-segments";

// Renders the markdown subset used by lesson content: `## heading`,
// `- bullets`, `1. numbered items`, pipe tables, `**bold**`, `*italic*`,
// `---` rules, and blank-line-separated paragraphs.
//
// Safe against HTML injection by construction: every string reaches the DOM as
// a React text child, and there is no dangerouslySetInnerHTML here. Lesson
// content is authored by upload, so it is untrusted input.

function renderInline(text: string): ReactNode[] {
  // Bold first so `**x**` is never consumed by the italic alternative.
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold text-foreground">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export function Markdown({ content }: { content: string }) {
  const segments = segmentMarkdown(content);

  return (
    <div className="space-y-4">
      {segments.map((segment, idx) => {
        switch (segment.kind) {
          case "heading":
            return (
              <h3 key={idx} className="text-base font-semibold text-foreground pt-1">
                {segment.text}
              </h3>
            );

          case "rule":
            return <hr key={idx} className="border-border" />;

          case "ul":
            return (
              <ul key={idx} className="space-y-2">
                {segment.items.map((item, li) => (
                  <li
                    key={li}
                    className="flex items-start gap-2.5 text-sm text-foreground/90 leading-relaxed"
                  >
                    <span className="mt-2 w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                    <span>{renderInline(item)}</span>
                  </li>
                ))}
              </ul>
            );

          case "ol":
            return (
              <ol key={idx} className="space-y-2">
                {segment.items.map((item, li) => (
                  <li
                    key={li}
                    className="flex items-start gap-2.5 text-sm text-foreground/90 leading-relaxed"
                  >
                    <span className="mt-0.5 text-xs font-semibold text-primary tabular-nums shrink-0">
                      {li + 1}.
                    </span>
                    <span>{renderInline(item)}</span>
                  </li>
                ))}
              </ol>
            );

          case "table":
            return (
              <div key={idx} className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-border-strong">
                      {segment.header.map((cell, ci) => (
                        <th
                          key={ci}
                          className="px-3 py-2 text-left font-semibold text-foreground"
                        >
                          {renderInline(cell)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {segment.rows.map((row, ri) => (
                      <tr key={ri} className="border-b border-border last:border-0">
                        {row.map((cell, ci) => (
                          <td key={ci} className="px-3 py-2 text-foreground/90 align-top">
                            {renderInline(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );

          default:
            return (
              <p key={idx} className="text-sm text-foreground/90 leading-relaxed">
                {renderInline(segment.text)}
              </p>
            );
        }
      })}
    </div>
  );
}
```

**Note on the removed `escapeHtml`:** the old file escaped `&`, `<` and `>` by hand *and* returned the result as a React text child, which double-escapes — a lesson containing `5 < 6` rendered as `5 &lt; 6`. React escapes text children itself, so dropping the manual pass both fixes that display bug and keeps the injection guarantee. There is still no `dangerouslySetInnerHTML` in this file.

- [ ] **Step 6: Register the new test file**

In `package.json`, append `scripts/test-markdown-segments.mts` to the end of the `test` script's file list.

- [ ] **Step 7: Run everything**

```bash
cd /c/Users/user/Desktop/scholarscrib
npx tsc --noEmit
npm test 2>&1 | tail -12
```

Expected: `tsc` clean, all suites pass.

- [ ] **Step 8: Commit**

```bash
git add src/lib/markdown-segments.ts src/components/lesson/markdown.tsx scripts/test-markdown-segments.mts package.json
git commit -m "feat(lessons): render tables, ordered lists and italics

Three tables in the first real lesson note reached students as run-on pipe
text: '| Fundamental Quantity | SI Unit | Symbol | |---|---|---| | Length |'.
The renderer knew only headings, bullets and bold.

Splits classification into segmentMarkdown(), a pure function, for two reasons.
It is testable without a React renderer -- and it segments *within* a block,
which the spec's per-block classifier could not. Learning Objectives is a
lead-in sentence followed by six numbered items with no blank line between, so
a per-block classifier calls the whole thing a paragraph and the list stays
run-on: the exact case it was added to fix.

Also drops the hand-rolled escapeHtml. It ran before returning a React text
child, which escapes again -- so '5 < 6' rendered as '5 &lt; 6'. React's own
escaping is the guarantee; nothing here uses dangerouslySetInnerHTML.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Show `docInfo` in the upload preview

**Files:**
- Modify: `src/components/admin/lesson-upload-form.tsx`

**Interfaces:**
- Consumes: `ParsedLesson["meta"]["docInfo"]` from Task 2.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Add the panel**

In `src/components/admin/lesson-upload-form.tsx`, in the right-hand preview column, directly **above** the `Preview — exactly what students will read` paragraph (currently line 385), insert:

```tsx
        {parsed?.meta.docInfo && Object.keys(parsed.meta.docInfo).length > 0 && (
          <div className="rounded-lg border border-border bg-card p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">
              From the file header
            </p>
            <dl className="mt-2 space-y-1">
              {Object.entries(parsed.meta.docInfo).map(([key, value]) => (
                <div key={key} className="flex gap-2 text-sm">
                  <dt className="font-semibold text-foreground shrink-0">{key}:</dt>
                  <dd className="text-foreground/90">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 text-xs text-muted">
              Shown so you can confirm the right note — not saved to the lesson.
            </p>
          </div>
        )}
```

That closing sentence is load-bearing: `docInfo` is genuinely written nowhere, and an admin who assumed the term was being recorded would be misled by a panel that did not say so.

- [ ] **Step 2: Verify it type-checks and builds**

```bash
cd /c/Users/user/Desktop/scholarscrib
npx tsc --noEmit
npx next build 2>&1 | tail -15
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/components/admin/lesson-upload-form.tsx
git commit -m "feat(admin): show the file's Class/Term header in the upload preview

The panel says plainly that these are not saved. There is no column for them,
and an admin who assumed the term was being recorded would be misled by a panel
that displayed it silently.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The fixture anchor test

The whole point of the work, asserted end to end. If this regresses, the feature has failed at the only thing it was built for.

**Files:**
- Modify: `scripts/test-lesson-markdown.mts` (append)
- Reads: `scripts/fixtures/measurement-and-units.md` (already committed in `d1bfaf0`)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test-lesson-markdown.mts`. Add these imports at the top of the file:

```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
```

Then the test:

```ts
const FIXTURE = readFileSync(
  fileURLToPath(new URL("./fixtures/measurement-and-units.md", import.meta.url)),
  "utf8",
);

test("the real lesson note parses with no errors at all", () => {
  const result = validateLessonMarkdown(FIXTURE);
  assert.deepEqual(
    result.errors,
    [],
    `fixture should upload clean, got:\n${result.errors.map((e) => `  line ${e.line}: ${e.message}`).join("\n")}`,
  );
});

test("the real lesson note produces the expected block mix", () => {
  const result = validateLessonMarkdown(FIXTURE);
  const counts = result.blocks.reduce<Record<string, number>>((acc, b) => {
    acc[b.type] = (acc[b.type] ?? 0) + 1;
    return acc;
  }, {});

  assert.equal(counts.check, 7, "seven multiple-choice questions become checks");
  assert.equal(counts.example, 3, "three worked examples become example blocks");

  const reveals = result.blocks.filter(
    (b) => b.type === "concept" && (b as ConceptBlock).reveal,
  );
  assert.equal(reveals.length, 3, "three short-answer questions become reveal cards");
});

test("the real lesson note's header is read, not carded", () => {
  const result = parseLessonMarkdown(FIXTURE);
  assert.equal(result.meta.title, "Measurement and Units");
  assert.equal(result.meta.docInfo?.Class, "SSS1");
  assert.equal(result.meta.docInfo?.Term, "First Term");

  const first = result.blocks[0] as ConceptBlock;
  assert.ok(
    !first.text.includes("**Class:**"),
    `header line leaked into the first card: ${first.text}`,
  );
});

test("every card in the real lesson note is within the word cap", () => {
  const result = parseLessonMarkdown(FIXTURE);
  for (const block of result.blocks) {
    if (block.type === "check") continue;
    const words = blockWordCount(block);
    assert.ok(words <= MAX_CARD_WORDS, `${block.id} is ${words} words`);
  }
});

test("no horizontal rule survives into the real lesson note's cards", () => {
  const result = parseLessonMarkdown(FIXTURE);
  for (const block of result.blocks) {
    if (block.type !== "concept") continue;
    assert.ok(
      !/^\s*---\s*$/m.test((block as ConceptBlock).text),
      `${block.id} still contains a rule`,
    );
  }
});
```

- [ ] **Step 2: Run it**

```bash
cd /c/Users/user/Desktop/scholarscrib
node --import tsx --test --test-force-exit scripts/test-lesson-markdown.mts 2>&1 | tail -12
```

Expected: all pass. If the block-mix test fails, read its message — it names the counts actually produced, which tells you which recogniser under-fired.

- [ ] **Step 3: Run the entire suite and the build**

```bash
cd /c/Users/user/Desktop/scholarscrib
npx tsc --noEmit
npm test 2>&1 | tail -15
npx next build 2>&1 | tail -10
```

Expected: `tsc` clean, all 21 test files pass, build succeeds.

- [ ] **Step 4: Verify in the real app**

Start the dev server, sign in as `michael@prep.com` (promoted to `ADMIN` by `scripts/promote-admin.ts`), and open `/admin/lessons/upload`. Upload `02_Measurement_and_Units.md` unmodified and confirm, against the screenshot that opened this work:

1. The red `1 problem to fix — A lesson should include at least one knowledge check` banner is **gone**.
2. The `Line 91: "Quiz (10 Questions)" is longer than 120 words` warning is **gone**.
3. **Save lesson** is **enabled**.
4. The three tables render as real tables, not pipe text.
5. Learning Objectives renders as a numbered list.
6. A `From the file header` panel shows `Class: SSS1` and `Term: First Term`.
7. The quiz renders as interactive knowledge checks.

Then click through and save it, and open the lesson as a student to confirm the checks work.

- [ ] **Step 5: Commit**

```bash
git add scripts/test-lesson-markdown.mts
git commit -m "test(lessons): anchor the format on the real lesson note

Asserts the fixture uploads with zero errors and yields 7 checks, 3 examples
and 3 reveal cards -- the file that produced 11 flat concept cards and a
blocking lint error when this work started.

The fixture is the real teacher's note, not a paraphrase, so a regression here
means the feature has failed at the only thing it was built for.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| Document header — title prefix, info line | 2 |
| Quiz — MCQ → check, markers, short answer → reveal | 3 |
| Quiz — four error cases, each with a line number | 3 |
| Quiz — heading emits no block, preamble kept | 3 |
| Worked examples — problem/steps/answer, bold extraction | 4 |
| Worked examples — colon tolerance, missing Solution errors | 4 |
| Horizontal rules dropped | 2 |
| Renderer — tables, ordered lists, italics, rules | 5 |
| Architecture — 6-module split (+`ids.ts`), old file deleted | 1 |
| `svg-sanitiser.ts` moved verbatim | 1 (Step 4) |
| `docInfo` in the upload preview | 6 |
| Tests — fixture anchor, quiz, examples, header, renderer, regression | 3, 4, 5, 7 |

No gaps.

**Placeholder scan:** none. Every code step carries runnable code; every command is exact.

**Type consistency:** `SectionArgs` / `SectionResult` are defined in Task 3 and reused unchanged in Task 4. `isSectionTerminator` and `isHorizontalRule` are defined in Tasks 3 and 2 respectively and used by both recognisers. `Segment` is defined once in Task 5 and consumed only there. `stripAnswerMarker` returns `{ text, marked }` and is destructured as such at its one call site. `LessonMeta.docInfo` is added in Task 2 and read in Tasks 6 and 7.

**One risk this plan carries:** Task 1 is a 1083-line move with no behaviour change, and its only guard is that the existing suite passes. If a subagent "tidies" `svg-sanitiser.ts` while moving it, the suite may still pass while the linear-time argument quietly stops holding. Step 4 says so explicitly; a reviewer should diff that file's function bodies against `git show HEAD:src/lib/lesson-markdown.ts` and expect zero changes.
