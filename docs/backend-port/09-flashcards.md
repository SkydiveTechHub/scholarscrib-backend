# Flashcards and spaced repetition

Algorithm: `src/lib/spaced-repetition.ts`. Persistence and queues: `src/lib/flashcards.ts`. Card text: `src/lib/flashcard-content.ts`. Re-sync: `src/lib/flashcard-diff.ts`. Ownership: `src/lib/flashcard-ownership.ts`. Stats: `src/lib/flashcard-analytics.ts`.

Entitlement: `flashcards` requires STANDARD. See [04-student-api.md](./04-student-api.md) for routes.

The model is SM-2 ease plus an FSRS-style stability, difficulty, and forgetting curve. It is not stock SM-2 and not stock FSRS. Port `reviewCard` and keep `scripts/test-spaced-repetition.mts`.

## Constants

| Name | Value |
|---|---|
| Ease floor / ceiling | 1.3 / 5.0 |
| Initial ease | 2.5 |
| Difficulty min / max / initial | 1 / 10 / 5 |
| Max interval | 36500 days |
| Min interval | `1/1440` day (1 minute) |
| Desired retention | 0.9 (used when interpreting the curve; scheduling below uses the growth factors directly) |
| Forgetting curve | `R(t) = (1 + (19/81) * (t / S)) ^ -0.5` |

Learning-step length in minutes: AGAIN 1, HARD 5, GOOD 10, EASY 1440 (one day).

Initial stability by first rating: AGAIN 0.1, HARD 0.5, GOOD 1.0, EASY 2.0 days.

Learning growth (while still in LEARNING): HARD 1.0, GOOD 1.6, EASY 2.5.

Review growth (REVIEW state): HARD 0.8, GOOD 1.0, EASY 1.3. The new stability is `stability * ease * growth`, and the interval is `round(stability)` days, at least 1.

On AGAIN during REVIEW, stability is **set to** `LAPSE_STABILITY` (1.0 day), not multiplied. Repetitions reset to 0, lapses increment, state becomes `RELEARNING`, and the card is due in 1 minute.

SM-2 quality `q`: AGAIN 1, HARD 3, GOOD 4, EASY 5.

Ease delta:

```
Δ = 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
ease = clamp(ease + Δ, 1.3, 5.0)
```

Difficulty delta:

```
Δ = -0.7 * (ratingIndex - 2) + 0.02 * (5 - difficulty)
```

`ratingIndex` is 0..3 for AGAIN..EASY. Authored seed before the first review: BASIC → 3, INTERMEDIATE → 5, ADVANCED → 7, then clamped to 1..10. The column default is 5, which matches INTERMEDIATE.

## State machine (`reviewCard`)

| State | Rating | Next |
|---|---|---|
| NEW | AGAIN | LEARNING, due in 1 minute |
| NEW | HARD or GOOD | LEARNING, due after that rating’s learning step |
| NEW | EASY | REVIEW, interval from initial stability |
| LEARNING or RELEARNING | AGAIN | stay, reset the step, due in 1 minute |
| LEARNING or RELEARNING | other, repetitions still short of 2 | stay in learning, grow stability |
| LEARNING or RELEARNING | other, after ≥ 2 successful reps | REVIEW, interval ≥ 1 day |
| REVIEW | AGAIN | RELEARNING, `lapses += 1` |
| REVIEW | HARD / GOOD / EASY | stay in REVIEW, grow stability, due in `round(stability)` days |

`dueAt` is an absolute timestamp. `retention` stored on the row is the predicted recall at the moment of scheduling. `repetitions` counts successful reviews, not AGAIN.

## Review persistence

`recordFlashcardReview`:

1. The student must own the deck (`createdBy`) or have a `FlashcardEnrollment`. Otherwise 404 (the route does not distinguish “missing” from “not yours”).
2. Load or create `FlashcardReview` (state NEW, ease 2.5, difficulty from the card).
3. `reviewCard`.
4. Upsert the review row. Insert `FlashcardReviewLog` with rating, optional `responseTimeMs`, `scheduledDays`, `objectiveCorrect`.
5. If the card’s deck has a topic, append `CARD_REVIEWED` with the score map AGAIN 0, HARD 0.5, GOOD 0.85, EASY 1.
6. After the response, `markPlanFromCardReview`.

## Queues and limits

`getStudyQueue`: due cards first (earliest `dueAt`), then new cards. `DAILY_NEW_BUDGET = 20`. New cards offered = `max(0, 20 - dueCount)` so a large due pile suppresses new cards that day.

`getDeckSummariesFor` / `getFlashcardStatsFor` / recommendations are what the list and stats routes return. Leeches (`flashcard-analytics.ts`): `lapses ≥ 4`, or at least 8 reviews with success rate under 35%. Overdue: due date older than `max(intervalDays * 2, 7)` days. Low-retention recommendation: predicted retention < 0.75.

## Generating cards from a lesson

`previewDeckFromLesson` is read-only (the preview route). `generateDeckFromLesson` upserts the deck with `source = LESSON` and unique `(lessonId, LESSON)`.

`flashcard-content.ts` walks lesson blocks deterministically:

| Block | Card type |
|---|---|
| concept | DEFINITION or SCENARIO |
| check (knowledge check) | SCENARIO |
| mistake | TRUE_FALSE |
| mnemonic or tip | DEFINITION |
| example | SCENARIO |

Scaffolding headings are skipped. A card over 120 words fails the linter and is dropped. If nothing survives, the route returns 422.

## Re-sync without wiping SRS

`flashcard-diff.ts` matches existing cards to newly generated ones by `sourceKey`, then by body, then by legacy position for rows that predate `sourceKey`. Updates happen in place. Unmatched old cards are removed; unmatched new cards are inserted. `FlashcardReview` rows point at the card id, so an in-place update keeps the schedule. A delete of a removed card cascades its reviews; that is intended when the source block disappeared.

## Ownership

`canManageDeck`: `deck.createdBy === userId`. Enrollment does not grant delete. Autored decks are created by `POST /api/flashcards` with `source = AUTHORED` and `createdBy` set. Lesson decks are created by generate; `createdBy` is the student who generated them (they can delete that deck).

## Page loaders without routes

`getFlashcardsPageData` and `getDeckPageData` in `src/lib/flashcards.ts` feed `src/app/(dashboard)/flashcards/**`. The REST routes cover list, create, review, stats, and recommendations. The study screen’s queue (`getStudyQueue`) is loaded by the page, not by `GET /api/flashcards`. Expose it if the client will not import the library. See [13-page-loaders.md](./13-page-loaders.md).
