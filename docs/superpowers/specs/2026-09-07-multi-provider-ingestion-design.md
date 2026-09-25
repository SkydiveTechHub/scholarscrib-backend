# Multi-Provider Question Ingestion — Recovery, Latency, and ALOC

> **Status (2026-09-07, same day): ALOC declined. Decisions 1–4 stand and are
> being implemented; Decisions 5–10 are abandoned.**
>
> After costing and probing ALOC the decision was to stay on sdashapi alone.
> Everything in this document about the empty SDash wallet, the terminal-403
> blackhole, the measured ingest latency, and Decisions 1 through 4 remains
> live and is implemented by
> `docs/superpowers/plans/2026-09-07-provider-recovery-and-latency.md`.
>
> Decisions 5 (adapter generalisation), 6 (provider roles), 7 (bought
> explanations), 8 (Markdown flattening), 9 (credit budget) and 10
> (demand-ordered backfill) describe a second provider that will not be
> added. The ALOC probe findings are retained because they were measured
> against the live API and would otherwise have to be re-bought at 10 credits
> an explanation if the question is ever reopened.

Supersedes nothing. Extends `2026-09-01-question-provider-cache-design.md`, whose
ledger, staging and promotion model all survive intact; what changes is when
ingestion runs, how failure is classified, and how many providers feed it.

## Problem

Three problems share one system, and only the third is new.

**The bank is being blackholed right now.** `sdashapi` answers every endpoint
with `403 {"status":403,"message":"Insufficient credit. Please top up your
wallet."}`. `classifyStatus` maps 403 to `terminal`
(`src/lib/question-provider/errors.ts:22`), so `drawOnce` marks the filter
`FAILED` and both `ensureQuestionsCached` and `saturate` treat that as final.
With `QUESTION_PROVIDER_ENABLED=true`, every cold paper a student touches is
permanently retired until someone runs `resetFailedFetch` by hand. The doc
comment on that function anticipated this exact case — "a 403 while a plan
lapsed" — and the case has arrived. The damage accumulates silently and in
proportion to traffic.

**Students wait on the provider.** `ensureQuestionsCached` is awaited before the
assessment is created (`src/lib/assessment-generation.ts:136`), and inside it
`drawOnce` runs a serial loop over 50 payloads doing one dedupe `findFirst`
(`ingest.ts:344`) plus a two-insert `$transaction` (`ingest.ts:437`) each —
roughly 200 sequential round trips — with `uploadRemoteImage` mirroring
provider images to Cloudinary *inside* that loop (`ingest.ts:~390`) at 1–3s
apiece. `prepareJambYear` multiplies this by four subjects through
`Promise.all`, against `connection_limit=5`. The target audience is teenagers;
this is the wrong place to spend their patience.

**One provider is one point of failure.** Its outage is total, its subject
coverage is a hard ceiling, and its explanations are a flat `solution` string.

## Goal

A bank deep enough that a student request never triggers a provider call, fed by
two providers whose different shapes are used for what each is good at, which
degrades without data loss while unfunded and resumes without intervention when
funded.

### Non-goals

- **Generating explanations.** ALOC sells them at 10 credits, putting the whole
  bank inside one ₦20,000 Growth month; generating the same 12,645 with Opus 5
  costs ~$76, several times more for the same class of artifact (both are
  AI-authored) and less structure. Decided against in deliberation; not
  revisited here.
- **Restructuring `Question.explanation`.** ALOC returns `steps[]` and
  `commonMistakes[]`, which deserve first-class storage eventually. Deferred —
  see Decision 8.
- **Serving questions without explanations.** The invariant at `mapper.ts:118`
  stands.
- **Replacing the in-memory rate limiter generally.** Only the provider-spend
  path moves to the database (Decision 9).

## Probe findings (2026-09-07)

Measured against both live APIs, not inferred.

### sdashapi

| | |
|---|---|
| Every endpoint | `403 Insufficient credit. Please top up your wallet.` |
| Latency | 0.9–2.0s |

The wallet is empty. Nothing else about SDash could be measured, so this
document assumes the contract in the 2026-09-01 spec still holds and requires
re-verification after top-up (Open question 1).

### ALOC (`https://dev.aloc.com.ng/api/v1`)

| | |
|---|---|
| Auth | `X-API-Key: <key>` or `Authorization: Bearer <key>`. **Not** SDash's `AccessToken` |
| Questions | `GET /questions?subject=&examType=&year=&limit=&cursor=` |
| Page size | **`limit` caps at 15** (400 above it) |
| Pagination | Opaque cursor; `pagination.{nextCursor,prevCursor,hasMore}` |
| Explanations | `POST /questions/{id}/explain` |
| Catalogue | `GET /subjects`, `GET /subjects/{name}` → `questionCount`, `examTypes`, `yearRange` |
| Envelope | `{ data, pagination?, meta: { creditsUsed, creditsRemaining, tier, requestId } }` |
| Cost | 1 credit per question page; **10 credits per explanation** |
| No bulk explain | `/explanations/bulk`, `/questions/explain-bulk` and POST `/questions/explain` all 404/405 |

Pagination terminates honestly. Walking chemistry/2015/jamb to exhaustion gave
4 pages, 50 unique questions, **zero duplicates**, `hasMore:false` — 6.8s total
(2.9s cold, ~1s warm). This is a real enumeration, not SDash's random redraw
from a pool, and it retires the coupon-collector heuristics in `saturation.ts`
for this provider.

Explanations are not idempotent in billing: the same question explained twice
returned identical text and charged 10 credits both times (982 → 972). Fetch
once, store forever.

Question payload:

```
id (stable uuid)  text  options{A,B,C,D}  correctAnswer  examType  subject  year
imageUrl  questionNumber  section  educationLevel  country  category
provenance{contentSource,reviewStatus,...}  metadata{topic,subtopic,difficultyScore,...}
```

Explanation payload:

```
explanation             full prose
simplifiedExplanation   one-sentence version
steps[]                 the working, in stages
commonMistakes[]        per distractor: { mistake, whyWrong }
solutionImageUrl        sourceType: "ai"   confidence: 0.9   needsReview: false
```

`options` arrive already upper-cased and `correctAnswer` already matches a key,
so `normalizeOptions` and `checkQuestionInvariants` should pass cleanly. There
is no `explanation` field on the question itself — it must be bought separately.

### ALOC catalogue (dev host)

12,645 questions across 15 subjects — `english-language` 1798, `government`
1491, `accounting` 1435, `mathematics` 1209, `physics` 1156,
`christian-religious-studies` 1017, `commerce` 886, `chemistry` 794,
`economics` 655, `literature-in-english` 557, `civic-education` 436,
`geography` 436, `biology` 383, `insurance` 342, `history` 50.

Exam-type coverage: jamb 12 subjects, waec 11, post_utme 8, neco 3. JAMB-heavy —
chemistry and biology are JAMB-only, physics has no WAEC, biology stops at 2012.

Full backfill: **843 credits of questions + 126,450 of explanations =
127,293 credits.** Against published pricing (Free ₦0/1,000 with only 100 trial
credits for L2–L4; Developer ₦5,000/50,000 incl. L1–L3; Growth
₦20,000/500,000): one Growth month covers the whole bank four times over, or
three Developer months cover it for ₦15,000.

### Infrastructure

Supabase transaction pooler, `connection_limit=5`, `pool_timeout=75`. Round
trip from a dev machine to eu-west-1: **1,206ms median warm, 3,374ms cold
connect.** Production co-located will be one to two orders of magnitude faster —
the number that matters is not the RTT but that the cold path multiplies it by
~200.

## Decisions

### 1. "Out of credit" is retryable and self-healing, never terminal

This is the decision the whole document hangs on, because both providers are
currently unfunded and the system must resume by itself when they are funded.

`classifyStatus` currently collapses every 403 into `terminal`. Two very
different conditions share that code: *this key may never access this
resource* (SDash's `post-utme` entitlement, a revoked key) and *this account
is temporarily out of money*. The first is genuinely final. The second is a
billing state that reverses the moment someone tops up, and treating it as
final is what converts a funding gap into permanent, hand-repairable data loss.

Split it. `classifyStatus` gains a fourth class, `exhausted`, selected by
matching the response body rather than the status code alone — SDash says
`Insufficient credit`, ALOC's credit responses carry `creditsRemaining`. An
`exhausted` result never writes `FAILED`; it leaves the ledger `PENDING` and
opens a **circuit breaker** for that provider.

The breaker is a `ProviderState` row per provider recording `state`,
`cooldownUntil`, `lastError` and `creditsRemaining`. `drawOnce` consults it
before spending a request, cached in process for ~30s so it costs nothing on
the hot path. On `exhausted`, `cooldownUntil` is set to now + 15 minutes; the
first draw after expiry probes once, and either closes the breaker or re-arms
it. **No admin action is required to resume** — topping up the wallet is
sufficient, and the bank starts filling within fifteen minutes.

This inverts the current failure mode: instead of traffic *deepening* the
damage while unfunded, traffic costs one probe per fifteen minutes and the
ledger stays exactly as recoverable as it was.

A one-off migration resets existing `FAILED` rows whose `error` matches the
insufficient-credit text back to `PENDING`, preserving `drawCount` the way
`resetFailedFetch` already does so a filter that failed on its ninth draw does
not receive twelve fresh ones.

### 2. No student request ever awaits a provider call

`ensureQuestionsCached` is split in two. The request path calls a strictly
read-only `readBank(filter, limit)` — the `readFromDb` query against
`@@index([subjectId, examType, examYear])` (`schema.prisma:490`) and nothing
else. Drawing moves entirely to `after()`, the admin backfill route, and the
scheduled backfill.

The consequence is honest and must be surfaced rather than hidden: the first
student to reach a genuinely cold paper gets whatever the bank holds, possibly
a short quiz or none. That is a *product* state — "this paper is still being
prepared" — and the uncommitted `prepare` route is already the right place for
it. Its own comment says the fetch and any shortfall should "land in the picker
rather than behind Start exam"; this decision finishes that thought by removing
the await as well as moving it.

Decision 10 then makes cold papers rare enough that the state is an edge case
rather than the common experience.

### 3. Ingest batches its writes

Inside `drawOnce`, per payload: one `findFirst`, one transaction, two inserts.
Replace with three statements per *draw*:

1. One `findMany` selecting this fetch's existing `providerQuestionId`s and
   `fingerprint`s into a `Set`; dedupe in memory.
2. One `createMany` for the staged rows.
3. Promotion in a single transaction per draw rather than per question.

~200 round trips become ~5. This is the largest available latency win, it is
independent of ALOC, and it also relieves the `connection_limit=5` contention
between background ingest and live traffic that makes concurrency painful.

Promotion is still per-question in its *semantics* — one `Question` row per
promoted payload — but the batch commits together. A mid-batch failure rolls
back the batch rather than leaving half of it committed, which is a strict
improvement on the current "whatever committed before the throw stays
committed" behaviour that `drawOnce`'s catch block documents.

### 4. Image mirroring leaves the draw loop

`uploadRemoteImage` is network-bound and cannot be made fast, so it must not be
synchronous with anything a person is waiting for. Questions whose payload
carries an image stage as `PENDING` with the existing
`Could not mirror the image` reason shape, and a separate pass — the same
`MAPPER_VERSION` sweep machinery, or a dedicated cron — mirrors and promotes
them.

This costs a little coverage latency on image-bearing questions and removes the
worst tail from the ingest path. It also fixes a real correctness wart: today a
Cloudinary hiccup mid-draw aborts the remaining payloads of that draw.

### 5. The adapter boundary widens; the shared core does not move

The ledger is already provider-keyed — `@@unique([provider, cacheKey])`
(`schema.prisma:942`) and `@@unique([provider, subjectId, examType, examYear])`
(`:987`) — so two providers get independent ledgers, independent leases, and
never contend. That design decision was made correctly in advance and needs no
change.

What is hardcoded is `const PROVIDER = "SDASH"` (`ingest.ts:10`) threaded
through roughly ten sites, `name: "SDASH"` as a literal type, and
`getSdashAdapter` as the default dependency. Thread the provider through
instead of closing over it.

Split what is provider-specific out of the shared core:

```
src/lib/question-provider/
  types.ts        widen `name` to a union; ProviderFailureKind gains "exhausted"
  registry.ts     NEW — provider name → adapter + policy
  ingest.ts       shared: lease, claim, stage, promote, ledger
  state.ts        NEW — circuit breaker + credit budget
  cache-key.ts    unchanged
  sdash/          adapter.ts alias.ts mapper.ts saturation.ts
  aloc/           adapter.ts alias.ts mapper.ts saturation.ts
```

`saturation.ts` moves under `sdash/` unchanged — every constant in it is a
measured property of SDash's random-redraw behaviour and none of it applies to
a cursor-paginated source. ALOC's saturation policy is "walk until
`hasMore` is false", which terminates honestly.

### 6. SDash is the live provider; ALOC is the backfill provider

The roles fall out of the API shapes rather than being imposed.

SDash returns 50 promotable questions — solutions included — in one round trip.
That is the only shape that can usefully sit behind an `after()` warm-up on a
paper a student just asked for.

ALOC needs 1 page-call per 15 questions plus a 10-credit, ~1s explain call per
question. A response-path ALOC draw is not merely slow, it is *useless*: a
question without an explanation cannot be promoted (`mapper.ts:118`), so the
student who triggered it gets nothing and only later students benefit. ALOC
therefore runs offline only — admin backfill route or cron — and never from a
user-facing request.

This also disposes of the runtime provider chain in the original framing.
Nothing chains at request time; the bank is the only thing on the read path.

### 7. Explanations are bought once and stored verbatim

`POST /questions/{id}/explain` costs 10 credits and is billed again on every
call, so it is fetched exactly once per question during backfill and the whole
response is written to `ProviderQuestion.payload` alongside the question
payload. That row is already defined as verbatim capture that is never edited,
which is what makes Decision 8 safe to defer.

Promotion gates on ALOC's own quality signals: promote when
`needsReview === false` and `confidence >= 0.7`; otherwise stage as `PENDING`
with the reason recorded, for a later sweep or human review. The thresholds are
a starting point to be tuned against observed data, not a measured result.

### 8. `Question.explanation` stays a string; structure is flattened to Markdown

`steps[]` and especially `commonMistakes[]` — per-distractor "why this option is
wrong" — are the most pedagogically valuable part of the payload and deserve
their own columns and their own results UI. That is a schema migration plus
front-end work, and it is not needed to ship a provider.

For now the mapper flattens `explanation`, `steps[]` and `commonMistakes[]`
into a single Markdown string. The project already renders Markdown
(`src/lib/markdown-segments.ts`) and LaTeX (`src/lib/latex.ts`), so structure
survives as headings and lists rather than being discarded.

Nothing is lost by waiting: the raw payload is captured, and bumping
`MAPPER_VERSION` re-runs the mapping against stored payloads **with zero new
API calls and zero new credits**. That is precisely the property the
capture-then-map split was built for. Restructuring later costs a migration and
a backfill sweep, not another ₦20,000.

### 9. Provider spend is guarded in the database, not in process memory

`src/lib/rate-limit.ts` is a per-process fixed window whose own comment concedes
the real ceiling is `limit × instances`. For SDash that overshoot wastes calls.
For ALOC it spends **money from a shared, finite monthly allowance** — a
per-instance counter structurally cannot guard a global budget, and 50,000
credits is an afternoon's worth of uncontrolled saturation.

Credit spend is therefore tracked on the `ProviderState` row with an atomic
increment, carrying `creditsSpentThisPeriod`, `periodStartedAt` and a
configured `creditCeiling`. A draw that would cross the ceiling declines and
opens the breaker exactly as an `exhausted` response does. `meta.creditsRemaining`
from ALOC's own envelope is written back on every call as a cross-check against
drift.

Because ALOC runs offline and single-threaded (Decision 6), this guard is
belt-and-braces rather than load-bearing today — but it is what makes it safe
to ever reconsider that.

### 10. Backfill is demand-ordered

The full backfill is roughly 13,500 calls — about an hour of wall clock at
Growth's 300 req/min, longer with retries. There is no reason to spend it
alphabetically. Order the backfill by what students
actually pick: the exam targets in `src/lib/exam-target.ts` and the years the
picker surfaces, most-requested first. The papers most likely to be hit cold are
warmed first, which is what turns Decision 2's honest "still being prepared"
state into a rarity.

## Data model

Additive. No column on `Question` changes; no existing constraint is dropped.

```prisma
enum QuestionProvider {
  SDASH
  ALOC          // new
}

enum ProviderCircuitState {
  OK
  EXHAUSTED     // out of credit — retry after cooldown
  BLOCKED       // 401, revoked key, unentitled — needs a human
}

/// One row per provider. The circuit breaker and the spend ledger.
model ProviderState {
  provider               QuestionProvider     @id
  state                  ProviderCircuitState @default(OK)
  cooldownUntil          DateTime?
  lastError              String?              @db.Text
  lastCheckedAt          DateTime             @updatedAt

  creditsRemaining       Int?                 // as last reported by the provider
  creditsSpentThisPeriod Int                  @default(0)
  periodStartedAt        DateTime             @default(now())
  creditCeiling          Int?                 // null = no local ceiling
}
```

And one index, without which the cross-provider duplicate guard seq-scans:

```prisma
model ProviderQuestion {
  // ...
  @@index([fingerprint])   // new
}
```

`ProviderQuestion` already carries `@@unique([fetchId, fingerprint])`
(`schema.prisma:972`), but `fetchId` leads, so a fingerprint-only lookup cannot
use it.

### The cross-provider duplicate guard

`ProviderQuestion` dedupes within a fetch, deliberately, so a question recycled
across sittings can promote once per paper (`schema.prisma:967`). Two providers
covering the same paper means two fetch rows, so the same question would promote
into `Question` twice and a student could meet it twice in one quiz.

Before promoting, check whether a `PROMOTED` `ProviderQuestion` with this
fingerprint already exists for the same `(subjectId, examType, examYear)`; if so,
stage the new row as `PROMOTED` pointing at the *existing* `questionId` rather
than minting a second `Question`. Provenance from both providers is preserved,
the student sees one question, and — usefully — a disagreement between the two
providers' `correctAnswer` for the same fingerprint becomes a detectable
answer-key audit signal.

This runs during batched promotion (Decision 3), off any response path.

### ALOC vocabulary

Subject names map to our `Subject.slug` almost directly — `english-language`,
`literature-in-english`, `christian-religious-studies`, `civic-education`,
`mathematics` all match, and ALOC's `aliases` array accepts
`financial-accounting` for its `accounting`. The alias table is therefore near
empty compared to SDash's, and `GET /subjects/{name}` supplies `examTypes` and
`yearRange` directly, so `ProviderCatalogue` sync needs no cartesian guessing.

Exam types map `jamb→JAMB`, `waec→WAEC`, `neco→NECO`. `post_utme` is excluded
for the same reason SDash's is: our `ExamType` enum could only hold it as
`CUSTOM`, which corrupts past-paper grouping.

## Phases

Ordered so that each phase is independently shippable and the urgent work does
not wait on the interesting work.

**Phase 0 — stop the bleeding.** Decision 1: the `exhausted` class,
`ProviderState`, the breaker, and the migration resetting wrongly-`FAILED`
rows. Ships before any top-up so that funding the wallets is sufficient to
resume. No new provider.

**Phase 1 — latency.** Decisions 2, 3, 4: read-only request path, batched
writes, image mirroring moved out. Still single-provider. This is the phase that
answers "will students wait", and its value does not depend on ALOC at all.

**Phase 2 — generalise.** Decision 5: thread the provider through, split the
adapter directories, registry. Behaviour-preserving refactor with SDash as the
only registered provider; the test suite should pass unchanged.

**Phase 3 — ALOC.** Decisions 6, 7, 8: adapter, alias, mapper, cursor
saturation, explanation fetch, Markdown flattening. Plus the fingerprint index
and cross-provider guard.

**Phase 4 — backfill operations.** Decisions 9, 10: the DB-backed spend guard,
demand-ordered backfill, catalogue sync from `/subjects/{name}`, and the admin
surface to run and monitor it.

## Testing

The existing suite's `IngestDb` fake (`ingest.ts:47`) is the right seam and
extends naturally; new work follows it rather than mocking Prisma.

- `classifyStatus` returns `exhausted` for SDash's insufficient-credit body and
  `terminal` for its entitlement 403 — the distinction Decision 1 rests on.
- A draw against an `exhausted` provider leaves the ledger `PENDING`, sets
  `cooldownUntil`, and spends no request while the breaker is open.
- Breaker expiry probes exactly once, and closes on success.
- The migration resets insufficient-credit `FAILED` rows and preserves
  `drawCount`.
- `readBank` issues no provider call under any ledger state.
- Batched dedupe rejects the same duplicates the per-row `findFirst` did.
- ALOC cursor saturation stops on `hasMore:false`, and never exceeds a
  configured page ceiling even if the provider lies about `hasMore`.
- An ALOC question promotes only with an explanation, `needsReview:false`, and
  `confidence >= 0.7`.
- The same fingerprint from both providers for one paper yields one `Question`
  and two `ProviderQuestion` rows.
- The credit ceiling opens the breaker before the provider's own limit does.

Contract tests against recorded fixtures for both providers, not live calls —
ALOC explanation calls cost 10 credits each and a chatty test suite is a
recurring bill.

## Environment

```
ALOC_API_KEY=            # X-API-Key header
ALOC_BASE_URL=           # https://dev.aloc.com.ng/api/v1 — verify vs production
ALOC_CREDIT_CEILING=     # optional local ceiling, per period
QUESTION_PROVIDER_ENABLED=true
```

`SDASH_ACCESS_TOKEN` and `SDASH_BASE_URL` are unchanged. Neither provider's key
belongs in a committed file.

## Open questions

1. **Does SDash's contract still hold?** Everything in the 2026-09-01 spec was
   measured on 2026-09-02 against a funded wallet. Re-verify subjects, years and
   draw shape immediately after top-up, before trusting Phase 1 in production.

2. **Is `dev.aloc.com.ng` representative of production?** 12,645 questions
   across 15 subjects is narrow beside SDash's 31 subjects, and ALOC's older
   public API advertised 20,000+. If dev is a reduced sandbox, the catalogue
   figures and the ₦ estimates in this document are wrong — probably
   understated. Resolve before buying a plan.

3. **Does Developer tier actually unlock L3 explanations?** The pricing page
   says L1–L3 for Developer; the docs' tier table calls explanations Growth.
   Our free key called `/explain` successfully on dev and was billed, which
   suggests dev does not enforce entitlement at all. ₦15,000 versus ₦20,000
   turns on this.

4. **What happens to a paper that is cold at request time?** Decision 2 makes
   this a product state. The `prepare` route models it for JAMB CBT; past-paper
   and topic quiz flows need the equivalent copy and UI.

5. **`confidence >= 0.7` is a guess.** Sample a few hundred ALOC explanations
   after Phase 3 and set the threshold against observed quality rather than
   against a round number.
