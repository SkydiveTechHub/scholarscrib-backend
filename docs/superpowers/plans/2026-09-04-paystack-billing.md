# Paystack Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a student pay Paystack for a fixed-term subscription that grants a tier, and let an admin comp the same thing, with `User.tier` kept as a derived cache.

**Architecture:** A `Subscription` row is the source of truth; `User.tier` becomes a cache written from it. Every rule worth getting wrong — term stacking, expiry precedence, amount tampering, replayed webhooks — lives in a pure, database-free module under `src/lib/billing/` that unit tests without a database, matching how `src/lib/subscription.ts` is already built. Payment confirmation arrives twice (a browser callback for instant feedback, a signed webhook as the authority) and both funnel into one idempotent `applyChargeSuccess()`.

**Tech Stack:** Next.js 16.2.11 (App Router), React 19, Prisma 6 on Supabase Postgres, NextAuth v5 beta (JWT sessions), zod 4, `node:test` run through `tsx`.

**Spec:** `docs/superpowers/specs/2026-09-04-paystack-billing-design.md`

## Global Constraints

- **This is not the Next.js you know.** Per `AGENTS.md`, read the relevant guide in `node_modules/next/dist/docs/` before writing code against an unfamiliar API. Two findings already verified and relied on below: middleware is renamed **Proxy** and lives at `src/proxy.ts` (v16.0.0); Route Handlers take a plain Web `Request`, so `await req.text()` yields the raw body with no body-parser config.
- **Tier values:** `FREEMIUM | STANDARD | PREMIUM`. Never add, rename, or reorder them.
- **Prices, in kobo, exact:** STANDARD monthly `250_000` (₦2,500), STANDARD yearly `2_400_000` (₦24,000), PREMIUM monthly `500_000` (₦5,000), PREMIUM yearly `5_000_000` (₦50,000), FREEMIUM `0` both.
- **Display names, exact:** `FREEMIUM` → "Free", `STANDARD` → "Basic", `PREMIUM` → "Premium". These differ from the enum on purpose; the landing page already sells them under these names.
- **Currency is `"NGN"`.** Paystack amounts are always in kobo.
- **Pure modules must not import `@prisma/client` or `@/lib/db`.** That is what makes them testable. If a task tempts you to, the boundary is wrong.
- **Every new test file must be appended to the `test` script in `package.json`** in the same task that creates it, or it never runs in CI.
- **Test command shape:** `node --import tsx --test --test-force-exit scripts/<file>.mts`
- **All dates are UTC.** Use `Date.UTC` and `getUTC*` in term arithmetic; local-time helpers produce off-by-one-day bugs across the West Africa offset.
- **Commit convention:** commits end with the trailers
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_011BZ6wRL3D7TAiZTAdZy27e`.
  They are omitted from the step snippets below only to keep them readable.

---

## File Structure

**Created — pure (no database, no network):**

| File | Responsibility |
|---|---|
| `src/lib/billing/term.ts` | When a term starts and ends, including stacking |
| `src/lib/billing/entitlement.ts` | Which tier a set of rows grants right now |
| `src/lib/billing/signature.ts` | Paystack webhook HMAC |
| `src/lib/billing/settlement.ts` | What to do with a verified transaction |
| `src/lib/billing/reference.ts` | Transaction reference generation |

**Created — IO:**

| File | Responsibility |
|---|---|
| `src/lib/billing/paystack.ts` | HTTP client for Paystack; billing on/off switch |
| `src/lib/billing/subscription-data.ts` | Every Prisma read and write for billing |
| `src/app/api/billing/checkout/route.ts` | Start a payment |
| `src/app/api/billing/callback/route.ts` | Browser return, instant feedback |
| `src/app/api/billing/webhook/route.ts` | Paystack server-to-server, authoritative |
| `src/app/(dashboard)/settings/billing/page.tsx` | Current plan + buy buttons |
| `src/components/billing/plan-picker.tsx` | Client component that calls checkout |

**Modified:**

| File | Change |
|---|---|
| `src/lib/subscription.ts` | Add `BillingPeriod`, statuses, sources, the `PLANS` table |
| `prisma/schema.prisma` | `Subscription`, `PaystackEvent`, three enums, `User.subscriptions` |
| `src/lib/validators.ts` | `checkoutSchema`; extend `studentTierSchema` |
| `src/proxy.ts` | Exclude the webhook from the auth matcher |
| `src/lib/auth.ts` | Refresh the cached tier in the `jwt` callback |
| `src/lib/admin-student-data.ts` | `setStudentTier` becomes comp-and-revoke |
| `src/app/admin/api/students/[id]/tier/route.ts` | Pass duration and note |
| `src/components/admin/student-tier-control.tsx` | Duration select, note field, revoke warning |
| `src/components/landing/pricing.tsx` | Read prices from `PLANS` |

**Test files created:** `scripts/test-billing-term.mts`, `scripts/test-billing-entitlement.mts`, `scripts/test-billing-signature.mts`, `scripts/test-billing-settlement.mts`, plus additions to `scripts/test-subscription.mts`.

---

## Task 1: The plan catalogue and billing unions

Everything else imports its vocabulary from here. `subscription.ts` is deliberately database-free — the tier union is declared, not imported from `@prisma/client` — and these additions follow that rule so the Prisma enums added in Task 6 mirror them rather than the other way round.

**Files:**
- Modify: `src/lib/subscription.ts` (append; do not touch existing exports)
- Test: `scripts/test-subscription.mts` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `BILLING_PERIODS`, `type BillingPeriod`, `SUBSCRIPTION_STATUSES`, `type SubscriptionStatus`, `SUBSCRIPTION_SOURCES`, `type SubscriptionSource`, `TIER_DISPLAY_NAMES`, `type Plan = { tier, period, amountKobo, displayName }`, `planFor(tier, period): Plan`, `isPurchasableTier(tier): boolean`, `formatNaira(kobo): string`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-subscription.mts` (extend the existing import from `../src/lib/subscription` rather than adding a second one):

```ts
import {
  BILLING_PERIODS,
  planFor,
  isPurchasableTier,
  formatNaira,
  SUBSCRIPTION_STATUSES,
  SUBSCRIPTION_SOURCES,
} from "../src/lib/subscription";

test("the billing periods are monthly and yearly", () => {
  assert.deepEqual(BILLING_PERIODS, ["MONTHLY", "YEARLY"]);
});

test("every purchasable tier and period has a price", () => {
  for (const tier of ["STANDARD", "PREMIUM"] as const) {
    for (const period of BILLING_PERIODS) {
      assert.ok(planFor(tier, period).amountKobo > 0, `${tier} ${period}`);
    }
  }
});

test("prices match what the landing page sells", () => {
  assert.equal(planFor("STANDARD", "MONTHLY").amountKobo, 250_000);
  assert.equal(planFor("STANDARD", "YEARLY").amountKobo, 2_400_000);
  assert.equal(planFor("PREMIUM", "MONTHLY").amountKobo, 500_000);
  assert.equal(planFor("PREMIUM", "YEARLY").amountKobo, 5_000_000);
});

test("yearly is cheaper than twelve months", () => {
  // The landing page advertises "Save 20%" — if a repricing breaks that, the
  // claim on the marketing page becomes false.
  for (const tier of ["STANDARD", "PREMIUM"] as const) {
    const monthly = planFor(tier, "MONTHLY").amountKobo * 12;
    assert.ok(planFor(tier, "YEARLY").amountKobo < monthly, tier);
  }
});

test("freemium is free and not purchasable", () => {
  assert.equal(planFor("FREEMIUM", "MONTHLY").amountKobo, 0);
  assert.equal(isPurchasableTier("FREEMIUM"), false);
  assert.equal(isPurchasableTier("STANDARD"), true);
  assert.equal(isPurchasableTier("PREMIUM"), true);
});

test("the display names are the ones the landing page uses", () => {
  assert.equal(planFor("STANDARD", "MONTHLY").displayName, "Basic");
  assert.equal(planFor("PREMIUM", "MONTHLY").displayName, "Premium");
  assert.equal(planFor("FREEMIUM", "MONTHLY").displayName, "Free");
});

test("kobo renders as naira", () => {
  assert.equal(formatNaira(250_000), "₦2,500");
  assert.equal(formatNaira(0), "₦0");
});

test("the statuses and sources are the ones the schema will mirror", () => {
  assert.deepEqual(SUBSCRIPTION_STATUSES, [
    "PENDING",
    "ACTIVE",
    "FAILED",
    "ABANDONED",
    "REVOKED",
  ]);
  assert.deepEqual(SUBSCRIPTION_SOURCES, ["PAYSTACK", "COMP"]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --import tsx --test --test-force-exit scripts/test-subscription.mts`
Expected: FAIL — the new names are not exported from `subscription.ts`.

- [ ] **Step 3: Implement**

Append to `src/lib/subscription.ts`:

```ts
// ─── Billing ──────────────────────────────────────────────
//
// Declared here, not imported from `@prisma/client`, for the same reason the
// tier union is: it keeps every billing rule unit-testable without a database.
// The Prisma enums added alongside the Subscription model mirror these exactly.

export const BILLING_PERIODS = ["MONTHLY", "YEARLY"] as const;
export type BillingPeriod = (typeof BILLING_PERIODS)[number];

export const SUBSCRIPTION_STATUSES = [
  "PENDING",
  "ACTIVE",
  "FAILED",
  "ABANDONED",
  "REVOKED",
] as const;
export type SubscriptionStatus = (typeof SUBSCRIPTION_STATUSES)[number];

export const SUBSCRIPTION_SOURCES = ["PAYSTACK", "COMP"] as const;
export type SubscriptionSource = (typeof SUBSCRIPTION_SOURCES)[number];

/**
 * What the marketing site calls each tier. Deliberately different from
 * TIER_LABELS: the landing page sells "Basic", the admin console says
 * "Standard", and both are correct for their audience.
 */
export const TIER_DISPLAY_NAMES: Record<SubscriptionTier, string> = {
  FREEMIUM: "Free",
  STANDARD: "Basic",
  PREMIUM: "Premium",
};

export const PERIOD_LABELS: Record<BillingPeriod, string> = {
  MONTHLY: "Monthly",
  YEARLY: "Yearly",
};

/** Kobo, because that is the unit the Paystack API takes. */
const PLAN_PRICES_KOBO: Record<
  SubscriptionTier,
  Record<BillingPeriod, number>
> = {
  FREEMIUM: { MONTHLY: 0, YEARLY: 0 },
  STANDARD: { MONTHLY: 250_000, YEARLY: 2_400_000 },
  PREMIUM: { MONTHLY: 500_000, YEARLY: 5_000_000 },
};

export type Plan = {
  tier: SubscriptionTier;
  period: BillingPeriod;
  amountKobo: number;
  displayName: string;
};

export function planFor(tier: SubscriptionTier, period: BillingPeriod): Plan {
  return {
    tier,
    period,
    amountKobo: PLAN_PRICES_KOBO[tier][period],
    displayName: TIER_DISPLAY_NAMES[tier],
  };
}

/** FREEMIUM is the absence of a subscription, so it can never be bought. */
export function isPurchasableTier(tier: SubscriptionTier): boolean {
  return tier !== "FREEMIUM";
}

export function formatNaira(kobo: number): string {
  return `₦${Math.round(kobo / 100).toLocaleString("en-NG")}`;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-subscription.mts`
Expected: PASS, including the pre-existing tier tests.

- [ ] **Step 5: Commit**

```bash
git add src/lib/subscription.ts scripts/test-subscription.mts
git commit -m "feat(billing): add the plan catalogue and billing unions"
```

---

## Task 2: Term arithmetic and stacking

**Files:**
- Create: `src/lib/billing/term.ts`
- Create: `scripts/test-billing-term.mts`
- Modify: `package.json` (the `test` script)

**Interfaces:**
- Consumes: `BillingPeriod` from `@/lib/subscription`.
- Produces: `addMonthsUTC(date, months): Date`, `termStart(now, currentEndsAt): Date`, `termEnd(start, period): Date`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-billing-term.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { addMonthsUTC, termStart, termEnd } from "../src/lib/billing/term";

const iso = (d: Date) => d.toISOString();

test("a monthly term ends one month later", () => {
  const start = new Date("2026-09-04T10:00:00.000Z");
  assert.equal(iso(termEnd(start, "MONTHLY")), "2026-10-04T10:00:00.000Z");
});

test("a yearly term ends one year later", () => {
  const start = new Date("2026-09-04T10:00:00.000Z");
  assert.equal(iso(termEnd(start, "YEARLY")), "2027-09-04T10:00:00.000Z");
});

test("month-end dates clamp instead of overflowing", () => {
  // Jan 31 + 1 month must be Feb 28, not Mar 3. Naive setUTCMonth overflows.
  const jan31 = new Date("2027-01-31T00:00:00.000Z");
  assert.equal(iso(addMonthsUTC(jan31, 1)), "2027-02-28T00:00:00.000Z");
});

test("a leap day clamps on a non-leap year", () => {
  const leap = new Date("2028-02-29T00:00:00.000Z");
  assert.equal(iso(addMonthsUTC(leap, 12)), "2029-02-28T00:00:00.000Z");
});

test("a first purchase starts now", () => {
  const now = new Date("2026-09-04T10:00:00.000Z");
  assert.equal(iso(termStart(now, null)), iso(now));
});

test("a purchase while still subscribed stacks onto the remaining time", () => {
  // The whole point: paying twice must extend, never overwrite. A user who
  // renews early has not thrown away the time they already paid for.
  const now = new Date("2026-09-04T10:00:00.000Z");
  const endsAt = new Date("2026-12-01T00:00:00.000Z");
  assert.equal(iso(termStart(now, endsAt)), iso(endsAt));
});

test("a purchase after expiry starts now, not at the old end", () => {
  const now = new Date("2026-09-04T10:00:00.000Z");
  const expired = new Date("2026-01-01T00:00:00.000Z");
  assert.equal(iso(termStart(now, expired)), iso(now));
});

test("stacking a year onto a live term lands a year after that term ends", () => {
  const now = new Date("2026-09-04T10:00:00.000Z");
  const endsAt = new Date("2026-12-01T00:00:00.000Z");
  const start = termStart(now, endsAt);
  assert.equal(iso(termEnd(start, "YEARLY")), "2027-12-01T00:00:00.000Z");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-term.mts`
Expected: FAIL — cannot resolve `../src/lib/billing/term`.

- [ ] **Step 3: Implement**

Create `src/lib/billing/term.ts`:

```ts
/**
 * When a paid term starts and ends.
 *
 * Database-free on purpose — see the note at the top of `subscription.ts`.
 * All arithmetic is UTC: local-time helpers drift by a day across the West
 * Africa offset, which shows up as subscriptions expiring on the wrong date.
 */

import type { BillingPeriod } from "@/lib/subscription";

/**
 * Calendar-month addition that clamps rather than overflows.
 *
 * `setUTCMonth` alone turns Jan 31 + 1 month into Mar 3, which would hand a
 * subscriber three free days every time they bought on a long month.
 */
export function addMonthsUTC(date: Date, months: number): Date {
  const day = date.getUTCDate();
  const result = new Date(date.getTime());

  // Move to the 1st first, so the month shift can never overflow, then clamp
  // the day back to whatever the destination month actually has.
  result.setUTCDate(1);
  result.setUTCMonth(result.getUTCMonth() + months);

  const daysInTarget = new Date(
    Date.UTC(result.getUTCFullYear(), result.getUTCMonth() + 1, 0),
  ).getUTCDate();

  result.setUTCDate(Math.min(day, daysInTarget));
  return result;
}

/**
 * Where a newly purchased term begins.
 *
 * Stacking: if the buyer still has time left, the new term begins when the old
 * one ends. Paying twice extends the subscription and never overwrites it —
 * which is both what a user expects and what makes a duplicated charge
 * recoverable rather than costly.
 */
export function termStart(now: Date, currentEndsAt: Date | null): Date {
  if (currentEndsAt && currentEndsAt.getTime() > now.getTime()) {
    return new Date(currentEndsAt.getTime());
  }
  return new Date(now.getTime());
}

export function termEnd(start: Date, period: BillingPeriod): Date {
  return addMonthsUTC(start, period === "YEARLY" ? 12 : 1);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-term.mts`
Expected: PASS, 8 tests.

- [ ] **Step 5: Register the test file**

In `package.json`, append ` scripts/test-billing-term.mts` to the end of the `test` script value.

- [ ] **Step 6: Run the whole suite**

Run: `npm test`
Expected: PASS, with the new file included in the run.

- [ ] **Step 7: Commit**

```bash
git add src/lib/billing/term.ts scripts/test-billing-term.mts package.json
git commit -m "feat(billing): add term arithmetic with stacking"
```

---

## Task 3: Tier resolution

**Files:**
- Create: `src/lib/billing/entitlement.ts`
- Create: `scripts/test-billing-entitlement.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `SubscriptionTier`, `SubscriptionStatus` from `@/lib/subscription`.
- Produces: `type EntitlementRow = { tier, status, startsAt, endsAt }`, `type Entitlement = { tier, expiresAt }`, `resolveTier(rows, now): Entitlement`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-billing-entitlement.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  resolveTier,
  type EntitlementRow,
} from "../src/lib/billing/entitlement";

const NOW = new Date("2026-09-04T10:00:00.000Z");

function row(over: Partial<EntitlementRow> = {}): EntitlementRow {
  return {
    tier: "PREMIUM",
    status: "ACTIVE",
    startsAt: new Date("2026-09-01T00:00:00.000Z"),
    endsAt: new Date("2026-10-01T00:00:00.000Z"),
    ...over,
  };
}

test("no rows means freemium", () => {
  assert.deepEqual(resolveTier([], NOW), { tier: "FREEMIUM", expiresAt: null });
});

test("a covering active row grants its tier", () => {
  const result = resolveTier([row()], NOW);
  assert.equal(result.tier, "PREMIUM");
  assert.equal(result.expiresAt?.toISOString(), "2026-10-01T00:00:00.000Z");
});

test("an expired row grants nothing", () => {
  const expired = row({ endsAt: new Date("2026-08-01T00:00:00.000Z") });
  assert.deepEqual(resolveTier([expired], NOW), {
    tier: "FREEMIUM",
    expiresAt: null,
  });
});

test("endsAt is exclusive", () => {
  // A term ending at exactly now has ended. Without this the last instant of a
  // subscription is ambiguous, and the boundary is exactly where a renewal
  // hands over.
  const ending = row({ endsAt: NOW });
  assert.equal(resolveTier([ending], NOW).tier, "FREEMIUM");
});

test("startsAt is inclusive", () => {
  const starting = row({ startsAt: NOW });
  assert.equal(resolveTier([starting], NOW).tier, "PREMIUM");
});

test("a future row grants nothing yet", () => {
  const future = row({
    startsAt: new Date("2026-11-01T00:00:00.000Z"),
    endsAt: new Date("2026-12-01T00:00:00.000Z"),
  });
  assert.equal(resolveTier([future], NOW).tier, "FREEMIUM");
});

test("non-active statuses are ignored", () => {
  for (const status of ["PENDING", "FAILED", "ABANDONED", "REVOKED"] as const) {
    assert.equal(resolveTier([row({ status })], NOW).tier, "FREEMIUM", status);
  }
});

test("the richest overlapping tier wins, not the newest", () => {
  // A comped PREMIUM overlapping a paid STANDARD must resolve in the
  // student's favour, whichever was created first.
  const standard = row({ tier: "STANDARD" });
  const premium = row({ tier: "PREMIUM" });
  assert.equal(resolveTier([premium, standard], NOW).tier, "PREMIUM");
  assert.equal(resolveTier([standard, premium], NOW).tier, "PREMIUM");
});

test("expiry reports the furthest end among rows of the winning tier", () => {
  const near = row({ endsAt: new Date("2026-10-01T00:00:00.000Z") });
  const far = row({ endsAt: new Date("2027-01-01T00:00:00.000Z") });
  const result = resolveTier([near, far], NOW);
  assert.equal(result.expiresAt?.toISOString(), "2027-01-01T00:00:00.000Z");
});

test("a null endsAt never covers now", () => {
  // A PENDING row promoted by a buggy write must not become an endless grant.
  const open = row({ endsAt: null });
  assert.equal(resolveTier([open], NOW).tier, "FREEMIUM");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-entitlement.mts`
Expected: FAIL — cannot resolve `../src/lib/billing/entitlement`.

- [ ] **Step 3: Implement**

Create `src/lib/billing/entitlement.ts`:

```ts
/**
 * Which tier a set of subscription rows grants right now.
 *
 * This is the rule `User.tier` caches. Any hard entitlement gate must call
 * this against live rows; `User.tier` is for chrome, admin lists, and
 * analytics, and can lag an expiry by up to the auth profile TTL.
 *
 * Database-free, so every boundary below is unit tested without a database.
 */

import {
  SUBSCRIPTION_TIERS,
  type SubscriptionStatus,
  type SubscriptionTier,
} from "@/lib/subscription";

export type EntitlementRow = {
  tier: SubscriptionTier;
  status: SubscriptionStatus;
  startsAt: Date | null;
  endsAt: Date | null;
};

export type Entitlement = {
  tier: SubscriptionTier;
  /** When the granting tier lapses, or null when nothing is granted. */
  expiresAt: Date | null;
};

/** Rank by position in the tier union, which is ordered cheapest to richest. */
function rank(tier: SubscriptionTier): number {
  return SUBSCRIPTION_TIERS.indexOf(tier);
}

function covers(row: EntitlementRow, now: number): boolean {
  if (row.status !== "ACTIVE") return false;
  // A row with no end is not an endless grant — it is an unfinished write.
  if (!row.endsAt) return false;
  if (row.startsAt && row.startsAt.getTime() > now) return false;
  // Exclusive: a term ending at exactly `now` has ended.
  return row.endsAt.getTime() > now;
}

export function resolveTier(
  rows: readonly EntitlementRow[],
  now: Date,
): Entitlement {
  const live = rows.filter((row) => covers(row, now.getTime()));
  if (live.length === 0) return { tier: "FREEMIUM", expiresAt: null };

  // Richest wins, not newest: a comped PREMIUM overlapping a paid STANDARD
  // resolves in the student's favour.
  const tier = live.reduce(
    (best, row) => (rank(row.tier) > rank(best) ? row.tier : best),
    live[0].tier,
  );

  const expiresAt = live
    .filter((row) => row.tier === tier)
    .reduce<Date | null>(
      (furthest, row) =>
        !furthest || row.endsAt!.getTime() > furthest.getTime()
          ? row.endsAt!
          : furthest,
      null,
    );

  return { tier, expiresAt };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-entitlement.mts`
Expected: PASS, 10 tests.

- [ ] **Step 5: Register the test file and run the suite**

Append ` scripts/test-billing-entitlement.mts` to the `test` script in `package.json`, then run `npm test`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/billing/entitlement.ts scripts/test-billing-entitlement.mts package.json
git commit -m "feat(billing): resolve the effective tier from subscription rows"
```

---

## Task 4: Webhook signature verification

**Files:**
- Create: `src/lib/billing/signature.ts`
- Create: `scripts/test-billing-signature.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `node:crypto`.
- Produces: `paystackSignature(rawBody, secret): string`, `verifyPaystackSignature({ rawBody, signature, secret }): boolean`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-billing-signature.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  paystackSignature,
  verifyPaystackSignature,
} from "../src/lib/billing/signature";

const SECRET = "sk_test_pretend_secret";
const BODY = JSON.stringify({ event: "charge.success", data: { id: 1 } });

test("a signature is a 128-character hex sha512 digest", () => {
  const signature = paystackSignature(BODY, SECRET);
  assert.match(signature, /^[0-9a-f]{128}$/);
});

test("a correctly signed body verifies", () => {
  assert.equal(
    verifyPaystackSignature({
      rawBody: BODY,
      signature: paystackSignature(BODY, SECRET),
      secret: SECRET,
    }),
    true,
  );
});

test("a tampered body fails", () => {
  const signature = paystackSignature(BODY, SECRET);
  const tampered = JSON.stringify({
    event: "charge.success",
    data: { id: 2 },
  });
  assert.equal(
    verifyPaystackSignature({ rawBody: tampered, signature, secret: SECRET }),
    false,
  );
});

test("a signature from the wrong secret fails", () => {
  assert.equal(
    verifyPaystackSignature({
      rawBody: BODY,
      signature: paystackSignature(BODY, "sk_test_other"),
      secret: SECRET,
    }),
    false,
  );
});

test("a missing or malformed signature fails without throwing", () => {
  // timingSafeEqual throws on a length mismatch — an attacker must not be able
  // to turn a short header into a 500 instead of a 401.
  for (const signature of [null, "", "abc", "z".repeat(128)]) {
    assert.equal(
      verifyPaystackSignature({ rawBody: BODY, signature, secret: SECRET }),
      false,
      String(signature),
    );
  }
});

test("an absent secret fails closed", () => {
  assert.equal(
    verifyPaystackSignature({
      rawBody: BODY,
      signature: paystackSignature(BODY, SECRET),
      secret: undefined,
    }),
    false,
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-signature.mts`
Expected: FAIL — cannot resolve `../src/lib/billing/signature`.

- [ ] **Step 3: Implement**

Create `src/lib/billing/signature.ts`:

```ts
/**
 * The Paystack webhook signature.
 *
 * Paystack signs the raw request body with HMAC-SHA512 keyed by the secret key
 * and sends the hex digest in `x-paystack-signature`. The check is worthless
 * against a re-serialized body, so the caller must pass the exact bytes it
 * received — `await req.text()`, never `JSON.stringify(await req.json())`.
 */

import { createHmac, timingSafeEqual } from "node:crypto";

export function paystackSignature(rawBody: string, secret: string): string {
  return createHmac("sha512", secret).update(rawBody, "utf8").digest("hex");
}

export function verifyPaystackSignature({
  rawBody,
  signature,
  secret,
}: {
  rawBody: string;
  signature: string | null | undefined;
  secret: string | undefined;
}): boolean {
  if (!secret || !signature) return false;

  const expected = paystackSignature(rawBody, secret);

  // Compare as hex text of equal length. timingSafeEqual throws outright on a
  // length mismatch, so a truncated header would otherwise be a 500 — which is
  // itself an oracle. Fail closed instead.
  if (signature.length !== expected.length) return false;

  try {
    return timingSafeEqual(Buffer.from(signature), Buffer.from(expected));
  } catch {
    return false;
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-signature.mts`
Expected: PASS, 6 tests.

- [ ] **Step 5: Register the test file and run the suite**

Append ` scripts/test-billing-signature.mts` to the `test` script in `package.json`, then run `npm test`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/billing/signature.ts scripts/test-billing-signature.mts package.json
git commit -m "feat(billing): verify Paystack webhook signatures"
```

---

## Task 5: Settlement — deciding what a verified transaction means

**Files:**
- Create: `src/lib/billing/settlement.ts`
- Create: `scripts/test-billing-settlement.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `SubscriptionTier`, `BillingPeriod`, `SubscriptionStatus` from `@/lib/subscription`.
- Produces: `type PendingRow`, `type VerifiedTransaction`, `type Settlement`, `settle(pending, transaction, now): Settlement`.

`VerifiedTransaction` is the normalised shape Task 7's Paystack client returns; both this module and `applyChargeSuccess` in Task 8 consume it.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-billing-settlement.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  settle,
  type PendingRow,
  type VerifiedTransaction,
} from "../src/lib/billing/settlement";

const NOW = new Date("2026-09-04T10:00:00.000Z");

function pending(over: Partial<PendingRow> = {}): PendingRow {
  return {
    reference: "pw_abc123",
    tier: "PREMIUM",
    period: "MONTHLY",
    amountKobo: 500_000,
    currency: "NGN",
    status: "PENDING",
    ...over,
  };
}

function transaction(over: Partial<VerifiedTransaction> = {}): VerifiedTransaction {
  return {
    reference: "pw_abc123",
    status: "success",
    amountKobo: 500_000,
    currency: "NGN",
    channel: "card",
    paidAt: new Date("2026-09-04T09:59:00.000Z"),
    ...over,
  };
}

test("a matching successful transaction activates", () => {
  const result = settle(pending(), transaction(), NOW);
  assert.equal(result.kind, "activate");
  if (result.kind !== "activate") return;
  assert.equal(result.channel, "card");
  assert.equal(result.paidAt.toISOString(), "2026-09-04T09:59:00.000Z");
});

test("a missing paidAt falls back to now", () => {
  const result = settle(pending(), transaction({ paidAt: null }), NOW);
  assert.equal(result.kind, "activate");
  if (result.kind !== "activate") return;
  assert.equal(result.paidAt.toISOString(), NOW.toISOString());
});

test("an already-active row is a no-op", () => {
  // The callback and the webhook both settle the same reference. The second
  // one to arrive must not extend the term a second time.
  const result = settle(pending({ status: "ACTIVE" }), transaction(), NOW);
  assert.equal(result.kind, "already-applied");
});

test("underpayment is rejected", () => {
  const result = settle(pending(), transaction({ amountKobo: 10_000 }), NOW);
  assert.equal(result.kind, "reject");
  if (result.kind !== "reject") return;
  assert.equal(result.reason, "amount-mismatch");
});

test("overpayment is rejected too", () => {
  // Not generosity — a mismatch either way means the charge did not come from
  // the checkout we authorised, and the row's price is the one we honour.
  const result = settle(pending(), transaction({ amountKobo: 900_000 }), NOW);
  assert.equal(result.kind, "reject");
});

test("a currency mismatch is rejected", () => {
  const result = settle(pending(), transaction({ currency: "USD" }), NOW);
  assert.equal(result.kind, "reject");
  if (result.kind !== "reject") return;
  assert.equal(result.reason, "currency-mismatch");
});

test("a reference mismatch is rejected", () => {
  const result = settle(pending(), transaction({ reference: "pw_other" }), NOW);
  assert.equal(result.kind, "reject");
  if (result.kind !== "reject") return;
  assert.equal(result.reason, "reference-mismatch");
});

test("an unsuccessful transaction is rejected", () => {
  for (const status of ["failed", "abandoned", "pending"]) {
    const result = settle(pending(), transaction({ status }), NOW);
    assert.equal(result.kind, "reject", status);
    if (result.kind !== "reject") continue;
    assert.equal(result.reason, "not-successful");
  }
});

test("a revoked row does not reactivate on a late webhook", () => {
  const result = settle(pending({ status: "REVOKED" }), transaction(), NOW);
  assert.equal(result.kind, "reject");
  if (result.kind !== "reject") return;
  assert.equal(result.reason, "not-pending");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-settlement.mts`
Expected: FAIL — cannot resolve `../src/lib/billing/settlement`.

- [ ] **Step 3: Implement**

Create `src/lib/billing/settlement.ts`:

```ts
/**
 * What a verified Paystack transaction means for the row that authorised it.
 *
 * Both confirmation paths — the browser callback and the signed webhook — pass
 * through here before anything is written, so the rules hold whichever arrives
 * first and whichever arrives twice.
 */

import type {
  BillingPeriod,
  SubscriptionStatus,
  SubscriptionTier,
} from "@/lib/subscription";

export type PendingRow = {
  reference: string;
  tier: SubscriptionTier;
  period: BillingPeriod;
  amountKobo: number;
  currency: string;
  status: SubscriptionStatus;
};

/** The normalised shape of a Paystack transaction, from `verifyTransaction`. */
export type VerifiedTransaction = {
  reference: string;
  status: string;
  amountKobo: number;
  currency: string;
  channel: string | null;
  paidAt: Date | null;
};

export type Settlement =
  | { kind: "activate"; paidAt: Date; channel: string | null }
  | { kind: "already-applied" }
  | {
      kind: "reject";
      reason:
        | "reference-mismatch"
        | "not-successful"
        | "not-pending"
        | "amount-mismatch"
        | "currency-mismatch";
    };

export function settle(
  pending: PendingRow,
  transaction: VerifiedTransaction,
  now: Date,
): Settlement {
  if (pending.reference !== transaction.reference) {
    return { kind: "reject", reason: "reference-mismatch" };
  }

  // Idempotency's first line: the row already carries the grant. A callback and
  // a webhook racing on one reference must produce exactly one activation.
  if (pending.status === "ACTIVE") return { kind: "already-applied" };

  if (pending.status !== "PENDING") {
    return { kind: "reject", reason: "not-pending" };
  }

  if (transaction.status !== "success") {
    return { kind: "reject", reason: "not-successful" };
  }

  // Checked in both directions. A mismatch either way means the charge is not
  // the one we authorised — this is what stops an edited redirect buying
  // PREMIUM for a hundred naira.
  if (transaction.amountKobo !== pending.amountKobo) {
    return { kind: "reject", reason: "amount-mismatch" };
  }

  if (transaction.currency !== pending.currency) {
    return { kind: "reject", reason: "currency-mismatch" };
  }

  return {
    kind: "activate",
    paidAt: transaction.paidAt ?? now,
    channel: transaction.channel,
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-billing-settlement.mts`
Expected: PASS, 9 tests.

- [ ] **Step 5: Register the test file and run the suite**

Append ` scripts/test-billing-settlement.mts` to the `test` script in `package.json`, then run `npm test`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/billing/settlement.ts scripts/test-billing-settlement.mts package.json
git commit -m "feat(billing): decide the outcome of a verified transaction"
```

---

## Task 6: Schema and migration

No test cycle of its own — it is verified by the catalog check in Step 4 and by every task after it compiling.

**Files:**
- Modify: `prisma/schema.prisma`

**Interfaces:**
- Consumes: the unions from Task 1, which the Prisma enums mirror exactly.
- Produces: Prisma models `Subscription` and `PaystackEvent`, enums `BillingPeriod`, `SubscriptionSource`, `SubscriptionStatus`, and the `User.subscriptions` back-relation.

- [ ] **Step 1: Add the enums**

In `prisma/schema.prisma`, next to the existing `SubscriptionTier` enum:

```prisma
enum BillingPeriod {
  MONTHLY
  YEARLY
}

enum SubscriptionSource {
  PAYSTACK
  COMP
}

enum SubscriptionStatus {
  PENDING
  ACTIVE
  FAILED
  ABANDONED
  REVOKED
}
```

- [ ] **Step 2: Add the models**

Append to `prisma/schema.prisma`:

```prisma
// ─── Billing ──────────────────────────────────────────────
//
// The source of truth that writes User.tier. See
// docs/superpowers/specs/2026-09-04-paystack-billing-design.md

model Subscription {
  id     String @id @default(cuid())
  userId String
  user   User   @relation(fields: [userId], references: [id], onDelete: Cascade)

  tier   SubscriptionTier
  period BillingPeriod
  source SubscriptionSource
  status SubscriptionStatus @default(PENDING)

  /// Ours, generated at checkout and handed to Paystack, so any transaction
  /// traces back to the row that authorised it.
  reference String @unique

  /// A snapshot of what was actually charged. This is what lets prices live in
  /// code: repricing a plan never rewrites history.
  amountKobo Int
  currency   String @default("NGN")
  channel    String?

  paidAt   DateTime?
  startsAt DateTime?
  endsAt   DateTime?

  /// Set on COMP rows only — the Admin who issued the grant.
  grantedById String?
  note        String?

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@index([userId, endsAt])
  @@index([status])
}

/// Idempotency ledger and raw audit trail for Paystack deliveries. The primary
/// key is the reference joined with the event type, so a redelivered event
/// collides instead of being applied twice.
model PaystackEvent {
  eventKey   String   @id
  type       String
  payload    Json
  receivedAt DateTime @default(now())
}
```

- [ ] **Step 3: Add the back-relation on User**

In `model User`, immediately after the `tierUpdatedAt` field, add:

```prisma
  subscriptions Subscription[]
```

- [ ] **Step 4: Generate the migration SQL without applying it**

`prisma migrate dev` cannot reach Supabase from this machine, so generate the SQL and stop:

```bash
npx prisma migrate diff --from-schema-datasource prisma/schema.prisma --to-schema-datamodel prisma/schema.prisma --script > migration.sql
```

If that shell fails on Windows path handling, run it through the Bash tool rather than PowerShell.

Review `migration.sql`. It must contain `CREATE TYPE` for the three enums, `CREATE TABLE "Subscription"`, `CREATE TABLE "PaystackEvent"`, and the indexes — and **no `DROP`**. A `DROP` means the diff was taken against the wrong baseline; stop and re-check rather than running it.

- [ ] **Step 5: Apply it by hand and verify the catalog**

Paste `migration.sql` into the Supabase SQL Editor and run it.

The editor reports success even when a batch half-applies, so do not trust the message. Verify against the catalog:

```sql
select table_name from information_schema.tables
where table_name in ('Subscription','PaystackEvent');

select typname from pg_type
where typname in ('BillingPeriod','SubscriptionSource','SubscriptionStatus');
```

Both queries must return every row. If any are missing, re-run only the missing statements.

- [ ] **Step 6: Record the migration locally with LF endings**

Create `prisma/migrations/<timestamp>_billing/migration.sql` containing the same SQL, written with **LF** line endings. `core.autocrlf=true` will otherwise silently drift the Prisma checksum. Confirm:

```bash
file prisma/migrations/*_billing/migration.sql
```

Expected: no "CRLF" in the output.

Then delete the scratch `migration.sql` from the repo root.

- [ ] **Step 7: Regenerate the client**

Stop the dev server first — it holds a lock on the query engine DLL and `prisma generate` fails with EPERM, which later surfaces as bogus `tsc` errors from a stale client.

Run: `npx prisma generate`
Expected: "Generated Prisma Client".

- [ ] **Step 8: Typecheck and commit**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

```bash
git add prisma/schema.prisma prisma/migrations
git commit -m "feat(billing): add the Subscription and PaystackEvent models"
```

---

## Task 7: Paystack client, references, and the on/off switch

**Files:**
- Create: `src/lib/billing/paystack.ts`
- Create: `src/lib/billing/reference.ts`
- Modify: `.env.example` (create it if absent)

**Interfaces:**
- Consumes: `VerifiedTransaction` from `@/lib/billing/settlement`.
- Produces: `isBillingEnabled(): boolean`, `initializeTransaction(args): Promise<{ authorizationUrl: string }>`, `verifyTransaction(reference): Promise<VerifiedTransaction>`, `newReference(): string`, `appUrl(): string`.

No unit test: this module is a thin HTTP wrapper with no branching worth pinning, and mocking `fetch` here would test the mock. The logic it feeds is already covered by Task 5.

- [ ] **Step 1: Write the reference generator**

Create `src/lib/billing/reference.ts`:

```ts
import { randomUUID } from "node:crypto";

/**
 * A transaction reference. Prefixed so Paystack dashboard searches and log
 * greps can tell our references apart from ones Paystack generates itself.
 *
 * The randomness source is injectable purely so a caller can make it
 * deterministic; nothing in the app passes it.
 */
export function newReference(random: () => string = randomUUID): string {
  return `pw_${random().replace(/-/g, "")}`;
}
```

- [ ] **Step 2: Write the client**

Create `src/lib/billing/paystack.ts`:

```ts
/**
 * The Paystack HTTP client.
 *
 * Kept deliberately thin: it normalises Paystack's payload into
 * `VerifiedTransaction` and does nothing else. Every decision made from that
 * data lives in `settlement.ts`, where it is unit tested.
 */

import type { VerifiedTransaction } from "@/lib/billing/settlement";

const PAYSTACK_API = "https://api.paystack.co";

const PLACEHOLDERS = new Set(["", "your-paystack-secret-key"]);

export function paystackSecret(): string | undefined {
  const key = process.env.PAYSTACK_SECRET_KEY?.trim();
  if (!key || PLACEHOLDERS.has(key)) return undefined;
  return key;
}

/**
 * Whether billing is configured at all.
 *
 * Follows the pattern auth.ts uses for the Google provider: with no key, the
 * feature is simply off — checkout answers 503 and the UI hides its buttons —
 * so local development without secrets keeps working instead of throwing.
 */
export function isBillingEnabled(): boolean {
  return paystackSecret() !== undefined;
}

export function appUrl(): string {
  return (
    process.env.NEXT_PUBLIC_APP_URL?.replace(/\/$/, "") ??
    "http://localhost:3000"
  );
}

function requireSecret(): string {
  const secret = paystackSecret();
  if (!secret) throw new Error("PAYSTACK_SECRET_KEY is not configured");
  return secret;
}

async function paystackFetch(
  path: string,
  init: RequestInit,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${PAYSTACK_API}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${requireSecret()}`,
      "Content-Type": "application/json",
      ...init.headers,
    },
    // Never cached: these are money operations, and a stale verify would be
    // worse than a slow one.
    cache: "no-store",
  });

  const body = (await res.json().catch(() => null)) as {
    status?: boolean;
    message?: string;
    data?: Record<string, unknown>;
  } | null;

  if (!res.ok || !body?.status || !body.data) {
    throw new Error(
      `Paystack ${path} failed (${res.status}): ${body?.message ?? "no body"}`,
    );
  }

  return body.data;
}

export async function initializeTransaction({
  email,
  amountKobo,
  reference,
  callbackUrl,
  metadata,
}: {
  email: string;
  amountKobo: number;
  reference: string;
  callbackUrl: string;
  metadata: Record<string, string>;
}): Promise<{ authorizationUrl: string }> {
  const data = await paystackFetch("/transaction/initialize", {
    method: "POST",
    body: JSON.stringify({
      email,
      amount: amountKobo,
      reference,
      currency: "NGN",
      callback_url: callbackUrl,
      metadata,
    }),
  });

  const authorizationUrl = data.authorization_url;
  if (typeof authorizationUrl !== "string") {
    throw new Error("Paystack initialize returned no authorization_url");
  }

  return { authorizationUrl };
}

export async function verifyTransaction(
  reference: string,
): Promise<VerifiedTransaction> {
  const data = await paystackFetch(
    `/transaction/verify/${encodeURIComponent(reference)}`,
    { method: "GET" },
  );

  const paidAt = typeof data.paid_at === "string" ? new Date(data.paid_at) : null;

  return {
    reference: String(data.reference ?? reference),
    status: String(data.status ?? "unknown"),
    amountKobo: Number(data.amount ?? -1),
    currency: String(data.currency ?? ""),
    channel: typeof data.channel === "string" ? data.channel : null,
    paidAt: paidAt && !Number.isNaN(paidAt.getTime()) ? paidAt : null,
  };
}
```

- [ ] **Step 3: Document the environment variables**

`.env.example` already defines `NEXT_PUBLIC_APP_URL="http://localhost:3000"` — leave it alone. Add only:

```
# Billing. With no secret key, billing is simply off: checkout answers 503 and
# the upgrade UI hides itself, so local development needs no Paystack account.
PAYSTACK_SECRET_KEY="your-paystack-secret-key"
```

The placeholder string must match the one `paystackSecret()` rejects, or a
developer who copies `.env.example` to `.env` verbatim gets a live-looking
config that fails on the first API call instead of cleanly reporting 503.

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/lib/billing/paystack.ts src/lib/billing/reference.ts .env.example
git commit -m "feat(billing): add the Paystack client and config switch"
```

---

## Task 8: The data layer

**Files:**
- Create: `src/lib/billing/subscription-data.ts`

**Interfaces:**
- Consumes: `db`, `resolveTier`, `termStart`, `termEnd`, `settle`, `planFor`, `newReference`.
- Produces: `activeRowsFor(userId)`, `currentEntitlement(userId)`, `refreshCachedTier(userId)`, `createPendingSubscription(args)`, `applyChargeSuccess(transaction)`, `recordPaystackEvent(args)`, `grantComp(args)`, `revokeSubscriptions(userId)`, `latestSubscriptionFor(userId)`.

- [ ] **Step 1: Implement**

Create `src/lib/billing/subscription-data.ts`:

```ts
/**
 * Every Prisma read and write billing performs.
 *
 * The rules themselves live in the pure modules alongside this one; this file
 * only moves rows. Keeping the split honest is what lets term stacking,
 * expiry, and settlement be tested without a database.
 */

import { db } from "@/lib/db";
import {
  planFor,
  type BillingPeriod,
  type SubscriptionTier,
} from "@/lib/subscription";
import { resolveTier, type Entitlement } from "@/lib/billing/entitlement";
import { termEnd, termStart } from "@/lib/billing/term";
import { settle, type VerifiedTransaction } from "@/lib/billing/settlement";
import { newReference } from "@/lib/billing/reference";

const ENTITLEMENT_SELECT = {
  tier: true,
  status: true,
  startsAt: true,
  endsAt: true,
} as const;

export async function activeRowsFor(userId: string) {
  return db.subscription.findMany({
    where: { userId, status: "ACTIVE" },
    select: ENTITLEMENT_SELECT,
  });
}

export async function currentEntitlement(
  userId: string,
  now: Date = new Date(),
): Promise<Entitlement> {
  return resolveTier(await activeRowsFor(userId), now);
}

/**
 * Writes the derived tier back onto the User cache column. Returns what it
 * settled on, so callers can avoid a second read.
 */
export async function refreshCachedTier(
  userId: string,
  now: Date = new Date(),
): Promise<SubscriptionTier> {
  const { tier } = await currentEntitlement(userId, now);

  await db.user.updateMany({
    where: { id: userId, tier: { not: tier } },
    data: { tier, tierUpdatedAt: now },
  });

  return tier;
}

export async function latestSubscriptionFor(userId: string) {
  return db.subscription.findFirst({
    where: { userId, status: "ACTIVE" },
    orderBy: { endsAt: "desc" },
  });
}

export async function createPendingSubscription({
  userId,
  tier,
  period,
}: {
  userId: string;
  tier: SubscriptionTier;
  period: BillingPeriod;
}): Promise<{ reference: string; amountKobo: number }> {
  const plan = planFor(tier, period);
  const reference = newReference();

  // Stale PENDING rows from abandoned checkouts would otherwise pile up on the
  // account forever. They grant nothing either way — resolveTier ignores
  // anything that is not ACTIVE — but they make the billing history unreadable.
  await db.subscription.updateMany({
    where: { userId, status: "PENDING" },
    data: { status: "ABANDONED" },
  });

  await db.subscription.create({
    data: {
      userId,
      tier,
      period,
      source: "PAYSTACK",
      status: "PENDING",
      reference,
      amountKobo: plan.amountKobo,
      currency: "NGN",
    },
  });

  return { reference, amountKobo: plan.amountKobo };
}

export type ChargeOutcome =
  | "activated"
  | "already-applied"
  | "rejected"
  | "unknown-reference";

/**
 * The single place a payment becomes access.
 *
 * Both the browser callback and the signed webhook call this, so it must be
 * safe to run twice on one reference — `settle` returns "already-applied" for
 * the second caller, and the term is never extended twice.
 */
export async function applyChargeSuccess(
  transaction: VerifiedTransaction,
  now: Date = new Date(),
): Promise<ChargeOutcome> {
  const pending = await db.subscription.findUnique({
    where: { reference: transaction.reference },
  });

  if (!pending) return "unknown-reference";

  const decision = settle(
    {
      reference: pending.reference,
      tier: pending.tier,
      period: pending.period,
      amountKobo: pending.amountKobo,
      currency: pending.currency,
      status: pending.status,
    },
    transaction,
    now,
  );

  if (decision.kind === "already-applied") return "already-applied";

  if (decision.kind === "reject") {
    if (decision.reason === "not-successful") {
      await db.subscription.update({
        where: { id: pending.id },
        data: { status: "FAILED" },
      });
    }
    return "rejected";
  }

  const live = await latestSubscriptionFor(pending.userId);
  const startsAt = termStart(now, live?.endsAt ?? null);
  const endsAt = termEnd(startsAt, pending.period);

  await db.$transaction([
    // Guarded on status so two concurrent callers cannot both activate: the
    // loser updates zero rows.
    db.subscription.updateMany({
      where: { id: pending.id, status: "PENDING" },
      data: {
        status: "ACTIVE",
        paidAt: decision.paidAt,
        channel: decision.channel,
        startsAt,
        endsAt,
      },
    }),
    db.user.update({
      where: { id: pending.userId },
      data: { tier: pending.tier, tierUpdatedAt: now },
    }),
  ]);

  // The user column is set optimistically above; re-derive in case a richer
  // comp is also live, so the cache never demotes someone mid-term.
  await refreshCachedTier(pending.userId, now);

  return "activated";
}

/**
 * Records a Paystack delivery. Returns false when this event was already
 * recorded, which is the idempotency gate: the caller stops there.
 */
export async function recordPaystackEvent({
  reference,
  type,
  payload,
}: {
  reference: string;
  type: string;
  payload: unknown;
}): Promise<boolean> {
  try {
    await db.paystackEvent.create({
      data: {
        eventKey: `${reference}:${type}`,
        type,
        payload: payload as object,
      },
    });
    return true;
  } catch {
    // A primary-key collision means Paystack redelivered an event we have
    // already handled. Anything else that throws here would also be safest
    // treated as "do not apply twice".
    return false;
  }
}

export async function grantComp({
  userId,
  tier,
  period,
  grantedById,
  note,
  now = new Date(),
}: {
  userId: string;
  tier: SubscriptionTier;
  period: BillingPeriod;
  grantedById: string;
  note?: string | null;
  now?: Date;
}): Promise<void> {
  const live = await latestSubscriptionFor(userId);
  const startsAt = termStart(now, live?.endsAt ?? null);

  await db.subscription.create({
    data: {
      userId,
      tier,
      period,
      source: "COMP",
      status: "ACTIVE",
      reference: newReference(),
      amountKobo: 0,
      currency: "NGN",
      paidAt: now,
      startsAt,
      endsAt: termEnd(startsAt, period),
      grantedById,
      note: note ?? null,
    },
  });

  await refreshCachedTier(userId, now);
}

/**
 * Ends every live subscription immediately — including paid ones. This is what
 * an admin setting a student back to Freemium means, and the UI says so before
 * asking for confirmation. Refunds stay a manual Paystack dashboard action.
 */
export async function revokeSubscriptions(
  userId: string,
  now: Date = new Date(),
): Promise<void> {
  await db.subscription.updateMany({
    where: { userId, status: "ACTIVE" },
    data: { status: "REVOKED", endsAt: now },
  });

  await refreshCachedTier(userId, now);
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors. If Prisma types are missing, Task 6 Step 7 did not regenerate — stop the dev server and rerun `npx prisma generate`.

- [ ] **Step 3: Commit**

```bash
git add src/lib/billing/subscription-data.ts
git commit -m "feat(billing): add the subscription data layer"
```

---

## Task 9: The checkout route

**Files:**
- Create: `src/app/api/billing/checkout/route.ts`
- Modify: `src/lib/validators.ts`

**Interfaces:**
- Consumes: `auth`, `createPendingSubscription`, `initializeTransaction`, `isBillingEnabled`, `appUrl`, `planFor`, `isPurchasableTier`.
- Produces: `POST /api/billing/checkout` → `{ authorizationUrl: string }`; `checkoutSchema` in validators.

- [ ] **Step 1: Add the schema**

In `src/lib/validators.ts`, beside `studentTierSchema`, add — and extend the existing `@/lib/subscription` import to include `BILLING_PERIODS`:

```ts
export const checkoutSchema = z.object({
  tier: z.enum(SUBSCRIPTION_TIERS),
  period: z.enum(BILLING_PERIODS),
});
```

And with the other type exports at the bottom:

```ts
export type CheckoutInput = z.infer<typeof checkoutSchema>;
```

- [ ] **Step 2: Implement the route**

Create `src/app/api/billing/checkout/route.ts`:

```ts
import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import { checkoutSchema } from "@/lib/validators";
import { isPurchasableTier, planFor } from "@/lib/subscription";
import { appUrl, initializeTransaction, isBillingEnabled } from "@/lib/billing/paystack";
import { createPendingSubscription } from "@/lib/billing/subscription-data";
import { rateLimit, tooManyRequests } from "@/lib/rate-limit";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const session = await auth();
  const userId = session?.user?.id;
  if (!userId) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  if (!isBillingEnabled()) {
    return NextResponse.json(
      { error: "Payments are not available right now." },
      { status: 503 },
    );
  }

  // Keyed by user, not IP: initializing transactions is cheap for us and noisy
  // in the Paystack dashboard, and a signed-in user is the right unit here.
  const limit = rateLimit({
    key: `billing-checkout:${userId}`,
    limit: 10,
    windowSeconds: 60,
  });
  if (!limit.ok) return tooManyRequests(limit.retryAfterSeconds);

  const parsed = checkoutSchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Validation failed" }, { status: 400 });
  }

  const { tier, period } = parsed.data;
  if (!isPurchasableTier(tier)) {
    return NextResponse.json(
      { error: "That plan cannot be purchased." },
      { status: 400 },
    );
  }

  const user = await db.user.findUnique({
    where: { id: userId },
    select: { email: true },
  });
  if (!user?.email) {
    return NextResponse.json(
      { error: "Add an email address to your account before subscribing." },
      { status: 400 },
    );
  }

  // The price comes from the catalogue, never from the request body — the
  // client says which plan, the server says what it costs.
  const plan = planFor(tier, period);
  const { reference } = await createPendingSubscription({ userId, tier, period });

  try {
    const { authorizationUrl } = await initializeTransaction({
      email: user.email,
      amountKobo: plan.amountKobo,
      reference,
      callbackUrl: `${appUrl()}/api/billing/callback`,
      metadata: { userId, tier, period },
    });

    return NextResponse.json({ authorizationUrl });
  } catch (error) {
    console.error("[billing] initialize failed", error);
    return NextResponse.json(
      { error: "Could not start the payment. Please try again." },
      { status: 502 },
    );
  }
}
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 4: Verify it fails closed without a key**

With `PAYSTACK_SECRET_KEY` unset, start the dev server and, signed in, run in the browser console:

```js
await (await fetch("/api/billing/checkout", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ tier: "PREMIUM", period: "MONTHLY" }),
})).status
```

Expected: `503`. Signed out, expect `401`.

- [ ] **Step 5: Commit**

```bash
git add src/app/api/billing/checkout/route.ts src/lib/validators.ts
git commit -m "feat(billing): add the checkout route"
```

---

## Task 10: The webhook, and letting it through the proxy

The proxy change is not optional housekeeping: `src/proxy.ts` currently answers any unauthenticated `/api/*` request with a 401, and Paystack never sends a session cookie. Without Step 1 the webhook silently never runs.

**Files:**
- Modify: `src/proxy.ts:88-92` (the `config.matcher`)
- Create: `src/app/api/billing/webhook/route.ts`

**Interfaces:**
- Consumes: `verifyPaystackSignature`, `paystackSecret`, `verifyTransaction`, `recordPaystackEvent`, `applyChargeSuccess`.
- Produces: `POST /api/billing/webhook`.

- [ ] **Step 1: Exclude the webhook from the proxy matcher**

In `src/proxy.ts`, replace the `config` export with:

```ts
export const config = {
  matcher: [
    // api/billing/webhook is excluded because Paystack is not a signed-in
    // user: the auth branch below would answer its POST with a 401, which
    // Paystack reads as a delivery failure and we would never see the charge.
    // The route authenticates itself by HMAC instead.
    "/((?!api/auth|api/billing/webhook|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
```

- [ ] **Step 2: Implement the route**

Create `src/app/api/billing/webhook/route.ts`:

```ts
import { NextResponse } from "next/server";
import { paystackSecret, verifyTransaction } from "@/lib/billing/paystack";
import { verifyPaystackSignature } from "@/lib/billing/signature";
import {
  applyChargeSuccess,
  recordPaystackEvent,
} from "@/lib/billing/subscription-data";

export const dynamic = "force-dynamic";

/**
 * The authoritative confirmation path.
 *
 * Route Handlers receive a plain Web Request, so `req.text()` is the exact
 * body Paystack signed. Never re-serialize it — `JSON.stringify(await
 * req.json())` reorders nothing but reformats everything, and the HMAC fails.
 */
export async function POST(req: Request) {
  const rawBody = await req.text();

  const ok = verifyPaystackSignature({
    rawBody,
    signature: req.headers.get("x-paystack-signature"),
    secret: paystackSecret(),
  });

  if (!ok) {
    return NextResponse.json({ error: "Invalid signature" }, { status: 401 });
  }

  let event: { event?: string; data?: { reference?: string } };
  try {
    event = JSON.parse(rawBody);
  } catch {
    return NextResponse.json({ error: "Malformed body" }, { status: 400 });
  }

  const type = event.event ?? "unknown";
  const reference = event.data?.reference;

  if (!reference) {
    // Nothing to key idempotency on. Answer 200 so Paystack stops retrying an
    // event we can never act on.
    return NextResponse.json({ received: true });
  }

  const fresh = await recordPaystackEvent({ reference, type, payload: event });
  if (!fresh) return NextResponse.json({ received: true, duplicate: true });

  if (type !== "charge.success") {
    // Recorded for the audit trail and acknowledged. Returning anything but a
    // 2xx here would have Paystack retrying every event type we do not use.
    return NextResponse.json({ received: true });
  }

  try {
    // Re-verify against the API rather than trusting the payload's amount. The
    // signature proves the body came from Paystack; verify proves the money
    // actually settled.
    const transaction = await verifyTransaction(reference);
    const outcome = await applyChargeSuccess(transaction);
    return NextResponse.json({ received: true, outcome });
  } catch (error) {
    console.error("[billing] webhook apply failed", reference, error);
    // A 500 tells Paystack to retry. The event row is already written, so the
    // retry would be swallowed as a duplicate — delete it so the retry can act.
    return NextResponse.json({ error: "Apply failed" }, { status: 500 });
  }
}
```

- [ ] **Step 3: Fix the retry hole the previous step just documented**

The `catch` above is unreachable-by-design only if the event row is removed. In `src/lib/billing/subscription-data.ts`, add:

```ts
/** Lets a failed apply be retried: without this the redelivery is a duplicate. */
export async function forgetPaystackEvent(
  reference: string,
  type: string,
): Promise<void> {
  await db.paystackEvent
    .delete({ where: { eventKey: `${reference}:${type}` } })
    .catch(() => {});
}
```

Then in the route's `catch`, before returning the 500:

```ts
    await forgetPaystackEvent(reference, type);
```

and add `forgetPaystackEvent` to the route's import from `@/lib/billing/subscription-data`.

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 5: Prove the proxy no longer blocks it**

With the dev server running and `PAYSTACK_SECRET_KEY` set to any non-placeholder value:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:3000/api/billing/webhook \
  -H "Content-Type: application/json" -d '{"event":"charge.success"}'
```

Expected: `401` from the signature check — **not** `401` with an `{"error":"Unauthorized"}` body from the proxy, and not a `307` redirect. Confirm by printing the body: it must read `{"error":"Invalid signature"}`. If it says `Unauthorized`, Step 1 did not take effect; restart the dev server, since matcher changes are read at build time.

- [ ] **Step 6: Prove a correctly signed body gets past the signature check**

```bash
node -e '
const {createHmac}=require("crypto");
const body=JSON.stringify({event:"charge.success",data:{reference:"pw_nonexistent"}});
const sig=createHmac("sha512",process.env.PAYSTACK_SECRET_KEY).update(body).digest("hex");
console.log(sig);console.log(body);'
```

POST that body with the printed signature in `x-paystack-signature`.
Expected: `200`. The reference is unknown, so the outcome is `unknown-reference` — which is exactly right: the signature passed, the ledger recorded it, and nothing was granted.

- [ ] **Step 7: Commit**

```bash
git add src/proxy.ts src/app/api/billing/webhook/route.ts src/lib/billing/subscription-data.ts
git commit -m "feat(billing): add the Paystack webhook and let it past the proxy"
```

---

## Task 11: The browser callback

**Files:**
- Create: `src/app/api/billing/callback/route.ts`

**Interfaces:**
- Consumes: `verifyTransaction`, `applyChargeSuccess`, `appUrl`.
- Produces: `GET /api/billing/callback?reference=…` → a redirect to `/settings/billing?status=…`.

- [ ] **Step 1: Implement**

Create `src/app/api/billing/callback/route.ts`:

```ts
import { NextResponse } from "next/server";
import { appUrl, verifyTransaction } from "@/lib/billing/paystack";
import { applyChargeSuccess } from "@/lib/billing/subscription-data";

export const dynamic = "force-dynamic";

/**
 * Where Paystack sends the buyer's browser back.
 *
 * This exists for instant feedback only. The webhook is the authority, and
 * correctness must never depend on the user's browser returning — they can
 * close the tab on the Paystack page and the charge still lands.
 */
export async function GET(req: Request) {
  const reference = new URL(req.url).searchParams.get("reference");
  const destination = (status: string) =>
    NextResponse.redirect(`${appUrl()}/settings/billing?status=${status}`);

  if (!reference) return destination("missing");

  try {
    const transaction = await verifyTransaction(reference);
    const outcome = await applyChargeSuccess(transaction);

    // "already-applied" means the webhook beat the browser back — from the
    // buyer's point of view that is a success, not an error.
    return destination(
      outcome === "activated" || outcome === "already-applied"
        ? "success"
        : "failed",
    );
  } catch (error) {
    console.error("[billing] callback verify failed", reference, error);
    // Deliberately not an error page: the webhook will still settle this.
    return destination("pending");
  }
}
```

- [ ] **Step 2: Typecheck and commit**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

```bash
git add src/app/api/billing/callback/route.ts
git commit -m "feat(billing): add the payment callback route"
```

---

## Task 12: Keeping the cached tier honest

**Files:**
- Modify: `src/lib/auth.ts` (`PROFILE_SELECT`, and the `jwt` callback after the revocation check)

**Interfaces:**
- Consumes: `resolveTier` from `@/lib/billing/entitlement`.
- Produces: no new exports; `User.tier` converges on the derived value within `PROFILE_TTL_MS`.

- [ ] **Step 1: Widen the profile read**

In `src/lib/auth.ts`, add to `PROFILE_SELECT`:

```ts
  tier: true,
  subscriptions: {
    where: { status: "ACTIVE" },
    select: { tier: true, status: true, startsAt: true, endsAt: true },
  },
```

- [ ] **Step 2: Reconcile in the jwt callback**

In the `jwt` callback, immediately **after** the `isSessionRevoked` block and **before** `cache.profile = {…}`, add:

```ts
        // User.tier is a cache of what the subscription rows grant. Refresh it
        // here rather than on a schedule: this read already runs at most once
        // per PROFILE_TTL_MS, so an expiry converges within that window and an
        // upgrade is instant because applyChargeSuccess writes it directly.
        const resolved = resolveTier(profile.subscriptions, new Date());
        if (resolved.tier !== profile.tier) {
          await db.user
            .update({
              where: { id: token.sub },
              data: { tier: resolved.tier, tierUpdatedAt: new Date() },
            })
            // A failed refresh must never cost the user their session — the
            // surrounding catch keeps the cached profile on a database blip,
            // and the next TTL expiry tries again.
            .catch(() => {});
        }
```

Add the import at the top of the file:

```ts
import { resolveTier } from "@/lib/billing/entitlement";
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 4: Verify against the database**

With the dev server running and signed in as a test student, insert an already-expired ACTIVE PREMIUM row for that user in the Supabase SQL Editor:

```sql
insert into "Subscription"
  (id, "userId", tier, period, source, status, reference, "amountKobo",
   currency, "startsAt", "endsAt", "createdAt", "updatedAt")
values
  ('test_expired', '<USER_ID>', 'PREMIUM', 'MONTHLY', 'COMP', 'ACTIVE',
   'pw_test_expired', 0, 'NGN', now() - interval '2 months',
   now() - interval '1 month', now(), now());

update "User" set tier = 'PREMIUM' where id = '<USER_ID>';
```

Reload the app, wait past the 60-second profile TTL, reload again, then:

```sql
select tier from "User" where id = '<USER_ID>';
```

Expected: `FREEMIUM` — the expired row granted nothing and the cache converged.

Clean up: `delete from "Subscription" where id = 'test_expired';`

- [ ] **Step 5: Commit**

```bash
git add src/lib/auth.ts
git commit -m "feat(billing): refresh the cached tier from subscription rows"
```

---

## Task 13: Admin comps and revocation

**Files:**
- Modify: `src/lib/admin-student-data.ts:214-222` (`setStudentTier`)
- Modify: `src/app/admin/api/students/[id]/tier/route.ts`
- Modify: `src/lib/validators.ts` (`studentTierSchema`)
- Modify: `src/components/admin/student-tier-control.tsx`

**Interfaces:**
- Consumes: `grantComp`, `revokeSubscriptions` from `@/lib/billing/subscription-data`.
- Produces: `setStudentTier(id, tier, options)` where `options = { period, grantedById, note }`.

- [ ] **Step 1: Extend the validator**

Replace `studentTierSchema` in `src/lib/validators.ts`:

```ts
export const studentTierSchema = z.object({
  tier: z.enum(SUBSCRIPTION_TIERS),
  // Ignored when the tier is FREEMIUM, which revokes rather than grants.
  period: z.enum(BILLING_PERIODS).default("MONTHLY"),
  note: z.string().trim().max(280).optional(),
});
```

- [ ] **Step 2: Rewrite `setStudentTier`**

Replace the body of `setStudentTier` in `src/lib/admin-student-data.ts`:

```ts
/**
 * The admin grant path. No longer writes User.tier directly — a comp is a
 * Subscription row like any other, so one resolver decides every student's
 * tier and comps expire instead of leaking free access forever.
 */
export async function setStudentTier(
  id: string,
  tier: SubscriptionTier,
  {
    period,
    grantedById,
    note,
  }: { period: BillingPeriod; grantedById: string; note?: string | null },
): Promise<void> {
  if (tier === "FREEMIUM") {
    // Freemium is the absence of a subscription, so setting it means revoke —
    // including paid terms. The console warns before asking for confirmation.
    await revokeSubscriptions(id);
    return;
  }

  await grantComp({ userId: id, tier, period, grantedById, note });
}
```

Add the imports:

```ts
import type { BillingPeriod } from "@/lib/subscription";
import { grantComp, revokeSubscriptions } from "@/lib/billing/subscription-data";
```

- [ ] **Step 3: Pass the new arguments from the route**

In `src/app/admin/api/students/[id]/tier/route.ts`, replace the `setStudentTier` call and the audit summary:

```ts
  await setStudentTier(id, parsed.data.tier, {
    period: parsed.data.period,
    grantedById: guard.actor.id,
    note: parsed.data.note ?? null,
  });

  const summary =
    parsed.data.tier === "FREEMIUM"
      ? `Revoked ${fullName(before)}'s subscription (was ${TIER_LABELS[before.tier]})`
      : `Comped ${fullName(before)} ${TIER_LABELS[parsed.data.tier]} for ${
          parsed.data.period === "YEARLY" ? "a year" : "a month"
        } (was ${TIER_LABELS[before.tier]})`;

  await recordAudit({
    actorId: guard.actor.id,
    action: "student.tier",
    entity: "User",
    entityId: id,
    summary,
  });
```

- [ ] **Step 4: Update the admin control**

In `src/components/admin/student-tier-control.tsx`:

Add to the imports:

```ts
import { BILLING_PERIODS, PERIOD_LABELS, type BillingPeriod } from "@/lib/subscription";
```

Add state beside the existing `next` state:

```ts
  const [period, setPeriod] = useState<BillingPeriod>("MONTHLY");
  const [note, setNote] = useState("");
```

Send them in the request body:

```ts
        body: JSON.stringify({ tier: next, period, note: note || undefined }),
```

Between the tier `<select>` and the `<Button>`, add the duration select, shown only when granting:

```tsx
          {next !== "FREEMIUM" && (
            <>
              <label htmlFor="period" className="sr-only">Duration</label>
              <select
                id="period"
                value={period}
                onChange={(e) => setPeriod(e.target.value as BillingPeriod)}
                className={INPUT_CLS}
              >
                {BILLING_PERIODS.map((value) => (
                  <option key={value} value={value}>{PERIOD_LABELS[value]}</option>
                ))}
              </select>
            </>
          )}

          <label htmlFor="tier-note" className="sr-only">Note</label>
          <input
            id="tier-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={280}
            placeholder="Why (optional)"
            className={INPUT_CLS}
          />
```

Replace the explanatory paragraph, so the revoke consequence is stated before the admin clicks:

```tsx
          <p className="w-full text-xs text-muted">
            {next === "FREEMIUM"
              ? "This ends every live subscription immediately, including one the student paid for. Refunds are handled in the Paystack dashboard. Recorded in the audit log."
              : "Grants a comped subscription that expires on its own. If the student already has time left, this is added on top. Recorded in the audit log."}
          </p>
```

- [ ] **Step 5: Typecheck and lint**

Run: `npx tsc --noEmit -p tsconfig.json && npx eslint src/components/admin/student-tier-control.tsx src/lib/admin-student-data.ts`
Expected: no errors.

- [ ] **Step 6: Verify by hand**

In the admin console, open a test student and comp them Premium for a month. Then:

```sql
select tier, source, status, "amountKobo", "endsAt", note
from "Subscription" where "userId" = '<USER_ID>' order by "createdAt" desc limit 1;
select tier from "User" where id = '<USER_ID>';
```

Expected: one `COMP` / `ACTIVE` / `PREMIUM` row with `amountKobo = 0` and `endsAt` about a month out, and `User.tier` = `PREMIUM`.

Now set the same student to Freemium and re-run both queries.
Expected: the row is `REVOKED` with `endsAt` at roughly now, and `User.tier` = `FREEMIUM`.

- [ ] **Step 7: Commit**

```bash
git add src/lib/admin-student-data.ts src/app/admin/api/students/\[id\]/tier/route.ts src/lib/validators.ts src/components/admin/student-tier-control.tsx
git commit -m "feat(billing): turn admin tier overrides into comped subscriptions"
```

---

## Task 14: The buying UI

**Files:**
- Create: `src/app/(dashboard)/settings/billing/page.tsx`
- Create: `src/components/billing/plan-picker.tsx`
- Modify: `src/components/landing/pricing.tsx`

**Interfaces:**
- Consumes: `currentEntitlement`, `isBillingEnabled`, `planFor`, `formatNaira`, `TIER_DISPLAY_NAMES`.
- Produces: the `/settings/billing` page.

- [ ] **Step 1: Point the landing page at the catalogue**

In `src/components/landing/pricing.tsx`, add the import:

```ts
import { planFor } from "@/lib/subscription";
```

Then replace the hardcoded `monthly` / `yearly` numbers in the `PLANS` array — keeping every other field, including the feature lists, exactly as it is. The prices become, in kobo-free naira:

```ts
// Free
    monthly: planFor("FREEMIUM", "MONTHLY").amountKobo / 100,
    yearly: planFor("FREEMIUM", "YEARLY").amountKobo / 100,
// Premium
    monthly: planFor("PREMIUM", "MONTHLY").amountKobo / 100,
    yearly: planFor("PREMIUM", "YEARLY").amountKobo / 100,
// Basic
    monthly: planFor("STANDARD", "MONTHLY").amountKobo / 100,
    yearly: planFor("STANDARD", "YEARLY").amountKobo / 100,
```

The rendered prices must not change — that is the point. Confirm on the landing page that Basic still reads ₦24,000 yearly and Premium ₦50,000.

- [ ] **Step 2: Build the picker**

Create `src/components/billing/plan-picker.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBanner } from "@/components/admin/status-banner";
import {
  BILLING_PERIODS,
  PERIOD_LABELS,
  TIER_DISPLAY_NAMES,
  formatNaira,
  planFor,
  type BillingPeriod,
  type SubscriptionTier,
} from "@/lib/subscription";

const BUYABLE: SubscriptionTier[] = ["STANDARD", "PREMIUM"];

export function PlanPicker({ enabled }: { enabled: boolean }) {
  const [period, setPeriod] = useState<BillingPeriod>("YEARLY");
  const [busy, setBusy] = useState<SubscriptionTier | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function buy(tier: SubscriptionTier) {
    setBusy(tier);
    setError(null);
    try {
      const res = await fetch("/api/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier, period }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not start the payment");
        return;
      }
      // Paystack hosts the payment page; we never touch card details.
      window.location.href = data.authorizationUrl;
    } catch {
      setError("Could not reach the server");
    } finally {
      setBusy(null);
    }
  }

  if (!enabled) {
    return (
      <StatusBanner
        tone="info"
        title="Payments are not available right now. Please check back shortly."
      />
    );
  }

  return (
    <div>
      {error && <StatusBanner tone="error" title={error} className="mb-4" />}

      <div className="flex gap-2" role="group" aria-label="Billing period">
        {BILLING_PERIODS.map((value) => (
          <Button
            key={value}
            variant={period === value ? "primary" : "outline"}
            onClick={() => setPeriod(value)}
          >
            {PERIOD_LABELS[value]}
          </Button>
        ))}
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {BUYABLE.map((tier) => {
          const plan = planFor(tier, period);
          return (
            <div
              key={tier}
              className="rounded-lg border border-border-strong bg-card p-4"
            >
              <h3 className="text-base font-bold text-foreground">
                {TIER_DISPLAY_NAMES[tier]}
              </h3>
              <p className="mt-1 text-2xl font-extrabold text-foreground">
                {formatNaira(plan.amountKobo)}
                <span className="ml-1 text-sm font-medium text-muted">
                  /{period === "YEARLY" ? "year" : "month"}
                </span>
              </p>
              <Button
                className="mt-4 w-full"
                onClick={() => buy(tier)}
                disabled={busy !== null}
              >
                {busy === tier ? "Starting…" : `Get ${TIER_DISPLAY_NAMES[tier]}`}
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

`src/components/ui/button.tsx` defines the variants `primary` (the default), `secondary`, `outline`, `ghost`, `success`, and `danger`. `StatusBanner` accepts the tones `error`, `success`, and `info` only.

- [ ] **Step 3: Build the page**

Create `src/app/(dashboard)/settings/billing/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { isBillingEnabled } from "@/lib/billing/paystack";
import { currentEntitlement } from "@/lib/billing/subscription-data";
import { TIER_DISPLAY_NAMES } from "@/lib/subscription";
import { PlanPicker } from "@/components/billing/plan-picker";
import { StatusBanner } from "@/components/admin/status-banner";

export const dynamic = "force-dynamic";

const NOTICES: Record<string, { tone: "success" | "error" | "info"; title: string }> = {
  success: { tone: "success", title: "Payment received — your plan is active." },
  failed: { tone: "error", title: "That payment did not go through. Nothing was charged." },
  pending: { tone: "info", title: "We are still confirming your payment. This page will show the new plan once it clears." },
  missing: { tone: "error", title: "We could not identify that payment." },
};

export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const session = await auth();
  const userId = session?.user?.id;
  if (!userId) redirect("/login");

  const { status } = await searchParams;
  const notice = status ? NOTICES[status] : undefined;
  const { tier, expiresAt } = await currentEntitlement(userId);

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <h1 className="text-2xl font-extrabold text-foreground">Billing</h1>

      {notice && (
        <StatusBanner tone={notice.tone} title={notice.title} className="mt-4" />
      )}

      <p className="mt-4 text-sm text-foreground">
        You are on <strong>{TIER_DISPLAY_NAMES[tier]}</strong>
        {expiresAt
          ? `, until ${expiresAt.toLocaleDateString("en-NG", {
              day: "numeric",
              month: "long",
              year: "numeric",
            })}.`
          : "."}
      </p>

      <div className="mt-8">
        <PlanPicker enabled={isBillingEnabled()} />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Typecheck, lint, build**

Run: `npx tsc --noEmit -p tsconfig.json && npx eslint src/components/billing src/app/\(dashboard\)/settings/billing && npm run build`
Expected: all pass. The build is what catches a client component importing a server-only module.

- [ ] **Step 5: End-to-end against Paystack test mode**

Set `PAYSTACK_SECRET_KEY` to your `sk_test_…` key and restart. Visit `/settings/billing`, choose Monthly, and buy Basic. Pay with the Paystack test card `4084 0840 8408 4081`, any future expiry, CVV `408`, OTP `123456`.

Expected: redirected back to `/settings/billing?status=success`, the page reads "You are on Basic, until <a month from today>", and:

```sql
select status, source, "amountKobo", "startsAt", "endsAt", channel
from "Subscription" where "userId" = '<USER_ID>' order by "createdAt" desc limit 1;
```

returns `ACTIVE` / `PAYSTACK` / `250000` with a channel and a one-month span.

Buy again immediately. Expected: `endsAt` moves out by a further month rather than resetting — stacking working against the real provider, not just in the unit test.

- [ ] **Step 6: Confirm the webhook path independently**

In the Paystack dashboard, set the test webhook URL to your tunnelled `/api/billing/webhook` and check the delivery log shows a `200` for `charge.success`. Then:

```sql
select "eventKey", type from "PaystackEvent" order by "receivedAt" desc limit 5;
```

Expected: a `charge.success` row for the reference. Use the dashboard's "resend" on that event and confirm it returns 200 without moving `endsAt` — idempotency proven against a real redelivery, which is the one case the unit tests cannot reach.

- [ ] **Step 7: Commit**

```bash
git add src/app/\(dashboard\)/settings/billing src/components/billing src/components/landing/pricing.tsx
git commit -m "feat(billing): add the billing settings page and wire pricing to the catalogue"
```

---

## Self-Review

**Spec coverage.** Every section maps to a task: data model → 6; plan catalogue → 1 and 14; module layout → 1–5, 7, 8; stacking → 2; resolution → 3; checkout → 9; callback → 11; webhook, idempotency, middleware and raw-body risks → 10; tier resolution at runtime → 12; admin comps and the revoke consequence → 13; configuration → 7; migration → 6; UI → 14; testing → 2–5.

**Two deviations from the spec, both deliberate:**

1. The spec listed `ABANDONED` as set "when a `PENDING` row is superseded". Task 8 implements exactly that, inside `createPendingSubscription`, rather than as its own task.
2. Task 10 Step 3 adds `forgetPaystackEvent`, which the spec does not mention. Without it, recording the event *before* applying it means a transient failure during apply is permanently swallowed as a duplicate on retry — the ledger would say handled when nothing was granted. Recording first is still correct; this makes the failure path recoverable.

**Not covered by automated tests, by design:** the Paystack HTTP client (Task 7) and the route handlers. Their logic lives in the pure modules that are tested; the routes are verified by the explicit manual steps in Tasks 9, 10, 13, and 14. Task 10 Steps 5–6 and Task 14 Step 6 exist specifically because the proxy exclusion and real redelivery are unreachable from unit tests and are the two failures that would otherwise be silent in production.

**Type consistency checked:** `VerifiedTransaction` is defined once in `settlement.ts` and imported by `paystack.ts` and `subscription-data.ts`. `EntitlementRow` matches the `ENTITLEMENT_SELECT` shape in Task 8 and the `subscriptions` select in Task 12. `setStudentTier`'s new third parameter is added in Task 13 Step 2 and supplied in Step 3 — its only two call sites.
