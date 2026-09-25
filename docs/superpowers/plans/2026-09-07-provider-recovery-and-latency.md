# Provider Recovery and Ingest Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop an unfunded provider wallet from permanently blackholing papers, and take every provider call and Cloudinary upload off the path a student waits on.

**Architecture:** A per-provider circuit breaker turns "out of credit" into a recoverable pause instead of a terminal `FAILED`, so topping up a wallet is sufficient to resume with no admin action. Separately, every remaining `ensureQuestionsCached` call moves behind `after()` so no request handler waits on a draw, and the draw itself has its ~200 serial database round trips batched down to ~5 with image mirroring moved to its own pass.

**Tech Stack:** TypeScript, Next.js, Prisma + PostgreSQL (Supabase transaction pooler), `node --test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-09-07-multi-provider-ingestion-design.md`

## Global Constraints

- **SDash is the only provider.** ALOC was declined on 2026-09-07 after costing and probing; spec Decisions 5–10 (provider generalisation, the ALOC adapter, backfill operations) are abandoned, not deferred. There is no follow-on plan. This plan implements spec Decisions 1, 2, 3 and 4, which are all SDash-only and all still live.
- **`QuestionProvider` stays single-valued.** Do not add members to the enum. The `provider` column and the `(provider, cacheKey)` uniqueness already in the schema stay as they are — they cost nothing and removing them would be a pointless migration.
- **`Question.explanation` stays required.** The invariant at `src/lib/question-provider/mapper.ts:118` is not touched.
- **Tests are `node --test`.** New test files live at `scripts/test-*.mts`, import from `../src/...`, use `node:test` + `node:assert/strict`, and must be appended to the `test` script in `package.json`. No new test framework.
- **Existing test suite must stay green.** `npm test` passes at every commit.
- **Migrations are applied by hand.** `DIRECT_URL` cannot be reached from this machine, so `prisma migrate deploy` will fail. Every migration task below states the exact manual procedure; follow it rather than improvising.
- **Migration files must be written with LF endings.** `core.autocrlf=true` on this repo silently drifts Prisma migration checksums if a migration file is ever rewritten with CRLF.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `src/lib/question-provider/state.ts` | Circuit-breaker policy: pure decision functions plus the narrow DB slice that reads and writes `ProviderState`. |
| `prisma/migrations/<ts>_provider_state/migration.sql` | `ProviderState` table + `ProviderCircuitState` enum. |
| `prisma/migrations/<ts>_reset_credit_failures/migration.sql` | One-off repair of rows wrongly marked `FAILED`. |
| `scripts/test-provider-state.mts` | Tests for the breaker's pure policy. |
| `scripts/test-provider-ingest-batching.mts` | Tests for batched dedupe and staging. |

**Modified**

| File | Change |
|---|---|
| `src/lib/question-provider/errors.ts` | `classifyStatus` becomes body-aware and gains the `exhausted` class. |
| `src/lib/question-provider/types.ts` | `ProviderFailureKind` gains `"exhausted"`. |
| `src/lib/question-provider/sdash.ts` | Parse the error body before classifying it. |
| `src/lib/question-provider/ingest.ts` | Breaker checks, batched writes, images out of the draw loop. |
| `src/lib/assessment-generation.ts:107-139` | Stop awaiting a draw on the request path. |
| `src/lib/jamb-cbt-preparation.ts:80-110` | Same, for the JAMB CBT prepare flow. |
| `prisma/schema.prisma` | `ProviderState` model, `ProviderCircuitState` enum. |
| `package.json` | Register the two new test files. |
| `scripts/test-provider-errors.mts` | Cases for the new `exhausted` class. |

---

# Phase 0 — Stop the bleeding

## Task 1: Classify "out of credit" separately from "not entitled"

Today `classifyStatus(403)` returns `terminal` for both a revoked key and an empty wallet, and `drawOnce` writes `FAILED` for terminal failures. That is why an unfunded wallet is permanently retiring papers. The two cases are told apart by the response body, not the status code.

**Files:**
- Modify: `src/lib/question-provider/errors.ts`
- Modify: `src/lib/question-provider/types.ts:17`
- Test: `scripts/test-provider-errors.mts`

**Interfaces:**
- Consumes: nothing.
- Produces: `ResponseClass` widened to `"ok" | "empty" | "terminal" | "retryable" | "exhausted"`; `classifyStatus(httpStatus: number, body?: { message?: string } | null): ResponseClass`; `ProviderFailureKind` widened to `"terminal" | "retryable" | "exhausted"`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-provider-errors.mts`:

```typescript
test("403 with an insufficient-credit body is exhausted, not terminal", () => {
  // Measured 2026-09-07 against sdashapi:
  // {"status":403,"message":"Insufficient credit. Please top up your wallet."}
  // Terminal here is what permanently retires a paper over a billing lapse.
  assert.equal(
    classifyStatus(403, { message: "Insufficient credit. Please top up your wallet." }),
    "exhausted",
  );
});

test("403 with an entitlement body stays terminal", () => {
  assert.equal(
    classifyStatus(403, { message: 'You have no permission to query the "post-utme" exam.' }),
    "terminal",
  );
});

test("403 with no body stays terminal", () => {
  // Unreadable body: assume the permanent cause. An exhausted misread would
  // retry a revoked key every 15 minutes forever.
  assert.equal(classifyStatus(403, null), "terminal");
  assert.equal(classifyStatus(403), "terminal");
});

test("402 Payment Required is exhausted without needing a body", () => {
  assert.equal(classifyStatus(402), "exhausted");
});

test("401 stays terminal even if the body mentions credit", () => {
  // A bad token is a bad token; the wording must not override the status.
  assert.equal(classifyStatus(401, { message: "Insufficient credit." }), "terminal");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-errors.mts`
Expected: FAIL — `classifyStatus` currently returns `"terminal"` for the credit body and `"retryable"` for 402.

- [ ] **Step 3: Implement**

Replace the body of `src/lib/question-provider/errors.ts` below the doc comment:

```typescript
export type ResponseClass = "ok" | "empty" | "terminal" | "retryable" | "exhausted";

/**
 * Wallet-empty wording, as sent by the providers we call.
 *
 * Matched against the body because the status code alone cannot separate
 * "this account is out of money" — which reverses the moment someone tops
 * up — from "this key may never have this resource", which does not.
 */
const OUT_OF_CREDIT =
  /insufficient\s+credit|top\s*[- ]?up|out\s+of\s+credits?|credits?\s+exhausted|quota\s+exceeded/i;

export function classifyStatus(
  httpStatus: number,
  body?: { message?: string } | null,
): ResponseClass {
  if (httpStatus === 200) return "ok";

  // The filter is genuinely empty. The ledger saturates it with rawCount 0 so
  // we never ask again — the catalogue contains combinations with nothing in
  // them, and retrying those forever is the runaway this design prevents.
  if (httpStatus === 404) return "empty";

  // Unambiguous by status alone.
  if (httpStatus === 402) return "exhausted";

  // A bad token is permanent regardless of what the body says.
  if (httpStatus === 401) return "terminal";

  // The fork this function exists for. Absent or unreadable body: assume the
  // permanent cause, because a wrong "exhausted" retries a revoked key every
  // cooldown forever, while a wrong "terminal" is repaired by one admin reset.
  if (httpStatus === 403) {
    return body?.message && OUT_OF_CREDIT.test(body.message) ? "exhausted" : "terminal";
  }

  // Everything else — throttling, server faults, anything unrecognised. Erring
  // toward "retryable" costs one call; erring toward "empty" would brand a
  // real paper as permanently barren.
  return "retryable";
}
```

In `src/lib/question-provider/types.ts`, widen the failure kind:

```typescript
export type ProviderFailureKind = "terminal" | "retryable" | "exhausted";
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-errors.mts`
Expected: PASS, including the seven pre-existing cases — the no-body 403 and 401 assertions must still return `terminal`.

- [ ] **Step 5: Commit**

```bash
git add src/lib/question-provider/errors.ts src/lib/question-provider/types.ts scripts/test-provider-errors.mts
git commit -m "fix(provider): tell an empty wallet apart from a revoked key"
```

---

## Task 2: Parse the error body before classifying it

`sdash.ts` calls `classifyStatus(res.status)` and only reads the body afterwards, so Task 1's body-aware branch would never see anything. Reorder.

**Files:**
- Modify: `src/lib/question-provider/sdash.ts:24-56`
- Test: `scripts/test-provider-sdash.mts`

**Interfaces:**
- Consumes: `classifyStatus(httpStatus, body?)` from Task 1.
- Produces: `ProviderError` instances whose `kind` is `"exhausted"` when the provider reports an empty wallet.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test-provider-sdash.mts`:

```typescript
test("an insufficient-credit 403 surfaces as an exhausted ProviderError", async () => {
  const adapter = createSdashAdapter({
    baseUrl: "https://example.test/api",
    token: "t",
    fetchImpl: async () =>
      new Response(
        JSON.stringify({ status: 403, message: "Insufficient credit. Please top up your wallet." }),
        { status: 403, headers: { "content-type": "application/json" } },
      ),
  });

  await assert.rejects(
    () => adapter.draw({ subjectSlug: "physics", examType: "JAMB", examYear: 2020 }, 50),
    (error: unknown) => {
      assert.ok(error instanceof ProviderError);
      assert.equal(error.kind, "exhausted");
      assert.equal(error.httpStatus, 403);
      return true;
    },
  );
});

test("an entitlement 403 still surfaces as terminal", async () => {
  const adapter = createSdashAdapter({
    baseUrl: "https://example.test/api",
    token: "t",
    fetchImpl: async () =>
      new Response(
        JSON.stringify({ status: 403, message: 'You have no permission to query the "post-utme" exam.' }),
        { status: 403, headers: { "content-type": "application/json" } },
      ),
  });

  await assert.rejects(
    () => adapter.draw({ subjectSlug: "physics", examType: "JAMB", examYear: 2020 }, 50),
    (error: unknown) => {
      assert.ok(error instanceof ProviderError);
      assert.equal(error.kind, "terminal");
      return true;
    },
  );
});
```

Ensure the file imports `ProviderError` from `../src/lib/question-provider/types`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-sdash.mts`
Expected: FAIL — the first test reports `kind` is `"terminal"`, because the body is not passed to `classifyStatus`.

- [ ] **Step 3: Implement**

In `src/lib/question-provider/sdash.ts`, replace the block from `const kind = classifyStatus(res.status);` through the second body read with:

```typescript
    // 404 is "nothing here", and has no body worth reading.
    if (res.status === 404) return null;

    // Read the body before classifying: a 403 means one of two opposite
    // things, and only the message tells them apart.
    if (res.status !== 200) {
      const body = (await res.json().catch(() => null)) as Envelope | null;
      throw new ProviderError(
        body?.message ?? `Provider returned ${res.status}`,
        classifyStatus(res.status, body),
        res.status,
      );
    }

    const body = (await res.json().catch(() => null)) as Envelope | null;
    if (!body || body.data === undefined) {
      throw new ProviderError("Provider returned an unreadable body", "retryable", res.status);
    }
    return body.data;
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-sdash.mts`
Expected: PASS, with all pre-existing cases in the file still green.

- [ ] **Step 5: Commit**

```bash
git add src/lib/question-provider/sdash.ts scripts/test-provider-sdash.mts
git commit -m "fix(provider): read the error body before classifying it"
```

---

## Task 3: Add the `ProviderState` table

**Files:**
- Modify: `prisma/schema.prisma`
- Create: `prisma/migrations/<timestamp>_provider_state/migration.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: Prisma model `ProviderState` keyed by `provider`, and enum `ProviderCircuitState` with members `OK`, `EXHAUSTED`, `BLOCKED`.

- [ ] **Step 1: Add the model to the schema**

Append to `prisma/schema.prisma`, next to the other provider models:

```prisma
enum ProviderCircuitState {
  OK
  EXHAUSTED // out of credit — retry after the cooldown, no human needed
  BLOCKED   // 401, revoked key, unentitled — a human must intervene
}

/// One row per provider: the circuit breaker, and what the provider last told
/// us about our balance. Keyed by provider so a breaker opened for one source
/// never stops another.
model ProviderState {
  provider      QuestionProvider     @id
  state         ProviderCircuitState @default(OK)
  cooldownUntil DateTime?
  lastError     String?              @db.Text
  lastCheckedAt DateTime             @updatedAt

  /// As last reported by the provider's own response envelope.
  creditsRemaining Int?
}
```

- [ ] **Step 2: Generate the migration SQL without applying it**

Run: `npx prisma migrate diff --from-schema-datasource prisma/schema.prisma --to-schema-datamodel prisma/schema.prisma --script`

If that cannot reach the database (expected on this machine), write the file by hand instead. Create `prisma/migrations/20260907000001_provider_state/migration.sql` **with LF line endings**:

```sql
CREATE TYPE "ProviderCircuitState" AS ENUM ('OK', 'EXHAUSTED', 'BLOCKED');

CREATE TABLE "ProviderState" (
    "provider" "QuestionProvider" NOT NULL,
    "state" "ProviderCircuitState" NOT NULL DEFAULT 'OK',
    "cooldownUntil" TIMESTAMP(3),
    "lastError" TEXT,
    "lastCheckedAt" TIMESTAMP(3) NOT NULL,
    "creditsRemaining" INTEGER,

    CONSTRAINT "ProviderState_pkey" PRIMARY KEY ("provider")
);
```

Confirm LF: `file prisma/migrations/20260907000001_provider_state/migration.sql` must not say "CRLF".

- [ ] **Step 3: Apply the migration by hand**

`prisma migrate deploy` cannot reach `DIRECT_URL` from this machine. Instead:

1. Open the Supabase SQL Editor for project `zzddhipniqovuyjvsiub`.
2. Paste the migration SQL and run it.
3. **Do not trust the success message** — the editor can report success on a half-applied batch. Verify against the catalog:

```sql
SELECT to_regclass('public."ProviderState"');            -- expect: ProviderState
SELECT unnest(enum_range(NULL::"ProviderCircuitState")); -- expect: OK, EXHAUSTED, BLOCKED
```

4. Record the migration as applied so Prisma does not try to re-run it:

```sql
INSERT INTO "_prisma_migrations"
  (id, checksum, migration_name, started_at, finished_at, applied_steps_count)
VALUES
  (gen_random_uuid()::text, '', '20260907000001_provider_state', now(), now(), 1);
```

- [ ] **Step 4: Regenerate the Prisma client and typecheck**

Stop any running dev server first — it holds a lock on the query engine DLL and `prisma generate` fails with `EPERM`, after which a stale client shows up as unrelated-looking `tsc` errors.

Run: `npx prisma generate && npx tsc --noEmit`
Expected: both succeed; `ProviderState` and `ProviderCircuitState` are exported from `@prisma/client`.

- [ ] **Step 5: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/20260907000001_provider_state
git commit -m "feat(provider): add the ProviderState circuit-breaker table"
```

---

## Task 4: The circuit-breaker policy

Pure decision functions first, so the cooldown arithmetic is testable without a database.

**Files:**
- Create: `src/lib/question-provider/state.ts`
- Create: `scripts/test-provider-state.mts`
- Modify: `package.json:12`

**Interfaces:**
- Consumes: `ProviderFailureKind` from Task 1.
- Produces:
  - `EXHAUSTED_COOLDOWN_MS: number`
  - `type CircuitRow = { state: "OK" | "EXHAUSTED" | "BLOCKED"; cooldownUntil: Date | null }`
  - `isCircuitOpen(row: CircuitRow | null, now: number): boolean`
  - `nextCircuit(kind: ProviderFailureKind, now: number): { state: CircuitRow["state"]; cooldownUntil: Date | null } | null`

- [ ] **Step 1: Write the failing tests**

Create `scripts/test-provider-state.mts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EXHAUSTED_COOLDOWN_MS,
  isCircuitOpen,
  nextCircuit,
} from "../src/lib/question-provider/state";

const NOW = Date.UTC(2026, 8, 7, 12, 0, 0);

test("a provider we have never failed against is closed", () => {
  assert.equal(isCircuitOpen(null, NOW), false);
});

test("an OK row is closed", () => {
  assert.equal(isCircuitOpen({ state: "OK", cooldownUntil: null }, NOW), false);
});

test("EXHAUSTED is open until the cooldown expires", () => {
  const row = { state: "EXHAUSTED" as const, cooldownUntil: new Date(NOW + 60_000) };
  assert.equal(isCircuitOpen(row, NOW), true);
});

test("EXHAUSTED reopens for one probe once the cooldown expires", () => {
  // This is what makes topping up a wallet sufficient to resume: no admin
  // action, the next scheduled draw simply tries again.
  const row = { state: "EXHAUSTED" as const, cooldownUntil: new Date(NOW - 1) };
  assert.equal(isCircuitOpen(row, NOW), false);
});

test("EXHAUSTED with no cooldown recorded is closed rather than stuck", () => {
  // Fail safe: a half-written row must not wedge the provider forever.
  assert.equal(isCircuitOpen({ state: "EXHAUSTED", cooldownUntil: null }, NOW), false);
});

test("BLOCKED stays open regardless of any cooldown", () => {
  assert.equal(isCircuitOpen({ state: "BLOCKED", cooldownUntil: null }, NOW), true);
  assert.equal(
    isCircuitOpen({ state: "BLOCKED", cooldownUntil: new Date(NOW - 1) }, NOW),
    true,
  );
});

test("an exhausted failure arms the cooldown", () => {
  const next = nextCircuit("exhausted", NOW);
  assert.deepEqual(next, {
    state: "EXHAUSTED",
    cooldownUntil: new Date(NOW + EXHAUSTED_COOLDOWN_MS),
  });
});

test("a terminal failure blocks and needs a human", () => {
  assert.deepEqual(nextCircuit("terminal", NOW), { state: "BLOCKED", cooldownUntil: null });
});

test("a retryable failure does not touch the breaker", () => {
  // Throttling and 5xx are the ledger's business, not the breaker's.
  assert.equal(nextCircuit("retryable", NOW), null);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-state.mts`
Expected: FAIL — `src/lib/question-provider/state.ts` does not exist.

- [ ] **Step 3: Implement**

Create `src/lib/question-provider/state.ts`:

```typescript
import type { ProviderFailureKind } from "./types";

/**
 * How long an out-of-credit provider is left alone before one probe.
 *
 * Short enough that topping up a wallet takes effect while someone is still
 * watching, long enough that a day of traffic against an unfunded account
 * costs ~96 calls rather than one per request.
 */
export const EXHAUSTED_COOLDOWN_MS = 15 * 60 * 1000;

export type CircuitRow = {
  state: "OK" | "EXHAUSTED" | "BLOCKED";
  cooldownUntil: Date | null;
};

/** May we spend a request on this provider right now? */
export function isCircuitOpen(row: CircuitRow | null, now: number): boolean {
  if (!row) return false;

  // A revoked key or an unentitled plan cannot fix itself; probing it is how
  // a key gets banned rather than restored.
  if (row.state === "BLOCKED") return true;

  if (row.state === "EXHAUSTED") {
    // No cooldown recorded means a half-written row. Fail closed-circuit
    // (i.e. allow the call) rather than wedging the provider indefinitely.
    return row.cooldownUntil !== null && row.cooldownUntil.getTime() > now;
  }

  return false;
}

/**
 * The breaker transition a failure implies, or null to leave it alone.
 *
 * Retryable failures — throttling, 5xx, a dropped connection — are the
 * ledger's concern: they leave the fetch PENDING and cost one call next time.
 * Only the two states that should stop us calling at all move the breaker.
 */
export function nextCircuit(
  kind: ProviderFailureKind,
  now: number,
): { state: CircuitRow["state"]; cooldownUntil: Date | null } | null {
  if (kind === "exhausted") {
    return { state: "EXHAUSTED", cooldownUntil: new Date(now + EXHAUSTED_COOLDOWN_MS) };
  }
  if (kind === "terminal") {
    return { state: "BLOCKED", cooldownUntil: null };
  }
  return null;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-state.mts`
Expected: PASS (9 tests).

- [ ] **Step 5: Register the test file**

In `package.json`, append ` scripts/test-provider-state.mts` to the end of the `test` script value.

Run: `npm test`
Expected: the whole suite passes, including the new file.

- [ ] **Step 6: Commit**

```bash
git add src/lib/question-provider/state.ts scripts/test-provider-state.mts package.json
git commit -m "feat(provider): add circuit-breaker policy for provider failures"
```

---

## Task 5: Wire the breaker into the draw path

**Files:**
- Modify: `src/lib/question-provider/ingest.ts` (`IngestDb`, `IngestDeps`, `drawOnce`, `ensureQuestionsCached`, `saturate`)
- Test: `scripts/test-provider-ingest.mts`

**Interfaces:**
- Consumes: `isCircuitOpen`, `nextCircuit`, `EXHAUSTED_COOLDOWN_MS` from Task 4.
- Produces:
  - `IngestDb` gains `providerState: { findUnique(args: { where: { provider: "SDASH" } }): Promise<CircuitRow | null>; upsert(args: { where: { provider: "SDASH" }; create: {...}; update: {...} }): Promise<unknown> }`
  - `IngestDeps` gains `now: () => number` (default `Date.now`).

**Deliberate simplification against the spec:** the spec proposed caching the breaker read in process for ~30s. Task 6 removes draws from the request path entirely, so the read only happens on background draws — one indexed lookup on a one-row-per-provider table. The cache is not worth the staleness, and is omitted. Revisit only if a draw path ever returns to a request handler.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-provider-ingest.mts`:

```typescript
test("an exhausted draw leaves the fetch PENDING and arms the breaker", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const deps: IngestDeps = {
    db,
    now: () => Date.UTC(2026, 8, 7, 12, 0, 0),
    getAdapter: () => ({
      name: "SDASH",
      async draw() {
        throw new ProviderError("Insufficient credit. Please top up your wallet.", "exhausted", 403);
      },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  };

  await ensureQuestionsCached(FILTER, 40, deps);

  // FAILED here is the bug this whole task exists to prevent: it would retire
  // the paper permanently over a billing lapse.
  assert.equal(db._fetchRow()?.status, "PENDING");
  const circuit = await db.providerState.findUnique({ where: { provider: "SDASH" } });
  assert.equal(circuit?.state, "EXHAUSTED");
  assert.equal(
    circuit?.cooldownUntil?.getTime(),
    Date.UTC(2026, 8, 7, 12, 0, 0) + EXHAUSTED_COOLDOWN_MS,
  );
});

test("no provider call is made while the breaker is open", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const now = Date.UTC(2026, 8, 7, 12, 0, 0);
  await db.providerState.upsert({
    where: { provider: "SDASH" },
    create: { provider: "SDASH", state: "EXHAUSTED", cooldownUntil: new Date(now + 60_000) },
    update: { state: "EXHAUSTED", cooldownUntil: new Date(now + 60_000) },
  });

  let calls = 0;
  const deps: IngestDeps = {
    db,
    now: () => now,
    getAdapter: () => ({
      name: "SDASH",
      async draw() { calls += 1; return []; },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  };

  await ensureQuestionsCached(FILTER, 40, deps);
  assert.equal(calls, 0);
});

test("once the cooldown expires exactly one probe is spent", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const now = Date.UTC(2026, 8, 7, 12, 0, 0);
  await db.providerState.upsert({
    where: { provider: "SDASH" },
    create: { provider: "SDASH", state: "EXHAUSTED", cooldownUntil: new Date(now - 1) },
    update: { state: "EXHAUSTED", cooldownUntil: new Date(now - 1) },
  });

  let calls = 0;
  const deps: IngestDeps = {
    db,
    now: () => now,
    getAdapter: () => ({
      name: "SDASH",
      async draw() { calls += 1; return [validPayload(1)]; },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  };

  await ensureQuestionsCached(FILTER, 40, deps);
  assert.equal(calls, 1);
  // A successful probe closes the breaker — this is the auto-recovery.
  const circuit = await db.providerState.findUnique({ where: { provider: "SDASH" } });
  assert.equal(circuit?.state, "OK");
});

test("a terminal failure blocks the provider and still marks the fetch FAILED", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const deps: IngestDeps = {
    db,
    now: () => Date.UTC(2026, 8, 7, 12, 0, 0),
    getAdapter: () => ({
      name: "SDASH",
      async draw() { throw new ProviderError("Invalid AccessToken.", "terminal", 401); },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  };

  await ensureQuestionsCached(FILTER, 40, deps);
  assert.equal(db._fetchRow()?.status, "FAILED");
  const circuit = await db.providerState.findUnique({ where: { provider: "SDASH" } });
  assert.equal(circuit?.state, "BLOCKED");
});
```

Add to the file's imports:

```typescript
import { EXHAUSTED_COOLDOWN_MS } from "../src/lib/question-provider/state";
```

- [ ] **Step 2: Extend the fake database**

In `makeFakeDb`, add alongside the other delegates:

```typescript
  let circuit: {
    provider: "SDASH";
    state: "OK" | "EXHAUSTED" | "BLOCKED";
    cooldownUntil: Date | null;
    lastError: string | null;
    creditsRemaining: number | null;
  } | null = null;
```

and, inside the returned `db` object:

```typescript
    providerState: {
      async findUnique({ where }) {
        return circuit && circuit.provider === where.provider ? { ...circuit } : null;
      },
      async upsert({ where, create, update }) {
        circuit = circuit
          ? { ...circuit, ...update }
          : { lastError: null, creditsRemaining: null, ...create, provider: where.provider };
        return { ...circuit };
      },
    },
```

Extend the `db` variable's type annotation with the matching `providerState` slice from the Interfaces block above.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-ingest.mts`
Expected: FAIL — `IngestDeps` has no `now`, and nothing reads or writes the breaker.

- [ ] **Step 4: Implement**

In `src/lib/question-provider/ingest.ts`:

Add the import and extend the injected types:

```typescript
import { isCircuitOpen, nextCircuit, type CircuitRow } from "./state";
```

Add to `IngestDb`:

```typescript
  providerState: {
    findUnique(args: {
      where: { provider: "SDASH" };
    }): Promise<(CircuitRow & { creditsRemaining: number | null }) | null>;
    upsert(args: {
      where: { provider: "SDASH" };
      create: {
        provider: "SDASH";
        state: CircuitRow["state"];
        cooldownUntil: Date | null;
        lastError?: string | null;
      };
      update: {
        state: CircuitRow["state"];
        cooldownUntil: Date | null;
        lastError?: string | null;
      };
    }): Promise<unknown>;
  };
```

Extend `IngestDeps` and its default:

```typescript
export type IngestDeps = {
  db: IngestDb;
  getAdapter: () => QuestionProviderAdapter;
  /** Injected so cooldown arithmetic is testable without waiting. */
  now: () => number;
};

const defaultDeps: IngestDeps = {
  db: realDb as unknown as IngestDb,
  getAdapter: getSdashAdapter,
  now: () => Date.now(),
};
```

Add two helpers above `drawOnce`:

```typescript
/** True when we must not spend a request on this provider right now. */
async function circuitIsOpen(deps: IngestDeps): Promise<boolean> {
  const row = await deps.db.providerState.findUnique({ where: { provider: PROVIDER } });
  return isCircuitOpen(row, deps.now());
}

async function recordCircuit(
  deps: IngestDeps,
  kind: ProviderFailureKind | "ok",
  message: string | null,
) {
  const next =
    kind === "ok"
      ? { state: "OK" as const, cooldownUntil: null }
      : nextCircuit(kind, deps.now());

  // Retryable failures leave the breaker untouched.
  if (!next) return;

  await deps.db.providerState.upsert({
    where: { provider: PROVIDER },
    create: { provider: PROVIDER, ...next, lastError: message },
    update: { ...next, lastError: message },
  });
}
```

Import `ProviderFailureKind` from `./types` alongside `ProviderError`.

In `drawOnce`, replace the try/catch around the adapter call:

```typescript
  let payloads: unknown[];
  try {
    // Inside the try: getAdapter() throws terminally when the access token is
    // unset, and outside it that escaped all the way to the route's catch-all,
    // 500ing every past-paper quiz instead of marking the filter FAILED and
    // falling back to a database-only one.
    payloads = await deps.getAdapter().draw(filter, DRAW_LIMIT);
  } catch (error) {
    const kind = error instanceof ProviderError ? error.kind : "retryable";
    const message = error instanceof Error ? error.message : String(error);
    await recordCircuit(deps, kind, message);
    await db.providerFetch.update({
      where: { id: fetchId },
      data: {
        // Only a genuinely permanent cause is final. An empty wallet leaves
        // the filter PENDING so that topping up is all the recovery needed.
        status: kind === "terminal" ? "FAILED" : "PENDING",
        error: message,
        completedAt: kind === "terminal" ? new Date() : null,
      },
    });
    return;
  }

  // A draw that returned is proof the provider is answering again.
  await recordCircuit(deps, "ok", null);
```

In `ensureQuestionsCached`, guard immediately before the `drawOnce` call at the end of the function:

```typescript
  if (await circuitIsOpen(deps)) {
    return {
      questions: await readFromDb(db, subject.id, filter, limit),
      source: "db" as const,
      ledger: {
        status: ledger.status,
        rawCount: ledger.rawCount,
        promotedCount: ledger.promotedCount,
      },
    };
  }

  await drawOnce(ledger.id, subject.id, filter, deps);
```

In `saturate`, add the same guard as the first statement inside the `for` loop, before the ledger read:

```typescript
    if (await circuitIsOpen(deps)) return;
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-ingest.mts`
Expected: PASS, including every pre-existing case in the file.

- [ ] **Step 6: Run the full suite and typecheck**

Run: `npm test && npx tsc --noEmit`
Expected: both pass. Other callers construct `IngestDeps` without `now`; if `tsc` flags them, they are passing partial deps and should use the default by omitting the argument entirely.

- [ ] **Step 7: Commit**

```bash
git add src/lib/question-provider/ingest.ts scripts/test-provider-ingest.mts
git commit -m "feat(provider): pause on an empty wallet instead of failing the paper"
```

---

## Task 6: Repair the papers already blackholed

**Files:**
- Create: `prisma/migrations/<timestamp>_reset_credit_failures/migration.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: no code. A data repair.

- [ ] **Step 1: Count the damage before changing anything**

In the Supabase SQL Editor:

```sql
SELECT status, count(*)
FROM "ProviderFetch"
WHERE "error" ILIKE '%insufficient credit%'
GROUP BY status;
```

Record the number. If it is zero, still create the migration — it is idempotent and belongs in history.

- [ ] **Step 2: Write the migration**

Create `prisma/migrations/20260907000002_reset_credit_failures/migration.sql` **with LF line endings**:

```sql
-- Papers retired by an empty wallet rather than a permanent cause.
--
-- classifyStatus used to fold "Insufficient credit" into the same terminal
-- class as a revoked key, so drawOnce wrote FAILED and both ensureQuestionsCached
-- and saturate then refused to look at the filter again.
--
-- drawCount is deliberately preserved, exactly as resetFailedFetch does: a
-- filter that failed on its ninth draw must not receive twelve fresh ones.
-- startedAt is backdated past the lease window so the next caller draws
-- immediately rather than mistaking the reset for an in-flight claim.
UPDATE "ProviderFetch"
SET
  "status"      = 'PENDING',
  "error"       = NULL,
  "completedAt" = NULL,
  "startedAt"   = now() - interval '5 minutes'
WHERE "status" = 'FAILED'
  AND "error" ILIKE '%insufficient credit%';
```

- [ ] **Step 3: Apply it by hand and verify against the catalog**

Run the SQL in the Supabase editor, then verify — the editor's success message is not sufficient evidence:

```sql
SELECT count(*) FROM "ProviderFetch"
WHERE "status" = 'FAILED' AND "error" ILIKE '%insufficient credit%';
-- expect: 0

SELECT count(*) FROM "ProviderFetch" WHERE "status" = 'PENDING';
-- expect: at least the number recorded in Step 1
```

Then record it as applied:

```sql
INSERT INTO "_prisma_migrations"
  (id, checksum, migration_name, started_at, finished_at, applied_steps_count)
VALUES
  (gen_random_uuid()::text, '', '20260907000002_reset_credit_failures', now(), now(), 1);
```

- [ ] **Step 4: Commit**

```bash
git add prisma/migrations/20260907000002_reset_credit_failures
git commit -m "fix(provider): un-retire papers failed by an empty wallet"
```

---

# Phase 1 — Latency

## Task 7: Take the draw off the request path

**Files:**
- Modify: `src/lib/assessment-generation.ts:107-139`
- Modify: `src/lib/jamb-cbt-preparation.ts:80-110`

**Interfaces:**
- Consumes: `ensureQuestionsCached`, `saturate` from Task 5, both unchanged.
- Produces: no new exports. Both call sites stop awaiting a provider call.

- [ ] **Step 1: Change the past-paper generator**

In `src/lib/assessment-generation.ts`, replace the two lines inside `if (outbound.ok) {`:

```typescript
      if (outbound.ok) {
        // Warm the paper behind the response. Nothing here is awaited: a draw
        // is a provider round trip plus a 50-payload write loop, and the
        // student who triggers it gains nothing from waiting — the questions
        // it fetches are not in the bank until it finishes, by which point
        // their quiz has already been built from what was there.
        after(async () => {
          await ensureQuestionsCached(filter, count);
          await saturate(filter);
        });
      }
```

- [ ] **Step 2: Change the JAMB CBT prepare flow**

In `src/lib/jamb-cbt-preparation.ts`, replace the `try` block:

```typescript
      // Same reasoning as the past-paper generator: the prepare call reports
      // what the bank holds now, and schedules the fill behind the response.
      // Awaiting four subjects' draws here made "pick a year" a multi-second
      // wait against a five-connection pool.
      after(async () => {
        try {
          await ensureQuestionsCached(filter, questionsForSubject(subject.code));
          await saturate(filter);
        } catch (error) {
          // One unreachable paper must not sink the other three.
          console.error(
            `JAMB ${examYear} ${subject.code}: provider fetch failed`,
            error,
          );
        }
      });
```

- [ ] **Step 3: Verify no request path awaits a draw**

Run: `grep -rn "await ensureQuestionsCached" src/`
Expected: matches only inside `after(...)` callbacks, and in `src/app/admin/api/provider/backfill/route.ts` where an inline await is the intended behaviour.

- [ ] **Step 4: Run the suite and typecheck**

Run: `npm test && npx tsc --noEmit`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/lib/assessment-generation.ts src/lib/jamb-cbt-preparation.ts
git commit -m "perf(provider): stop student requests waiting on a provider draw"
```

---

## Task 8: Move image mirroring out of the draw loop

`uploadRemoteImage` runs inside the per-payload loop, so a Cloudinary hiccup aborts the rest of the draw and every image adds 1–3s. Stage image-bearing questions instead and let the existing `MAPPER_VERSION` sweep promote them.

**Files:**
- Modify: `src/lib/question-provider/ingest.ts` (`drawOnce`)
- Test: `scripts/test-provider-ingest.mts`

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change. `drawOnce` no longer imports `uploadRemoteImage`.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test-provider-ingest.mts`:

```typescript
test("an image-bearing question stages for the mirror pass and is not promoted inline", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const deps: IngestDeps = {
    db,
    now: () => Date.now(),
    getAdapter: () => ({
      name: "SDASH",
      async draw() {
        return [
          validPayload(1),
          validPayload(2, { image: "https://provider.test/diagram.png" }),
        ];
      },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  };

  await ensureQuestionsCached(FILTER, 40, deps);

  // The plain question promotes; the image one waits for the mirror pass.
  assert.equal(db._questions.length, 1);
  const staged = db._providerQuestions.find((row) => row.providerQuestionId === "2");
  assert.equal(staged?.status, "PENDING");
  assert.equal(db._fetchRow()?.promotedCount, 1);
  // Neither promoted nor rejected — it is pending work, not a failure.
  assert.equal(db._fetchRow()?.rejectedCount, 0);
});

test("a draw containing images does not call fetch during the loop", async () => {
  // The mirror is the slowest thing in ingest and must not sit between a
  // draw and its ledger write.
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = (async () => {
    fetchCalls += 1;
    return new Response("", { status: 200 });
  }) as typeof fetch;

  try {
    const db = makeFakeDb({ physics: "subj-1" });
    await ensureQuestionsCached(FILTER, 40, {
      db,
      now: () => Date.now(),
      getAdapter: () => ({
        name: "SDASH",
        async draw() { return [validPayload(1, { image: "https://provider.test/a.png" })]; },
        async listSubjects() { return []; },
        async listYears() { return []; },
      }),
    });
    assert.equal(fetchCalls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-ingest.mts`
Expected: FAIL — the image question is currently mirrored and promoted inline.

- [ ] **Step 3: Implement**

In `src/lib/question-provider/ingest.ts`, delete the `uploadRemoteImage` / `UploadRejectedError` import and replace the entire image block inside the payload loop with:

```typescript
      // Questions carrying a provider image are staged, not promoted.
      //
      // Mirroring is a download plus an upload — the slowest thing in ingest,
      // and unbounded in the tail. Running it here made every image add
      // seconds to the draw and let one Cloudinary hiccup abandon the rest of
      // the payloads. The mirror pass promotes these later from the stored
      // payload, at no further cost to the provider.
      if (result.question.providerImageUrl) {
        await db.providerQuestion.create({
          data: {
            fetchId,
            provider: PROVIDER,
            providerQuestionId: result.providerQuestionId,
            fingerprint: result.fingerprint,
            payload: payload as object,
            status: "PENDING",
            rejectionReasons: [
              {
                field: "questionImageUrl",
                message: "Awaiting the image mirror pass.",
              },
            ],
            mapperVersion: MAPPER_VERSION,
          },
        });
        newCount += 1;
        // Neither promoted nor rejected: it is pending work, not a failure.
        continue;
      }
```

In the promotion transaction, `questionImageUrl` is now always `null` — replace `questionImageUrl: imageUrl,` with `questionImageUrl: null,` and delete the now-unused `imageUrl` variable.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-ingest.mts`
Expected: PASS. Pre-existing tests asserting inline mirror behaviour will fail — those assertions are now wrong and should be updated to expect staging, not deleted.

- [ ] **Step 5: Run the suite and typecheck**

Run: `npm test && npx tsc --noEmit`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add src/lib/question-provider/ingest.ts scripts/test-provider-ingest.mts
git commit -m "perf(provider): stage image questions instead of mirroring inline"
```

---

## Task 9: Batch the dedupe and staging writes

With images gone, the loop is pure database work: one `findFirst` and one two-insert transaction per payload, ~200 serial round trips per draw. Collapse to a handful.

**Files:**
- Modify: `src/lib/question-provider/ingest.ts` (`IngestDb`, `drawOnce`)
- Create: `scripts/test-provider-ingest-batching.mts`
- Modify: `package.json:12`

**Interfaces:**
- Consumes: everything from Tasks 5, 7, 8.
- Produces: `IngestDb.providerQuestion` gains
  `findMany(args: { where: { fetchId: string }; select: { providerQuestionId: true; fingerprint: true } }): Promise<{ providerQuestionId: string | null; fingerprint: string }[]>`
  and `createMany(args: { data: object[] }): Promise<{ count: number }>`.

- [ ] **Step 1: Write the failing tests**

Create `scripts/test-provider-ingest-batching.mts`. Copy the imports, the
`FILTER` constant, `validPayload` and `makeFakeDb` verbatim from
`scripts/test-provider-ingest.mts` — the two files are independent and the
counters added in Step 2 must not perturb the existing suite — then append:

```typescript
test("one draw reads existing rows once, not once per payload", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const payloads = Array.from({ length: 20 }, (_, i) => validPayload(i + 1));

  await ensureQuestionsCached(FILTER, 40, {
    db,
    now: () => Date.now(),
    getAdapter: () => ({
      name: "SDASH",
      async draw() { return payloads; },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  });

  // The whole point of the task: round trips must not scale with payloads.
  assert.equal(db._findManyCalls(), 1);
  assert.equal(db._findFirstCalls(), 0);
  assert.equal(db._questions.length, 20);
});

test("batching still rejects a duplicate already staged in this fetch", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const duplicate = validPayload(7);

  await ensureQuestionsCached(FILTER, 40, {
    db,
    now: () => Date.now(),
    // The same question twice inside one draw, and again on the next.
    getAdapter: () => ({
      name: "SDASH",
      async draw() { return [duplicate, duplicate, validPayload(8)]; },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  });

  assert.equal(db._questions.length, 2);
  assert.equal(db._fetchRow()?.promotedCount, 2);
});

test("a duplicate by fingerprint under a different provider id is still caught", async () => {
  const db = makeFakeDb({ physics: "subj-1" });
  const original = validPayload(11);
  const reissued = { ...original, id: 999 }; // same text and options, new id

  await ensureQuestionsCached(FILTER, 40, {
    db,
    now: () => Date.now(),
    getAdapter: () => ({
      name: "SDASH",
      async draw() { return [original, reissued]; },
      async listSubjects() { return []; },
      async listYears() { return []; },
    }),
  });

  assert.equal(db._questions.length, 1);
});
```

- [ ] **Step 2: Extend the fake with counters and the new delegates**

In this file's `makeFakeDb`, add counters and expose them:

```typescript
  let findManyCalls = 0;
  let findFirstCalls = 0;
```

Add to the `providerQuestion` delegate:

```typescript
      async findMany({ where }) {
        findManyCalls += 1;
        return providerQuestions
          .filter((row) => row.fetchId === where.fetchId)
          .map((row) => ({
            providerQuestionId: row.providerQuestionId,
            fingerprint: row.fingerprint,
          }));
      },
      async createMany({ data }) {
        for (const row of data as ProviderQuestionRow[]) {
          providerQuestions.push({ ...row, id: `pq-${++pqSeq}` });
        }
        return { count: data.length };
      },
```

Increment `findFirstCalls` at the top of the existing `findFirst`, and expose both:

```typescript
    _findManyCalls: () => findManyCalls,
    _findFirstCalls: () => findFirstCalls,
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-ingest-batching.mts`
Expected: FAIL — `_findManyCalls()` is 0 and `_findFirstCalls()` is 20.

- [ ] **Step 4: Implement**

In `drawOnce`, before the payload loop:

```typescript
  // One read for the whole draw. Dedupe then happens in memory against this
  // set rather than costing a round trip per payload — the difference between
  // ~200 sequential queries and one, against a five-connection pool that
  // background ingest shares with live traffic.
  const existing = await db.providerQuestion.findMany({
    where: { fetchId },
    select: { providerQuestionId: true, fingerprint: true },
  });
  const seenIds = new Set(
    existing.map((row) => row.providerQuestionId).filter((id): id is string => id !== null),
  );
  const seenFingerprints = new Set(existing.map((row) => row.fingerprint));
```

Replace the per-payload `findFirst` dedupe with:

```typescript
      // Dedupe on their id first, then on the content fingerprint — but only
      // within this fetch. A draw redraws the same pool repeatedly, so we must
      // skip what this filter already holds; we must NOT skip a question
      // another paper happens to share, or the second paper to contain a
      // recycled question would silently go without it.
      if (
        (result.providerQuestionId && seenIds.has(result.providerQuestionId)) ||
        seenFingerprints.has(result.fingerprint)
      ) {
        continue;
      }
      // Claim it now, so a payload repeated inside this same draw is caught.
      if (result.providerQuestionId) seenIds.add(result.providerQuestionId);
      seenFingerprints.add(result.fingerprint);
```

Accumulate rejected and image-staged rows into a local array instead of writing each immediately:

```typescript
  const staged: object[] = [];
```

Replace each `await db.providerQuestion.create({ data: X })` in the rejection and image branches with `staged.push(X);`, then flush once after the loop, before the ledger update:

```typescript
  if (staged.length > 0) {
    await db.providerQuestion.createMany({ data: staged });
  }
```

Leave the promotion path as its own `$transaction` per promoted question: it writes a `Question` and a `ProviderQuestion` that must commit together, and correctness there outranks the remaining round trips.

Add the two new methods to the `IngestDb.providerQuestion` type per the Interfaces block.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-ingest-batching.mts`
Expected: PASS (3 tests in the file).

- [ ] **Step 6: Register the test file**

In `package.json`, append ` scripts/test-provider-ingest-batching.mts` to the end of the `test` script value.

- [ ] **Step 7: Run the full suite and typecheck**

Run: `npm test && npx tsc --noEmit`
Expected: both pass, and the new file appears in the run. `scripts/test-provider-ingest.mts` exercises the same paths through the older fake — if its `_throwOnFindFirstCall` hook no longer fires, repoint that test at `findMany`.

- [ ] **Step 8: Commit**

```bash
git add src/lib/question-provider/ingest.ts scripts/test-provider-ingest-batching.mts package.json
git commit -m "perf(provider): batch dedupe and staging writes per draw"
```

---

## Task 10: Verify the whole phase end to end

**Files:**
- Modify: none (verification only)

**Interfaces:**
- Consumes: Tasks 1–9.
- Produces: nothing.

- [ ] **Step 1: Confirm the breaker holds against the live 403**

With `SDASH_ACCESS_TOKEN` still unfunded, run the admin backfill route once for a single filter, then:

```sql
SELECT provider, state, "cooldownUntil", "lastError" FROM "ProviderState";
-- expect: SDASH | EXHAUSTED | ~15 minutes out | Insufficient credit...

SELECT status, count(*) FROM "ProviderFetch" GROUP BY status;
-- expect: no growth in FAILED
```

- [ ] **Step 2: Confirm no request path can draw**

Run: `grep -rn "ensureQuestionsCached\|saturate(" src/app src/lib --include=*.ts | grep -v "src/lib/question-provider/"`
Expected: every hit is inside an `after(...)` callback or the admin backfill route.

- [ ] **Step 3: Record the round-trip reduction**

Temporarily set `log: ["query"]` in `src/lib/db.ts`, run one draw through the admin backfill route against a funded or stubbed provider, and count the emitted queries.
Expected: roughly 5 per draw plus one transaction per promoted question, against ~200 before this phase. Revert the log change.

- [ ] **Step 4: Commit nothing; report**

Report the query count and the `ProviderState` row contents. If `FAILED` grew during Step 1, stop — Task 5's guard is not wired correctly.

---

## Self-review notes

**Spec coverage.** Decision 1 → Tasks 1–6. Decision 2 → Task 7. Decision 3 → Task 9. Decision 4 → Task 8. Decisions 5–10 are abandoned per Global Constraints — ALOC was declined, so there is nothing left of the spec that this plan does not cover.

**Dropped after review:** an earlier draft added a read-only `readBank` helper for request handlers, per the spec’s wording in Decision 2. Verified against the code: all three callers of `ensureQuestionsCached` discard its return value and select questions from the bank separately, so `readBank` would have been dead on arrival. Decision 2 is satisfied by Task 7 alone — moving the call behind `after()`.

**Deliberate divergences from the spec, both noted at their task:**
- Task 5 omits the spec’s in-process breaker cache — Task 7 removes draws from the request path, so the read is background-only and one query on a one-row table.
- Task 9 keeps promotion as a per-question transaction rather than batching it, because a `Question` and its `ProviderQuestion` must commit together; the spec's "one transaction per draw" would widen the blast radius of a mid-batch failure for a handful of round trips.

**Carried into the next plan:** `ProviderState.creditsSpentThisPeriod`, `periodStartedAt` and `creditCeiling` (spec Decision 9) are not in Task 3's migration — there is no second provider to spend against yet, and an unused column invites drift. They arrive with the ALOC adapter.
