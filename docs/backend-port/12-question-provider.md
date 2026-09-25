# Question provider cache (SDASH)

Modules under `src/lib/question-provider/`: `sdash.ts`, `cache-key.ts`, `alias.ts`, `ingest.ts`, `saturation.ts`, `state.ts`, `errors.ts`, `mapper.ts`, `types.ts`. Callers: assessment generation (`after()`), JAMB prepare/generate, admin `POST /admin/api/provider/backfill`. Catalogue sync is the script `scripts/sync-provider-catalogue.ts`, not a request.

Design spec: `docs/superpowers/specs/2026-09-01-question-provider-cache-design.md`. Tests: `scripts/test-provider-*.mts`.

## Enablement

Fetches are scheduled only when `QUESTION_PROVIDER_ENABLED` is the string `"true"`. `SDASH_ACCESS_TOKEN` is required to actually call the API. `SDASH_BASE_URL` defaults to `https://sdashapi.com/api`.

The assessment path also spends a global rate budget: key `provider:outbound`, 30 per 60 seconds. If the budget is exhausted, generation continues with whatever is already in Postgres and does not schedule a draw.

## Cache key

```
{subjectSlug trimmed, lowercased, "|" encoded as %7C}|{examType}|{examYear}
```

Unique with `provider = SDASH` on `ProviderFetch`. One row per paper we have ever asked for. **The row is the cache entry.** A `SATURATED` or `FAILED` row is a hit even when it promoted zero questions.

## Aliases (`alias.ts`)

Provider exam names: `utme` → JAMB, `wassce` → WAEC, `neco` → NECO. Subject slugs the provider uses are mapped onto ours (for example `english-language` → `english`). An unknown subject or exam is a **filter-scoped** failure: that cache key becomes `FAILED`, and the circuit breaker is **not** opened. A bad slug must not take down Physics.

## Draw pipeline

`ensureQuestionsCached(filter, limit)`:

1. Resolve our subject. Unknown → filter failure, no HTTP call.
2. Load `ProviderFetch` by cache key.
3. `SATURATED` or `FAILED` → read promoted `Question` rows only.
4. `PENDING` whose `startedAt` is inside `LEASE_WINDOW_MS` (120_000) → someone else holds the lease; read the database and return. Do not start a second draw.
5. Otherwise insert the row or claim the lease (update `startedAt`). The unique key makes the first insert win.
6. If the circuit is open, do not call. Read the database (or record the error).
7. One `drawOnce`: HTTP GET to SDASH, stage every raw item as `ProviderQuestion`, map, promote.
8. Return questions now in our bank for that filter.

`saturate(filter)` loops, claiming and drawing, until status is no longer `PENDING` or `MAX_DRAWS` is hit. Admin backfill calls `ensureQuestionsCached(filter, 50)` then `saturate`.

Student-facing generation only schedules `ensure` in the background. It does not wait for saturation. Empty bank + scheduled fetch → HTTP 503 `preparing: true`.

## Saturation (`saturation.ts`)

| Constant | Value |
|---|---|
| `DRAW_LIMIT` | 50 (page size asked of the provider) |
| `MIN_NEW_PER_DRAW` | 10 |
| `MAX_DRAWS` | 12 |

Stop and mark `SATURATED` when any of:

- the provider returned fewer than 50 items (end of their catalogue), or
- this draw added fewer than 10 questions we did not already have, or
- `drawCount` has reached 12

`FAILED` is terminal until `resetFailedFetch` (admin `reset: true`). `clearProviderBlock` (admin `clearBlock: true`) is separate: it opens a `BLOCKED` circuit and does not by itself reset a failed fetch.

## Circuit breaker (`state.ts`)

One `ProviderState` row per provider.

| State | Calls allowed? |
|---|---|
| `OK` | yes |
| `EXHAUSTED` | no, until `cooldownUntil`. Cooldown is **15 minutes**. After it lapses, one probe is allowed; a second caller while the probe is in flight should not stampede. |
| `BLOCKED` | no, and there is no cooldown. A human must call `clearProviderBlock`. |

`nextCircuit` from the HTTP classification (`errors.ts`):

| Provider HTTP | Class | Breaker |
|---|---|---|
| 404 | empty catalogue for this filter | do not open the breaker; the filter can saturate empty |
| 402 | out of credit | `EXHAUSTED` |
| 401 | bad or revoked token | `BLOCKED` |
| 403 | `EXHAUSTED` if the body says the account is out of credit, else `BLOCKED` | |
| other 5xx / network | retryable | breaker unchanged; the fetch stays `PENDING` so a later request retries |
| filter alias unknown | terminal for that key only | breaker unchanged |

`creditsRemaining` is stored from the provider envelope when present, for the admin UI. It does not gate draws by itself; HTTP 402 does.

## Mapping (`mapper.ts`)

`MAPPER_VERSION = 2`.

Fingerprint: SHA-256 of normalised question text plus the sorted option texts. It does **not** include the provider id, the solution, or image URLs. Two payloads with the same stem and choices collapse inside one fetch.

Reject (row stays `REJECTED` with `rejectionReasons`) when:

- the payload’s exam type or year does not match the filter that was requested
- the solution is empty
- objective invariants fail (same rules as admin questions: enough options, answer key exists)

Images are not hot-linked into `Question` as the provider URL if the mapper marks them for mirroring. The current mapper can leave the question `PENDING` until an image is mirrored; promotion creates the `Question` and sets `ProviderQuestion.status = PROMOTED`, `questionId`, `promotedAt`, `mapperVersion`.

Within one fetch, dedupe by provider question id first, then by fingerprint. Across fetches (different years), the same stem is allowed to promote twice. That is deliberate: WAEC 2019 and WAEC 2021 may share a question, and each paper must contain it. See the comment on `@@unique([fetchId, fingerprint])` in the schema.

Promoted `Question` rows are ordinary objective questions: `examType`, `examYear`, options JSON, `correctAnswer`, explanation, difficulty, subject, topic when the mapper could resolve one. They participate in generation like authored questions. Admin delete still refuses them once a student has answered.

## What to reimplement carefully

- Lease: 120 seconds, so a crashed worker does not block a paper forever, and a live worker is not doubled.
- Do not mark `SATURATED` before staging the raw payload. The raw JSON is the audit trail and the input for a future mapper version.
- Bumping `MAPPER_VERSION` should reprocess `PENDING` rows, not rewrite `PROMOTED` ones that students have already seen.
- `ProviderCatalogue` is a separate table filled offline. The past-paper picker shows those years with `cached: false` so the UI can offer a paper that will 503-then-fill on first generate.
