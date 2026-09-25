# Classroom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the Subjects section to Classroom, make the topic page a readable note, and let a student browse the syllabus at their own class level and drill it without leaving.

**Architecture:** All decision logic (which blocks become notes, which class to default to, which resources to show, which topic is next) lives in one pure module, `src/lib/classroom.ts`, unit-tested with no database or React. The route tree moves from `/subjects` to `/classroom` with permanent redirects, and `lessonId` leaves the URL space because every topic has exactly one lesson. UI components are thin renderers over the pure module.

**Tech Stack:** Next.js 16 (App Router), React 19, Prisma 6 / PostgreSQL, Tailwind v4, `node:test` + `tsx` for tests.

**Spec:** `docs/superpowers/specs/2026-08-05-classroom-design.md`

## Global Constraints

- **No schema migration.** `prisma migrate` currently hangs because `DIRECT_URL` points at the pgbouncer pooler. Any task needing a migration is out of scope.
- **This is not the Next.js you know.** Read the relevant guide under `node_modules/next/dist/docs/` before using an App Router API you have not used in this repo already (per `AGENTS.md`).
- **Tests** are `node:test` + `tsx`, live in `scripts/test-*.mts`, import from `../src/...` with no file extension, and must be added to the `test` script in `package.json`.
- **React Compiler lint rules are enforced as errors.** No `setState` in an effect body, no ref writes during render, no `Date.now()` during render. Fetch on user events, not effects, where possible.
- **Colours must use semantic tokens** (`bg-tone-*-soft`, `text-tone-*-ink`, `text-muted`, `bg-card`). Raw Tailwind palette classes such as `bg-blue-100` break dark mode.
- **Verification for every task:** `npx tsc --noEmit`, `npx eslint src scripts`, `npm test`, and `npm run build` must all pass before the commit step.

---

### Task 1: Pure classroom logic

**Files:**
- Create: `src/lib/classroom.ts`
- Create: `scripts/test-classroom.mts`
- Modify: `package.json` (add the new test file to the `test` script)

**Interfaces:**
- Consumes: `LessonBlock`, `CheckBlock` from `src/lib/lesson-engine.ts`; `ClassLevel`, `Term`, `scopeOrdinal` from `src/lib/curriculum-scope.ts`
- Produces:
  - `toNotes(blocks: LessonBlock[]): NotesBlock[]`
  - `resolveClassLevel(preferred: string | null | undefined, classesWithTopics: readonly string[]): ClassLevel`
  - `topicNeighbours(topics: TopicNavItem[], currentSlug: string): { previous: TopicNavItem | null; next: TopicNavItem | null }`
  - `selectResources<T>(lessonResources: readonly T[], subjectResources: readonly T[]): { items: T[]; source: "topic" | "subject" | "none" }`
  - types `NotesBlock`, `TopicNavItem`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-classroom.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  resolveClassLevel,
  selectResources,
  toNotes,
  topicNeighbours,
  type TopicNavItem,
} from "../src/lib/classroom";
import type { LessonBlock } from "../src/lib/lesson-engine";

const concept = (id: string): LessonBlock => ({
  type: "concept",
  id,
  title: `Concept ${id}`,
  text: "Body text",
});
const check = (id: string): LessonBlock => ({
  type: "check",
  id,
  question: "Q?",
  options: { A: "a", B: "b" },
  answer: "A",
  explanation: "because",
  afterCard: "c1",
});

// ─── toNotes ───────────────────────────────────────────────

test("toNotes drops check blocks", () => {
  // A knowledge check belongs to the player, where an answer is graded.
  const notes = toNotes([concept("c1"), check("k1"), concept("c2")]);
  assert.deepEqual(notes.map((b) => b.id), ["c1", "c2"]);
});

test("toNotes preserves authored order", () => {
  const notes = toNotes([concept("c3"), concept("c1"), concept("c2")]);
  assert.deepEqual(notes.map((b) => b.id), ["c3", "c1", "c2"]);
});

test("toNotes keeps every non-check type", () => {
  const blocks: LessonBlock[] = [
    concept("c"),
    { type: "diagram", id: "d", svg: "<svg/>", hotspots: [] },
    { type: "example", id: "e", problem: "p", steps: ["s"], answer: "a" },
    { type: "tip", id: "t", text: "tip" },
    { type: "mistake", id: "m", wrong: "w", right: "r" },
    { type: "mnemonic", id: "n", phrase: "p", encoded: ["e"] },
  ];
  assert.equal(toNotes(blocks).length, 6);
});

test("toNotes on an empty list returns empty", () => {
  assert.deepEqual(toNotes([]), []);
});

test("toNotes on checks only returns empty", () => {
  assert.deepEqual(toNotes([check("k1"), check("k2")]), []);
});

// ─── resolveClassLevel ─────────────────────────────────────

test("resolveClassLevel honours the student's own class", () => {
  assert.equal(resolveClassLevel("SS2", ["SS1", "SS2", "SS3"]), "SS2");
});

test("resolveClassLevel falls back when the student's class has no topics", () => {
  assert.equal(resolveClassLevel("SS3", ["SS1", "SS2"]), "SS1");
});

test("resolveClassLevel falls back for junior, absent and unknown values", () => {
  for (const value of ["JSS3", null, undefined, "", "SS4"]) {
    assert.equal(resolveClassLevel(value, ["SS2", "SS3"]), "SS2", `value=${value}`);
  }
});

test("resolveClassLevel returns SS1 when no class has topics", () => {
  assert.equal(resolveClassLevel(null, []), "SS1");
});

test("resolveClassLevel picks the lowest available class, not list order", () => {
  assert.equal(resolveClassLevel(null, ["SS3", "SS1"]), "SS1");
});

// ─── topicNeighbours ───────────────────────────────────────

const topic = (
  slug: string,
  classLevel: string,
  term: string,
  orderIndex: number,
): TopicNavItem => ({ slug, title: slug, classLevel, term, orderIndex });

const SYLLABUS: TopicNavItem[] = [
  topic("a", "SS1", "FIRST", 0),
  topic("b", "SS1", "FIRST", 1),
  topic("c", "SS1", "SECOND", 0),
  topic("d", "SS1", "THIRD", 0),
  topic("e", "SS2", "FIRST", 0),
];

test("topicNeighbours moves within a term by orderIndex", () => {
  const { previous, next } = topicNeighbours(SYLLABUS, "a");
  assert.equal(previous, null);
  assert.equal(next?.slug, "b");
});

test("topicNeighbours carries across a term boundary", () => {
  const { previous, next } = topicNeighbours(SYLLABUS, "b");
  assert.equal(previous?.slug, "a");
  assert.equal(next?.slug, "c");
});

test("topicNeighbours stops at the end of a class", () => {
  // "d" is the last SS1 topic; "e" is SS2 and must not be offered.
  const { previous, next } = topicNeighbours(SYLLABUS, "d");
  assert.equal(previous?.slug, "c");
  assert.equal(next, null);
});

test("topicNeighbours stops at the start of a class", () => {
  const { previous, next } = topicNeighbours(SYLLABUS, "e");
  assert.equal(previous, null);
  assert.equal(next, null);
});

test("topicNeighbours orders terms by curriculum, not alphabetically", () => {
  // FIRST < SECOND < THIRD; alphabetical would put THIRD before SECOND.
  const { next } = topicNeighbours(SYLLABUS, "c");
  assert.equal(next?.slug, "d");
});

test("topicNeighbours returns nulls for an unknown slug", () => {
  assert.deepEqual(topicNeighbours(SYLLABUS, "missing"), {
    previous: null,
    next: null,
  });
});

// ─── selectResources ───────────────────────────────────────

test("selectResources prefers topic resources", () => {
  const result = selectResources(["lesson-a"], ["subject-a", "subject-b"]);
  assert.equal(result.source, "topic");
  assert.deepEqual(result.items, ["lesson-a"]);
});

test("selectResources falls back to subject resources", () => {
  const result = selectResources([], ["subject-a"]);
  assert.equal(result.source, "subject");
  assert.deepEqual(result.items, ["subject-a"]);
});

test("selectResources reports none when both are empty", () => {
  const result = selectResources([], []);
  assert.equal(result.source, "none");
  assert.deepEqual(result.items, []);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-classroom.mts`
Expected: FAIL — cannot find module `../src/lib/classroom`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/classroom.ts`:

```ts
import type { CheckBlock, LessonBlock } from "@/lib/lesson-engine";
import {
  CLASS_LEVELS,
  scopeOrdinal,
  type ClassLevel,
  type ScopePoint,
} from "@/lib/curriculum-scope";

// Decision logic for the Classroom section. Pure — no database, no React — so
// the rules that decide what a student sees can be tested directly.

/** Everything a note renders. `check` belongs to the player, not the note. */
export type NotesBlock = Exclude<LessonBlock, CheckBlock>;

/**
 * The lesson as a continuous note.
 *
 * Knowledge checks are dropped: a note is read, not answered, and a check
 * rendered here would grade nothing and record nothing.
 */
export function toNotes(blocks: readonly LessonBlock[]): NotesBlock[] {
  return blocks.filter((block): block is NotesBlock => block.type !== "check");
}

/**
 * Which class tab to open on.
 *
 * The student's own class when it has topics — an SS2 student should reach SS2
 * Physics in zero taps. Otherwise the lowest class that has any, so the page
 * never opens on an empty tab.
 */
export function resolveClassLevel(
  preferred: string | null | undefined,
  classesWithTopics: readonly string[],
): ClassLevel {
  const available = CLASS_LEVELS.filter((level) =>
    classesWithTopics.includes(level),
  );
  if (preferred && available.includes(preferred as ClassLevel)) {
    return preferred as ClassLevel;
  }
  return available[0] ?? "SS1";
}

export type TopicNavItem = {
  slug: string;
  title: string;
  classLevel: string;
  term: string;
  orderIndex: number;
};

/**
 * Previous and next topic, within the same class.
 *
 * Ordering runs term-by-term then by `orderIndex`, so navigation carries across
 * a term boundary but stops at a class boundary — moving from SS1 straight into
 * SS2 would silently skip a year.
 */
export function topicNeighbours(
  topics: readonly TopicNavItem[],
  currentSlug: string,
): { previous: TopicNavItem | null; next: TopicNavItem | null } {
  const current = topics.find((t) => t.slug === currentSlug);
  if (!current) return { previous: null, next: null };

  const ordered = topics
    .filter((t) => t.classLevel === current.classLevel)
    .sort((a, b) => {
      const byTerm =
        scopeOrdinal({ classLevel: a.classLevel, term: a.term } as ScopePoint) -
        scopeOrdinal({ classLevel: b.classLevel, term: b.term } as ScopePoint);
      return byTerm !== 0 ? byTerm : a.orderIndex - b.orderIndex;
    });

  const index = ordered.findIndex((t) => t.slug === currentSlug);
  return {
    previous: index > 0 ? ordered[index - 1] : null,
    next: index >= 0 && index < ordered.length - 1 ? ordered[index + 1] : null,
  };
}

/**
 * Which resources the topic page shows.
 *
 * Topic-specific resources win. Falling back to the subject's is better than an
 * empty section, but the caller must label it honestly — the `source` field is
 * what lets it say "More Physics resources" rather than implying these belong
 * to this topic.
 */
export function selectResources<T>(
  lessonResources: readonly T[],
  subjectResources: readonly T[],
): { items: T[]; source: "topic" | "subject" | "none" } {
  if (lessonResources.length > 0) {
    return { items: [...lessonResources], source: "topic" };
  }
  if (subjectResources.length > 0) {
    return { items: [...subjectResources], source: "subject" };
  }
  return { items: [], source: "none" };
}
```

- [ ] **Step 4: Register the test**

In `package.json`, add `scripts/test-classroom.mts` to the `test` script, immediately after `scripts/test-curriculum-scope.mts`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test`
Expected: PASS, with 20 more tests than before.

- [ ] **Step 6: Verify and commit**

```bash
npx tsc --noEmit && npx eslint src scripts && npm test
git add src/lib/classroom.ts scripts/test-classroom.mts package.json
git commit -m "Add pure Classroom decision logic"
```

---

### Task 2: Notes renderer

**Files:**
- Create: `src/components/classroom/lesson-notes.tsx`

**Interfaces:**
- Consumes: `toNotes`, `NotesBlock` from Task 1; existing `Markdown` (`src/components/lesson/markdown.tsx`), `WorkedExample` (`src/components/lesson/worked-example.tsx`), `InteractiveDiagram` (`src/components/lesson/interactive-diagram.tsx`)
- Produces: `<LessonNotes blocks={LessonBlock[]} fallbackContent={string | null} />`

- [ ] **Step 1: Read the components being reused**

Read `src/components/lesson/worked-example.tsx` and `src/components/lesson/interactive-diagram.tsx` and note their exact prop signatures. Do not guess them — the notes renderer must pass what they already accept, unchanged.

- [ ] **Step 2: Write the component**

Create `src/components/classroom/lesson-notes.tsx`. It is a server component — no `"use client"` — unless a reused child requires client rendering, in which case only that child stays client.

Requirements:
- Call `toNotes(blocks)` and render the result in order.
- `concept` → `<h2>` from `title` when present, then `<Markdown>` of `text`; render `reveal` as a `<details>` with summary "Show more".
- `example` → the existing worked-example component.
- `diagram` → the existing interactive-diagram component.
- `tip` → callout using `bg-tone-blue-soft` / `text-tone-blue-ink`.
- `mistake` → callout using `bg-tone-red-soft` / `text-tone-red-ink`, showing `wrong` struck through above `right`.
- `mnemonic` → callout using `bg-tone-purple-soft` / `text-tone-purple-ink`, showing `phrase` then the `encoded` list.
- When `toNotes(blocks)` is empty and `fallbackContent` is a non-empty string, render `<Markdown>` of `fallbackContent`. When both are empty render nothing (the caller decides the empty state).

Every block gets `key={block.id}`. Use a `switch` on `block.type` with an exhaustive default returning `null`.

Structure:

```tsx
import { toNotes, type NotesBlock } from "@/lib/classroom";
import type { LessonBlock } from "@/lib/lesson-engine";
import { Markdown } from "@/components/lesson/markdown";

export function LessonNotes({
  blocks,
  fallbackContent,
}: {
  blocks: LessonBlock[];
  fallbackContent: string | null;
}) {
  const notes = toNotes(blocks);

  // All 150 authored lessons have blocks; this path is defensive.
  if (notes.length === 0) {
    return fallbackContent ? <Markdown content={fallbackContent} /> : null;
  }

  return (
    <article className="space-y-6">
      {notes.map((block) => (
        <NoteBlock key={block.id} block={block} />
      ))}
    </article>
  );
}

function NoteBlock({ block }: { block: NotesBlock }) {
  switch (block.type) {
    case "concept":
      return (
        <section>
          {block.title && (
            <h2 className="text-lg font-bold tracking-tight text-foreground">
              {block.title}
            </h2>
          )}
          <div className="mt-2 leading-relaxed text-foreground">
            <Markdown content={block.text} />
          </div>
          {block.reveal && (
            <details className="mt-3 rounded-xl border border-border bg-secondary/40 p-3.5">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">
                Show more
              </summary>
              <div className="mt-2 text-sm text-muted">
                <Markdown content={block.reveal} />
              </div>
            </details>
          )}
        </section>
      );

    case "example":
      // Reuse the authored worked-example component; pass its existing props.
      return null; // replace with <WorkedExample … /> per its real signature

    case "diagram":
      return null; // replace with <InteractiveDiagram … /> per its real signature

    case "tip":
      return (
        <aside className="rounded-xl bg-tone-blue-soft p-4 text-sm text-tone-blue-ink">
          {block.text}
        </aside>
      );

    case "mistake":
      return (
        <aside className="rounded-xl bg-tone-red-soft p-4 text-sm text-tone-red-ink">
          <p className="line-through opacity-70">{block.wrong}</p>
          <p className="mt-1 font-semibold">{block.right}</p>
        </aside>
      );

    case "mnemonic":
      return (
        <aside className="rounded-xl bg-tone-purple-soft p-4 text-sm text-tone-purple-ink">
          <p className="font-bold">{block.phrase}</p>
          <ul className="mt-2 space-y-0.5">
            {block.encoded.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </aside>
      );

    default:
      return null;
  }
}
```

The two `return null` placeholders in `example` and `diagram` exist because their components' prop signatures must be read from source in Step 1 rather than guessed. Fill them in that step; leaving them null is not an acceptable final state.

- [ ] **Step 3: Verify it compiles and lints**

Run: `npx tsc --noEmit && npx eslint src/components/classroom`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/components/classroom/lesson-notes.tsx
git commit -m "Add lesson notes renderer"
```

---

### Task 3: Move the route tree to /classroom

This task is mechanical but wide. It creates the new tree, updates every internal link, adds redirects, and deletes the old tree. It ends with a green build, which is the real test.

**Files:**
- Create: `src/app/(dashboard)/classroom/page.tsx`, `loading.tsx`, `[subjectSlug]/page.tsx`, `[subjectSlug]/[topicSlug]/page.tsx`, `[subjectSlug]/[topicSlug]/quiz/page.tsx`
- Delete: the whole `src/app/(dashboard)/subjects/` tree
- Modify: `next.config.ts`, `src/lib/navigation.ts`, `src/components/ui/mobile-nav.tsx`, `src/app/(dashboard)/dashboard/page.tsx:149`, `src/app/(dashboard)/performance/page.tsx`, `src/components/path/gap-list.tsx`, `src/components/path/graph-view.tsx`, `src/components/path/next-topics.tsx`, `src/components/path/revision-queue.tsx`, `src/components/lesson/practice-result-actions.tsx`

- [ ] **Step 1: Copy the tree**

```bash
mkdir -p "src/app/(dashboard)/classroom"
cp -r "src/app/(dashboard)/subjects/." "src/app/(dashboard)/classroom/"
rm -rf "src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/lessons"
```

The `lessons` branch is dropped here and rebuilt as `/study` in Task 5.

- [ ] **Step 2: Rewrite every internal link**

Replace `/subjects` with `/classroom` across `src` — in string literals and template literals alike.

**The API route `/api/subjects` is not moving and must survive this.** A naive global replace rewrites it to `/api/classroom` and breaks the endpoint, so protect it with a sentinel first:

```bash
# 1. Park /api/subjects behind a sentinel
grep -rl '/api/subjects' src --include=*.ts --include=*.tsx \
  | xargs -r sed -i 's|/api/subjects|__API_SUBJECTS__|g'

# 2. Move the page routes
grep -rl '/subjects' src --include=*.ts --include=*.tsx \
  | xargs -r sed -i 's|/subjects|/classroom|g'

# 3. Restore the API route
grep -rl '__API_SUBJECTS__' src --include=*.ts --include=*.tsx \
  | xargs -r sed -i 's|__API_SUBJECTS__|/api/subjects|g'
```

Then confirm the outcome:

```bash
# No page route should remain
grep -rn '/subjects' src --include=*.ts --include=*.tsx | grep -v '/api/subjects'
# The API route should be intact
grep -rn '/api/subjects' src --include=*.ts --include=*.tsx
# No sentinel should survive
grep -rn '__API_SUBJECTS__' src
```

Expected: the first and third commands return nothing; the second still shows the API references.

Note this also updates the `IMMERSIVE_ROUTES` regex in `src/components/ui/mobile-nav.tsx`, which is correct — the quiz route still needs the app nav hidden.

- [ ] **Step 3: Rename the nav labels**

In `src/lib/navigation.ts` and `src/components/ui/mobile-nav.tsx`, change the item `name` from `"Subjects"` to `"Classroom"`. Leave the icon as `LuBookOpen`.

In `src/app/(dashboard)/dashboard/page.tsx`, change the quick-link `title` from `"Subjects"` to `"Classroom"` and its `description` from `"Browse curriculum"` to `"Notes, quizzes and practice"`.

- [ ] **Step 4: Add the redirects**

In `next.config.ts`, add to the `nextConfig` object:

```ts
  // The section moved from /subjects to /classroom. Permanent so bookmarks,
  // browser history and anything already shared keep working.
  async redirects() {
    return [
      {
        source: "/subjects",
        destination: "/classroom",
        permanent: true,
      },
      {
        source: "/subjects/:path*",
        destination: "/classroom/:path*",
        permanent: true,
      },
    ];
  },
```

- [ ] **Step 5: Delete the old tree**

```bash
rm -rf "src/app/(dashboard)/subjects"
```

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit && npx eslint src scripts && npm test && npm run build`
Expected: all clean. In the build's route list, `/classroom/...` entries appear and no `/subjects/...` entries remain.

A stale `.next/types/validator.ts` error referencing a deleted route means the build has not been re-run; run `npm run build` again.

- [ ] **Step 7: Commit**

```bash
git add -A "src/app/(dashboard)" src/lib/navigation.ts src/components next.config.ts
git commit -m "Move Subjects to Classroom with permanent redirects"
```

---

### Task 4: Class/term browser on the subject page

**Files:**
- Create: `src/components/classroom/class-term-browser.tsx`
- Modify: `src/app/(dashboard)/classroom/[subjectSlug]/page.tsx`

**Interfaces:**
- Consumes: `resolveClassLevel` from Task 1; `TERMS`, `TERM_LABELS` from `src/lib/curriculum-scope.ts`
- Produces: `<ClassTermBrowser subjectSlug={string} classes={ClassGroup[]} initialClassLevel={ClassLevel} practiceHref={(classLevel: string) => string} />` where `ClassGroup = { classLevel: string; terms: { term: string; topics: BrowserTopic[] }[] }` and `BrowserTopic = { slug: string; title: string; completed: boolean }`

- [ ] **Step 1: Write the component**

Create `src/components/classroom/class-term-browser.tsx` as a client component (it holds the selected-class state).

Requirements:
- `useState` initialised to `initialClassLevel` — the server resolves the default via `resolveClassLevel`, the client only holds the selection.
- A segmented control of `SS1 | SS2 | SS3`, `role="tablist"`, each button `role="tab"` with `aria-selected`. Classes with no topics render disabled.
- Wrapper is `sticky top-0 z-10` with the `sticky-chrome` class already used elsewhere.
- Below it, three term sections for the selected class. Each heading shows `TERM_LABELS[term]` and a completion count `{done}/{total} done` computed from `topics.filter(t => t.completed).length`.
- A term with no topics renders a muted "No topics yet" row rather than being omitted.
- Each topic is a `Link` to `/classroom/${subjectSlug}/${topic.slug}` with a check icon when `completed`.
- A practice button calling `practiceHref(selectedClass)`.

- [ ] **Step 2: Wire it into the subject page**

In `src/app/(dashboard)/classroom/[subjectSlug]/page.tsx`:

- Keep the existing query but also select `StudentProgress` for this student and subject so `completed` can be computed per topic. A topic counts as completed when a `StudentProgress` row exists for it with `status: "COMPLETED"`.
- Build `classes: ClassGroup[]` from the existing `grouped` structure.
- Compute `initialClassLevel` with `resolveClassLevel(session.user.classLevel, classesWithTopics)` where `classesWithTopics` lists class levels having at least one topic.
- Build `practiceHref` as:
  ```ts
  const practiceHref = (classLevel: string) =>
    `/practice/mock-exam?subjectId=${subject.id}` +
    `&fromClass=${classLevel}&fromTerm=FIRST` +
    `&toClass=${classLevel}&toTerm=THIRD`;
  ```
- Render `<ClassTermBrowser>` inside the existing `CurriculumViewToggle` list slot, leaving the graph slot untouched.

- [ ] **Step 3: Verify**

Run: `npx tsc --noEmit && npx eslint src scripts && npm run build`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/components/classroom/class-term-browser.tsx "src/app/(dashboard)/classroom/[subjectSlug]/page.tsx"
git commit -m "Add class and term browser to the subject page"
```

---

### Task 5: Topic page as notes, and the /study route

**Files:**
- Create: `src/components/classroom/topic-action-bar.tsx`
- Create: `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/study/page.tsx`
- Create: `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/practice/page.tsx`
- Create: `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/practice/result/page.tsx`
- Modify: `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/page.tsx`
- Modify: `src/components/lesson/lesson-player.tsx`, `src/components/lesson/practice-exit.tsx`, `src/components/lesson/practice-result-actions.tsx` (drop `lessonId` from the hrefs they build)

**Interfaces:**
- Consumes: `LessonNotes` (Task 2), `toNotes`, `topicNeighbours` (Task 1)
- Produces: `<TopicActionBar subjectSlug topicSlug hasDeck={boolean} />`

- [ ] **Step 1: Create the action bar**

Create `src/components/classroom/topic-action-bar.tsx`, a client component, sticky once scrolled (`sticky top-14 z-10` plus `sticky-chrome`).

Four actions:
- "Study step by step" → `/classroom/${subjectSlug}/${topicSlug}/study`
- "Quick quiz" → `/classroom/${subjectSlug}/${topicSlug}/quiz`
- Flashcards → label `hasDeck ? "Flashcards" : "Build flashcards"`. When `hasDeck`, link to `/flashcards`. When not, POST to `/api/flashcards/generate` with `{ lessonId }`, then navigate to the returned deck. The label must state which will happen so a click never silently creates data.
- "Practice" → `/classroom/${subjectSlug}/${topicSlug}/practice`

- [ ] **Step 2: Rebuild the topic page**

In `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/page.tsx`:

- Load the topic with its single lesson (`topic.subtopics[0].lessons[0]`), the student's progress, the lesson's resources, and sibling topics for navigation.
- Header: breadcrumb `Classroom / {subject} / {classLevel} · {termLabel}`, topic title, and the existing mastery value read-only.
- `<TopicActionBar>` beneath the header.
- `<LessonNotes blocks={parseBlocks(lesson.blocks)} fallbackContent={lesson.content} />`.
- Footer: previous/next from `topicNeighbours`, rendering nothing on the side that is `null`.
- Remove the old "lessons list" section entirely — it listed exactly one item.

- [ ] **Step 3: Create /study**

`src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/study/page.tsx` resolves the topic's single lesson server-side and renders the existing `LessonPlayer` with the same props it receives today, except that `backHref` is now `/classroom/${subjectSlug}/${topicSlug}`.

- [ ] **Step 4: Move practice and result under the topic**

Recreate the two deleted pages at `/classroom/[subjectSlug]/[topicSlug]/practice` and `.../practice/result`, resolving `lessonId` server-side from the topic instead of taking it from the URL. Update `practice-exit.tsx` and `practice-result-actions.tsx` so the hrefs they build no longer contain `/lessons/${lessonId}`.

- [ ] **Step 5: Verify no lessonId remains in any route**

```bash
grep -rn 'lessons/\${\|/lessons/' src/app src/components --include=*.tsx
```
Expected: no matches referring to a URL path. `lessonId` may still appear as an API argument (`/api/lessons/${id}/progress`), which is correct and must be left alone.

- [ ] **Step 6: Verify and commit**

```bash
npx tsc --noEmit && npx eslint src scripts && npm test && npm run build
git add -A "src/app/(dashboard)/classroom" src/components/classroom src/components/lesson
git commit -m "Make the topic page a note and drop lessonId from URLs"
```

---

### Task 6: Untimed quick quiz

The spec promises the quick quiz is "short and untimed". Today it is not: `/quiz` renders `QuizEngine`, which always runs a countdown, and `SessionData.deadlineAt` is a non-nullable `number` that `parseStoredSession` rejects when absent. This task widens the shared session to allow no deadline.

The runtime is already most of the way there — `hasExpired(null, now)` returns `false` and the timer effect early-returns when `deadlineAt` is null — so this is largely types and storage validation. It still touches code shared by the topic quiz, the scoped mock and the JAMB CBT, which is why it gets its own review gate.

**Files:**
- Modify: `src/components/assessment/exam-state.ts`
- Modify: `src/components/assessment/use-exam-session.ts`
- Modify: `src/components/assessment/exam-surface.tsx`
- Modify: `src/components/assessment/quiz-engine.tsx`
- Modify: `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/quiz/page.tsx`
- Modify: `scripts/test-exam-state.mts`

**Interfaces:**
- Produces: `SessionData.deadlineAt: number | null`; `QuizEngine` gains an optional `untimed?: boolean` prop

- [ ] **Step 1: Write the failing tests**

Add to `scripts/test-exam-state.mts`:

```ts
test("parseStoredSession restores an untimed session", () => {
  // A quick quiz has no deadline; it must still be resumable.
  const session = storedSession({ deadlineAt: null });
  const parsed = parseStoredSession(JSON.stringify(session), NOW);
  assert.ok(parsed);
  assert.equal(parsed.deadlineAt, null);
});

test("an untimed session never expires", () => {
  assert.equal(hasExpired(null, NOW), false);
  assert.equal(hasExpired(null, NOW + 10_000_000), false);
});

test("parseStoredSession still rejects a malformed deadline", () => {
  // null means untimed; a string is corruption and must not resume.
  const broken = { ...storedSession(), deadlineAt: "soon" };
  assert.equal(parseStoredSession(JSON.stringify(broken), NOW), null);
});
```

`storedSession` currently types `deadlineAt` as `number`; widen its parameter type so `{ deadlineAt: null }` compiles.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --import tsx --test --test-force-exit scripts/test-exam-state.mts`
Expected: FAIL — the untimed session is rejected because `typeof null !== "number"`.

- [ ] **Step 3: Widen the type and the validation**

In `src/components/assessment/exam-state.ts`:

```ts
export type SessionData = {
  attemptId: string;
  title: string;
  questions: ExamQuestion[];
  /**
   * Absolute epoch ms, or null for an untimed session such as the quick quiz.
   * Survives refreshes and tab-throttling; a countdown does not.
   */
  deadlineAt: number | null;
};
```

Replace the deadline validation in `parseStoredSession` with:

```ts
  // null is a legitimate value meaning "untimed"; anything non-numeric that
  // is not null is corruption.
  if (parsed.deadlineAt !== null) {
    if (
      typeof parsed.deadlineAt !== "number" ||
      !Number.isFinite(parsed.deadlineAt)
    ) {
      return null;
    }
    if (parsed.deadlineAt <= now) return null;
  }
```

`hasExpired` already accepts `number | null` and returns `false` for null — leave it unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Thread `untimed` through the session and surface**

In `use-exam-session.ts`, when the generated exam has no `deadlineAt` **and** `timeLimitMinutes` is falsy, set `deadlineAt: null` rather than computing one from `defaultTimeLimitMinutes`.

In `exam-surface.tsx`, when `session.timeRemaining` has no deadline behind it, hide the timer chip and its show/hide toggle entirely rather than rendering `0:00`. Add `const untimed = session.deadlineAt == null;` to the destructured session and guard the timer block with it. Export `deadlineAt` from `useExamSession`'s return object so the surface can read it.

In `quiz-engine.tsx`, add an optional `untimed?: boolean` prop. When true, pass `defaultTimeLimitMinutes: 0` so the session resolves to no deadline.

- [ ] **Step 6: Make the topic quiz untimed and short**

In `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/quiz/page.tsx`, pass `untimed` and reduce `count` from `10` to `5`, matching "short" in the spec:

```tsx
    <QuizEngine
      subjectSlug={subjectSlug}
      topicSlug={topicSlug}
      count={5}
      untimed
      backHref={`/classroom/${subjectSlug}/${topicSlug}`}
    />
```

Note `generateQuizSchema` enforces `count` between 5 and 60, so 5 is the legal minimum.

- [ ] **Step 7: Confirm the timed surfaces are unaffected**

Run: `npm test` and check the JAMB CBT and scoped mock still receive a deadline — both generators return `deadlineAt`, so neither should take the untimed path.

Then start the dev server and confirm a scoped mock still shows a counting-down timer.

- [ ] **Step 8: Verify and commit**

```bash
npx tsc --noEmit && npx eslint src scripts && npm test && npm run build
git add src/components/assessment "src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/quiz/page.tsx" scripts/test-exam-state.mts
git commit -m "Allow untimed exam sessions and make the quick quiz untimed"
```

---

### Task 7: Resources section

**Files:**
- Create: `src/components/classroom/topic-resources.tsx`
- Modify: `src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/page.tsx`

**Interfaces:**
- Consumes: `selectResources` from Task 1
- Produces: `<TopicResources lessonResources={ResourceItem[]} subjectResources={ResourceItem[]} subjectName={string} />` where `ResourceItem = { id: string; title: string; url: string; resourceType: string; description?: string | null }`

- [ ] **Step 1: Write the component**

Create `src/components/classroom/topic-resources.tsx`, a server component.

- Call `selectResources(lessonResources, subjectResources)`.
- `source === "none"` → return `null`. The section must not render an empty shell.
- `source === "topic"` → heading "More resources".
- `source === "subject"` → heading `More ${subjectName} resources`, so it never implies these belong to this topic.
- Each item is a card linking to `url` with `target="_blank" rel="noopener noreferrer"`, showing an icon chosen by `resourceType` and the title.

Note `LessonResource` has `caption` where `SubjectResource` has `title`; the page maps both into `ResourceItem` before passing them in, so the component sees one shape.

- [ ] **Step 2: Wire it in**

Render `<TopicResources>` in the topic page between the notes and the previous/next footer.

- [ ] **Step 3: Verify and commit**

```bash
npx tsc --noEmit && npx eslint src scripts && npm run build
git add src/components/classroom/topic-resources.tsx "src/app/(dashboard)/classroom/[subjectSlug]/[topicSlug]/page.tsx"
git commit -m "Add topic resources with subject fallback"
```

---

### Task 8: Mock picker deep-linking

**Files:**
- Modify: `src/components/practice/mock-exam-picker.tsx`
- Modify: `src/app/(dashboard)/practice/mock-exam/page.tsx`

**Interfaces:**
- Consumes: the practice CTA href built in Task 4
- Produces: `<MockExamPicker initialSubjectId={string | null} initialFrom={ScopePoint | null} initialTo={ScopePoint | null} />`

- [ ] **Step 1: Accept the params**

`src/app/(dashboard)/practice/mock-exam/page.tsx` reads `searchParams` and passes `initialSubjectId`, `initialFrom` and `initialTo` to the picker. Validate the scope values with `isValidScope` from `src/lib/curriculum-scope.ts`; anything invalid becomes `null` rather than throwing.

- [ ] **Step 2: Hydrate the picker**

In `mock-exam-picker.tsx`:

- Hold the incoming values in state initialised from props. **The picker still opens at the board step** — a subject cannot be resolved before a board is chosen, because subjects are listed per board.
- Inside the existing `chooseBoard` handler, after `setSubjects(data.subjects ?? [])`, check whether `initialSubjectId` appears in the returned list. If it does, select it and apply `initialFrom`/`initialTo`, enabling range mode when the two differ. If it does not, drop the pre-fill silently and behave as if opened cold.
- Do not add an effect for this. The rule against `setState` in an effect body is enforced as an error, and the board choice is already a user event.

- [ ] **Step 3: Verify the two paths by hand**

Start the dev server and check both:
- `/practice/mock-exam?subjectId=<a real Biology id>&fromClass=SS1&fromTerm=FIRST&toClass=SS1&toTerm=THIRD` then choosing **JAMB** → Biology selected, range mode on, scope reads "all of SS1".
- The same URL then choosing **NECO** → pre-fill dropped, picker behaves as if opened cold (NECO has no scoped subjects).

- [ ] **Step 4: Verify and commit**

```bash
npx tsc --noEmit && npx eslint src scripts && npm test && npm run build
git add src/components/practice/mock-exam-picker.tsx "src/app/(dashboard)/practice/mock-exam/page.tsx"
git commit -m "Let the mock picker hydrate from query params"
```

---

## Final verification

- [ ] `npx tsc --noEmit` — no errors
- [ ] `npx eslint src scripts` — no errors
- [ ] `npm test` — all pass
- [ ] `npm run build` — compiles, route list shows `/classroom/*` and no `/subjects/*`
- [ ] `curl -sI localhost:3000/subjects/physics` returns 308 to `/classroom/physics`
- [ ] Open a topic: notes render, all four actions work, previous/next stop at the class boundary
- [ ] Open a topic on a phone viewport: the class control stays pinned, the action bar stays reachable
- [ ] Toggle OS dark mode on the topic page: no raw palette colours leak through
