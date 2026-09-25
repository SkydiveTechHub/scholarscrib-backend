# Build Cards From A Lesson — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rebuilding a lesson-derived flashcard deck non-destructive, and give the "Build cards from a lesson" section a searchable picker, a preview of what a build would do, and a link to the deck it produced.

**Architecture:** Each generated card carries the `id` of the lesson block it came from. Persisted as `Flashcard.sourceKey`, that becomes the natural key for matching a regenerated deck against the stored one. A pure `diffDeck()` classifies each card as unchanged / updated / created / removed; the write path applies that diff in place instead of deleting every card, so `FlashcardReview` rows survive. The same pure function powers a read-only preview endpoint, so what the student is shown and what happens cannot disagree.

**Tech Stack:** Next.js (App Router, route handlers), Prisma + Postgres (Supabase), Zod, React client components, `node:test` via `tsx` for unit tests.

**Spec:** `docs/superpowers/specs/2026-08-25-flashcard-build-from-lesson-design.md`

## Global Constraints

- **This is not the Next.js you know.** Per `AGENTS.md`, read the relevant guide in `node_modules/next/dist/docs/` before writing route handlers or components. Heed deprecation notices.
- **Migration SQL must be LF.** `.gitattributes` pins `prisma/migrations/**/*.sql text eol=lf`. Do not defeat it; a CRLF checkout changes Prisma's checksum and reports drift.
- **`prisma migrate` cannot reach the database from this machine.** `DIRECT_URL` points at the pgbouncer pooler. The migration directory is authored by hand and the SQL applied through the Supabase SQL Editor. Never run `prisma migrate dev` or `prisma migrate deploy` as part of a task.
- **Tests are `node:test` run through `tsx`.** New test files must be added to the `test` script in `package.json` or they will never run.
- **Card generation is deterministic and pure.** `generateCardsFromLesson` and `diffDeck` must not touch the database, the clock, or randomness.
- **Every block yields at most one card**, so `sourceKey` is unique within a deck by construction. Do not add de-duplication logic.
- Existing outcome unions (`"lesson-not-found"`, `"no-cards"`) are part of the contract — the route maps them to 404 and 422. Keep them.

---

### Task 1: Cards remember which block made them

`GeneratedCard` gains `sourceKey`, set from the originating block's `id`. Set it inside each converter rather than patching it on in the loop, so "a card knows where it came from" is a property of construction.

**Files:**
- Modify: `src/lib/flashcard-content.ts:261-266` (type), `:309-346` (`conceptToCards`), `:348-366` (`checkToCard`), `:368-380` (`mistakeToCard`), `:382-393` (`mnemonicToCard`), `:395-416` (`exampleToCard`), `:418-429` (`tipToCard`), `:433-473` (`generateCardsFromLesson`)
- Test: `scripts/test-flashcard-content.mts`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `export type GeneratedCard = { sourceKey: string; cardType: FlashcardType; prompt: string; payload: FlashcardPayload; difficulty: AuthoredDifficulty }`. Task 2 and Task 4 depend on `sourceKey` being present and non-empty on every generated card.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-flashcard-content.mts`:

```ts
test("each card records the id of the block that produced it", () => {
  const cards = cardsFor([
    concept({ id: "c-alpha", title: "Density", text: "Mass per unit volume." }),
    { type: "mistake", id: "m-beta", wrong: "Mass is weight.", right: "Weight is a force." } as LessonBlock,
    { type: "tip", id: "t-gamma", text: "Always convert to SI first." } as LessonBlock,
  ]);
  assert.deepEqual(
    cards.map((c) => c.sourceKey),
    ["c-alpha", "m-beta", "t-gamma"],
  );
});

test("source keys are unique across a generated deck", () => {
  const blocks = [
    concept({ id: "c-1", title: "One", text: "First." }),
    concept({ id: "c-2", title: "Two", text: "Second." }),
    concept({ id: "c-3", title: "Three", text: "Third." }),
  ];
  const keys = cardsFor(blocks).map((c) => c.sourceKey);
  assert.equal(new Set(keys).size, keys.length);
});

test("a skipped block contributes no source key", () => {
  const cards = cardsFor([
    concept({ id: "objectives-1", title: "Learning Objectives", text: "By the end of this lesson, students should be able to:\n1. Define x" }),
    concept({ id: "c-real", title: "Real Content", text: "Something teachable." }),
  ]);
  assert.deepEqual(cards.map((c) => c.sourceKey), ["c-real"]);
});
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `npx tsx --test scripts/test-flashcard-content.mts`
Expected: FAIL — the three new tests report `undefined` where a source key is expected (`sourceKey` is not yet on `GeneratedCard`). Pre-existing tests in the file must still pass.

- [ ] **Step 3: Add `sourceKey` to the type**

In `src/lib/flashcard-content.ts`, change the `GeneratedCard` type:

```ts
export type GeneratedCard = {
  /** id of the lesson block this card was generated from. */
  sourceKey: string;
  cardType: FlashcardType;
  prompt: string;
  payload: FlashcardPayload;
  difficulty: AuthoredDifficulty;
};
```

- [ ] **Step 4: Thread the block id through every converter**

Each converter's parameter type gains `id: string`, and each returned object gains `sourceKey: block.id`. The full set of changes:

```ts
function conceptToCards(block: {
  id: string;
  title?: string;
  text: string;
  reveal?: string;
}): GeneratedCard | null {
  if (isScaffolding(block)) return null;

  const title = stripHeadingNumber(block.title?.trim() ?? "");

  if (!title) {
    if (!block.reveal?.trim()) return null;
    const question = block.text.trim();
    const payload: ScenarioPayload = {
      scenario: "",
      question,
      answer: block.reveal.trim(),
    };
    return { sourceKey: block.id, cardType: "SCENARIO", prompt: question, payload, difficulty: "BASIC" };
  }

  const payload: DefinitionPayload = {
    term: title,
    definition: block.text,
    ...(block.reveal ? { example: block.reveal } : {}),
  };
  return { sourceKey: block.id, cardType: "DEFINITION", prompt: title, payload, difficulty: "BASIC" };
}

function checkToCard(block: {
  id: string;
  question: string;
  options: Record<string, string>;
  answer: string;
  explanation: string;
}): GeneratedCard {
  const answer = block.options[block.answer];
  const payload: ScenarioPayload = {
    scenario: block.question,
    question: "Which option is correct?",
    answer: answer ?? block.answer,
    explanation: block.explanation,
  };
  return {
    sourceKey: block.id,
    cardType: "SCENARIO",
    prompt: shortLabel(block.question, 40),
    payload,
    difficulty: "INTERMEDIATE",
  };
}

function mistakeToCard(block: { id: string; wrong: string; right: string }): GeneratedCard {
  const payload: TrueFalsePayload = {
    statement: block.wrong,
    answer: false,
    explanation: block.right,
  };
  return {
    sourceKey: block.id,
    cardType: "TRUE_FALSE",
    prompt: shortLabel(block.wrong, 40),
    payload,
    difficulty: "INTERMEDIATE",
  };
}

function mnemonicToCard(block: {
  id: string;
  phrase: string;
  encoded: string[];
}): GeneratedCard {
  const payload: DefinitionPayload = {
    term: block.phrase,
    definition: `"${block.phrase}" encodes:\n${block.encoded.join(", ")}`,
  };
  return {
    sourceKey: block.id,
    cardType: "DEFINITION",
    prompt: block.phrase,
    payload,
    difficulty: "BASIC",
  };
}

function exampleToCard(block: {
  id: string;
  problem: string;
  steps: string[];
  answer: string;
}): GeneratedCard {
  const payload: ScenarioPayload = {
    scenario: block.problem,
    question: "Solve it, then check your working.",
    answer: block.answer,
    explanation: block.steps.join("\n"),
  };
  return {
    sourceKey: block.id,
    cardType: "SCENARIO",
    prompt: shortLabel(block.problem, 40),
    payload,
    difficulty: "ADVANCED",
  };
}

function tipToCard(block: { id: string; text: string }): GeneratedCard {
  const payload: DefinitionPayload = {
    term: "Exam tip",
    definition: block.text,
  };
  return {
    sourceKey: block.id,
    cardType: "DEFINITION",
    prompt: "Exam tip",
    payload,
    difficulty: "BASIC",
  };
}
```

`generateCardsFromLesson` needs no change — every `LessonBlock` already carries `id`, so the existing `conceptToCards(block)` / `checkToCard(block)` calls now satisfy the widened parameter types.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `npx tsx --test scripts/test-flashcard-content.mts`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 6: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors in `src/lib/flashcard-content.ts`. There is one known pre-existing error elsewhere — `src/lib/dashboard.ts(160,22): Cannot find name 'recommendNext'` — which is unrelated to this work and must be left alone.

- [ ] **Step 7: Commit**

```bash
git add src/lib/flashcard-content.ts scripts/test-flashcard-content.mts
git commit -m "feat(flashcards): record the source block id on every generated card"
```

---

### Task 2: The diff

A pure function that matches a regenerated deck against the stored one. This is the heart of the feature and the only part that is thoroughly testable, so it goes in before anything writes to the database.

**Files:**
- Create: `src/lib/flashcard-diff.ts`
- Create: `scripts/test-flashcard-diff.mts`
- Modify: `package.json` (add the new test file to the `test` script)

**Interfaces:**
- Consumes: `GeneratedCard` (with `sourceKey`) from Task 1.
- Produces:
  ```ts
  export type ExistingCard = {
    id: string;
    sourceKey: string | null;
    orderIndex: number;
    cardType: string;
    prompt: string | null;
    payload: unknown;
    difficulty: string;
  };
  export type DeckDiff = {
    unchanged: { id: string; card: GeneratedCard; orderIndex: number; needsWrite: boolean }[];
    updated: { id: string; card: GeneratedCard; orderIndex: number }[];
    created: { card: GeneratedCard; orderIndex: number }[];
    removed: { id: string }[];
  };
  export function diffDeck(existing: ExistingCard[], generated: GeneratedCard[]): DeckDiff;
  export type DiffCounts = { unchanged: number; updated: number; created: number; removed: number };
  export function diffCounts(diff: DeckDiff): DiffCounts;
  ```
  Task 4 uses `diffDeck` + `diffCounts` on the write path; Task 5 uses both on the read path.

**Design notes the implementer needs:**

- `orderIndex` on every entry is the card's index in the freshly generated array — its new position, not its old one.
- `needsWrite` on an `unchanged` entry is true when the stored row still has to be touched: its `orderIndex` moved, or its `sourceKey` is null and needs stamping. An unchanged card in a fully-keyed deck at the same position has `needsWrite: false` and is not written at all.
- "Unchanged" compares the *card body a student would see* — `cardType`, `prompt`, `payload`, `difficulty`. A card that only moved position is unchanged; its memory is intact. That is why `ExistingCard` carries those four fields: without the stored body there is nothing to compare against, and every card would read as updated.
- Both sides are compared as **canonical JSON** (object keys sorted recursively), because Prisma returns JSON columns with no key-order guarantee. This also absorbs the type asymmetry: `GeneratedCard.prompt` is `string` while the column is `string | null`, and `cardType` / `difficulty` come back from Prisma as enum strings.
- **`existing` rows are not assumed sorted.** Sort defensively by `orderIndex` before the positional pass.

- [ ] **Step 1: Write the failing tests**

Create `scripts/test-flashcard-diff.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { diffDeck, diffCounts, type ExistingCard } from "../src/lib/flashcard-diff";
import type { GeneratedCard } from "../src/lib/flashcard-content";

function card(sourceKey: string, text = "body"): GeneratedCard {
  return {
    sourceKey,
    cardType: "DEFINITION",
    prompt: sourceKey,
    payload: { term: sourceKey, definition: text },
    difficulty: "BASIC",
  };
}

/**
 * A stored row. `bodyKey` says which card's body it holds — normally the same
 * as `sourceKey`, but they are separate arguments so the legacy tests can build
 * an unkeyed row that nonetheless holds a known card's body.
 */
function row(opts: {
  id: string;
  sourceKey: string | null;
  orderIndex: number;
  bodyKey: string;
  text?: string;
}): ExistingCard {
  const body = card(opts.bodyKey, opts.text);
  return {
    id: opts.id,
    sourceKey: opts.sourceKey,
    orderIndex: opts.orderIndex,
    cardType: body.cardType,
    prompt: body.prompt,
    payload: body.payload,
    difficulty: body.difficulty,
  };
}

/** Three keyed rows holding a, b, c at positions 0, 1, 2. */
function abcRows(): ExistingCard[] {
  return [
    row({ id: "row-a", sourceKey: "a", orderIndex: 0, bodyKey: "a" }),
    row({ id: "row-b", sourceKey: "b", orderIndex: 1, bodyKey: "b" }),
    row({ id: "row-c", sourceKey: "c", orderIndex: 2, bodyKey: "c" }),
  ];
}

test("regenerating an identical deck changes nothing", () => {
  const diff = diffDeck(abcRows(), [card("a"), card("b"), card("c")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 3, updated: 0, created: 0, removed: 0 });
  assert.equal(
    diff.unchanged.every((u) => u.needsWrite === false),
    true,
    "a fully-keyed deck at rest writes nothing at all",
  );
});

test("an edited block updates exactly its own card", () => {
  const diff = diffDeck(abcRows(), [card("a"), card("b", "REWRITTEN"), card("c")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 2, updated: 1, created: 0, removed: 0 });
  assert.equal(diff.updated[0].id, "row-b");
  assert.equal(diff.updated[0].card.sourceKey, "b");
});

test("reordering blocks keeps every card but rewrites its position", () => {
  const diff = diffDeck(abcRows(), [card("c"), card("b"), card("a")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 3, updated: 0, created: 0, removed: 0 });
  const moved = diff.unchanged.find((u) => u.card.sourceKey === "a");
  assert.equal(moved?.orderIndex, 2, "a is now last");
  assert.equal(moved?.needsWrite, true, "a moved, so its row must be written");
  const stayed = diff.unchanged.find((u) => u.card.sourceKey === "b");
  assert.equal(stayed?.needsWrite, false, "b did not move");
});

test("a removed block removes exactly its card", () => {
  const diff = diffDeck(abcRows(), [card("a"), card("c")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 2, updated: 0, created: 0, removed: 1 });
  assert.deepEqual(diff.removed, [{ id: "row-b" }]);
});

test("an added block creates exactly its card", () => {
  const rows = abcRows().slice(0, 2);
  const diff = diffDeck(rows, [card("a"), card("b"), card("d")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 2, updated: 0, created: 1, removed: 0 });
  assert.equal(diff.created[0].card.sourceKey, "d");
  assert.equal(diff.created[0].orderIndex, 2);
});

test("a legacy deck with no keys matches by position and gets stamped", () => {
  const rows = [
    row({ id: "legacy-0", sourceKey: null, orderIndex: 0, bodyKey: "a" }),
    row({ id: "legacy-1", sourceKey: null, orderIndex: 1, bodyKey: "b" }),
    row({ id: "legacy-2", sourceKey: null, orderIndex: 2, bodyKey: "c" }),
  ];
  const diff = diffDeck(rows, [card("a"), card("b"), card("c")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 3, updated: 0, created: 0, removed: 0 });
  assert.equal(
    diff.unchanged.every((u) => u.needsWrite === true),
    true,
    "every legacy row still needs its key stamped",
  );
  assert.equal(diff.unchanged.find((u) => u.id === "legacy-1")?.card.sourceKey, "b");
});

test("keyed rows win before the positional fallback runs", () => {
  // "b" is keyed but sits last; the unkeyed rows must not steal it on the way past.
  const rows = [
    row({ id: "legacy-0", sourceKey: null, orderIndex: 0, bodyKey: "a" }),
    row({ id: "legacy-1", sourceKey: null, orderIndex: 1, bodyKey: "z" }),
    row({ id: "keyed-b", sourceKey: "b", orderIndex: 2, bodyKey: "b" }),
  ];
  const diff = diffDeck(rows, [card("b"), card("a"), card("z")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 3, updated: 0, created: 0, removed: 0 });
  assert.equal(diff.unchanged.find((u) => u.card.sourceKey === "b")?.id, "keyed-b");
  assert.equal(diff.unchanged.find((u) => u.card.sourceKey === "a")?.id, "legacy-0");
  assert.equal(diff.unchanged.find((u) => u.card.sourceKey === "z")?.id, "legacy-1");
});

test("a legacy deck that gained a leading block shifts every positional match", () => {
  // Documented consequence, not an accident: with no keys to match on, position
  // is all there is, so inserting at the top re-points every legacy row. Those
  // two cards lose their schedule — once, and only for decks predating the key.
  const rows = [
    row({ id: "legacy-0", sourceKey: null, orderIndex: 0, bodyKey: "a" }),
    row({ id: "legacy-1", sourceKey: null, orderIndex: 1, bodyKey: "b" }),
  ];
  const diff = diffDeck(rows, [card("new"), card("a"), card("b")]);

  assert.deepEqual(diffCounts(diff), { unchanged: 0, updated: 2, created: 1, removed: 0 });
  assert.equal(diff.updated.find((u) => u.id === "legacy-0")?.card.sourceKey, "new");
});

test("an empty stored deck creates everything", () => {
  const diff = diffDeck([], [card("a"), card("b")]);
  assert.deepEqual(diffCounts(diff), { unchanged: 0, updated: 0, created: 2, removed: 0 });
});

test("payload key order does not count as a change", () => {
  const reordered: GeneratedCard = {
    ...card("a"),
    payload: { definition: "body", term: "a" } as GeneratedCard["payload"],
  };
  const diff = diffDeck([abcRows()[0]], [reordered]);
  assert.deepEqual(diffCounts(diff), { unchanged: 1, updated: 0, created: 0, removed: 0 });
});
```

- [ ] **Step 2: Register the test file**

In `package.json`, append `scripts/test-flashcard-diff.mts` to the end of the `test` script's file list (after `scripts/test-attempt-abandonment.mts`).

- [ ] **Step 3: Run the tests and verify they fail**

Run: `npx tsx --test scripts/test-flashcard-diff.mts`
Expected: FAIL — `Cannot find module '../src/lib/flashcard-diff'`.

- [ ] **Step 4: Write the implementation**

Create `src/lib/flashcard-diff.ts`:

```ts
// Matches a regenerated deck against the cards already stored for it, so a
// rebuild can update in place instead of deleting and recreating. Deleting a
// Flashcard cascades away every student's FlashcardReview and
// FlashcardReviewLog rows for it (prisma/schema.prisma), so "which cards are
// the same card" is a data-loss question, not a cosmetic one.
//
// Pure by design: the write path in flashcards.ts computes the diff before it
// opens a transaction, which is what makes this testable without a database.

import type { GeneratedCard } from "./flashcard-content";

export type ExistingCard = {
  id: string;
  sourceKey: string | null;
  orderIndex: number;
  // The stored body, so "same card" can be distinguished from "same slot".
  cardType: string;
  prompt: string | null;
  payload: unknown;
  difficulty: string;
};

export type DeckDiff = {
  /** Same card, same content. `needsWrite` when its position or key must change. */
  unchanged: { id: string; card: GeneratedCard; orderIndex: number; needsWrite: boolean }[];
  /** Same card, different content. */
  updated: { id: string; card: GeneratedCard; orderIndex: number }[];
  /** No stored row claimed this card. */
  created: { card: GeneratedCard; orderIndex: number }[];
  /** No generated card claimed this row — its block is gone. */
  removed: { id: string }[];
};

export type DiffCounts = {
  unchanged: number;
  updated: number;
  created: number;
  removed: number;
};

/** Stable stringification, so JSON column key order never reads as a change. */
function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
}

/** The card body a student actually sees. Position is deliberately excluded. */
function bodyOf(card: GeneratedCard): string {
  return canonical({
    cardType: card.cardType,
    prompt: card.prompt,
    payload: card.payload,
    difficulty: card.difficulty,
  });
}

/** The same four fields as they are stored. */
function storedBodyOf(row: ExistingCard): string {
  return canonical({
    cardType: row.cardType,
    prompt: row.prompt,
    payload: row.payload,
    difficulty: row.difficulty,
  });
}

export function diffDeck(
  existing: ExistingCard[],
  generated: GeneratedCard[],
): DeckDiff {
  const diff: DeckDiff = { unchanged: [], updated: [], created: [], removed: [] };

  const claimed = new Set<string>();
  const byKey = new Map<string, ExistingCard>();
  for (const row of existing) {
    if (row.sourceKey !== null) byKey.set(row.sourceKey, row);
  }

  // Pass 1 — match on the source block id. Once a deck has been through one
  // re-sync this is the only pass that does anything.
  const matches = new Map<number, ExistingCard>();
  generated.forEach((card, index) => {
    const row = byKey.get(card.sourceKey);
    if (row && !claimed.has(row.id)) {
      claimed.add(row.id);
      matches.set(index, row);
    }
  });

  // Pass 2 — legacy rows only. Cards were written in block order, so for a deck
  // predating sourceKey, position *is* the old identity. Runs once per deck:
  // the caller stamps the key, and the next re-sync is pure pass 1.
  const legacy = existing
    .filter((row) => row.sourceKey === null && !claimed.has(row.id))
    .sort((a, b) => a.orderIndex - b.orderIndex);
  let legacyCursor = 0;
  generated.forEach((_, index) => {
    if (matches.has(index)) return;
    const row = legacy[legacyCursor];
    if (!row) return;
    legacyCursor += 1;
    claimed.add(row.id);
    matches.set(index, row);
  });

  generated.forEach((card, index) => {
    const row = matches.get(index);
    if (!row) {
      diff.created.push({ card, orderIndex: index });
      return;
    }
    if (storedBodyOf(row) === bodyOf(card)) {
      diff.unchanged.push({
        id: row.id,
        card,
        orderIndex: index,
        needsWrite: row.orderIndex !== index || row.sourceKey === null,
      });
    } else {
      diff.updated.push({ id: row.id, card, orderIndex: index });
    }
  });

  for (const row of existing) {
    if (!claimed.has(row.id)) diff.removed.push({ id: row.id });
  }

  return diff;
}

export function diffCounts(diff: DeckDiff): DiffCounts {
  return {
    unchanged: diff.unchanged.length,
    updated: diff.updated.length,
    created: diff.created.length,
    removed: diff.removed.length,
  };
}
```

The asserted counts in Step 1 are the specification. If a test fails, the implementation is wrong — do not adjust the assertions to match it.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `npx tsx --test scripts/test-flashcard-diff.mts`
Expected: PASS, all 10 tests.

- [ ] **Step 6: Verify the whole suite still runs**

Run: `npm test`
Expected: PASS, and the new file appears in the run.

- [ ] **Step 7: Commit**

```bash
git add src/lib/flashcard-diff.ts scripts/test-flashcard-diff.mts package.json
git commit -m "feat(flashcards): add the pure deck diff that keeps review state alive"
```

---

### Task 3: The migration

One column and one unique index. Authored by hand; applied through the Supabase SQL Editor.

**Files:**
- Modify: `prisma/schema.prisma:758-775` (the `Flashcard` model)
- Create: `prisma/migrations/20260825000000_flashcard_source_key/migration.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: `Flashcard.sourceKey` (`String?`) and `@@unique([deckId, sourceKey])`, which Task 4 selects and writes.

- [ ] **Step 1: Edit the Prisma schema**

In `prisma/schema.prisma`, add the field and index to `Flashcard`:

```prisma
model Flashcard {
  id         String        @id @default(cuid())
  deckId     String
  deck       FlashcardDeck @relation(fields: [deckId], references: [id], onDelete: Cascade)
  cardType   FlashcardType
  prompt     String? // short front label for lists ("Mitochondrion")
  payload    Json // typed card body (authoring format in the spec)
  difficulty Difficulty    @default(INTERMEDIATE)
  tags       Json?
  orderIndex Int           @default(0)
  // id of the lesson block this card was generated from. Nullable: rows that
  // predate re-sync have no key (stamped on first re-sync), and AUTHORED decks
  // have no source block at all. Postgres does not collide nulls under a
  // unique index, so any number of them coexist in one deck.
  sourceKey  String?

  reviews   FlashcardReview[]
  reviewLog FlashcardReviewLog[]

  createdAt DateTime @default(now())

  @@unique([deckId, sourceKey])
  @@index([deckId, orderIndex])
}
```

- [ ] **Step 2: Write the migration SQL**

Create `prisma/migrations/20260825000000_flashcard_source_key/migration.sql` **with LF line endings**:

```sql
-- AlterTable
ALTER TABLE "Flashcard" ADD COLUMN     "sourceKey" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "Flashcard_deckId_sourceKey_key" ON "Flashcard"("deckId", "sourceKey");
```

- [ ] **Step 3: Verify the line endings survived**

Run: `git add prisma/migrations/20260825000000_flashcard_source_key/migration.sql && git diff --cached --stat`
Expected: the file is staged with **no** "LF will be replaced by CRLF" warning. `.gitattributes` pins it; if a warning appears, the pin is not taking effect — stop and report rather than working around it.

- [ ] **Step 4: Regenerate the Prisma client**

Run: `npx prisma generate`
Expected: succeeds and writes the client. This is a local codegen step and does **not** touch the database.

- [ ] **Step 5: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: only the known pre-existing `src/lib/dashboard.ts(160,22)` error.

- [ ] **Step 6: Apply the SQL by hand**

Do **not** run `prisma migrate`. Paste the contents of `migration.sql` into the Supabase SQL Editor and run it, then record the migration as applied so `prisma migrate status` does not report drift. If you lack access to do this, stop and hand it back rather than guessing — Task 4 cannot be verified against a real database without it, but its code and Task 4's tests do not depend on it.

- [ ] **Step 7: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/20260825000000_flashcard_source_key/migration.sql
git commit -m "feat(db): key flashcards to the lesson block that produced them"
```

---

### Task 4: Apply the diff instead of deleting everything

The actual fix. `generateDeckFromLesson` stops calling `deleteMany` on the whole deck.

**Files:**
- Modify: `src/lib/flashcards.ts:180-250` (`generateDeckFromLesson`)
- Modify: `src/app/api/flashcards/generate/route.ts` (doc comment only)

**Interfaces:**
- Consumes: `diffDeck`, `diffCounts`, `ExistingCard`, `DiffCounts` from Task 2; `sourceKey` on `GeneratedCard` from Task 1; the `sourceKey` column from Task 3.
- Produces: `generateDeckFromLesson(userId: string, lessonId: string)` now resolves to `"lesson-not-found" | "no-cards" | { deck: FlashcardDeck; counts: DiffCounts; cardCount: number }`. Task 7 reads `counts` and `cardCount` off the JSON response; Task 5 reuses the same `DiffCounts` shape.

- [ ] **Step 1: Replace the body of `generateDeckFromLesson`**

In `src/lib/flashcards.ts`, update the import line and rewrite the function. Add to the imports at the top:

```ts
import { diffDeck, diffCounts, type ExistingCard } from "./flashcard-diff";
```

Then replace the whole function (docblock included):

```ts
/**
 * Converts a lesson's blocks into a shared deck (source: LESSON), re-syncing in
 * place when the deck already exists.
 *
 * This deliberately does NOT delete and recreate. Deleting a Flashcard cascades
 * away every student's FlashcardReview and FlashcardReviewLog rows for it, and
 * lesson decks are shared — one student re-running the build used to reset the
 * whole cohort's memory state. Cards are matched to stored rows by the id of
 * the lesson block that produced them, so unchanged cards keep their schedule
 * and only a card whose block is genuinely gone is deleted.
 *
 * `"lesson-not-found"` and `"no-cards"` are outcomes, not exceptions, so the
 * caller can map them to 404 and 422.
 */
export async function generateDeckFromLesson(userId: string, lessonId: string) {
  const lesson = await db.lesson.findUnique({
    where: { id: lessonId },
    include: {
      subtopic: { include: { topic: { select: { id: true, subjectId: true } } } },
    },
  });
  if (!lesson) return "lesson-not-found" as const;

  const generated = generateCardsFromLesson(lesson);
  if (generated.cards.length === 0) return "no-cards" as const;

  const subjectId = lesson.subtopic.topic.subjectId;
  const topicId = lesson.subtopic.topicId;

  const result = await db.$transaction(async (tx) => {
    const deckRow = await tx.flashcardDeck.upsert({
      where: { lessonId_source: { lessonId, source: "LESSON" } },
      create: {
        title: generated.title,
        description: generated.description,
        source: "LESSON",
        lessonId,
        subjectId,
        topicId,
        createdBy: userId,
      },
      update: {
        title: generated.title,
        description: generated.description,
        subjectId,
        topicId,
      },
    });

    const existing: ExistingCard[] = await tx.flashcard.findMany({
      where: { deckId: deckRow.id },
      select: {
        id: true,
        sourceKey: true,
        orderIndex: true,
        cardType: true,
        prompt: true,
        payload: true,
        difficulty: true,
      },
    });

    const diff = diffDeck(existing, generated.cards);

    // Removals first: a deleted row frees its (deckId, sourceKey) slot before
    // any surviving card is written into it.
    if (diff.removed.length > 0) {
      await tx.flashcard.deleteMany({
        where: { id: { in: diff.removed.map((r) => r.id) } },
      });
    }

    for (const entry of diff.updated) {
      await tx.flashcard.update({
        where: { id: entry.id },
        data: {
          sourceKey: entry.card.sourceKey,
          cardType: entry.card.cardType,
          prompt: entry.card.prompt,
          payload: entry.card.payload as object,
          difficulty: entry.card.difficulty,
          orderIndex: entry.orderIndex,
        },
      });
    }

    // Unchanged cards keep their review state untouched; only position and a
    // missing key are ever written, and only when they actually differ.
    for (const entry of diff.unchanged) {
      if (!entry.needsWrite) continue;
      await tx.flashcard.update({
        where: { id: entry.id },
        data: { sourceKey: entry.card.sourceKey, orderIndex: entry.orderIndex },
      });
    }

    if (diff.created.length > 0) {
      await tx.flashcard.createMany({
        data: diff.created.map((entry) => ({
          deckId: deckRow.id,
          sourceKey: entry.card.sourceKey,
          cardType: entry.card.cardType,
          prompt: entry.card.prompt,
          payload: entry.card.payload as object,
          difficulty: entry.card.difficulty,
          orderIndex: entry.orderIndex,
        })),
      });
    }

    return { deck: deckRow, counts: diffCounts(diff) };
  });

  return { ...result, cardCount: generated.cards.length };
}
```

- [ ] **Step 2: Update the route's doc comment**

In `src/app/api/flashcards/generate/route.ts`, replace the comment above the handler. The code is unchanged — same auth, same Zod schema, same 404/422 mapping — only the description is now wrong:

```ts
// POST /api/flashcards/generate
// Builds or re-syncs a lesson's shared deck (source: LESSON). Idempotent per
// lesson via @@unique([lessonId, source]). A repeat call updates cards in place
// rather than recreating them, so students' review schedules survive; the
// response carries a counts breakdown of what changed.
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: only the known pre-existing `src/lib/dashboard.ts(160,22)` error. In particular, `existing` must satisfy `ExistingCard[]` without a cast — if it does not, the select and the type have drifted, and the type is the one to trust.

- [ ] **Step 4: Run the suite**

Run: `npm test`
Expected: PASS. No test exercises the transaction (there is no DB harness), but nothing may regress.

- [ ] **Step 5: Verify by hand against a running app**

This is the only check that proves the bug is fixed, and it needs the migration from Task 3 applied.

1. `npm run dev`, sign in, go to `/flashcards`.
2. Build a deck from a completed lesson. Study it and rate a few cards so they leave the `NEW` state.
3. Note one reviewed card's `stability` and `dueAt` (Supabase table editor, `FlashcardReview`).
4. Edit that lesson's note so one block's text changes and one block is added.
5. Build from the same lesson again.
6. Confirm: the reviewed card's `FlashcardReview` row still exists with the same `stability` and `dueAt`; the edited card's `Flashcard.payload` changed; the new block produced a new card; nothing else was deleted.

Record the observed before/after values in the commit message or the PR body — "verified" without the numbers is not verification.

- [ ] **Step 6: Commit**

```bash
git add src/lib/flashcards.ts src/app/api/flashcards/generate/route.ts
git commit -m "fix(flashcards): re-sync a lesson deck in place instead of wiping review state"
```

---

### Task 5: The preview endpoint

Read-only. Runs the same generator and the same diff, writes nothing.

**Files:**
- Create: `src/app/api/flashcards/preview/route.ts`
- Modify: `src/lib/flashcards.ts` (add `previewDeckFromLesson`)
- Modify: `src/lib/validators.ts:297-299` (add the query schema next to `generateFlashcardDeckSchema`)

**Interfaces:**
- Consumes: `diffDeck`, `diffCounts`, `ExistingCard` (Task 2); `generateCardsFromLesson` (Task 1).
- Produces: `GET /api/flashcards/preview?lessonId=<id>` returning
  ```ts
  type DeckPreview = {
    exists: boolean;
    total: number;
    byType: { cardType: string; count: number }[];
    counts: { unchanged: number; updated: number; created: number; removed: number };
    samples: { cardType: string; prompt: string }[];
  };
  ```
  Task 7 fetches exactly this shape.

- [ ] **Step 1: Add the validator**

In `src/lib/validators.ts`, immediately after `generateFlashcardDeckSchema`:

```ts
export const previewFlashcardDeckSchema = z.object({
  lessonId: z.string().min(1),
});
```

and next to the other inferred types near line 322:

```ts
export type PreviewFlashcardDeckInput = z.infer<typeof previewFlashcardDeckSchema>;
```

- [ ] **Step 2: Add the read-only preview to the lib**

In `src/lib/flashcards.ts`, after `generateDeckFromLesson`:

```ts
export type DeckPreview = {
  /** A deck already exists for this lesson. */
  exists: boolean;
  total: number;
  byType: { cardType: string; count: number }[];
  counts: ReturnType<typeof diffCounts>;
  samples: { cardType: string; prompt: string }[];
};

/**
 * What building this lesson's deck would do — same generator, same diff, no
 * writes. Sharing diffDeck with the write path is the point: a preview that can
 * disagree with the result is worse than no preview.
 */
export async function previewDeckFromLesson(
  lessonId: string,
): Promise<DeckPreview | "lesson-not-found"> {
  const lesson = await db.lesson.findUnique({
    where: { id: lessonId },
    select: { id: true, title: true, blocks: true },
  });
  if (!lesson) return "lesson-not-found" as const;

  const generated = generateCardsFromLesson(lesson);

  const deck = await db.flashcardDeck.findUnique({
    where: { lessonId_source: { lessonId, source: "LESSON" } },
    select: { id: true },
  });

  const existing: ExistingCard[] = deck
    ? await db.flashcard.findMany({
        where: { deckId: deck.id },
        select: {
          id: true,
          sourceKey: true,
          orderIndex: true,
          cardType: true,
          prompt: true,
          payload: true,
          difficulty: true,
        },
      })
    : [];

  const counts = diffCounts(diffDeck(existing, generated.cards));

  const byType = new Map<string, number>();
  for (const card of generated.cards) {
    byType.set(card.cardType, (byType.get(card.cardType) ?? 0) + 1);
  }

  return {
    exists: deck !== null,
    total: generated.cards.length,
    byType: [...byType.entries()]
      .map(([cardType, count]) => ({ cardType, count }))
      .sort((a, b) => b.count - a.count || a.cardType.localeCompare(b.cardType)),
    counts,
    samples: generated.cards.slice(0, 5).map((c) => ({
      cardType: c.cardType,
      prompt: c.prompt,
    })),
  };
}
```

- [ ] **Step 3: Write the route handler**

Read `node_modules/next/dist/docs/` on route handlers before writing this — the App Router conventions in this Next version may differ from what you expect. Create `src/app/api/flashcards/preview/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { previewFlashcardDeckSchema } from "@/lib/validators";
import { previewDeckFromLesson } from "@/lib/flashcards";
import { rateLimit, tooManyRequests } from "@/lib/rate-limit";

export const dynamic = "force-dynamic";

// GET /api/flashcards/preview?lessonId=…
// What building this lesson's deck would do: type breakdown, the diff against
// any existing deck, and a few sample prompts. Writes nothing.
export async function GET(req: NextRequest) {
  try {
    const session = await auth();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    // Parses a lesson's blocks and reads its whole card set on every call, and
    // it is trivially loopable from the client.
    const limit = rateLimit({
      key: `flashcard-preview:${session.user.id}`,
      limit: 40,
      windowSeconds: 60,
    });
    if (!limit.ok) return tooManyRequests(limit.retryAfterSeconds);

    const parsed = previewFlashcardDeckSchema.safeParse({
      lessonId: req.nextUrl.searchParams.get("lessonId") ?? "",
    });
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const preview = await previewDeckFromLesson(parsed.data.lessonId);
    if (preview === "lesson-not-found") {
      return NextResponse.json({ error: "Lesson not found" }, { status: 404 });
    }

    return NextResponse.json(preview);
  } catch (error) {
    console.error("Error previewing flashcard deck:", error);
    return NextResponse.json(
      { error: "Failed to preview deck" },
      { status: 500 },
    );
  }
}
```

- [ ] **Step 4: Typecheck and lint**

Run: `npx tsc --noEmit -p tsconfig.json && npx eslint src/app/api/flashcards/preview/route.ts src/lib/flashcards.ts src/lib/validators.ts`
Expected: only the known pre-existing `src/lib/dashboard.ts(160,22)` error; eslint clean.

- [ ] **Step 5: Verify by hand**

With `npm run dev` running and signed in, hit the endpoint in the browser for a lesson with no deck and for one with a deck:

- No deck: `exists: false`, `counts.created === total`, other counts `0`.
- Existing, untouched deck: `counts.unchanged === total`, `created`/`updated`/`removed` all `0`.
- Unknown lesson id: `404`.
- No session: `401`.

- [ ] **Step 6: Commit**

```bash
git add src/app/api/flashcards/preview/route.ts src/lib/flashcards.ts src/lib/validators.ts
git commit -m "feat(flashcards): preview what building a lesson's deck would do"
```

---

### Task 6: Page data for a real picker

Drop the 12-lesson cap; carry the grouping labels and which lessons already have a deck.

**Files:**
- Modify: `src/lib/flashcards.ts:36-76` (`FlashcardsPageData` and `getFlashcardsPageData`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  ```ts
  lessons: {
    lessonId: string;
    title: string;
    subjectName: string;
    topicTitle: string;
    deck: { id: string; cardCount: number } | null;
  }[]
  ```
  Task 7 renders exactly this.

- [ ] **Step 1: Widen the type**

In `src/lib/flashcards.ts`, replace the `lessons` field on `FlashcardsPageData`:

```ts
  /** Finished lessons that can be turned into a deck. */
  lessons: {
    lessonId: string;
    title: string;
    subjectName: string;
    topicTitle: string;
    /** The deck already built from this lesson, if there is one. */
    deck: { id: string; cardCount: number } | null;
  }[];
```

- [ ] **Step 2: Rewrite the query**

Replace the `completedLessons` query and the `lessons` mapping in `getFlashcardsPageData`. The `take: 12` goes; the relation walk is `lesson → subtopic → topic → subject`, matching `prisma/schema.prisma:365-381`:

```ts
  const [decks, recommendations, completedLessons] = await Promise.all([
    getDeckSummaries(db, userId),
    getFlashcardRecommendations(db, userId),
    db.studentProgress.findMany({
      where: { studentId: userId, status: "COMPLETED", lessonId: { not: null } },
      select: {
        lesson: {
          select: {
            id: true,
            title: true,
            subtopic: {
              select: {
                topic: {
                  select: { title: true, subject: { select: { name: true } } },
                },
              },
            },
          },
        },
      },
      orderBy: { lastAccessedAt: "desc" },
    }),
  ]);

  const lessonRows = completedLessons
    .map((p) => p.lesson)
    .filter((l): l is NonNullable<typeof l> => l !== null);

  // One grouped lookup marks which of those lessons already has a deck, so the
  // picker can say "already built" instead of silently offering a re-sync.
  const builtDecks =
    lessonRows.length === 0
      ? []
      : await db.flashcardDeck.findMany({
          where: {
            source: "LESSON",
            lessonId: { in: lessonRows.map((l) => l.id) },
          },
          select: {
            id: true,
            lessonId: true,
            _count: { select: { cards: true } },
          },
        });

  const deckByLesson = new Map(
    builtDecks
      .filter((d): d is typeof d & { lessonId: string } => d.lessonId !== null)
      .map((d) => [d.lessonId, { id: d.id, cardCount: d._count.cards }]),
  );
```

and the returned field:

```ts
    lessons: lessonRows.map((l) => ({
      lessonId: l.id,
      title: l.title,
      subjectName: l.subtopic.topic.subject.name,
      topicTitle: l.subtopic.topic.title,
      deck: deckByLesson.get(l.id) ?? null,
    })),
```

Note this makes the deck lookup sequential after the `Promise.all` — it must be, since it depends on the lesson ids. It is a single indexed `IN` query.

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: two errors and no more — the known `src/lib/dashboard.ts(160,22)`, plus `src/components/flashcards/generate-deck-form.tsx` complaining that `CompletedLesson` no longer matches. Task 7 fixes the second. If `src/lib/flashcards.ts` itself errors, the relation path is wrong — check the model definitions rather than casting.

- [ ] **Step 4: Commit**

```bash
git add src/lib/flashcards.ts
git commit -m "feat(flashcards): give the lesson picker every finished lesson and its deck state"
```

---

### Task 7: The section itself

Searchable picker, inline preview, build-or-re-sync button, and a result that links to the deck.

**Files:**
- Modify: `src/components/flashcards/generate-deck-form.tsx` (whole file)
- Modify: `src/app/(dashboard)/flashcards/page.tsx:108-122` (section copy)

**Interfaces:**
- Consumes: the `lessons` shape from Task 6; `GET /api/flashcards/preview` from Task 5; the `{ deck, counts, cardCount }` response from Task 4.
- Produces: nothing downstream.

- [ ] **Step 1: Rewrite the form component**

Replace `src/components/flashcards/generate-deck-form.tsx` entirely:

```tsx
"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { LuSparkles, LuWandSparkles, LuSearch, LuRefreshCw, LuArrowRight, LuTriangleAlert } from "react-icons/lu";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { CARD_TYPE_LABEL, type FlashcardType } from "@/lib/flashcard-content";

type CompletedLesson = {
  lessonId: string;
  title: string;
  subjectName: string;
  topicTitle: string;
  deck: { id: string; cardCount: number } | null;
};

type DeckPreview = {
  exists: boolean;
  total: number;
  byType: { cardType: string; count: number }[];
  counts: { unchanged: number; updated: number; created: number; removed: number };
  samples: { cardType: string; prompt: string }[];
};

type BuildResult = {
  deck: { id: string };
  counts: DeckPreview["counts"];
  cardCount: number;
};

function typeLabel(cardType: string): string {
  return CARD_TYPE_LABEL[cardType as FlashcardType] ?? cardType;
}

export function GenerateDeckForm({ lessons }: { lessons: CompletedLesson[] }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<CompletedLesson | null>(null);
  const [preview, setPreview] = useState<DeckPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BuildResult | null>(null);

  // Grouped subject → topic, so a long list stays navigable.
  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = needle
      ? lessons.filter((l) =>
          `${l.title} ${l.topicTitle} ${l.subjectName}`.toLowerCase().includes(needle),
        )
      : lessons;

    const bySubject = new Map<string, Map<string, CompletedLesson[]>>();
    for (const lesson of matches) {
      const topics = bySubject.get(lesson.subjectName) ?? new Map();
      topics.set(lesson.topicTitle, [...(topics.get(lesson.topicTitle) ?? []), lesson]);
      bySubject.set(lesson.subjectName, topics);
    }
    return [...bySubject.entries()].map(([subjectName, topics]) => ({
      subjectName,
      topics: [...topics.entries()].map(([topicTitle, items]) => ({ topicTitle, items })),
    }));
  }, [lessons, query]);

  async function choose(lesson: CompletedLesson) {
    setSelected(lesson);
    setPreview(null);
    setResult(null);
    setError(null);
    setPreviewing(true);
    try {
      const res = await fetch(`/api/flashcards/preview?lessonId=${encodeURIComponent(lesson.lessonId)}`);
      if (!res.ok) throw new Error("failed");
      setPreview((await res.json()) as DeckPreview);
    } catch {
      setError("Couldn't preview this lesson. You can still build it.");
    } finally {
      setPreviewing(false);
    }
  }

  async function build() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/flashcards/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lessonId: selected.lessonId }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Failed to build deck");
        return;
      }
      setResult(data as BuildResult);
      setPreview(null);
      router.refresh();
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (lessons.length === 0) {
    return (
      <p className="text-xs text-muted">
        Finish a lesson first — its concepts, mistakes and examples become your cards.
      </p>
    );
  }

  const existing = preview?.exists ?? selected?.deck != null;

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="relative">
        <LuSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
        <input
          type="search"
          className="input pl-9"
          placeholder="Search your finished lessons…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search finished lessons"
        />
      </div>

      {/* Lesson list */}
      <div className="max-h-72 overflow-y-auto rounded-xl border border-border">
        {groups.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-muted">
            No finished lesson matches “{query}”.
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.subjectName}>
              <p className="sticky top-0 bg-secondary px-4 py-1.5 text-xs font-bold text-secondary-foreground">
                {group.subjectName}
              </p>
              {group.topics.map((topic) => (
                <div key={topic.topicTitle}>
                  <p className="px-4 pt-2 text-xs font-semibold text-muted">{topic.topicTitle}</p>
                  {topic.items.map((lesson) => (
                    <button
                      key={lesson.lessonId}
                      type="button"
                      onClick={() => choose(lesson)}
                      aria-pressed={selected?.lessonId === lesson.lessonId}
                      className={cn(
                        "flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm transition-colors",
                        selected?.lessonId === lesson.lessonId
                          ? "bg-primary-soft font-semibold text-primary"
                          : "hover:bg-secondary",
                      )}
                    >
                      <span className="min-w-0 truncate">{lesson.title}</span>
                      {lesson.deck && (
                        <Badge className="flex-shrink-0">
                          Already built · {lesson.deck.cardCount} cards
                        </Badge>
                      )}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      {/* Preview */}
      {selected && previewing && <Spinner className="py-4" />}

      {selected && preview && !previewing && (
        <div className="rounded-xl border border-border bg-secondary/40 p-4">
          <p className="text-sm font-bold text-foreground">
            {preview.total} card{preview.total === 1 ? "" : "s"} from “{selected.title}”
          </p>

          <div className="mt-2 flex flex-wrap gap-1.5">
            {preview.byType.map((t) => (
              <Badge key={t.cardType}>
                {t.count} × {typeLabel(t.cardType)}
              </Badge>
            ))}
          </div>

          {existing && (
            <div className="mt-3 space-y-1.5 border-t border-border pt-3">
              <p className="text-sm">
                <span className="font-bold text-success">
                  {preview.counts.unchanged} card
                  {preview.counts.unchanged === 1 ? "" : "s"} unchanged
                </span>
                <span className="text-muted"> — your progress is kept</span>
                <span className="text-muted">
                  {" · "}
                  {preview.counts.updated} updated · {preview.counts.created} new
                </span>
              </p>
              {preview.counts.removed > 0 && (
                <p className="flex items-start gap-2 text-sm text-warning">
                  <LuTriangleAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
                  <span>
                    {preview.counts.removed} card
                    {preview.counts.removed === 1 ? "" : "s"} no longer in the lesson will be
                    removed, along with their review history.
                  </span>
                </p>
              )}
            </div>
          )}

          {preview.samples.length > 0 && (
            <ul className="mt-3 space-y-1 border-t border-border pt-3">
              {preview.samples.map((s, i) => (
                <li key={`${s.prompt}-${i}`} className="truncate text-xs text-muted">
                  <span className="font-semibold">{typeLabel(s.cardType)}:</span> {s.prompt}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Action */}
      <Button type="button" onClick={build} disabled={busy || !selected || preview?.total === 0}>
        {existing ? <LuRefreshCw className="h-4 w-4" /> : <LuWandSparkles className="h-4 w-4" />}
        {busy ? (existing ? "Re-syncing…" : "Building…") : existing ? "Re-sync deck" : "Build cards"}
      </Button>

      {error && (
        <p className="rounded-lg border border-danger/30 bg-danger-soft/40 px-3 py-2 text-sm font-medium text-danger">
          {error}
        </p>
      )}

      {/* Result */}
      {result && (
        <div className="rounded-xl border border-success/30 bg-success-soft/50 p-4">
          <p className="text-sm font-semibold text-success">
            <LuSparkles className="mr-1 inline h-3.5 w-3.5" />
            {result.counts.created > 0 && `${result.counts.created} new · `}
            {result.counts.updated > 0 && `${result.counts.updated} updated · `}
            {result.counts.unchanged > 0 && `${result.counts.unchanged} kept · `}
            {result.cardCount} card{result.cardCount === 1 ? "" : "s"} in the deck.
          </p>
          <Link
            href={`/flashcards/${result.deck.id}`}
            className="mt-3 inline-flex items-center gap-2 text-sm font-bold text-primary hover:underline"
          >
            Study now
            <LuArrowRight className="h-4 w-4" />
          </Link>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Update the section copy**

In `src/app/(dashboard)/flashcards/page.tsx`, replace the description paragraph in the "Generate from lesson" section so it describes re-syncing too:

```tsx
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Pick a lesson you finished and turn its concepts, examples and
            mistakes into a deck. Already built one? Re-syncing pulls in your
            latest edits and keeps your review progress.
          </p>
```

- [ ] **Step 3: Typecheck and lint**

Run: `npx tsc --noEmit -p tsconfig.json && npx eslint "src/components/flashcards/generate-deck-form.tsx" "src/app/(dashboard)/flashcards/page.tsx"`
Expected: only the known pre-existing `src/lib/dashboard.ts(160,22)` error; eslint clean. If `CARD_TYPE_LABEL` or `FlashcardType` do not export from `@/lib/flashcard-content`, check `src/lib/flashcard-content.ts:107` — they do, but the import path matters.

- [ ] **Step 4: Verify in the browser**

`npm run dev`, sign in, go to `/flashcards`:

1. The picker lists more than 12 lessons if you have them, grouped by subject → topic.
2. Typing filters across lesson title, topic and subject.
3. A lesson with a deck shows "Already built · N cards"; selecting it makes the button read "Re-sync deck".
4. Selecting a lesson shows a type breakdown; for an already-built one it leads with "N cards unchanged — your progress is kept".
5. Building lands a result card whose "Study now" link opens that deck.
6. The deck list and hero counts above update without a manual reload.

- [ ] **Step 5: Commit**

```bash
git add src/components/flashcards/generate-deck-form.tsx "src/app/(dashboard)/flashcards/page.tsx"
git commit -m "feat(flashcards): searchable lesson picker with a build preview and a way into the deck"
```

---

## Verification

After Task 7, the whole feature:

- [ ] `npm test` — passes, including `scripts/test-flashcard-diff.mts`.
- [ ] `npx tsc --noEmit -p tsconfig.json` — only the known pre-existing `src/lib/dashboard.ts(160,22)` error.
- [ ] `npx eslint src/lib/flashcard-diff.ts src/lib/flashcards.ts src/components/flashcards/generate-deck-form.tsx src/app/api/flashcards/preview/route.ts` — clean.
- [ ] The Task 4 Step 5 hand-check, with the before/after `stability` and `dueAt` values written down.
- [ ] `npx prisma migrate status` — no drift (requires the Task 3 SQL to have been applied).
