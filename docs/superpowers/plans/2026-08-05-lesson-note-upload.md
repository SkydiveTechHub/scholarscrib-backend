# Lesson Note Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin upload a markdown file against a topic and have it parsed, sanitised and stored as structured `Lesson.blocks` that the Classroom notes view and card player render.

**Architecture:** A pure line-scanning parser (`src/lib/lesson-markdown.ts`) turns markdown into the `LessonBlock[]` shapes already defined in `src/lib/lesson-engine.ts`. An admin page reads the file client-side with `file.text()`, previews it with the real `LessonNotes` component, and POSTs the **raw markdown** to a route handler that re-parses server-side before writing. No schema migration, no new dependency.

**Tech Stack:** Next.js 16 (App Router, route handlers — this codebase has **zero** server actions), React 19, Prisma 6, zod 4, Tailwind 4, `node:test` + `tsx` for tests.

**Spec:** `docs/superpowers/specs/2026-08-05-lesson-note-upload-design.md`

## Global Constraints

- **Read `node_modules/next/dist/docs/` before writing any Next.js code.** This Next version has breaking changes from what you may expect (per `AGENTS.md`).
- **No new npm dependencies.** No YAML parser, no markdown library, no sanitiser library. Everything is hand-rolled against the existing toolchain.
- **No Prisma schema migration.** `prisma migrate` currently hangs because `DIRECT_URL` points at the pgbouncer pooler rather than a session-mode connection. Every write in this plan targets an existing column.
- **`src/lib/lesson-markdown.ts` must stay pure** — no imports from `@prisma/client`, `next/*`, `react`, or `@/lib/db`. It is imported by both a client component and a route handler.
- **No server actions.** The codebase has none (`grep "use server"` returns nothing). Client → `fetch()` → `/api/admin/...` route handler is the only pattern.
- **Route handler order is fixed:** `requireAdmin()` → zod `safeParse` → work → `recordAudit()` → `revalidateTag(CATALOGUE_TAG, "max")` → `NextResponse.json`.
- **Block ids are derived, never authored** — heading slug + ordinal.
- `MAX_CARD_WORDS = 120`, exported from `src/lib/lesson-engine.ts`. Never redefine it.
- **Tests are `node:test` + `assert/strict`** in `scripts/test-*.mts`, and every new test file must be appended to the `test` script in `package.json`.
- Difficulty enum values are exactly `BASIC | INTERMEDIATE | ADVANCED`.
- Exam type tags are exactly `WAEC | JAMB | NECO` (`EXAM_TYPES` in `lesson-engine.ts`).
- **`keyPoints` is out of scope.** Do not add frontmatter or parsing for it.

## File Structure

| File | Responsibility |
|---|---|
| `src/lib/lesson-markdown.ts` (create) | Pure parser: frontmatter, headings, fences, id generation, auto-split, lint merge. Exports `parseLessonMarkdown`, `validateLessonMarkdown`, `sanitizeSvg`. |
| `src/lib/admin-lesson.ts` (create) | Pure DB-shaping helper: turns a `ParsedLesson` into the `lesson.update` data object. Mirrors `src/lib/admin-question.ts`. |
| `src/lib/validators.ts` (modify) | Add `adminLessonImportSchema`. |
| `src/lib/admin-nav.ts` (modify) | Add the Lessons entry. |
| `src/app/api/admin/lessons/[topicId]/route.ts` (create) | `GET` — current lesson state for the pre-save comparison. |
| `src/app/api/admin/lessons/import/route.ts` (create) | `POST` — re-parse, lint, write, audit, revalidate. |
| `src/app/admin/lessons/page.tsx` (create) | Server component: subjects → topics with authored/placeholder markers. |
| `src/app/admin/lessons/upload/page.tsx` (create) | Server component: loads the subject/topic tree, renders the client form. |
| `src/components/admin/lesson-upload-form.tsx` (create) | `"use client"` — selectors, file read, preview, confirm, submit. |
| `scripts/test-lesson-markdown.mts` (create) | Parser + sanitiser tests. |
| `scripts/test-admin-lesson.mts` (create) | `buildLessonUpdate` tests. |
| `package.json` (modify) | Register both test files. |

Tasks 1–4 build the parser bottom-up; each leaves `src/lib/lesson-markdown.ts` in a working, tested state. Task 5 is the server boundary. Tasks 6–7 are the UI.

---

### Task 1: Parser skeleton — frontmatter, headings, concept blocks

**Files:**
- Create: `src/lib/lesson-markdown.ts`
- Create: `scripts/test-lesson-markdown.mts`
- Modify: `package.json:11` (the `test` script)

**Interfaces:**
- Consumes: `LessonBlock`, `ConceptBlock` from `src/lib/lesson-engine.ts`
- Produces:
  ```ts
  export type Issue = { line?: number; message: string };
  export type LessonDifficulty = "BASIC" | "INTERMEDIATE" | "ADVANCED";
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
  export function parseLessonMarkdown(source: string): ParsedLesson;
  export function slugify(text: string): string;
  ```

- [ ] **Step 1: Write the failing test**

Create `scripts/test-lesson-markdown.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseLessonMarkdown } from "../src/lib/lesson-markdown";
import type { ConceptBlock } from "../src/lib/lesson-engine";

test("a bare heading and paragraph become one concept block", () => {
  const result = parseLessonMarkdown(
    "## What is photosynthesis?\n\nGreen plants use light energy.",
  );
  assert.deepEqual(result.errors, []);
  assert.equal(result.blocks.length, 1);
  const block = result.blocks[0] as ConceptBlock;
  assert.equal(block.type, "concept");
  assert.equal(block.title, "What is photosynthesis?");
  assert.equal(block.text, "Green plants use light energy.");
  assert.equal(block.id, "what-is-photosynthesis-1");
});

test("frontmatter is parsed and stripped from the body", () => {
  const result = parseLessonMarkdown(
    [
      "---",
      "title: Photosynthesis",
      "summary: How plants make food.",
      "estimatedMinutes: 25",
      "difficulty: INTERMEDIATE",
      "subject: biology",
      "topic: photosynthesis",
      "---",
      "",
      "## Overview",
      "",
      "Body text.",
    ].join("\n"),
  );
  assert.equal(result.meta.title, "Photosynthesis");
  assert.equal(result.meta.summary, "How plants make food.");
  assert.equal(result.meta.estimatedMinutes, 25);
  assert.equal(result.meta.difficulty, "INTERMEDIATE");
  assert.equal(result.meta.subject, "biology");
  assert.equal(result.meta.topic, "photosynthesis");
  assert.equal(result.blocks.length, 1);
  assert.equal((result.blocks[0] as ConceptBlock).title, "Overview");
});

test("a file with no frontmatter parses fine", () => {
  const result = parseLessonMarkdown("## A\n\nText.");
  assert.deepEqual(result.meta, {});
  assert.deepEqual(result.errors, []);
});

test("an unknown frontmatter key warns but does not fail", () => {
  const result = parseLessonMarkdown(
    "---\ntitle: A\nkeyPoints: nope\n---\n\n## A\n\nText.",
  );
  assert.deepEqual(result.errors, []);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0].message, /keyPoints/);
});

test("a non-numeric estimatedMinutes is an error, not a silent zero", () => {
  const result = parseLessonMarkdown(
    "---\nestimatedMinutes: soon\n---\n\n## A\n\nText.",
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /estimatedMinutes/);
});

test("an out-of-range difficulty is an error", () => {
  const result = parseLessonMarkdown(
    "---\ndifficulty: EASY\n---\n\n## A\n\nText.",
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /difficulty/);
});

test("a single-hash heading supplies the title when frontmatter omits it", () => {
  const result = parseLessonMarkdown("# Photosynthesis\n\n## Overview\n\nText.");
  assert.equal(result.meta.title, "Photosynthesis");
});

test("frontmatter title wins over a single-hash heading", () => {
  const result = parseLessonMarkdown(
    "---\ntitle: From frontmatter\n---\n\n# From heading\n\n## A\n\nText.",
  );
  assert.equal(result.meta.title, "From frontmatter");
});

test("### Reveal inside a concept becomes the reveal field", () => {
  const result = parseLessonMarkdown(
    "## Osmosis\n\nWater moves down a gradient.\n\n### Reveal\n\nThe membrane must be partially permeable.",
  );
  assert.equal(result.blocks.length, 1);
  const block = result.blocks[0] as ConceptBlock;
  assert.equal(block.text, "Water moves down a gradient.");
  assert.equal(block.reveal, "The membrane must be partially permeable.");
});

test("multiple headings produce blocks in document order with unique ids", () => {
  const result = parseLessonMarkdown("## First\n\nA.\n\n## Second\n\nB.");
  assert.equal(result.blocks.length, 2);
  assert.equal(result.blocks[0].id, "first-1");
  assert.equal(result.blocks[1].id, "second-1");
});

test("two headings with the same text get distinct ids", () => {
  const result = parseLessonMarkdown("## Same\n\nA.\n\n## Same\n\nB.");
  assert.notEqual(result.blocks[0].id, result.blocks[1].id);
});

test("an empty document is an error rather than an empty success", () => {
  const result = parseLessonMarkdown("   \n\n  ");
  assert.equal(result.blocks.length, 0);
  assert.equal(result.errors.length, 1);
});
```

- [ ] **Step 2: Register the test file and run it to verify it fails**

Append `scripts/test-lesson-markdown.mts` to the space-separated list at the end of the `test` script in `package.json:11`.

Run: `npm test -- --test-name-pattern="concept block"`
Expected: FAIL — `Cannot find module '../src/lib/lesson-markdown'`

- [ ] **Step 3: Write the implementation**

Create `src/lib/lesson-markdown.ts`:

```ts
import type { LessonBlock, ConceptBlock } from "@/lib/lesson-engine";

// Pure markdown → LessonBlock[] parser for admin lesson-note upload.
// See docs/superpowers/specs/2026-08-05-lesson-note-upload-design.md.
//
// Deliberately has no Prisma, React or next/* imports: it runs both in the
// browser (upload preview) and in a route handler (the authoritative parse).

export type Issue = { line?: number; message: string };

export type LessonDifficulty = "BASIC" | "INTERMEDIATE" | "ADVANCED";

const DIFFICULTIES: readonly LessonDifficulty[] = [
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

const TEXT_KEYS = ["title", "summary", "subject", "topic"] as const;
const NUMBER_KEYS = [
  "estimatedMinutes",
  "passMarkPercent",
  "practiceCount",
] as const;

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
function makeIdFactory() {
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

type Frontmatter = {
  meta: LessonMeta;
  bodyLines: string[];
  bodyOffset: number;
  warnings: Issue[];
  errors: Issue[];
};

function parseFrontmatter(lines: string[]): Frontmatter {
  const meta: LessonMeta = {};
  const warnings: Issue[] = [];
  const errors: Issue[] = [];

  if (lines[0]?.trim() !== "---") {
    return { meta, bodyLines: lines, bodyOffset: 0, warnings, errors };
  }

  const close = lines.findIndex((line, i) => i > 0 && line.trim() === "---");
  if (close === -1) {
    errors.push({ line: 1, message: "Frontmatter opened with --- but never closed." });
    return { meta, bodyLines: lines, bodyOffset: 0, warnings, errors };
  }

  for (let i = 1; i < close; i++) {
    const raw = lines[i];
    if (!raw.trim()) continue;
    const sep = raw.indexOf(":");
    if (sep === -1) {
      errors.push({ line: i + 1, message: `Frontmatter line "${raw.trim()}" is not "key: value".` });
      continue;
    }
    const key = raw.slice(0, sep).trim();
    const value = raw.slice(sep + 1).trim();

    if ((TEXT_KEYS as readonly string[]).includes(key)) {
      meta[key as (typeof TEXT_KEYS)[number]] = value;
      continue;
    }
    if ((NUMBER_KEYS as readonly string[]).includes(key)) {
      const num = Number(value);
      if (!Number.isFinite(num) || num <= 0) {
        errors.push({ line: i + 1, message: `${key} must be a positive number, got "${value}".` });
        continue;
      }
      meta[key as (typeof NUMBER_KEYS)[number]] = Math.round(num);
      continue;
    }
    if (key === "difficulty") {
      if (!(DIFFICULTIES as readonly string[]).includes(value)) {
        errors.push({
          line: i + 1,
          message: `difficulty must be one of ${DIFFICULTIES.join(", ")}, got "${value}".`,
        });
        continue;
      }
      meta.difficulty = value as LessonDifficulty;
      continue;
    }
    warnings.push({ line: i + 1, message: `Unknown frontmatter key "${key}" — ignored.` });
  }

  return {
    meta,
    bodyLines: lines.slice(close + 1),
    bodyOffset: close + 1,
    warnings,
    errors,
  };
}

/** A heading section, before it is turned into one or more concept blocks. */
type Section = { title?: string; text: string; reveal?: string; line: number };

export function parseLessonMarkdown(source: string): ParsedLesson {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const front = parseFrontmatter(lines);
  const warnings = [...front.warnings];
  const errors = [...front.errors];
  const meta = { ...front.meta };

  const nextId = makeIdFactory();
  const blocks: LessonBlock[] = [];

  let section: Section | null = null;
  let buffer: string[] = [];
  let inReveal = false;

  function flush() {
    if (!section) {
      buffer = [];
      inReveal = false;
      return;
    }
    const text = buffer.join("\n").trim();
    if (inReveal) section.reveal = text;
    else section.text = text;
    buffer = [];

    if (!section.text) {
      section = null;
      inReveal = false;
      return;
    }
    const block: ConceptBlock = {
      type: "concept",
      id: nextId(slugify(section.title ?? "concept")),
      title: section.title,
      text: section.text,
      reveal: section.reveal || undefined,
    };
    blocks.push(block);
    section = null;
    inReveal = false;
  }

  for (let i = 0; i < front.bodyLines.length; i++) {
    const line = front.bodyLines[i];
    const lineNo = front.bodyOffset + i + 1;

    const h1 = /^#\s+(.*)$/.exec(line);
    if (h1) {
      flush();
      if (!meta.title) meta.title = h1[1].trim();
      continue;
    }

    const h2 = /^##\s+(.*)$/.exec(line);
    if (h2) {
      flush();
      section = { title: h2[1].trim(), text: "", line: lineNo };
      continue;
    }

    const h3 = /^###\s+(.*)$/.exec(line);
    if (h3) {
      if (section && /^reveal$/i.test(h3[1].trim())) {
        section.text = buffer.join("\n").trim();
        buffer = [];
        inReveal = true;
        continue;
      }
      // Any other h3 is prose inside the section, not a new block.
      buffer.push(line);
      continue;
    }

    if (!section && line.trim()) {
      // Prose before any heading still deserves a card.
      section = { title: undefined, text: "", line: lineNo };
    }
    buffer.push(line);
  }
  flush();

  if (blocks.length === 0 && errors.length === 0) {
    errors.push({ message: "This file has no lesson content." });
  }

  return { meta, blocks, warnings, errors };
}
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `npm test -- --test-name-pattern="concept|frontmatter|heading|Reveal|document"`
Expected: PASS, 12 tests.

- [ ] **Step 5: Typecheck and lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/lib/lesson-markdown.ts scripts/test-lesson-markdown.mts package.json
git commit -m "feat(lessons): markdown parser skeleton — frontmatter and concept blocks"
```

---

### Task 2: Fenced blocks — example, tip, mistake, mnemonic, check

**Files:**
- Modify: `src/lib/lesson-markdown.ts`
- Modify: `scripts/test-lesson-markdown.mts`

**Interfaces:**
- Consumes: `parseLessonMarkdown`, `slugify` and the id factory from Task 1; `ExampleBlock`, `TipBlock`, `MistakeBlock`, `MnemonicBlock`, `CheckBlock`, `EXAM_TYPES` from `src/lib/lesson-engine.ts`
- Produces: no new exports. `parseLessonMarkdown` now emits all six non-diagram block types.

Fence grammar, for reference while implementing:

| Fence | Labels | Repeating |
|---|---|---|
| `:::example` | `Problem:`, `Step:`, `Answer:`, `Mode:`, `Title:` | `Step:` |
| `:::tip` | `Exam:` + unlabelled prose | — |
| `:::mistake` | `Wrong:`, `Right:` | — |
| `:::mnemonic` | `Phrase:`, `Encoded:` | `Encoded:` |
| `:::check` | `Q:`, `A)`–`H)`, `Correct:`, `Why:`, `After:` | the letter options |

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-lesson-markdown.mts`:

```ts
import type {
  ExampleBlock,
  TipBlock,
  MistakeBlock,
  MnemonicBlock,
  CheckBlock,
} from "../src/lib/lesson-engine";

const LEAD = "## Forces\n\nA force is a push or a pull.\n\n";

test("an example fence becomes an ExampleBlock with ordered steps", () => {
  const result = parseLessonMarkdown(
    LEAD +
      [
        ":::example",
        "Problem: A 4 kg mass is pushed with 20 N. Find the acceleration.",
        "Step: Write F = ma.",
        "Step: Substitute F = 20, m = 4.",
        "Answer: 5 m/s²",
        "Mode: worked",
        ":::",
      ].join("\n"),
  );
  assert.deepEqual(result.errors, []);
  const block = result.blocks[1] as ExampleBlock;
  assert.equal(block.type, "example");
  assert.equal(block.problem, "A 4 kg mass is pushed with 20 N. Find the acceleration.");
  assert.deepEqual(block.steps, ["Write F = ma.", "Substitute F = 20, m = 4."]);
  assert.equal(block.answer, "5 m/s²");
  assert.equal(block.mode, "worked");
});

test("an example with no Mode defaults to worked", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::example\nProblem: P.\nStep: S.\nAnswer: A.\n:::",
  );
  assert.equal((result.blocks[1] as ExampleBlock).mode, "worked");
});

test("an unlabelled line continues the previous field", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::example\nProblem: Line one\nline two.\nStep: S.\nAnswer: A.\n:::",
  );
  assert.equal((result.blocks[1] as ExampleBlock).problem, "Line one\nline two.");
});

test("a tip fence carries prose and an optional exam tag", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::tip\nExam: WAEC\nCheck the units before choosing a formula.\n:::",
  );
  const block = result.blocks[1] as TipBlock;
  assert.equal(block.type, "tip");
  assert.equal(block.text, "Check the units before choosing a formula.");
  assert.equal(block.examType, "WAEC");
});

test("an unrecognised exam tag warns and is dropped rather than stored invalid", () => {
  const result = parseLessonMarkdown(LEAD + ":::tip\nExam: GCSE\nSome advice.\n:::");
  assert.deepEqual(result.errors, []);
  assert.equal((result.blocks[1] as TipBlock).examType, undefined);
  assert.equal(result.warnings.length, 1);
});

test("a mistake fence carries wrong and right", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::mistake\nWrong: Adding opposing forces.\nRight: Subtract them, then apply F = ma.\n:::",
  );
  const block = result.blocks[1] as MistakeBlock;
  assert.equal(block.wrong, "Adding opposing forces.");
  assert.equal(block.right, "Subtract them, then apply F = ma.");
});

test("a mnemonic fence keeps encoded lines in order", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::mnemonic\nPhrase: My Very Easy Method\nEncoded: Mercury\nEncoded: Venus\n:::",
  );
  const block = result.blocks[1] as MnemonicBlock;
  assert.equal(block.phrase, "My Very Easy Method");
  assert.deepEqual(block.encoded, ["Mercury", "Venus"]);
});

test("a check fence becomes a CheckBlock with lettered options", () => {
  const result = parseLessonMarkdown(
    LEAD +
      [
        ":::check",
        "Q: What is the SI unit of force?",
        "A) Joule",
        "B) Newton",
        "C) Watt",
        "Correct: B",
        "Why: Force is measured in newtons.",
        ":::",
      ].join("\n"),
  );
  assert.deepEqual(result.errors, []);
  const block = result.blocks[1] as CheckBlock;
  assert.equal(block.type, "check");
  assert.equal(block.question, "What is the SI unit of force?");
  assert.deepEqual(block.options, { A: "Joule", B: "Newton", C: "Watt" });
  assert.equal(block.answer, "B");
  assert.equal(block.explanation, "Force is measured in newtons.");
});

test("a check attaches to the preceding non-check block by default", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::check\nQ: Q?\nA) One\nB) Two\nCorrect: A\nWhy: Because.\n:::",
  );
  assert.equal((result.blocks[1] as CheckBlock).afterCard, result.blocks[0].id);
});

test("an explicit After: overrides the implicit attachment", () => {
  const result = parseLessonMarkdown(
    "## First\n\nA.\n\n## Second\n\nB.\n\n" +
      ":::check\nQ: Q?\nA) One\nB) Two\nCorrect: A\nWhy: Because.\nAfter: first-1\n:::",
  );
  assert.equal((result.blocks[2] as CheckBlock).afterCard, "first-1");
});

test("a check whose Correct names no option is an error", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::check\nQ: Q?\nA) One\nB) Two\nCorrect: D\nWhy: Because.\n:::",
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /Correct/);
});

test("a check with fewer than two options is an error", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::check\nQ: Q?\nA) Only one\nCorrect: A\nWhy: Because.\n:::",
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /two/i);
});

test("an unclosed fence is an error naming the line it opened on", () => {
  const result = parseLessonMarkdown(LEAD + ":::example\nProblem: P.");
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /never closed/i);
  assert.equal(result.errors[0].line, 5);
});

test("an unknown fence type is an error", () => {
  const result = parseLessonMarkdown(LEAD + ":::video\nsrc: x\n:::");
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /video/);
});

test("a repeated single-value label is an error", () => {
  const result = parseLessonMarkdown(
    LEAD + ":::mistake\nWrong: A.\nWrong: B.\nRight: C.\n:::",
  );
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /Wrong/);
});

test("a fence interrupts the concept section it sits inside", () => {
  const result = parseLessonMarkdown(
    "## Forces\n\nProse before.\n\n:::tip\nAdvice.\n:::\n\nProse after.",
  );
  assert.equal(result.blocks.length, 3);
  assert.equal(result.blocks[0].type, "concept");
  assert.equal(result.blocks[1].type, "tip");
  assert.equal(result.blocks[2].type, "concept");
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm test -- --test-name-pattern="fence|check|example|tip|mistake|mnemonic"`
Expected: FAIL — the parser treats `:::example` as prose, so `result.blocks[1]` is undefined.

- [ ] **Step 3: Implement fence parsing**

Add to `src/lib/lesson-markdown.ts`, above `parseLessonMarkdown`:

```ts
import {
  EXAM_TYPES,
  type ExampleBlock,
  type TipBlock,
  type MistakeBlock,
  type MnemonicBlock,
  type CheckBlock,
  type ExamTypeTag,
  type ExampleMode,
} from "@/lib/lesson-engine";

const FENCE_TYPES = [
  "example",
  "tip",
  "mistake",
  "mnemonic",
  "check",
  "diagram",
] as const;
type FenceType = (typeof FENCE_TYPES)[number];

/** One `Label: value` group inside a fence, in document order. */
type FenceFields = {
  singles: Map<string, string>;
  steps: string[];
  encoded: string[];
  hotspots: string[];
  options: Map<string, string>;
  prose: string[];
  raw: string[];
};

const SINGLE_LABELS = new Set([
  "problem", "answer", "mode", "title", "exam", "wrong", "right",
  "phrase", "q", "correct", "why", "after", "caption",
]);

/**
 * Short scalar fields that never wrap onto a second line. Without this, the
 * prose line after `Exam: WAEC` in a tip fence would be appended to the exam
 * tag instead of becoming the tip's text.
 */
const SCALAR_LABELS = new Set(["exam", "mode", "correct", "after"]);

/**
 * Splits a fence body into labelled fields. An unlabelled line appends to
 * whichever field was opened last, so authors can wrap prose naturally.
 */
function readFence(
  body: string[],
  openLine: number,
  errors: Issue[],
): FenceFields {
  const fields: FenceFields = {
    singles: new Map(),
    steps: [],
    encoded: [],
    hotspots: [],
    options: new Map(),
    prose: [],
    raw: body,
  };
  let last: { kind: "single" | "step" | "encoded" | "hotspot" | "option" | "prose"; key: string } =
    { kind: "prose", key: "" };

  body.forEach((line, i) => {
    const lineNo = openLine + i + 1;
    const option = /^([A-H])\)\s?(.*)$/.exec(line);
    if (option) {
      const [, letter, value] = option;
      if (fields.options.has(letter)) {
        errors.push({ line: lineNo, message: `Option ${letter}) appears twice in this check.` });
      }
      fields.options.set(letter, value.trim());
      last = { kind: "option", key: letter };
      return;
    }

    const labelled = /^([A-Za-z]+):\s?(.*)$/.exec(line);
    if (labelled) {
      const key = labelled[1].toLowerCase();
      const value = labelled[2].trim();
      if (key === "step") {
        fields.steps.push(value);
        last = { kind: "step", key: "" };
        return;
      }
      if (key === "encoded") {
        fields.encoded.push(value);
        last = { kind: "encoded", key: "" };
        return;
      }
      if (key === "hotspot") {
        fields.hotspots.push(value);
        last = { kind: "hotspot", key: "" };
        return;
      }
      if (SINGLE_LABELS.has(key)) {
        if (fields.singles.has(key)) {
          errors.push({
            line: lineNo,
            message: `"${labelled[1]}" appears more than once in this block.`,
          });
          return;
        }
        fields.singles.set(key, value);
        // A scalar label closes itself — the next unlabelled line is prose,
        // not a continuation of it.
        last = SCALAR_LABELS.has(key)
          ? { kind: "prose", key: "" }
          : { kind: "single", key };
        return;
      }
    }

    if (!line.trim()) return;

    // Markup never continues a labelled field — otherwise the <svg> lines in a
    // diagram fence get appended to whatever Caption: or Title: preceded them.
    if (line.trim().startsWith("<")) {
      fields.prose.push(line);
      last = { kind: "prose", key: "" };
      return;
    }

    // Unlabelled: continue whatever field was opened last.
    switch (last.kind) {
      case "single":
        fields.singles.set(last.key, `${fields.singles.get(last.key)}\n${line.trim()}`);
        return;
      case "step":
        fields.steps[fields.steps.length - 1] += `\n${line.trim()}`;
        return;
      case "encoded":
        fields.encoded[fields.encoded.length - 1] += `\n${line.trim()}`;
        return;
      case "hotspot":
        fields.hotspots[fields.hotspots.length - 1] += `\n${line.trim()}`;
        return;
      case "option":
        fields.options.set(last.key, `${fields.options.get(last.key)}\n${line.trim()}`);
        return;
      default:
        fields.prose.push(line.trim());
    }
  });

  return fields;
}

type FenceContext = {
  id: string;
  openLine: number;
  previousNonCheckId: string | null;
  warnings: Issue[];
  errors: Issue[];
};

function buildFenceBlock(
  type: FenceType,
  fields: FenceFields,
  ctx: FenceContext,
): LessonBlock | null {
  const { errors, warnings, openLine } = ctx;
  const get = (key: string) => fields.singles.get(key) ?? "";

  switch (type) {
    case "example": {
      const mode = get("mode").toLowerCase();
      const block: ExampleBlock = {
        type: "example",
        id: ctx.id,
        title: get("title") || undefined,
        problem: get("problem"),
        steps: fields.steps,
        answer: get("answer"),
        mode:
          mode === "partial" || mode === "solo"
            ? (mode as ExampleMode)
            : "worked",
      };
      if (!block.problem) {
        errors.push({ line: openLine, message: "An example needs a Problem: line." });
        return null;
      }
      if (!block.answer) {
        errors.push({ line: openLine, message: "An example needs an Answer: line." });
        return null;
      }
      return block;
    }
    case "tip": {
      // A tip is prose with an optional Exam: tag — its text is never labelled.
      const text = fields.prose.join("\n").trim();
      if (!text) {
        errors.push({ line: openLine, message: "A tip fence has no text." });
        return null;
      }
      const exam = get("exam").toUpperCase();
      let examType: ExamTypeTag | undefined;
      if (exam) {
        if ((EXAM_TYPES as readonly string[]).includes(exam)) {
          examType = exam as ExamTypeTag;
        } else {
          warnings.push({
            line: openLine,
            message: `Exam tag "${exam}" is not one of ${EXAM_TYPES.join(", ")} — dropped.`,
          });
        }
      }
      const block: TipBlock = { type: "tip", id: ctx.id, text, examType };
      return block;
    }
    case "mistake": {
      const block: MistakeBlock = {
        type: "mistake",
        id: ctx.id,
        wrong: get("wrong"),
        right: get("right"),
      };
      if (!block.wrong || !block.right) {
        errors.push({
          line: openLine,
          message: "A mistake fence needs both Wrong: and Right: lines.",
        });
        return null;
      }
      return block;
    }
    case "mnemonic": {
      const block: MnemonicBlock = {
        type: "mnemonic",
        id: ctx.id,
        phrase: get("phrase"),
        encoded: fields.encoded,
      };
      if (!block.phrase) {
        errors.push({ line: openLine, message: "A mnemonic fence needs a Phrase: line." });
        return null;
      }
      return block;
    }
    case "check": {
      const question = get("q");
      const options = Object.fromEntries(fields.options);
      const answer = get("correct").trim().toUpperCase();
      if (!question) {
        errors.push({ line: openLine, message: "A check needs a Q: line." });
        return null;
      }
      if (Object.keys(options).length < 2) {
        errors.push({ line: openLine, message: "A check needs at least two options." });
        return null;
      }
      if (!Object.prototype.hasOwnProperty.call(options, answer)) {
        errors.push({
          line: openLine,
          message: `Correct: "${answer}" is not one of this check's options.`,
        });
        return null;
      }
      const afterCard = get("after") || ctx.previousNonCheckId || "";
      if (!afterCard) {
        errors.push({
          line: openLine,
          message: "A check cannot be the first block — put it after a card.",
        });
        return null;
      }
      const block: CheckBlock = {
        type: "check",
        id: ctx.id,
        question,
        options,
        answer,
        explanation: get("why"),
        afterCard,
      };
      return block;
    }
    default:
      return null; // diagram is handled in Task 3
  }
}
```

Then wire it into the main loop in `parseLessonMarkdown`. Immediately after the `h3` branch and before the `if (!section && line.trim())` branch, insert:

```ts
    const fenceOpen = /^:::\s*([a-z]+)\s*$/i.exec(line.trim());
    if (fenceOpen) {
      flush();
      const type = fenceOpen[1].toLowerCase();
      const closeOffset = front.bodyLines
        .slice(i + 1)
        .findIndex((l) => l.trim() === ":::");
      if (closeOffset === -1) {
        errors.push({ line: lineNo, message: `Fence ":::${type}" was never closed.` });
        break;
      }
      const body = front.bodyLines.slice(i + 1, i + 1 + closeOffset);
      i += closeOffset + 1; // skip past the closing :::

      if (!(FENCE_TYPES as readonly string[]).includes(type)) {
        errors.push({ line: lineNo, message: `Unknown fence type ":::${type}".` });
        continue;
      }

      const fields = readFence(body, lineNo, errors);
      const previousNonCheckId =
        [...blocks].reverse().find((b) => b.type !== "check")?.id ?? null;
      const block = buildFenceBlock(type as FenceType, fields, {
        id: nextId(type),
        openLine: lineNo,
        previousNonCheckId,
        warnings,
        errors,
      });
      if (block) blocks.push(block);
      continue;
    }
```

Note the loop must use `let i` (it already does) so `i += closeOffset + 1` advances past the fence.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `npm test -- --test-name-pattern="fence|check|example|tip|mistake|mnemonic|attaches|option"`
Expected: PASS. All Task 1 tests still pass — run the whole file: `node --import tsx --test scripts/test-lesson-markdown.mts`

- [ ] **Step 5: Typecheck and lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/lib/lesson-markdown.ts scripts/test-lesson-markdown.mts
git commit -m "feat(lessons): parse example, tip, mistake, mnemonic and check fences"
```

---

### Task 3: SVG sanitiser and the diagram fence

**Files:**
- Modify: `src/lib/lesson-markdown.ts`
- Modify: `scripts/test-lesson-markdown.mts`

**Interfaces:**
- Consumes: `readFence`, `buildFenceBlock`, `FenceContext` from Task 2; `DiagramBlock`, `DiagramHotspot` from `src/lib/lesson-engine.ts`
- Produces:
  ```ts
  export function sanitizeSvg(svg: string): { svg: string; warnings: Issue[] };
  ```

**Why this matters:** `src/components/lesson/interactive-diagram.tsx:31` renders `block.svg` through `dangerouslySetInnerHTML`. Uploaded SVG is executable markup on every student's page. This is the security boundary of the whole feature — the tests below are not optional.

Hotspot line grammar: `Hotspot: <label> @ <x>,<y> — <text>`. The `@ x,y` part is optional. The separator is an em dash (`—`) or a double hyphen (`--`).

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-lesson-markdown.mts`:

```ts
import { sanitizeSvg } from "../src/lib/lesson-markdown";
import type { DiagramBlock } from "../src/lib/lesson-engine";

test("a plain svg survives sanitisation unchanged in substance", () => {
  const { svg, warnings } = sanitizeSvg(
    '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="red"/></svg>',
  );
  assert.match(svg, /<svg/);
  assert.match(svg, /viewBox="0 0 10 10"/);
  assert.match(svg, /<circle/);
  assert.match(svg, /fill="red"/);
  assert.deepEqual(warnings, []);
});

test("a script element is removed with its contents", () => {
  const { svg, warnings } = sanitizeSvg(
    '<svg><script>alert(1)</script><circle r="1"/></svg>',
  );
  assert.doesNotMatch(svg, /script/i);
  assert.doesNotMatch(svg, /alert/);
  assert.equal(warnings.length >= 1, true);
});

test("event handler attributes are stripped", () => {
  const { svg, warnings } = sanitizeSvg('<svg onload="steal()"><circle onclick="x()" r="1"/></svg>');
  assert.doesNotMatch(svg, /onload/i);
  assert.doesNotMatch(svg, /onclick/i);
  assert.doesNotMatch(svg, /steal/);
  assert.equal(warnings.length >= 2, true);
});

test("foreignObject is removed", () => {
  const { svg } = sanitizeSvg(
    "<svg><foreignObject><body><img src=x onerror=alert(1)></body></foreignObject></svg>",
  );
  assert.doesNotMatch(svg, /foreignObject/i);
  assert.doesNotMatch(svg, /onerror/i);
});

test("javascript: hrefs are stripped but fragment hrefs survive", () => {
  const bad = sanitizeSvg('<svg><a href="javascript:alert(1)"><circle r="1"/></a></svg>');
  assert.doesNotMatch(bad.svg, /javascript:/i);
  const ok = sanitizeSvg('<svg><path d="M0 0" fill="url(#grad)" id="p"/></svg>');
  assert.match(ok.svg, /id="p"/);
});

test("use and image elements are removed — they can pull in remote content", () => {
  const { svg } = sanitizeSvg(
    '<svg><use href="https://evil.test/x.svg#a"/><image href="https://evil.test/x.png"/><circle r="1"/></svg>',
  );
  assert.doesNotMatch(svg, /<use/i);
  assert.doesNotMatch(svg, /<image/i);
  assert.match(svg, /<circle/);
});

test("a style element is removed", () => {
  const { svg } = sanitizeSvg("<svg><style>* { background: url(evil) }</style><circle r=\"1\"/></svg>");
  assert.doesNotMatch(svg, /<style/i);
});

test("input with no svg root yields empty output and a warning", () => {
  const { svg, warnings } = sanitizeSvg("<div>not an svg</div>");
  assert.equal(svg, "");
  assert.equal(warnings.length, 1);
});

test("a diagram fence becomes a DiagramBlock with sanitised svg and hotspots", () => {
  const result = parseLessonMarkdown(
    "## The eye\n\nLight enters here.\n\n" +
      [
        ":::diagram",
        "Title: The human eye",
        "Caption: The path light takes.",
        '<svg viewBox="0 0 200 120"><circle cx="10" cy="10" r="5"/></svg>',
        "Hotspot: Cornea @ 20,50 — Bends incoming light.",
        "Hotspot: Retina — Where the image forms.",
        ":::",
      ].join("\n"),
  );
  assert.deepEqual(result.errors, []);
  const block = result.blocks[1] as DiagramBlock;
  assert.equal(block.type, "diagram");
  assert.equal(block.title, "The human eye");
  assert.equal(block.caption, "The path light takes.");
  assert.match(block.svg, /<svg/);
  assert.equal(block.hotspots.length, 2);
  assert.equal(block.hotspots[0].label, "Cornea");
  assert.equal(block.hotspots[0].text, "Bends incoming light.");
  assert.equal(block.hotspots[0].x, 20);
  assert.equal(block.hotspots[0].y, 50);
  assert.equal(block.hotspots[1].x, undefined);
});

test("a diagram whose svg is dangerous still parses, with warnings", () => {
  const result = parseLessonMarkdown(
    "## D\n\nText.\n\n:::diagram\n<svg onload=\"x()\"><script>y()</script><circle r=\"1\"/></svg>\n:::",
  );
  const block = result.blocks[1] as DiagramBlock;
  assert.doesNotMatch(block.svg, /onload/i);
  assert.doesNotMatch(block.svg, /script/i);
  assert.equal(result.warnings.length >= 2, true);
});

test("a diagram fence with no svg is an error", () => {
  const result = parseLessonMarkdown("## D\n\nText.\n\n:::diagram\nTitle: Nothing here\n:::");
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0].message, /svg/i);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm test -- --test-name-pattern="svg|diagram|handler|foreignObject"`
Expected: FAIL — `sanitizeSvg` is not exported.

- [ ] **Step 3: Implement the sanitiser and diagram fence**

Add to `src/lib/lesson-markdown.ts`:

```ts
import type { DiagramBlock, DiagramHotspot } from "@/lib/lesson-engine";

// ─── SVG sanitiser ───────────────────────────────────────────
//
// InteractiveDiagram renders block.svg through dangerouslySetInnerHTML, so
// uploaded markup executes in student pages. This is an allowlist: anything
// not named here is removed. Never convert it to a blocklist.

const SVG_ELEMENTS = new Set([
  "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline",
  "polygon", "text", "tspan", "defs", "marker", "lineargradient",
  "radialgradient", "stop", "title", "desc",
]);

/** Elements dropped along with everything inside them. */
const SVG_VOID_HOSTILE = new Set([
  "script", "style", "foreignobject", "use", "image", "iframe", "animate",
  "set", "handler",
]);

const SVG_ATTRS = new Set([
  "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
  "width", "height", "points", "transform", "viewbox", "preserveaspectratio",
  "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
  "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity",
  "font-size", "font-family", "font-weight", "text-anchor", "dominant-baseline",
  "offset", "stop-color", "stop-opacity", "gradientunits", "marker-end",
  "marker-start", "id", "class", "xmlns",
]);

export function sanitizeSvg(svg: string): { svg: string; warnings: Issue[] } {
  const warnings: Issue[] = [];
  const seen = new Set<string>();
  const warn = (message: string) => {
    if (seen.has(message)) return;
    seen.add(message);
    warnings.push({ message });
  };

  let out = svg;

  // 1. Drop hostile elements with their contents (and self-closing forms).
  for (const tag of SVG_VOID_HOSTILE) {
    const paired = new RegExp(`<${tag}\\b[\\s\\S]*?</${tag}\\s*>`, "gi");
    const selfClosing = new RegExp(`<${tag}\\b[^>]*/?>`, "gi");
    if (paired.test(out) || selfClosing.test(out)) {
      warn(`<${tag}> is not allowed in a diagram and was removed.`);
    }
    out = out.replace(paired, "").replace(selfClosing, "");
  }

  // 2. Walk remaining tags, dropping unknown elements and attributes.
  out = out.replace(/<\/?([a-zA-Z][a-zA-Z0-9-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g,
    (match, rawName: string, rawAttrs: string) => {
      const name = rawName.toLowerCase();
      if (!SVG_ELEMENTS.has(name)) {
        warn(`<${rawName}> is not an allowed diagram element and was removed.`);
        return "";
      }
      if (match.startsWith("</")) return `</${name}>`;

      const kept: string[] = [];
      const attrRe = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("[^"]*"|'[^']*')/g;
      let m: RegExpExecArray | null;
      while ((m = attrRe.exec(rawAttrs))) {
        const attr = m[1].toLowerCase();
        const value = m[2].slice(1, -1);
        if (attr.startsWith("on")) {
          warn(`Event handler "${m[1]}" was removed from <${name}>.`);
          continue;
        }
        if (attr === "href" || attr === "xlink:href") {
          if (value.startsWith("#")) {
            kept.push(`${attr}="${value}"`);
          } else {
            warn(`href "${value}" is not a same-document fragment and was removed.`);
          }
          continue;
        }
        if (attr.startsWith("aria-") || SVG_ATTRS.has(attr)) {
          kept.push(`${m[1]}="${value}"`);
          continue;
        }
        warn(`Attribute "${m[1]}" is not allowed on <${name}> and was removed.`);
      }
      const selfClose = /\/\s*$/.test(rawAttrs) ? "/" : "";
      return `<${name}${kept.length ? " " + kept.join(" ") : ""}${selfClose}>`;
    });

  if (!/<svg\b/i.test(out)) {
    return {
      svg: "",
      warnings: [{ message: "No <svg> element found in this diagram." }],
    };
  }
  return { svg: out.trim(), warnings };
}

/** `Cornea @ 20,50 — Bends incoming light.` → a hotspot. */
function parseHotspot(raw: string, index: number): DiagramHotspot {
  const [head, ...rest] = raw.split(/\s+—\s+|\s+--\s+/);
  const text = rest.join(" — ").trim();
  const coords = /@\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)/.exec(head);
  const label = head.replace(/@\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?/, "").trim();
  return {
    id: `${slugify(label || "hotspot")}-${index + 1}`,
    label: label || "Part",
    text,
    x: coords ? Number(coords[1]) : undefined,
    y: coords ? Number(coords[2]) : undefined,
  };
}
```

In `buildFenceBlock`, replace the `default: return null; // diagram is handled in Task 3` arm with:

```ts
    case "diagram": {
      // Everything that was not a recognised label is the SVG source.
      const rawSvg = fields.raw
        .filter((line) => !/^([A-Za-z]+):/.test(line.trim()))
        .join("\n")
        .trim();
      const { svg, warnings: svgWarnings } = sanitizeSvg(rawSvg);
      if (!svg) {
        errors.push({ line: openLine, message: "A diagram fence needs an inline <svg> element." });
        return null;
      }
      svgWarnings.forEach((w) => warnings.push({ line: openLine, message: w.message }));
      const block: DiagramBlock = {
        type: "diagram",
        id: ctx.id,
        title: get("title") || undefined,
        caption: get("caption") || undefined,
        svg,
        hotspots: fields.hotspots.map(parseHotspot),
      };
      return block;
    }
    default:
      return null;
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `node --import tsx --test scripts/test-lesson-markdown.mts`
Expected: PASS — all Task 1, 2 and 3 tests.

- [ ] **Step 5: Typecheck and lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/lib/lesson-markdown.ts scripts/test-lesson-markdown.mts
git commit -m "feat(lessons): inline SVG diagram fence with allowlist sanitiser"
```

---

### Task 4: Auto-split at the card cap, and lint integration

**Files:**
- Modify: `src/lib/lesson-markdown.ts`
- Modify: `scripts/test-lesson-markdown.mts`

**Interfaces:**
- Consumes: `MAX_CARD_WORDS`, `wordCount`, `lintLessonBlocks` from `src/lib/lesson-engine.ts`
- Produces:
  ```ts
  export function validateLessonMarkdown(source: string): ParsedLesson;
  ```
  `validateLessonMarkdown` is what Tasks 5–7 call. `parseLessonMarkdown` stays exported for tests.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-lesson-markdown.mts`:

```ts
import { validateLessonMarkdown } from "../src/lib/lesson-markdown";
import { MAX_CARD_WORDS, blockWordCount } from "../src/lib/lesson-engine";

const WORD = "word ";

test("a concept section over the card cap splits at paragraph boundaries", () => {
  const para = WORD.repeat(80).trim();
  const result = parseLessonMarkdown(`## Long\n\n${para}\n\n${para}`);
  assert.equal(result.blocks.length, 2);
  assert.equal(result.blocks[0].type, "concept");
  assert.equal(result.blocks[1].type, "concept");
  for (const block of result.blocks) {
    assert.ok(blockWordCount(block) <= MAX_CARD_WORDS);
  }
});

test("the split keeps the heading on the first card only", () => {
  const para = WORD.repeat(80).trim();
  const result = parseLessonMarkdown(`## Long\n\n${para}\n\n${para}`);
  assert.equal((result.blocks[0] as { title?: string }).title, "Long");
  assert.equal((result.blocks[1] as { title?: string }).title, undefined);
});

test("a split emits a warning naming the heading and the card count", () => {
  const para = WORD.repeat(80).trim();
  const result = parseLessonMarkdown(`## Long\n\n${para}\n\n${para}`);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0].message, /Long/);
  assert.match(result.warnings[0].message, /2/);
});

test("a section under the cap is never split", () => {
  const result = parseLessonMarkdown("## Short\n\nOne.\n\nTwo.\n\nThree.");
  assert.equal(result.blocks.length, 1);
});

test("one unsplittable over-long paragraph stays whole and fails the lint", () => {
  const giant = WORD.repeat(MAX_CARD_WORDS + 40).trim();
  const result = validateLessonMarkdown(`## Giant\n\n${giant}`);
  assert.equal(result.blocks.length, 1);
  assert.ok(result.errors.some((e) => /words/.test(e.message)));
});

test("validateLessonMarkdown merges lint issues into errors", () => {
  // Valid syntax, but no knowledge check — a lint rule, not a parse rule.
  const result = validateLessonMarkdown("## A\n\nSome text.");
  assert.deepEqual(parseLessonMarkdown("## A\n\nSome text.").errors, []);
  assert.ok(result.errors.some((e) => /knowledge check/i.test(e.message)));
});

test("a complete, well-formed lesson validates with no errors", () => {
  const source = [
    "---",
    "title: Newton's First Law",
    "estimatedMinutes: 20",
    "---",
    "",
    "## What the law says",
    "",
    "An object stays at rest or in uniform motion unless a net force acts on it.",
    "",
    ":::example",
    "Problem: A book rests on a table. Why does it not move?",
    "Step: Identify the forces: weight down, normal force up.",
    "Step: They are equal and opposite, so the net force is zero.",
    "Answer: With zero net force, the book stays at rest.",
    ":::",
    "",
    ":::tip",
    "Exam: WAEC",
    "Say 'net force', not 'force' — the distinction earns the mark.",
    ":::",
    "",
    ":::check",
    "Q: A car moves at constant velocity. What is the net force on it?",
    "A) Zero",
    "B) Equal to its weight",
    "C) Equal to its momentum",
    "Correct: A",
    "Why: Constant velocity means no acceleration, so no net force.",
    ":::",
  ].join("\n");
  const result = validateLessonMarkdown(source);
  assert.deepEqual(result.errors, []);
  assert.equal(result.blocks.length, 4);
  assert.equal(result.meta.title, "Newton's First Law");
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm test -- --test-name-pattern="split|cap|validateLessonMarkdown|well-formed"`
Expected: FAIL — `validateLessonMarkdown` is not exported and long sections emit one oversized block.

- [ ] **Step 3: Implement splitting and the validate wrapper**

In `src/lib/lesson-markdown.ts`, extend the imports:

```ts
import {
  MAX_CARD_WORDS,
  blockWordCount,
  lintLessonBlocks,
  wordCount,
} from "@/lib/lesson-engine";
```

Replace the block-emitting tail of `flush()` (everything from `const block: ConceptBlock = {` to `blocks.push(block);`) with a call to a new helper, and add the helper above `parseLessonMarkdown`:

```ts
/**
 * Emits a section as one concept card, or several split at paragraph
 * boundaries when it exceeds MAX_CARD_WORDS. The heading rides the first
 * card; the notes view renders consecutive concepts as continuous prose, so
 * a split is invisible there and only shapes the card player.
 */
function emitConcept(
  section: Section,
  nextId: (slug: string) => string,
  blocks: LessonBlock[],
  warnings: Issue[],
): void {
  const slug = slugify(section.title ?? "concept");
  const whole: ConceptBlock = {
    type: "concept",
    id: nextId(slug),
    title: section.title,
    text: section.text,
    reveal: section.reveal || undefined,
  };

  if (blockWordCount(whole) <= MAX_CARD_WORDS) {
    blocks.push(whole);
    return;
  }

  const paragraphs = section.text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  if (paragraphs.length < 2) {
    // Nothing to split on. Keep it whole — the lint will reject it, which is
    // the honest outcome: splitting mid-sentence would be worse.
    blocks.push(whole);
    return;
  }

  const cards: string[] = [];
  let current: string[] = [];
  let running = 0;
  for (const paragraph of paragraphs) {
    const words = wordCount(paragraph);
    if (current.length > 0 && running + words > MAX_CARD_WORDS) {
      cards.push(current.join("\n\n"));
      current = [];
      running = 0;
    }
    current.push(paragraph);
    running += words;
  }
  if (current.length > 0) cards.push(current.join("\n\n"));

  // The id minted for `whole` is already claimed; reuse it for the first card.
  cards.forEach((text, index) => {
    blocks.push({
      type: "concept",
      id: index === 0 ? whole.id : nextId(slug),
      title: index === 0 ? section.title : undefined,
      text,
      // The reveal belongs with the last card, where the idea completes.
      reveal: index === cards.length - 1 ? section.reveal || undefined : undefined,
    });
  });

  warnings.push({
    line: section.line,
    message: `"${section.title ?? "Untitled section"}" is longer than ${MAX_CARD_WORDS} words and was split into ${cards.length} cards.`,
  });
}
```

The `flush()` body becomes:

```ts
    emitConcept(section, nextId, blocks, warnings);
    section = null;
    inReveal = false;
```

Then append the public wrapper at the end of the file:

```ts
/**
 * Parse plus the lesson-engine authoring lint. This is what the admin form
 * and the import route call — the parser owns syntax, the lint owns pedagogy
 * (card length, at least one concept, at least one check, afterCard targets).
 */
export function validateLessonMarkdown(source: string): ParsedLesson {
  const parsed = parseLessonMarkdown(source);
  if (parsed.blocks.length === 0) return parsed;
  const lintIssues = lintLessonBlocks(parsed.blocks).map((issue) => ({
    message: issue.blockId ? `${issue.blockId}: ${issue.message}` : issue.message,
  }));
  return { ...parsed, errors: [...parsed.errors, ...lintIssues] };
}
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `node --import tsx --test scripts/test-lesson-markdown.mts`
Expected: PASS — the whole file.

- [ ] **Step 5: Typecheck and lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/lib/lesson-markdown.ts scripts/test-lesson-markdown.mts
git commit -m "feat(lessons): auto-split over-long sections and merge the authoring lint"
```

---

### Task 5: Server boundary — update builder, zod schema, route handlers

**Files:**
- Create: `src/lib/admin-lesson.ts`
- Create: `scripts/test-admin-lesson.mts`
- Create: `src/app/api/admin/lessons/import/route.ts`
- Create: `src/app/api/admin/lessons/[topicId]/route.ts`
- Modify: `src/lib/validators.ts` (append near `bulkImportSchema`, around line 225)
- Modify: `package.json:11`

**Interfaces:**
- Consumes: `validateLessonMarkdown`, `ParsedLesson` (Task 4); `requireAdmin` from `src/lib/admin-guard.ts`; `recordAudit` from `src/lib/admin-audit.ts`; `CATALOGUE_TAG` from `src/lib/catalogue.ts`; `db` from `src/lib/db.ts`
- Produces:
  ```ts
  // src/lib/admin-lesson.ts
  export const SYSTEM_AUTHOR = "system";
  export type LessonUpdateData = {
    blocks: LessonBlock[];
    content: string;
    createdBy: string;
    title?: string;
    summary?: string;
    estimatedMinutes?: number;
    difficulty?: LessonDifficulty;
    passMarkPercent?: number;
    practiceCount?: number;
  };
  export function buildLessonUpdate(
    parsed: ParsedLesson,
    markdown: string,
    adminId: string,
  ): LessonUpdateData;
  export function isAuthored(createdBy: string | null): boolean;

  // src/lib/validators.ts
  export const adminLessonImportSchema: z.ZodType<{
    topicId: string; markdown: string; confirm: true;
  }>;
  ```

**Read first:** `node_modules/next/dist/docs/` on route handlers and dynamic route params — param handling changed in this Next version, and `src/app/api/admin/questions/[id]/route.ts` shows the shape this codebase uses.

- [ ] **Step 1: Write the failing tests**

Create `scripts/test-admin-lesson.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildLessonUpdate, isAuthored, SYSTEM_AUTHOR } from "../src/lib/admin-lesson";
import { validateLessonMarkdown } from "../src/lib/lesson-markdown";

const GOOD = [
  "## What the law says",
  "",
  "An object stays at rest unless a net force acts on it.",
  "",
  ":::check",
  "Q: What is the net force on a car at constant velocity?",
  "A) Zero",
  "B) Its weight",
  "Correct: A",
  "Why: No acceleration means no net force.",
  ":::",
].join("\n");

test("the update carries blocks, the raw markdown, and the admin as author", () => {
  const parsed = validateLessonMarkdown(GOOD);
  const update = buildLessonUpdate(parsed, GOOD, "admin-123");
  assert.equal(update.blocks.length, 2);
  assert.equal(update.content, GOOD);
  assert.equal(update.createdBy, "admin-123");
});

test("frontmatter keys that are absent are omitted, not written as undefined", () => {
  const parsed = validateLessonMarkdown(GOOD);
  const update = buildLessonUpdate(parsed, GOOD, "admin-123");
  assert.equal("title" in update, false);
  assert.equal("estimatedMinutes" in update, false);
  assert.equal("difficulty" in update, false);
});

test("frontmatter keys that are present are written", () => {
  const source = `---\ntitle: Newton I\nestimatedMinutes: 30\ndifficulty: BASIC\npassMarkPercent: 70\npracticeCount: 5\nsummary: A summary.\n---\n\n${GOOD}`;
  const parsed = validateLessonMarkdown(source);
  const update = buildLessonUpdate(parsed, source, "admin-123");
  assert.equal(update.title, "Newton I");
  assert.equal(update.summary, "A summary.");
  assert.equal(update.estimatedMinutes, 30);
  assert.equal(update.difficulty, "BASIC");
  assert.equal(update.passMarkPercent, 70);
  assert.equal(update.practiceCount, 5);
});

test("subject and topic routing keys never reach the update", () => {
  const source = `---\nsubject: physics\ntopic: newtons-laws\n---\n\n${GOOD}`;
  const parsed = validateLessonMarkdown(source);
  const update = buildLessonUpdate(parsed, source, "admin-123");
  assert.equal("subject" in update, false);
  assert.equal("topic" in update, false);
});

test("seeded lessons are not authored; uploaded ones are", () => {
  assert.equal(isAuthored(SYSTEM_AUTHOR), false);
  assert.equal(isAuthored(null), false);
  assert.equal(isAuthored("admin-123"), true);
});
```

- [ ] **Step 2: Register and run to verify failure**

Append `scripts/test-admin-lesson.mts` to the `test` script in `package.json:11`.

Run: `node --import tsx --test scripts/test-admin-lesson.mts`
Expected: FAIL — `Cannot find module '../src/lib/admin-lesson'`

- [ ] **Step 3: Write `src/lib/admin-lesson.ts`**

```ts
import type { LessonBlock } from "@/lib/lesson-engine";
import type { LessonDifficulty, ParsedLesson } from "@/lib/lesson-markdown";

// Shapes a parsed markdown lesson into a Prisma update payload. Pure — kept
// out of the route handler so it can be tested without a database.

/** What seedLessons() writes to Lesson.createdBy (src/lib/lessons.ts:205). */
export const SYSTEM_AUTHOR = "system";

export type LessonUpdateData = {
  blocks: LessonBlock[];
  content: string;
  createdBy: string;
  title?: string;
  summary?: string;
  estimatedMinutes?: number;
  difficulty?: LessonDifficulty;
  passMarkPercent?: number;
  practiceCount?: number;
};

/** A lesson is authored once an upload has stamped a real admin id on it. */
export function isAuthored(createdBy: string | null): boolean {
  return Boolean(createdBy) && createdBy !== SYSTEM_AUTHOR;
}

export function buildLessonUpdate(
  parsed: ParsedLesson,
  markdown: string,
  adminId: string,
): LessonUpdateData {
  const { meta } = parsed;
  const update: LessonUpdateData = {
    blocks: parsed.blocks,
    content: markdown,
    createdBy: adminId,
  };

  // Only keys the author actually supplied are written — an omitted key must
  // leave the lesson's current value alone, not overwrite it with a default.
  if (meta.title !== undefined) update.title = meta.title;
  if (meta.summary !== undefined) update.summary = meta.summary;
  if (meta.estimatedMinutes !== undefined) update.estimatedMinutes = meta.estimatedMinutes;
  if (meta.difficulty !== undefined) update.difficulty = meta.difficulty;
  if (meta.passMarkPercent !== undefined) update.passMarkPercent = meta.passMarkPercent;
  if (meta.practiceCount !== undefined) update.practiceCount = meta.practiceCount;

  // meta.subject and meta.topic are routing hints for bulk upload. They
  // deliberately never reach the database.
  return update;
}
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `node --import tsx --test scripts/test-admin-lesson.mts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Add the zod schema**

Append to `src/lib/validators.ts`, after `bulkImportSchema`:

```ts
export const MAX_LESSON_MARKDOWN_BYTES = 200_000;

export const adminLessonImportSchema = z.object({
  topicId: z.string().min(1, "A topic is required"),
  markdown: z
    .string()
    .min(1, "The file is empty")
    .max(MAX_LESSON_MARKDOWN_BYTES, "That file is too large to import"),
  confirm: z.literal(true),
});

export type AdminLessonImportInput = z.infer<typeof adminLessonImportSchema>;
```

- [ ] **Step 6: Write the import route**

Create `src/app/api/admin/lessons/import/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { Prisma } from "@prisma/client";
import { db } from "@/lib/db";
import { requireAdmin } from "@/lib/admin-guard";
import { recordAudit } from "@/lib/admin-audit";
import { adminLessonImportSchema } from "@/lib/validators";
import { validateLessonMarkdown } from "@/lib/lesson-markdown";
import { buildLessonUpdate } from "@/lib/admin-lesson";
import { revalidateTag } from "next/cache";
import { CATALOGUE_TAG } from "@/lib/catalogue";

export const dynamic = "force-dynamic";

// POST /api/admin/lessons/import — replace a topic's lesson from markdown.
export async function POST(req: NextRequest) {
  try {
    const guard = await requireAdmin();
    if (!guard.ok) return guard.response;

    const body = await req.json();
    const parsedBody = adminLessonImportSchema.safeParse(body);
    if (!parsedBody.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsedBody.error.flatten() },
        { status: 400 },
      );
    }
    const { topicId, markdown } = parsedBody.data;

    // The client parses only to render a preview. This parse is the one that
    // counts — trusting client-sent blocks would let a crafted request post
    // unsanitised SVG straight into student pages.
    const parsed = validateLessonMarkdown(markdown);
    if (parsed.errors.length > 0) {
      return NextResponse.json(
        { error: "This lesson has errors and was not saved", issues: parsed.errors },
        { status: 400 },
      );
    }

    const topic = await db.topic.findUnique({
      where: { id: topicId },
      select: {
        id: true,
        title: true,
        subject: { select: { name: true } },
        subtopics: {
          orderBy: { orderIndex: "asc" },
          take: 1,
          select: { id: true, lessons: { take: 1, select: { id: true } } },
        },
      },
    });
    if (!topic) {
      return NextResponse.json({ error: "Unknown topic" }, { status: 404 });
    }

    const update = buildLessonUpdate(parsed, markdown, guard.actor.id);
    // `blocks` is a Json column. Prisma types it as InputJsonValue, which a
    // LessonBlock[] does not structurally satisfy (optional fields typed as
    // `T | undefined`), so the cast is required — not laziness.
    const blocksJson = update.blocks as unknown as Prisma.InputJsonValue;

    // A topic with no subtopic or lesson yet is not an error — a newly added
    // topic must be authorable without running the seed first.
    let subtopicId = topic.subtopics[0]?.id;
    if (!subtopicId) {
      const created = await db.subtopic.create({
        data: { topicId: topic.id, title: "Core Concepts", orderIndex: 0 },
        select: { id: true },
      });
      subtopicId = created.id;
    }

    const lessonId = topic.subtopics[0]?.lessons[0]?.id;
    const lesson = lessonId
      ? await db.lesson.update({
          where: { id: lessonId },
          data: { ...update, blocks: blocksJson },
          select: { id: true },
        })
      : await db.lesson.create({
          data: {
            subtopicId,
            title: update.title ?? topic.title,
            content: update.content,
            blocks: blocksJson,
            createdBy: update.createdBy,
            summary: update.summary,
            estimatedMinutes: update.estimatedMinutes,
            difficulty: update.difficulty,
            passMarkPercent: update.passMarkPercent,
            practiceCount: update.practiceCount,
          },
          select: { id: true },
        });

    await recordAudit({
      actorId: guard.actor.id,
      action: "lesson.import",
      entity: "Lesson",
      entityId: lesson.id,
      summary: `${topic.subject.name} — ${topic.title}: ${parsed.blocks.length} blocks from markdown`,
    });

    revalidateTag(CATALOGUE_TAG, "max");

    return NextResponse.json({
      message: `Saved ${parsed.blocks.length} blocks to "${topic.title}".`,
      lessonId: lesson.id,
      blockCount: parsed.blocks.length,
      warnings: parsed.warnings,
    });
  } catch (error) {
    console.error("Error importing lesson:", error);
    return NextResponse.json({ error: "Failed to import lesson" }, { status: 500 });
  }
}
```

Check `recordAudit`'s signature in `src/lib/admin-audit.ts` before running — if it takes no `entityId`, drop that line rather than inventing a parameter.

- [ ] **Step 7: Write the current-state route**

Create `src/app/api/admin/lessons/[topicId]/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { requireAdmin } from "@/lib/admin-guard";
import { parseBlocks } from "@/lib/lesson-engine";
import { isAuthored } from "@/lib/admin-lesson";

export const dynamic = "force-dynamic";

// GET /api/admin/lessons/[topicId] — what is currently stored, so the upload
// form can show the admin what they are about to replace.
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ topicId: string }> },
) {
  const guard = await requireAdmin();
  if (!guard.ok) return guard.response;

  const { topicId } = await params;

  const topic = await db.topic.findUnique({
    where: { id: topicId },
    select: {
      title: true,
      subtopics: {
        orderBy: { orderIndex: "asc" },
        take: 1,
        select: {
          lessons: {
            take: 1,
            select: {
              title: true,
              blocks: true,
              content: true,
              createdBy: true,
              updatedAt: true,
            },
          },
        },
      },
    },
  });
  if (!topic) return NextResponse.json({ error: "Unknown topic" }, { status: 404 });

  const lesson = topic.subtopics[0]?.lessons[0] ?? null;
  return NextResponse.json({
    topicTitle: topic.title,
    lesson: lesson
      ? {
          title: lesson.title,
          blockCount: parseBlocks(lesson.blocks).length,
          authored: isAuthored(lesson.createdBy),
          updatedAt: lesson.updatedAt,
          markdown: isAuthored(lesson.createdBy) ? lesson.content : null,
        }
      : null,
  });
}
```

`markdown` is returned only for authored lessons — for a seeded lesson, `content` is generated filler and handing it back as an editable source would be misleading.

- [ ] **Step 8: Typecheck, lint, and run the full suite**

Run: `npx tsc --noEmit && npm run lint && npm test`
Expected: no errors, all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/lib/admin-lesson.ts src/lib/validators.ts scripts/test-admin-lesson.mts src/app/api/admin/lessons package.json
git commit -m "feat(lessons): import and current-state route handlers with server-side re-parse"
```

---

### Task 6: Admin lessons list page and nav entry

**Files:**
- Create: `src/app/admin/lessons/page.tsx`
- Modify: `src/lib/admin-nav.ts`

**Interfaces:**
- Consumes: `isAuthored`, `SYSTEM_AUTHOR` from `src/lib/admin-lesson.ts` (Task 5); `parseBlocks` from `src/lib/lesson-engine.ts`
- Produces: the `/admin/lessons` route that Task 7's upload page links from

**Read first:** `src/app/admin/questions/page.tsx` for the list-page shape (`PageHeader`, table classes, `TH_CLS`), and `src/app/admin/layout.tsx` — the admin role check lives there, so this page does not repeat it.

- [ ] **Step 1: Add the nav entry**

Modify `src/lib/admin-nav.ts`:

```ts
import { LuBookOpen, LuDatabase, LuLayoutDashboard, LuUpload } from "react-icons/lu";

// Every entry must have a page behind it. An earlier version listed Subjects,
// Users and Lessons with no routes — three links straight to a 404.
export const ADMIN_NAV = [
  { name: "Overview", href: "/admin", icon: LuLayoutDashboard },
  { name: "Questions", href: "/admin/questions", icon: LuDatabase },
  { name: "Import", href: "/admin/questions/import", icon: LuUpload },
  { name: "Lessons", href: "/admin/lessons", icon: LuBookOpen },
] as const;
```

- [ ] **Step 2: Write the list page**

Create `src/app/admin/lessons/page.tsx`:

```tsx
import Link from "next/link";
import { db } from "@/lib/db";
import { PageHeader } from "@/components/ui/page-header";
import { buttonClass } from "@/components/ui/button";
import { parseBlocks } from "@/lib/lesson-engine";
import { isAuthored } from "@/lib/admin-lesson";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

const TH_CLS = "text-[11px] font-semibold uppercase tracking-wider text-muted";

export default async function AdminLessonsPage() {
  const subjects = await db.subject.findMany({
    orderBy: { name: "asc" },
    select: {
      id: true,
      name: true,
      topics: {
        orderBy: { orderIndex: "asc" },
        select: {
          id: true,
          title: true,
          subtopics: {
            orderBy: { orderIndex: "asc" },
            take: 1,
            select: {
              lessons: { take: 1, select: { blocks: true, createdBy: true } },
            },
          },
        },
      },
    },
  });

  const rows = subjects.flatMap((subject) =>
    subject.topics.map((topic) => {
      const lesson = topic.subtopics[0]?.lessons[0] ?? null;
      return {
        subjectName: subject.name,
        topicId: topic.id,
        topicTitle: topic.title,
        blockCount: lesson ? parseBlocks(lesson.blocks).length : 0,
        authored: lesson ? isAuthored(lesson.createdBy) : false,
      };
    }),
  );

  const authoredCount = rows.filter((r) => r.authored).length;

  return (
    <div>
      <PageHeader
        title="Lessons"
        description="Upload a markdown lesson note against a topic. Uploaded notes replace the generated placeholder."
      />

      <p className="mb-4 text-sm text-muted">
        <span className="font-semibold tabular-nums text-foreground">{authoredCount}</span> of{" "}
        <span className="tabular-nums">{rows.length}</span> topics have an authored lesson note.
      </p>

      <div className="overflow-x-auto rounded-lg border border-border-strong bg-card">
        <table className="w-full text-sm">
          <caption className="sr-only">Topics and their lesson note status</caption>
          <thead>
            <tr className="border-b border-border-strong bg-secondary/50">
              <th scope="col" className={cn(TH_CLS, "px-4 py-3 text-left")}>Subject</th>
              <th scope="col" className={cn(TH_CLS, "px-4 py-3 text-left")}>Topic</th>
              <th scope="col" className={cn(TH_CLS, "px-4 py-3 text-left")}>Blocks</th>
              <th scope="col" className={cn(TH_CLS, "px-4 py-3 text-left")}>Status</th>
              <th scope="col" className={cn(TH_CLS, "px-4 py-3 text-right")}>Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-strong">
            {rows.map((row) => (
              <tr key={row.topicId}>
                <td className="px-4 py-3 text-muted">{row.subjectName}</td>
                <td className="px-4 py-3 font-medium text-foreground">{row.topicTitle}</td>
                <td className="px-4 py-3 tabular-nums text-muted">{row.blockCount}</td>
                <td className="px-4 py-3">
                  <span
                    className={cn(
                      "rounded-full px-2.5 py-0.5 text-[11px] font-semibold",
                      row.authored
                        ? "bg-tone-green-soft text-tone-green-ink"
                        : "bg-secondary text-muted",
                    )}
                  >
                    {row.authored ? "Authored" : "Placeholder"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <Link
                    href={`/admin/lessons/upload?topicId=${row.topicId}`}
                    className={buttonClass("outline", "sm")}
                  >
                    {row.authored ? "Replace" : "Upload"}
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

Confirm the tone classes (`bg-tone-green-soft`, `text-tone-green-ink`) exist — `src/components/classroom/lesson-notes.tsx` uses the blue, red and purple variants. If green is absent, use `bg-tone-blue-soft` / `text-tone-blue-ink` rather than inventing a token. Confirm `buttonClass` accepts a `"sm"` size in `src/components/ui/button.tsx`; if not, use `"md"`.

- [ ] **Step 3: Verify it renders**

Run: `npm run dev`, sign in as an admin, and open `http://localhost:3000/admin/lessons`.
Expected: every topic listed, all marked "Placeholder", the count reading `0 of 150`, and a "Lessons" entry in the admin nav.

- [ ] **Step 4: Typecheck and lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/app/admin/lessons/page.tsx src/lib/admin-nav.ts
git commit -m "feat(admin): lessons list showing authored vs placeholder topics"
```

---

### Task 7: Upload page with live preview and confirm

**Files:**
- Create: `src/app/admin/lessons/upload/page.tsx`
- Create: `src/components/admin/lesson-upload-form.tsx`

**Interfaces:**
- Consumes: `validateLessonMarkdown`, `Issue`, `ParsedLesson` (Task 4); `POST /api/admin/lessons/import` and `GET /api/admin/lessons/[topicId]` (Task 5); `LessonNotes` from `src/components/classroom/lesson-notes.tsx`; `StatusBanner` and `ConfirmDialog` from `src/components/admin/`
- Produces: nothing downstream — this is the last task

**Read first:** `src/app/admin/questions/import/page.tsx` — the phase state machine, `file.text()` read, and `StatusBanner` usage are all lifted from it. Also check `src/components/admin/confirm-dialog.tsx` for its actual props before using it.

- [ ] **Step 1: Write the server page that loads the topic tree**

Create `src/app/admin/lessons/upload/page.tsx`:

```tsx
import { db } from "@/lib/db";
import { PageHeader } from "@/components/ui/page-header";
import { LessonUploadForm } from "@/components/admin/lesson-upload-form";

export const dynamic = "force-dynamic";

export default async function AdminLessonUploadPage({
  searchParams,
}: {
  searchParams: Promise<{ topicId?: string }>;
}) {
  const { topicId } = await searchParams;

  const subjects = await db.subject.findMany({
    orderBy: { name: "asc" },
    select: {
      id: true,
      name: true,
      slug: true,
      topics: {
        orderBy: { orderIndex: "asc" },
        select: {
          id: true,
          title: true,
          slug: true,
          curriculumLevel: { select: { classLevel: true, term: true } },
        },
      },
    },
  });

  return (
    <div>
      <PageHeader
        title="Upload a lesson note"
        description="Write the note as markdown, check the preview, then replace the topic's lesson."
      />
      <LessonUploadForm subjects={subjects} initialTopicId={topicId ?? null} />
    </div>
  );
}
```

- [ ] **Step 2: Write the client form**

Create `src/components/admin/lesson-upload-form.tsx`:

```tsx
"use client";

import { useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Button, buttonClass } from "@/components/ui/button";
import { StatusBanner } from "@/components/admin/status-banner";
import { LessonNotes } from "@/components/classroom/lesson-notes";
import { validateLessonMarkdown, type ParsedLesson } from "@/lib/lesson-markdown";
import type { CheckBlock } from "@/lib/lesson-engine";

type TopicOption = {
  id: string;
  title: string;
  slug: string;
  curriculumLevel: { classLevel: string; term: string } | null;
};

type SubjectOption = {
  id: string;
  name: string;
  slug: string;
  topics: TopicOption[];
};

type Current = {
  topicTitle: string;
  lesson: { title: string; blockCount: number; authored: boolean } | null;
};

const SAMPLE = `---
title: Newton's First Law
estimatedMinutes: 20
---

## What the law says

An object stays at rest, or moves at constant velocity, unless a net
force acts on it.

:::example
Problem: A book rests on a table. Why does it not move?
Step: Identify the forces — weight down, normal force up.
Step: They are equal and opposite, so the net force is zero.
Answer: With zero net force, the book stays at rest.
:::

:::tip
Exam: WAEC
Say "net force", not "force" — the distinction earns the mark.
:::

:::check
Q: A car moves at constant velocity. What is the net force on it?
A) Zero
B) Equal to its weight
C) Equal to its momentum
Correct: A
Why: Constant velocity means no acceleration, so no net force.
:::`;

export function LessonUploadForm({
  subjects,
  initialTopicId,
}: {
  subjects: SubjectOption[];
  initialTopicId: string | null;
}) {
  const initialSubjectId =
    subjects.find((s) => s.topics.some((t) => t.id === initialTopicId))?.id ?? "";

  const [subjectId, setSubjectId] = useState(initialSubjectId);
  const [topicId, setTopicId] = useState(initialTopicId ?? "");
  const [markdown, setMarkdown] = useState("");
  const [current, setCurrent] = useState<Current | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const fileInputId = useId();
  const subjectInputId = useId();
  const topicInputId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const subject = subjects.find((s) => s.id === subjectId) ?? null;
  const topics = subject?.topics ?? [];

  // Parsed on every keystroke. The parser is a line scanner over one lesson —
  // cheap enough that debouncing would add a bug surface for no gain.
  const parsed: ParsedLesson | null = useMemo(
    () => (markdown.trim() ? validateLessonMarkdown(markdown) : null),
    [markdown],
  );

  const checks = (parsed?.blocks ?? []).filter(
    (b): b is CheckBlock => b.type === "check",
  );

  const canSave =
    Boolean(topicId) && parsed !== null && parsed.errors.length === 0 && !submitting;

  async function loadCurrent(nextTopicId: string) {
    setCurrent(null);
    if (!nextTopicId) return;
    try {
      const res = await fetch(`/api/admin/lessons/${nextTopicId}`);
      if (res.ok) setCurrent(await res.json());
    } catch {
      // A failed lookup only costs the comparison panel, not the upload.
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      setMarkdown(text);
      setResult(null);
      // Frontmatter can name its own target — honour it when it resolves.
      const meta = validateLessonMarkdown(text).meta;
      if (meta.subject && meta.topic) {
        const bySlug = subjects.find((s) => s.slug === meta.subject);
        const topic = bySlug?.topics.find((t) => t.slug === meta.topic);
        if (bySlug && topic) {
          setSubjectId(bySlug.id);
          setTopicId(topic.id);
          void loadCurrent(topic.id);
        }
      }
    } catch {
      setResult({ ok: false, message: "Could not read that file." });
    }
  }

  async function handleSave() {
    setConfirming(false);
    setSubmitting(true);
    try {
      const res = await fetch("/api/admin/lessons/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topicId, markdown, confirm: true }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || !data) {
        setResult({ ok: false, message: data?.error ?? `Save failed (${res.status}).` });
        return;
      }
      setResult({ ok: true, message: data.message as string });
      void loadCurrent(topicId);
    } catch {
      setResult({ ok: false, message: "Could not reach the server. Nothing was saved." });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <div className="space-y-6">
        {result && (
          <StatusBanner
            tone={result.ok ? "success" : "error"}
            title={result.ok ? "Lesson saved" : "Lesson not saved"}
            message={result.message}
          />
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={subjectInputId} className="block text-sm font-semibold text-foreground">
              Subject
            </label>
            <select
              id={subjectInputId}
              value={subjectId}
              onChange={(e) => {
                setSubjectId(e.target.value);
                setTopicId("");
                setCurrent(null);
              }}
              className="mt-2 block w-full rounded-lg border border-border bg-card p-2.5 text-sm text-foreground"
            >
              <option value="">Choose a subject…</option>
              {subjects.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor={topicInputId} className="block text-sm font-semibold text-foreground">
              Topic
            </label>
            <select
              id={topicInputId}
              value={topicId}
              disabled={!subject}
              onChange={(e) => {
                setTopicId(e.target.value);
                void loadCurrent(e.target.value);
              }}
              className="mt-2 block w-full rounded-lg border border-border bg-card p-2.5 text-sm text-foreground disabled:opacity-50"
            >
              <option value="">Choose a topic…</option>
              {topics.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.curriculumLevel
                    ? `${t.curriculumLevel.classLevel} ${t.curriculumLevel.term} — ${t.title}`
                    : t.title}
                </option>
              ))}
            </select>
          </div>
        </div>

        {current && (
          <div className="rounded-lg border border-border-strong bg-card p-4">
            <p className="text-sm font-semibold text-foreground">Currently stored</p>
            {current.lesson ? (
              <p className="mt-1 text-sm text-muted">
                &ldquo;{current.lesson.title}&rdquo; — {current.lesson.blockCount} blocks,{" "}
                {current.lesson.authored
                  ? "authored from a previous upload."
                  : "the generated placeholder."}
              </p>
            ) : (
              <p className="mt-1 text-sm text-muted">No lesson yet. One will be created.</p>
            )}
          </div>
        )}

        <div>
          <label htmlFor={fileInputId} className="block text-sm font-semibold text-foreground">
            Markdown file
          </label>
          <input
            ref={fileInputRef}
            id={fileInputId}
            type="file"
            accept=".md,.markdown,text/markdown"
            onChange={handleFileChange}
            className="mt-2 block w-full text-sm text-foreground file:mr-3 file:rounded-lg file:border file:border-border file:bg-secondary file:px-3 file:py-2 file:text-sm file:font-semibold file:text-foreground hover:file:bg-border"
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-foreground" htmlFor="lesson-markdown">
            Source
          </label>
          <textarea
            id="lesson-markdown"
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            rows={20}
            spellCheck={false}
            placeholder={SAMPLE}
            className="mt-2 block w-full rounded-lg border border-border bg-card p-3 font-mono text-xs text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          />
        </div>

        {parsed && parsed.errors.length > 0 && (
          <StatusBanner
            tone="error"
            title={`${parsed.errors.length} problem${parsed.errors.length === 1 ? "" : "s"} to fix`}
            message={parsed.errors
              .map((e) => (e.line ? `Line ${e.line}: ${e.message}` : e.message))
              .join(" · ")}
          />
        )}

        {parsed && parsed.warnings.length > 0 && (
          <StatusBanner
            tone="info"
            title={`${parsed.warnings.length} warning${parsed.warnings.length === 1 ? "" : "s"}`}
            message={parsed.warnings
              .map((w) => (w.line ? `Line ${w.line}: ${w.message}` : w.message))
              .join(" · ")}
          />
        )}

        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={() => setConfirming(true)} disabled={!canSave}>
            {submitting ? "Saving…" : "Save lesson"}
          </Button>
          <Link href="/admin/lessons" className={buttonClass("outline", "md")}>
            Back to lessons
          </Link>
        </div>
      </div>

      <div className="space-y-4">
        <p className="text-sm font-semibold text-foreground">
          Preview — exactly what students will read
        </p>
        <div className="rounded-lg border border-border-strong bg-card p-5">
          {parsed && parsed.blocks.length > 0 ? (
            <LessonNotes blocks={parsed.blocks} fallbackContent={null} />
          ) : (
            <p className="text-sm text-muted">
              The preview appears here once there is something to render.
            </p>
          )}
        </div>

        {checks.length > 0 && (
          <div className="rounded-lg border border-border-strong bg-card p-5">
            <p className="text-sm font-semibold text-foreground">
              {checks.length} knowledge check{checks.length === 1 ? "" : "s"}
            </p>
            <p className="mt-1 text-xs text-muted">
              Checks appear in the card player, not in the notes view above.
            </p>
            <ul className="mt-3 space-y-3">
              {checks.map((check) => (
                <li key={check.id} className="text-sm">
                  <p className="font-medium text-foreground">{check.question}</p>
                  <p className="mt-1 text-xs text-muted">
                    Answer {check.answer}: {check.options[check.answer]} · after{" "}
                    <code>{check.afterCard}</code>
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {confirming && (
        <ConfirmSave
          topicTitle={current?.topicTitle ?? "this topic"}
          existing={current?.lesson ?? null}
          blockCount={parsed?.blocks.length ?? 0}
          onCancel={() => setConfirming(false)}
          onConfirm={handleSave}
        />
      )}
    </div>
  );
}

function ConfirmSave({
  topicTitle,
  existing,
  blockCount,
  onCancel,
  onConfirm,
}: {
  topicTitle: string;
  existing: { title: string; blockCount: number; authored: boolean } | null;
  blockCount: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-save-title"
        className="w-full max-w-md rounded-2xl border border-border-strong bg-card p-6"
      >
        <h2 id="confirm-save-title" className="text-lg font-bold text-foreground">
          Replace the lesson for {topicTitle}?
        </h2>
        <p className="mt-2 text-sm text-muted">
          {existing
            ? `${existing.blockCount} existing block${existing.blockCount === 1 ? "" : "s"} (${existing.authored ? "authored" : "generated placeholder"}) will be replaced by ${blockCount}.`
            : `A new lesson with ${blockCount} block${blockCount === 1 ? "" : "s"} will be created.`}
        </p>
        <div className="mt-5 flex items-center justify-end gap-3">
          <Button variant="outline" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" onClick={onConfirm}>Replace lesson</Button>
        </div>
      </div>
    </div>
  );
}
```

Before writing `ConfirmSave`, check `src/components/admin/confirm-dialog.tsx` — if its props cover this (title, body, confirm label, callbacks), use it and delete `ConfirmSave` rather than shipping a second dialog.

- [ ] **Step 3: Typecheck and lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: no errors. If `LessonNotes` refuses to import into a client component, check whether `WorkedExample` or `Markdown` is server-only — neither should be, but if one is, that is a real constraint to solve rather than work around by duplicating the renderer.

- [ ] **Step 4: Verify end to end**

Run `npm run dev`, sign in as an admin, then:

1. Open `/admin/lessons`, click **Upload** on any topic.
2. Paste the sample from the textarea placeholder. Confirm the preview renders a heading, a worked example and a blue tip, and that the knowledge check appears in the checks panel below rather than in the preview.
3. Delete the `:::check` fence. Confirm an error banner appears saying a lesson should include at least one knowledge check, and that **Save lesson** is disabled.
4. Restore it, click **Save lesson**, confirm the dialog names the topic and the placeholder being replaced, and accept.
5. Open `/classroom/<subject>/<topic>` as a student and confirm the notes render.
6. Return to `/admin/lessons` and confirm that topic now reads **Authored** with the right block count.
7. Paste a diagram fence containing `<svg onload="alert(1)"><script>alert(2)</script><circle r="10"/></svg>`, save it, then view the topic in the classroom. Confirm no alert fires and the circle renders.

- [ ] **Step 5: Run the full suite**

Run: `npm test`
Expected: all tests pass, including the two new files.

- [ ] **Step 6: Commit**

```bash
git add src/app/admin/lessons/upload/page.tsx src/components/admin/lesson-upload-form.tsx
git commit -m "feat(admin): lesson note upload with live preview and replace confirmation"
```

---

## Out of scope for this plan

Phase 2 bulk upload is deliberately not planned yet. The spec gates it behind phase 1 taking real lesson notes from a real teacher, because the format's weaknesses will surface in that first upload and are cheaper to fix before a second consumer exists. When it comes, it reuses `validateLessonMarkdown` unchanged and routes files by their frontmatter `subject` + `topic` slugs.
