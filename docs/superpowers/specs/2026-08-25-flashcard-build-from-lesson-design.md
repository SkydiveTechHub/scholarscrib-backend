# Build Cards From A Lesson — Non-Destructive Re-Sync, Preview, and a Real Picker

Date: 2026-08-25
Status: Draft
Builds on: `2026-08-01-flashcards-design.md` (deck/card model, SRS state),
`2026-08-05-lesson-note-upload-design.md` (the authoring path that makes lesson
blocks mutable in the first place)

## Problem

The "Build cards from a lesson" section on `/flashcards` is one `<select>` and
one button. It works exactly once per lesson, and the second use destroys data.

1. **Rebuilding a deck wipes every student's memory state.**
   `generateDeckFromLesson` (`src/lib/flashcards.ts:189`) opens its transaction
   with `tx.flashcard.deleteMany({ where: { deckId } })` and then re-inserts the
   cards from scratch. `FlashcardReview.flashcard` is `onDelete: Cascade`
   (`prisma/schema.prisma:782`), as is `FlashcardReviewLog.flashcard`
   (`prisma/schema.prisma:807`). So every ease factor, stability, interval,
   lapse count and review log row for that deck disappears.

2. **The blast radius is everyone, not the clicker.** Decks generated from a
   lesson are shared, not per-student: `FlashcardDeck` is keyed
   `@@unique([lessonId, source])` (`prisma/schema.prisma:753`) with no student
   scoping, and `createdBy` records only who happened to build it first. Any
   student who picks an already-built lesson resets the whole cohort's progress
   on that deck.

3. **Nothing warns you.** The picker lists completed lessons with no indication
   which already have a deck, and the form reports `Deck created with N cards`
   identically whether it created or destroyed-and-recreated.

4. **This is about to matter much more.** Lesson notes became editable with the
   note-upload work. Re-uploading a note is exactly when a deck *should*
   refresh — and today that refresh is the destructive path.

5. **The picker hides most of what you have earned.** Page data takes the 12
   most recently accessed completed lessons (`src/lib/flashcards.ts:53-58`).
   Lesson 13 onward is unreachable, with no search and no grouping.

6. **You build blind and land nowhere.** There is no way to see what a lesson
   would produce before committing, and success is a green toast — no link to
   the deck that was just built.

## Goal

A student opens the section, finds any lesson they have finished, sees what
cards it would produce and what a rebuild would change, builds or re-syncs
without losing anyone's progress, and lands in the deck ready to study.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Card identity | Persist the source block's `id` as `Flashcard.sourceKey` | Every block carries a required non-empty `id` (`src/lib/lesson-engine.ts:130` drops blocks without one), and each block yields at most one card. The natural key already exists in the source data. |
| Legacy cards | Nullable column + `orderIndex` fallback on first re-sync | Generation is deterministic and block-ordered, so position *is* the old identity. Self-healing — no backfill script, no manual prod step on a setup where migrations are hand-applied. |
| Rejected: match on `(cardType, prompt)` | No | Avoids a migration but reintroduces the bug: `prompt` is nullable and derived from block text, so any wording edit silently unmatches a card and resets it. |
| Rejected: required `sourceKey` + backfill script | No | Tidier end state, but needs a one-off script run against production before the code ships. Not worth it for a null set that self-corrects on first touch. |
| Rebuild semantics | Non-destructive re-sync, available to students | Upsert by key; delete only cards whose block is gone. Keeps re-uploaded notes flowing into live decks. |
| Diff computation | Pure `diffDeck()`, outside the transaction | The transaction path has no test harness in `npm test`. Keeping the logic pure is what makes it testable. |
| Preview | On-demand `GET`, persists nothing | `generateCardsFromLesson` is a pure function over `lesson.blocks`; there is nothing to cache and nothing to write. |
| Preview/result agreement | Both call the same `diffDeck()` | A preview that can disagree with the result is worse than no preview. |
| Picker scope | All completed lessons, searchable | Drops the 12-cap. Keeps the "you finished it, now drill it" framing. |
| Schema | One migration: `sourceKey` + unique index | Applied via the Supabase SQL Editor by hand; `prisma migrate` cannot reach the database from this machine. The `.sql` must be committed with LF endings or the checksum drifts. |

Out of scope: admin-side re-sync triggered by lesson upload, per-student decks,
and moving the section higher on the page.

## Schema

```prisma
model Flashcard {
  // …
  sourceKey String? // id of the lesson block this card was generated from

  @@unique([deckId, sourceKey])
  @@index([deckId, orderIndex])
}
```

Nullable because existing rows have no key, and because authored (non-lesson)
decks have no source block at all. The unique constraint is on
`[deckId, sourceKey]`; in Postgres, nulls do not collide under a unique index,
so any number of legacy or authored cards coexist in a deck without conflict.

One migration directory, `prisma/migrations/<ts>_flashcard_source_key/`,
containing `ALTER TABLE` + `CREATE UNIQUE INDEX`. Applied by hand in the SQL
Editor, then recorded so Prisma's checksum matches.

## Generation

`GeneratedCard` gains one field:

```ts
export type GeneratedCard = {
  sourceKey: string; // the originating block's id
  cardType: FlashcardType;
  // …unchanged
};
```

`generateCardsFromLesson` (`src/lib/flashcard-content.ts:433`) sets it from
`block.id` as it walks the block list. Each per-type converter
(`conceptToCards`, `checkToCard`, `mistakeToCard`, `mnemonicToCard`,
`exampleToCard`, `tipToCard`) already receives its block, so the key is set in
the converter rather than patched on afterwards — that keeps "a card knows where
it came from" a property of construction.

No block produces more than one card, so keys are unique within a deck by
construction.

## Diffing

A new pure function, alongside the generator:

```ts
type ExistingCard = { id: string; sourceKey: string | null; orderIndex: number };

type DeckDiff = {
  unchanged: { id: string; card: GeneratedCard; orderIndex: number }[];
  updated:   { id: string; card: GeneratedCard; orderIndex: number }[];
  created:   { card: GeneratedCard; orderIndex: number }[];
  removed:   { id: string }[];
};

export function diffDeck(
  existing: ExistingCard[],
  generated: GeneratedCard[],
): DeckDiff;
```

Matching runs in two passes:

1. **By key.** Generated card ↔ existing row with the same non-null `sourceKey`.
2. **By position, legacy only.** Remaining generated cards are matched to
   remaining `sourceKey === null` rows by `orderIndex`. This runs once per deck:
   the apply step stamps the key, so the second re-sync is pure pass 1.

Everything unmatched on the generated side is `created`; everything unmatched on
the existing side is `removed`.

`unchanged` vs `updated` is decided by deep-comparing the rendered card body
(`cardType`, `prompt`, `payload`, `difficulty`). A card that only moved position
counts as unchanged — its content, and therefore its memory, is intact — but its
`orderIndex` is still written. This distinction exists to be shown to the
student, so it must track "would this feel like the same card", not "is the row
byte-identical".

## Persistence

`generateDeckFromLesson` keeps its signature and its outcome union
(`"lesson-not-found" | "no-cards"`), and changes its body and return type.

Order inside the transaction:

1. Read existing cards (`id`, `sourceKey`, `orderIndex`) for the deck.
2. `diffDeck()` — pure, computed from data already in hand.
3. Upsert the deck row (title/description/subject/topic), as today.
4. Update `updated` rows in full (body fields, `orderIndex`, `sourceKey`).
   Touch `unchanged` rows only where something actually differs — a changed
   `orderIndex`, or a null `sourceKey` to stamp. An unchanged card in a
   fully-keyed deck at the same position is not written at all.
5. `createMany` the `created` cards.
6. `deleteMany` the `removed` ids.

Step 6 still cascades reviews away — correct, since the source block no longer
exists — and it is now the only path that loses anything. The preview names that
count before the student commits.

Return value becomes:

```ts
{ deck, counts: { unchanged, updated, created, removed }, cardCount }
```

`cardCount` is retained so the existing success copy has something to say.

`POST /api/flashcards/generate` is otherwise untouched: same auth, same Zod
schema, same 404/422 mapping of the outcome union. It passes the richer result
through to the client as-is.

**Ordering hazard.** `@@unique([deckId, sourceKey])` means a re-sync that swaps
two blocks' positions could transiently collide if updates land one at a time
against the old values. Keys are per-card and stable, so a swap changes
`orderIndex` only, not `sourceKey` — no collision. The one case that could
collide is a legacy deck where pass 2 assigns keys: those rows all move from
`null` to distinct values, and distinct values cannot collide with each other.
Both are safe, and the tests below pin them.

## Preview endpoint

`GET /api/flashcards/preview?lessonId=<id>`

- Same auth as the generate route (`auth()`, 401 without a session).
- `rateLimit` from `src/lib/rate-limit.ts`, matching the pattern in
  `src/app/api/assessments/generate/route.ts` — it parses lesson blocks on
  demand and is trivially loopable from the client.
- Zod-validated query (`lessonId`), 404 for an unknown lesson.
- Loads the lesson and, if present, the existing `source: LESSON` deck's cards.
- Runs `generateCardsFromLesson` + `diffDeck`. Writes nothing.

Response:

```ts
{
  exists: boolean,                              // a deck already exists
  total: number,
  byType: { cardType: FlashcardType; count: number }[],
  counts: { unchanged, updated, created, removed },
  samples: { cardType: FlashcardType; prompt: string }[],  // first 5
}
```

When `exists` is false, `counts.created === total` and the rest are zero, so the
client renders from one shape either way.

## Page data

`getFlashcardsPageData` (`src/lib/flashcards.ts:48`) drops `take: 12` and widens
its select:

```ts
lessons: {
  lessonId: string;
  title: string;
  subjectName: string;
  topicTitle: string;
  deck: { id: string; cardCount: number } | null;
}[]
```

Two queries instead of one: completed lessons (joined out to subtopic → topic →
subject for the grouping labels), then a single
`flashcardDeck.findMany({ where: { source: "LESSON", lessonId: { in: [...] } }, select: { id, lessonId, _count: { select: { cards: true } } } })`
to mark which are already built. Both are indexed lookups; neither grows with
deck size.

## UI

**Picker** (`generate-deck-form.tsx`). The `<select>` becomes a searchable list:
a filter input over title/topic/subject, results grouped subject → topic. Each
row shows the lesson title and, when a deck exists, an "Already built · N cards"
marker. Keyboard-navigable; the filter input owns focus on open.

**Preview.** Selecting a lesson fetches the preview and renders it inline above
the button: the type breakdown, then — only when `exists` — a diff line that
leads with what is safe:

> **9 cards unchanged** — your progress is kept · 2 updated · 1 new · 1 removed

Then up to five sample prompts. A removed count greater than zero is the only
part styled as a warning, and it says what removal means (those cards' review
history goes with them).

**Action.** The button reads "Build cards" when no deck exists and "Re-sync
deck" when one does, so the two operations are never the same click.

**Result.** The green toast is replaced by a result card: the diff restated in
past tense plus a "Study now" link to `/flashcards/<deckId>`. `router.refresh()`
still runs so the deck list and hero counts update behind it.

**Empty and error states.** Unchanged in kind: no completed lessons keeps
today's "Finish a lesson first" copy; a `no-cards` lesson (blocks that produce
nothing convertible) surfaces the existing 422 message, and the preview shows
`total: 0` before the student can click.

## Testing

Everything load-bearing is pure, and that is deliberate — the diff lives outside
the transaction precisely so it can be tested without a database.

`scripts/test-flashcard-content.mts` (extends the existing file):

- `sourceKey` is set from `block.id` for every convertible block type.
- Keys are unique across a generated deck.

`scripts/test-flashcard-diff.mts` (new, added to the `test` script):

- Identical regeneration → all unchanged, nothing else.
- Edited block text → that card `updated`, siblings `unchanged`.
- Reordered blocks → all `unchanged`, `orderIndex` values updated.
- Block removed → exactly that card `removed`.
- Block added → exactly that card `created`.
- Legacy deck (all `sourceKey === null`) → matched by `orderIndex`, classified
  correctly, keys assigned.
- Mixed deck (some keyed, some null) → keyed rows match by key first; leftovers
  fall back to position.
- Legacy deck whose lesson gained a block at the top → pins what the positional
  fallback does when positions shift under it, so the behaviour is a decision
  rather than an accident.

The transaction and the endpoint are verified by hand against a local run: build
a deck, review a few cards to move them off `NEW`, edit the lesson note,
re-sync, and confirm the reviewed cards keep their `stability` and `dueAt`.
