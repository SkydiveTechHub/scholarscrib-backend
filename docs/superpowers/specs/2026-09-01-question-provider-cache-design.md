# Question Provider Cache — Read-Through Ingestion from sdashapi

Date: 2026-09-01
Status: Designed, not implemented
Role: Backend / Data Engineering

## Problem

Our question bank is seeded and hand-authored. `src/lib/questions.ts` and
`src/lib/question-pool.ts` read it, and every practice flow in the app is
limited by how much of it exists. A third-party API (sdashapi) sells access to
a past-question bank covering 2001–2026 across UTME, WASSCE, NECO, post-UTME
and university papers.

We want that content, but we do not want a permanent runtime dependency on
them. Calling their endpoint on every student request would mean their outage
is our outage, their rate limit is our ceiling, and their pricing is our
pricing — forever.

## Goal

Every successful provider call is captured, permanently, into our own database
on the way past. A filter is fetched **once**, ever. Every subsequent student
asking for the same subject/exam/year is served entirely from Postgres.

The end state is that the provider becomes unnecessary: once every paper we
care about has been drawn once, we hold a complete offline copy and all further
quality work happens against our own data.

### Non-goals

- Topic-tagging imported questions. They arrive untagged and stay untagged for
  now (see "Known consequences").
- Ingesting `post-utme` or `university` papers. Our `ExamType` enum cannot
  represent them distinctly and folding them into `CUSTOM` would corrupt
  past-paper grouping. We simply never request them.
- Replacing the admin bulk-import path (`src/lib/admin-import.ts`). It stays;
  this reuses its validation rather than competing with it.

## Probe findings (2026-09-02)

34 live requests against the real API. Everything below is measured, not
assumed, and the decisions that follow are calibrated against it.

**Yield — 87%.** Across 249 questions from five subject/type/year combinations
spanning 2005–2022, 217 would promote under the rules in this document.
`solution` is present on 88% (100% for `english/utme/2012` and
`biology/wassce/2018`, 94% for `mathematics/utme/2019`, 82% for
`chemistry/utme/2022`, 62% for `chemistry/utme/2005`). Older papers are barer,
as expected. **`answer` was a valid option key on 100% of 249 rows** and
options were `ABCD` on all but a handful of `ABC` maths rows. Decision 5 costs
roughly 12% of the catalogue, not most of it.

**Their bank is a pool, not a paper.** Four successive draws of 50 for
`chemistry/utme/2022` yielded 50, 39, 32 and 26 new ids — 147 distinct after
200 draws, still 52% new on the fourth. Mark-recapture puts that single filter
at roughly 300–400 questions, far more than a real 40-question UTME paper. This
invalidates the original stop rule; see decision 4.

**NECO is entitled but sparse.** `type=neco` returns `200` with no year filter
(seen: 2021, 2023, 2024) but `404` for `biology/2018` and `chemistry/2014`.
`/v1/years` is a **global** list, not per subject or per type, so the catalogue
cannot be a cross product — it must be swept.

**`post-utme` and `university` return `403`** — our token is not entitled to
them. The non-goal of never requesting them is now enforced upstream too.

**The error envelope is consistent** and maps cleanly onto three behaviours:

| HTTP + body `status` | Meaning | Ledger outcome |
| --- | --- | --- |
| `404` `"No questions found for those filters."` | Filter is genuinely empty | `SATURATED`, `rawCount: 0` — **never retried** |
| `403` `"...do not have permission to query the \"x\" exam."` | Not entitled | `FAILED`, terminal, no retry |
| `401` `"Invalid AccessToken."` | Bad credentials | `FAILED`, terminal, alert |
| `5xx` / transport | Transient | `FAILED`, retryable |

Treating `404` as an error would be the costly mistake here: the catalogue will
contain empty combinations, and retrying them forever is exactly the runaway
this design exists to prevent.

**Other measurements.** `limit=500` silently clamps to 50 rather than erroring.
No rate-limit headers are exposed at all, so we cannot self-regulate from
responses. Latency is 316–621ms per call, which makes decision 3's synchronous
write cheap. `section` is populated on 25% overall but 100% of
`english/utme/2012` — it looks like comprehension-passage grouping.

**Images are hotlinks to someone else's Cloudinary.** `image` URLs look like
`https://res.cloudinary.com/aloc-ng/image/upload/v1724006808/ALOC-Questions/Mathematics/2019/MATH_JAMB_2019_Q15_cfctff.jpg`
— sdashapi is fronting the ALOC question bank. 4% of rows overall carry one,
16% for maths. See "Known consequences".

## Decisions

Five decisions were settled during design. The rejected options are recorded
because the rationale is the load-bearing part.

### 1. Coverage is tracked by a ledger, not inferred from question counts

**Rejected: "treat any matching rows as a cache hit."** If a student pulls 20
questions for WASSCE Physics 2019, we hold 20 rows. A later request for 40 would
find 20 and wrongly conclude that is the whole paper. Row counts cannot
distinguish "complete" from "partial".

**Rejected: "top up on shortfall."** Best UX, but the provider exposes no
offset or cursor, so topping up means re-drawing and discarding heavy overlap.

**Chosen:** a `ProviderFetch` ledger row per filter. Its *existence and status*,
not the presence of questions, decides whether we call out.

### 2. Raw capture is separated from mapping

**Rejected: "drop what we cannot map."** The ledger would record the filter as
fetched while the bank silently held a fraction of it. We would never re-fetch,
and the unmappable questions would be gone permanently. Independence with holes.

**Rejected: "force everything in with placeholders."** Ships unmarkable
questions to students and makes imported rows indistinguishable from authored
ones.

**Chosen: two tables.** Every payload is written verbatim to `ProviderQuestion`
before any mapping is attempted. A separate pure mapper promotes what passes
validation into `Question`. What fails stays raw with recorded reasons.

The insight: **the network call is expensive and unrepeatable; the mapping is
cheap and infinitely repeatable.** Separating them means a bad mapper costs
nothing and a fetch is never wasted. Bumping `MAPPER_VERSION` and re-running it
over stored rows promotes more questions with zero new API calls — which is what
turns the staging table into a growing asset rather than a dead-letter box.

### 3. On a miss, the user is served from what we just wrote

**Rejected: "serve the provider payload immediately, persist in `after()`."**
Fastest, but the practice flow turns a question list into an `Assessment` with
`AssessmentQuestion` rows keyed on `Question.id`. If those rows are not
committed yet, starting a quiz on what you are looking at fails on a foreign
key. It also shows the user questions that may then fail promotion.

**Rejected: "write, then re-read from Postgres."** Buys consistency we already
have, at the cost of an extra round trip on our slowest path.

**Chosen:** stage, promote and write the ledger synchronously, then serve the
promoted set from memory. One pass, real committed ids, and the user sees
exactly what a future cached user will see.

`after()` (confirmed available in Next 16.2.11 —
`node_modules/next/dist/docs/01-app/03-api-reference/04-functions/after.md`) is
reserved for work off the user's path: the remaining saturation draws, coverage
refreshes, audit logging.

### 4. Coverage means saturation, not completeness

The provider caps `limit` at 50 and exposes no `offset`, `page` or `total`. We
can draw batches but cannot walk a paper deterministically or learn its size.
Completeness is unknowable. Saturation is measurable.

The probe showed draws are randomly redrawn from a pool of roughly 300–400 per
filter, not a 40-question paper. A "zero new ids" stop condition would
therefore almost never fire inside a sane budget — this is a coupon-collector
problem, and exhausting a 350-question pool with random draws of 50 takes far
more calls than the value of the tail justifies.

So the rule is **diminishing returns, not exhaustion**. A filter is
`SATURATED` when any of these holds:

- a draw returns fewer than 10 ids we have not already stored (under 20% new) —
  the pool's useful yield has collapsed;
- a draw returns fewer than 50 payloads, meaning the pool is smaller than one
  batch and we have just seen all of it;
- the API returns `404` — the filter is genuinely empty; recorded with
  `rawCount: 0` so it is never requested again;
- `drawCount` reaches a hard cap of **12**.

At 12 draws we expect ~85% of a 350-question pool. The measured decay
(50/39/32/26 new) crosses the 20% threshold at around draw 8, so most filters
will stop before the cap.

Once saturated we never call out for that filter again. Both thresholds live in
`saturation.ts` as named constants, tuned in one place as real numbers arrive.

### 5. Explanations stay required; explanation-less questions wait in staging

`Question.explanation` is `String @db.Text` — required. It stays that way, and
no existing table is altered.

**Rejected: relaxing the column to `String?`.** It would raise the promotion
rate, but it changes a shared schema and two student-facing render sites for
the benefit of imported content only, and it lets a question reach a student
with nothing to read after they get it wrong. The explanation is the part of a
past question that actually teaches.

**Chosen:** a payload with no `solution` fails validation and stays
`REJECTED` in `ProviderQuestion` with its raw payload intact.

This costs nothing that the staging design was not already built to absorb. The
fetch still counts toward saturation, so we never pay for that filter twice;
the content is ours permanently; and when an explanation source appears — an
author, or a generated-then-reviewed pass — those rows promote through the
normal `MAPPER_VERSION` sweep with no new API calls.

**Measured cost: about 12%.** The probe found `solution` on 88% of 249 sampled
rows, and 87% would promote in full. The risk that motivated a possible
relaxation of the column did not materialise. Coverage is weakest on the oldest
papers (62% for 2005 against 100% for several 2012–2018 samples), so the
rejected cohort will skew old — and being concentrated by year makes it easier
to enrich in bulk later.

## Provider API (as documented)

```
Base URL   https://sdashapi.com/api
Auth       AccessToken: <token>   (custom header, not Authorization)
Endpoint   GET /v1/q?subject=<slug>&type=<slug>&year=<yyyy>&limit=<n>
Discovery  GET /v1/subjects, GET /v1/years
```

`type` slugs: `utme`, `wassce`, `neco`, `post-utme`, `university`.
`limit` defaults to 1, maximum 50. **`limit=1` returns `data` as an object;
`limit>1` returns an array.** The adapter normalises this away.

Response shape:

```json
{
  "status": 200,
  "data": {
    "id": 4821,
    "question": "Which of the following is the chemical formula for table salt?",
    "section": null,
    "option": { "a": "NaCl", "b": "KCl", "c": "CaCO3", "d": "NaOH" },
    "answer": "a",
    "solution": "NaCl is sodium chloride...",
    "image": null,
    "examtype": "UTME",
    "examyear": "2022"
  }
}
```

### Field mapping

| Provider | Ours | Notes |
| --- | --- | --- |
| `data.id` | `ProviderQuestion.providerQuestionId` | Stable; the primary dedupe key |
| `data.question` | `Question.questionText` | |
| `data.option` | `Question.options` | Keys upper-cased by `normalizeOptions` |
| `data.answer` | `Question.correctAnswer` | Upper-cased, then verified as an option key |
| `data.solution` | `Question.explanation` | `null` when absent |
| `data.image` | `Question.questionImageUrl` | |
| `data.examtype` | `Question.examType` | `UTME→JAMB`, `WASSCE→WAEC`, `NECO→NECO` |
| `data.examyear` | `Question.examYear` | String → Int |
| `data.section` | — | No column; retained in the raw payload |
| request `subject` | `Question.subjectId` | Via `Subject.slug` + alias table |

Constant on every imported row: `questionType: OBJECTIVE`,
`difficulty: INTERMEDIATE` (they send none), `questionNumber: null`,
`topicId: null`.

Subject slugs rarely line up. `prisma/seed.ts:280` sets `slug: slugify(name)`,
so only the single-word subjects match directly (`chemistry`, `physics`,
`biology`, `mathematics`, `economics`, `commerce`, `geography`, `government`,
`history`, `insurance`, `music`, `yoruba`, `igbo`, `hausa`). The rest need the
alias table, now written from their real `/v1/subjects` response:

| Ours (`Subject.slug`) | Theirs |
| --- | --- |
| `english-language` | `english` |
| `literature-in-english` | `englishlit` |
| `christian-religious-studies` | `crk` |
| `islamic-studies` | `irk` |
| `civic-education` | `civiledu` |
| `computer-studies` | `computer` |
| `fine-art` | `fineart` |
| `agricultural-science` | `agriculture` |
| `financial-accounting` | `accounting` |
| `arabic` | `arabic` (their "Arabic Studies") |

The mapping is asserted in `test-provider-alias.mts` and re-verified against a
live `/v1/subjects` by `sync-provider-catalogue.ts`, which fails loudly if a
mapped slug disappears from their catalogue.

### Known consequences

- **Imported questions never reach topic-scoped practice.**
  `src/lib/question-pool.ts` notes that a question with `topicId IS NULL` "can
  never satisfy a scope filter — it is silently outside every slot". Imported
  questions therefore serve past-paper and subject-level practice only. The
  imported bank and the curriculum bank remain separate populations until
  something tags them.
- **Questions without a `solution` are never served.** `Question.explanation`
  stays required, so a payload with no solution cannot be promoted. It is
  captured, held in staging with a recorded reason, and waits for an
  explanation. Measured cost: ~12% of rows. See decision 5.
- **Images must be mirrored, or independence is a fiction.** `image` URLs point
  at `res.cloudinary.com/aloc-ng/...` — a third party's Cloudinary account that
  we neither control nor pay for. If they rotate, rename or remove those
  assets, every imported question carrying a diagram silently breaks, and no
  amount of local caching helps because we never held the bytes. On promotion,
  any payload with an `image` has it uploaded to **our** Cloudinary via the
  existing `src/lib/cloudinary.ts`, and `questionImageUrl` is set to our copy.
  It affects only 4% of rows overall (16% for maths), so the added work is
  small — but skipping it would leave a permanent external dependency in a
  design whose entire purpose is removing one.
- **Six of our subjects have no provider coverage.** Their catalogue has 31
  subjects; matching against `prisma/seed.ts` leaves Further Mathematics,
  Technical Drawing, Health Education, Marketing, Office Practice and French
  unmatched. Those subjects simply never appear in `ProviderCatalogue` and are
  unaffected by this work. They also carry five literature set-texts and
  Current Affairs that we have no `Subject` row for; those are ignored.

## Data model

Three new tables. **No columns are added to or altered on any existing table.**

```prisma
enum QuestionProvider { SDASH }

enum ProviderFetchStatus { PENDING  SATURATED  FAILED }
enum ProviderQuestionStatus { PENDING  PROMOTED  REJECTED }

/// The coverage ledger. One row per filter we have ever asked the provider for.
/// Its existence — not the presence of questions — is what makes a read a hit.
model ProviderFetch {
  id        String              @id @default(cuid())
  provider  QuestionProvider
  cacheKey  String              // canonical serialisation of the normalised filter
  status    ProviderFetchStatus @default(PENDING)

  // Denormalised so the admin console can browse coverage without parsing keys.
  subjectId String?
  examType  ExamType?
  examYear  Int?

  drawCount     Int @default(0)  // calls made
  rawCount      Int @default(0)  // distinct questions captured
  newInLastDraw Int @default(0)  // saturation signal
  promotedCount Int @default(0)  // became real questions
  rejectedCount Int @default(0)  // awaiting a human or a better mapper

  error       String?  @db.Text
  questions   ProviderQuestion[]
  startedAt   DateTime @default(now())
  completedAt DateTime?

  @@unique([provider, cacheKey])   // claims the fetch; this is the in-flight lock
  @@index([subjectId, examType, examYear])
}

/// Verbatim capture. Written before any mapping is attempted, and never edited.
model ProviderQuestion {
  id      String        @id @default(cuid())
  fetchId String
  fetch   ProviderFetch @relation(fields: [fetchId], references: [id], onDelete: Cascade)

  provider           QuestionProvider
  providerQuestionId String?
  fingerprint        String   // sha256 of normalised text + sorted option values
  payload            Json     // their response object, untouched

  status           ProviderQuestionStatus @default(PENDING)
  rejectionReasons Json?    // [{ field, message }] — the ImportRowError shape
  mapperVersion    Int      @default(1)

  questionId String?   @unique
  question   Question? @relation(fields: [questionId], references: [id], onDelete: SetNull)

  createdAt  DateTime @default(now())
  promotedAt DateTime?

  @@unique([provider, providerQuestionId])
  @@unique([provider, fingerprint])
  @@index([status, mapperVersion])
}

/// Papers the provider actually holds, so the picker can offer papers we have
/// not fetched yet. Built by sweep — see "Building the catalogue".
model ProviderCatalogue {
  id        String           @id @default(cuid())
  provider  QuestionProvider
  subjectId String
  subject   Subject          @relation(fields: [subjectId], references: [id], onDelete: Cascade)
  examType  ExamType
  examYear  Int
  syncedAt  DateTime         @updatedAt

  @@unique([provider, subjectId, examType, examYear])
  @@index([examType, examYear])
}
```

`Question` and `Subject` each gain one **back-relation** so Prisma can validate
the two new foreign keys:

```prisma
// on Question
  providerQuestion ProviderQuestion?

// on Subject
  providerCatalogue ProviderCatalogue[]
```

These are virtual fields. Prisma back-relations produce **no database column
and no DDL** — the foreign keys live on `ProviderQuestion` and
`ProviderCatalogue`. `Question` is untouched at the SQL level.

`ProviderQuestion.providerQuestionId` is nullable and carries a unique
constraint. That is intentional and safe: Postgres permits multiple `NULL`s in
a unique index, so payloads without an id fall through to the `fingerprint`
constraint instead of colliding with each other.

### Why these shapes

`@@unique([provider, cacheKey])` does double duty. It records coverage *and*
serves as the concurrency lock: the first request inserts a `PENDING` row and
wins, a concurrent second request hits the constraint, finds the existing row,
and waits rather than firing its own API call.

`fingerprint` is a secondary guard, not the primary key — `data.id` is stable,
so it is preferred. The hash catches the same question reissued under a new id.

`mapperVersion` + `status` is the re-promotion machinery: sweep `REJECTED` rows
with a version below the current one and retry them offline.

`rejectedCount` is deliberately **not** a failure signal. A fetch that returns
50 and promotes 34 is `SATURATED` — we hold all 50 raw, permanently. Only a
network, auth or protocol failure marks a fetch `FAILED` and leaves it eligible
for a retry.

## Module layout

```
src/lib/question-provider/
  types.ts        — provider interface + payload types
  sdash.ts        — the adapter: URLs, AccessToken header, zod response schema
  alias.ts        — subject-slug and exam-type alias tables (pure)
  cache-key.ts    — filter normalisation → canonical key (pure)
  mapper.ts       — raw payload → Question create input | rejections (pure)
  saturation.ts   — the stop rule and its thresholds (pure)
  errors.ts       — response classifier: empty / terminal / retryable (pure)
  ingest.ts       — orchestrator: claim, fetch, stage, promote, count
scripts/
  sync-provider-catalogue.ts
  probe-sdash.ts                    — live smoke checks, not in `npm test`
  test-provider-mapper.mts
  test-provider-cache-key.mts
  test-provider-alias.mts
  test-provider-saturation.mts
```

Everything but `ingest.ts` and `sdash.ts` is pure.

### The adapter boundary

```ts
export type ProviderFilter = {
  subjectSlug: string;
  examType: "WAEC" | "JAMB" | "NECO";
  examYear: number;
};

export interface QuestionProviderAdapter {
  readonly name: QuestionProvider;
  /** Their stable id for a payload, when they expose one. */
  identify(payload: unknown): string | null;
  /** One draw. Throws ProviderError (retryable vs terminal) on failure. */
  draw(filter: ProviderFilter, limit: number): Promise<unknown[]>;
}
```

`draw` returns `unknown[]` deliberately. It normalises the object-vs-array
split so nothing downstream cares, and hands payloads on **unvalidated**.
Validation belongs to the mapper, which runs against what we stored rather than
what came off the wire — that is what makes offline re-promotion possible.

`sdash.ts` takes an injected `fetch`, so it is testable against canned
responses with no network.

### The entry point

```ts
/**
 * Serves a filter from our own bank, calling the provider only the first time
 * we ever see it. Returns the questions and how they were obtained.
 */
export async function ensureQuestionsCached(
  filter: ProviderFilter,
  limit: number,
): Promise<{
  questions: Question[];
  source: "db" | "provider";
  ledger: { status: ProviderFetchStatus; rawCount: number; promotedCount: number };
}>;
```

Callers get questions. Whether a network call happened is observable but never
something they must handle, and once a filter saturates that branch is dead
code for it forever.

## Two prerequisites in existing code

Both are pre-existing gaps that this design cannot work around.

### The picker can never trigger a miss

`PastQuestionPicker` (`src/components/practice/past-question-picker.tsx:42`)
builds its entire three-step UI from `/api/questions/past-papers`, which is
`listPastPapers` grouping over `Question`. A paper we have never fetched is not
in that list, so it cannot be selected, so it is never fetched. Cache-on-read
cannot bootstrap itself.

**Fix:** `listPastPapers` returns the union of papers we hold and papers
`ProviderCatalogue` records. `PastPaper` gains:

```ts
cached: boolean;
questionCount: number | null;   // null when not yet fetched
```

The picker renders uncached papers without a count. The first student to pick
one pays a single API call.

### The year is collected and then discarded

The picker's link already carries the year —
`src/components/practice/past-question-picker.tsx:224` builds
`?exam=...&year=...`. It is dropped one layer down:
`src/app/(dashboard)/practice/past-questions/[subjectSlug]/page.tsx:12` reads
only `exam`, and `generateQuizSchema` (`src/lib/validators.ts:60-76`) has no
`examYear` field to receive it in any case. Today a "2022 JAMB Chemistry" pick
generates from every JAMB Chemistry year in the bank.

Our cache key is (subject, examType, year), so this must be fixed regardless.
`examYear` is added to `generateQuizSchema` and to `QuestionPoolFilter`, read
from the search params in the quiz page, and passed through `generateQuiz` —
including into `findResumableAttempt`, or picking 2022 would resume an
unfinished 2019 paper of the same subject and length.

### Building the catalogue

`/v1/years` returns a single global list (2001–2026), not per-subject or
per-type coverage, and the probe found `type=neco` empty for `biology/2018`
and `chemistry/2014` while returning 2021/2023/2024 questions with no year
filter. A cross product of subjects × years × types would therefore advertise
thousands of papers that yield nothing, and the picker would offer them.

So `sync-provider-catalogue.ts` **sweeps**: one `limit=1` request per
(mapped subject × year × exam type), writing a `ProviderCatalogue` row only
where the response is `200`. At 24 mapped subjects × 26 years × 3 types that is
~1,870 requests, about 20 minutes with polite spacing — once, offline, run as
an ops script rather than in a request path. Re-running it is safe and
idempotent; it is worth repeating annually as new years appear.

A `404` during the sweep is a definitive "nothing here", not a failure, and is
simply not recorded.

## The read path

The fetch fires in `generateQuiz` (`src/lib/assessment-generation.ts:56`),
after the subject resolves and before `pickQuestionsPreferringUnseen`. Not in
`listQuestions` — that is the admin browse path and must not hit the network.

```
resolve subject
  ↓
examType && examYear present?  ──no──→  unchanged: pick from DB
  ↓ yes
ledger row SATURATED?          ──yes─→  unchanged: pick from DB
  ↓ no
await ensureQuestionsCached({ subjectSlug, examType, examYear }, 50)
  ↓
after(() => saturate(filter))   ← remaining draws, off the response path
  ↓
pick from DB as normal
```

Gated on all three of subject/type/year, so topic quizzes, mock exams and JAMB
CBT never reach it. Those flows keep reading `Question` through the raw SQL in
`question-pool.ts` and gain the new rows for free. **No generator changes.**

`src/lib/rate-limit.ts` also caps outbound provider calls so a burst of misses
cannot hammer them.

## Merge rules

Per staged payload, in one transaction:

1. **Dedupe.** Upsert on `(provider, providerQuestionId)` using `data.id`.
   Already present → skip entirely; do not re-count, do not re-promote. The
   `fingerprint` is checked as a second key.
2. **Map** via the pure mapper (field table above). Option entries that are
   `null` or empty are dropped before counting.
3. **Validate** with a zod schema for their envelope, then
   `checkQuestionInvariants` from `src/lib/admin-question.ts` — the same gate
   the admin bulk import already passes through.
4. **Mirror the image, if any.** A payload with an `image` has it uploaded to
   our own Cloudinary through `src/lib/cloudinary.ts`, and `questionImageUrl`
   is set to our copy — never to `res.cloudinary.com/aloc-ng/...`. A mirror
   failure rejects the row rather than promoting a question that points at a
   third party's asset; it retries on the next `MAPPER_VERSION` sweep. Affects
   ~4% of rows.
5. **Promote or reject.** Promote → create the `Question`, set
   `ProviderQuestion.questionId`, `status: PROMOTED`. Reject → `status:
   REJECTED` with `rejectionReasons` in the `{field, message}` shape
   `ImportRowError` already uses, so the admin console can render it with
   existing components.
6. **Update ledger counts** in the same transaction, so they cannot drift.

### Rejects

- `answer` is not a key of `option`. This is the dangerous case: nothing in the
  schema stops it, and such a question marks every student wrong, silently.
- Fewer than `MIN_OBJECTIVE_OPTIONS` (4) usable options.
- Empty or whitespace-only `question`.
- `examyear` that will not parse to an integer.
- Missing, `null`, or whitespace-only `solution` — `Question.explanation` is
  required (decision 5). Reason field `explanation`, so the admin console can
  filter this cohort on its own; it is the one rejection class that is a
  content gap rather than a data defect, and the one most likely to be
  recoverable in bulk.

Everything rejected keeps its raw payload and is eligible for re-promotion when
`MAPPER_VERSION` bumps — or, for the `explanation` cohort, as soon as an
explanation exists for it.

## Testing

The interesting logic is pure, so it tests in the existing `node --test` +
`tsx` style with no database and no network, matching
`scripts/test-admin-question.mts` and `scripts/test-admin-import.mts`.

- **`test-provider-mapper.mts`** — the documented payload as a fixture, plus
  mutations that must reject (`answer: "e"` against options a–d, three options,
  empty `question`, `examyear: "n/a"`, and `solution: null` / `solution: "  "`)
  and mutations that must promote (`image` present, `image: null`, lower-case
  answer key, options given out of order).
- **`test-provider-cache-key.mts`** — `chemistry/JAMB/2022` and
  `Chemistry/jamb/2022 ` produce one key; different years never collide.
- **`test-provider-alias.mts`** — `utme→JAMB`, `wassce→WAEC`, `neco→NECO`;
  `post-utme` and `university` are refused rather than folded into `CUSTOM`;
  `english-language` and `further-mathematics` resolve.
- **`test-provider-saturation.mts`** — the stop rule as a pure function of
  `(drawCount, rawCount, newInLastDraw, returnedCount)`: fewer than 10 new ids
  saturates; a short draw (under 50 returned) saturates; the 12-draw cap
  saturates; and the measured decay sequence 50/39/32/26 must *not* saturate at
  draw 4, guarding the threshold against being tightened by accident.
- **`test-provider-errors.mts`** — the response classifier, using the real
  envelopes the probe captured: `404` → saturated-empty (never retried),
  `403`/`401` → terminal failure, `5xx`/transport → retryable. The `404` case
  is the one that matters most; misclassifying it as a failure reintroduces
  unbounded retries.

`sdash.ts` is exercised against canned responses through its injected `fetch`,
covering the `limit=1` object vs `limit>1` array split, a non-200 `status` in
the body, and malformed JSON. `ingest.ts` stays thin — claim, call, delegate,
count — so little logic lives in the untested impure layer.

All four suites are added to the `test` script in `package.json`.

## Migration

This does **not** go through `prisma migrate deploy` (`DIRECT_URL` is
unreachable from the dev machine).

1. Hand-write
   `prisma/migrations/20260901000000_question_provider_cache/migration.sql`
   with **LF line endings** — with `core.autocrlf=true`, a CRLF file silently
   drifts the migration checksum.
2. Apply it through the Supabase SQL editor, then **verify against
   `information_schema`** rather than trusting the success message; that editor
   reports success on partly-applied batches.
3. Contents: three enums and three tables. **No `ALTER TABLE` on any existing
   table** — the migration is purely additive, which makes rolling it back a
   matter of dropping the new objects.
4. **Stop the dev server before `prisma generate`** — otherwise it fails EPERM
   on the query engine DLL and leaves a stale client throwing bogus `tsc`
   errors.

No existing render site changes. `src/components/assessment/results-view.tsx`,
the classroom practice result page, `src/components/admin/question-form.tsx`
and the zod schemas in `src/lib/validators.ts` all keep treating `explanation`
as a non-null string, because it still is one.

## Environment

```
SDASH_BASE_URL=https://sdashapi.com/api
SDASH_ACCESS_TOKEN=
QUESTION_PROVIDER_ENABLED=false
```

In `.env` (gitignored) with placeholders mirrored into `.env.example`, matching
how `TERMII_API_KEY` and `CLOUDINARY_API_SECRET` are handled. The flag ships
the whole feature dark.

The access token was shared in plain text during design and should be rotated
once the integration is wired up.

## Rollout

Seven steps (step 0 already done), each independently shippable.
Imported-question quality is verified before any student sees one.

0. ~~Probe~~ **Done 2026-09-02** — see "Probe findings". Yield is 87%; the
   design is calibrated to the measurements and cleared to proceed.
1. Migration, schema, regenerated client. Purely additive; nothing references
   it yet.
2. Pure modules and their tests. No behaviour change.
3. Adapter plus `sync-provider-catalogue.ts`. Run the sweep once (~1,600
   requests, roughly 20 minutes); `ProviderCatalogue` fills with combinations
   that actually hold questions. Still dark.
4. `ingest.ts`, reachable **only** from an admin-triggered backfill. Saturate a
   few papers, then review them in the admin console — real data, real
   rejection reasons, nothing at stake if their bank is not good enough.
5. Thread `examYear` through `generateQuizSchema`, the picker link and
   `generateQuiz`. Fixes the existing bug on its own merits.
6. `ProviderCatalogue` into `listPastPapers`; flag on. Student misses now
   self-serve.

## Open questions

Resolved by the 2026-09-02 probe: `solution` coverage (88%), draw randomness
(random redraw, ~300–400 per pool), the error envelope, and rate-limit headers
(none exposed).

Still open:

- **Their licensing terms.** The image URLs show sdashapi is fronting the ALOC
  question bank. Systematically drawing their entire catalogue into our own
  database is a different act from per-request lookups, and mirroring their
  images to our Cloudinary more so. This is a commercial question, not a
  technical one, and it should be settled with them before the step 3 sweep —
  it is the one item here that could invalidate the whole approach.
- **Total cost to full independence.** ~1,870 sweep requests plus roughly 8–12
  draws for each non-empty combination. If a third of swept combinations hold
  questions, that is on the order of 5,000–7,000 further requests to saturate
  everything — hours, not months, but it needs to sit inside whatever their
  plan allows.
- **No rate limits are published or exposed in headers.** The outbound cap
  starts conservative (spacing comparable to the probe's 350ms) and can be
  raised once we know what they permit.
- **`data.section` is unmapped.** Populated on 25% of rows overall but 100% of
  `english/utme/2012`, so it likely groups comprehension passages. Questions
  sharing a passage arguably should not be served apart. It stays in the raw
  payload; if it proves to matter, it can be mapped later and back-promoted
  with a `MAPPER_VERSION` bump — no re-fetching.
- **Question numbering is partly recoverable.** Image filenames embed it
  (`MATH_JAMB_2019_Q15_...`), so `questionNumber` could be parsed for the ~4%
  of rows with images. Not worth doing on its own; noted in case ordering
  matters later.
