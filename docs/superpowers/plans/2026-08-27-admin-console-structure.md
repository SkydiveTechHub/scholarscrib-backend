# Admin Console Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the admin console around a grouped, server-first shell, add student management with suspension, and introduce a subscription tier seam plus a visible audit log.

**Architecture:** Every new admin section is server-rendered — `searchParams` flow through a pure `normalise*Filter()` in a lib, into one server query, into a server-rendered table. Pure logic (validation, normalisation, permissions, display shaping) lives in Prisma-free modules unit-tested by `.mts` scripts; Prisma queries live in sibling `*-data.ts` modules. Nothing is gated on tiers yet — a single `hasAtLeast` seam is built so entitlements can be defined later without touching call sites.

**Tech Stack:** Next.js 16.2.11 (App Router, Server Components), React 19.2.4, Prisma 6 + PostgreSQL (Supabase), NextAuth v5 beta (JWT sessions), Zod 4, Tailwind CSS 4, `react-icons/lu`, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-08-27-admin-console-structure-design.md`

## Global Constraints

- **Read the Next docs before writing framework code.** Per `AGENTS.md`, this Next.js version has breaking changes. Consult `node_modules/next/dist/docs/` — do not write App Router code from memory.
- **Pure modules import no Prisma and no React.** `subscription.ts`, `account-status.ts`, `admin-access.ts`, `admin-student.ts`, `admin-nav.ts`, `admin-audit-filter.ts` must stay database-free so their `.mts` tests run without a database.
- **Every new test script must be appended to the `test` script in `package.json`.** A test that is not registered does not run in CI.
- **Test command shape:** `node --import tsx --test --test-force-exit scripts/<name>.mts`
- **Every admin page calls its own guard.** `requireAdminPage()` or `requireOwnerPage()` at the top of every page component. The layout's check does not re-run on client-side navigation between admin routes (Partial Rendering), so the layout is not the wall.
- **Every admin API route calls `requireAdminApi()` or `requireOwnerApi()` first** and returns `guard.response` when `!guard.ok`.
- **Every mutation route calls `recordAudit()`** after the write succeeds.
- **Hiding a control is presentation, never authorization.** UI hiding and route enforcement are both required.
- **`prisma migrate` cannot reach Supabase from this machine.** Migrations are applied by hand through the Supabase SQL Editor. Migration `.sql` files must use **LF** line endings (`core.autocrlf=true` silently drifts Prisma checksums). The SQL Editor can report success on a half-applied batch — always verify via `information_schema` / `pg_enum` / `pg_indexes`, never by trusting the success message.
- **Tier values are exactly `FREEMIUM`, `STANDARD`, `PREMIUM`.**
- **Entitlements are out of scope.** Do not add any code that gates a feature on tier.
- **Do not rewrite `questions-client.tsx`.** It is explicitly out of scope.
- **Do not add nav entries without a page behind them.** No `Curriculum`, no `Billing` links this round.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `src/lib/subscription.ts` | Tier constants, ordering, `hasAtLeast`, display shaping. Pure. |
| `src/lib/account-status.ts` | Student account suspension + session-revocation rules. Pure. |
| `src/lib/admin-student.ts` | Student list filter normalisation, display shaping. Pure. |
| `src/lib/admin-student-data.ts` | Prisma queries for the student list, detail and deletion impact. |
| `src/lib/admin-audit-filter.ts` | Audit log filter normalisation. Pure. |
| `src/lib/admin-audit-data.ts` | Prisma query for the audit log list. |
| `src/components/admin/admin-table.tsx` | Shared table chrome. |
| `src/components/admin/empty-state.tsx` | Shared empty state. |
| `src/components/admin/pagination.tsx` | URL-driven server pagination. |
| `src/components/admin/detail-shell.tsx` | Breadcrumb + title + actions for detail pages. |
| `src/components/admin/admin-nav-more.tsx` | Mobile "More" bottom sheet. |
| `src/components/admin/badge.tsx` | Tone-coded pill for plan and status columns. |
| `src/components/admin/student-filter-bar.tsx` | URL-writing filters for the student list. |
| `src/components/admin/audit-filter-bar.tsx` | URL-writing filters for the audit log. |
| `src/components/admin/student-danger-zone.tsx` | Suspend / force sign-out / delete controls. |
| `src/components/admin/student-profile-form.tsx` | Inline profile editing. |
| `src/components/admin/student-tier-control.tsx` | Manual tier override. |
| `src/app/admin/(console)/students/page.tsx` | Student list. |
| `src/app/admin/(console)/students/[id]/page.tsx` | Student detail. |
| `src/app/admin/(console)/audit/page.tsx` | Audit log viewer. |
| `src/app/admin/api/students/[id]/route.ts` | `PATCH` profile, `DELETE` account. |
| `src/app/admin/api/students/[id]/status/route.ts` | `POST` suspend / reactivate. |
| `src/app/admin/api/students/[id]/tier/route.ts` | `POST` set tier. |
| `src/app/admin/api/students/[id]/force-signout/route.ts` | `POST` revoke sessions. |
| `scripts/test-subscription.mts` | Tier seam tests. |
| `scripts/test-account-status.mts` | Suspension / revocation tests. |
| `scripts/test-admin-nav.mts` | Grouped nav tests. |
| `scripts/test-admin-pagination.mts` | Page-window tests. |
| `scripts/test-admin-student.mts` | Student filter + validation tests. |
| `scripts/test-admin-audit-filter.mts` | Audit filter tests. |
| `prisma/migrations/20260827000000_subscription_tier/migration.sql` | Tier enum + columns. |
| `prisma/migrations/20260827000100_student_account_status/migration.sql` | Suspension columns. |

**Modified:** `prisma/schema.prisma`, `src/lib/admin-nav.ts`, `src/components/admin/admin-nav.tsx`, `src/lib/admin-access.ts`, `src/lib/admin-audit.ts`, `src/lib/auth.ts`, `src/lib/validators.ts`, `src/app/admin/(console)/page.tsx`, `src/app/admin/(console)/lessons/page.tsx`, `scripts/test-admin-access.mts`, `package.json`.

---

## Task 1: Subscription tier seam

**Files:**
- Create: `src/lib/subscription.ts`
- Create: `scripts/test-subscription.mts`
- Modify: `package.json` (the `test` script)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SUBSCRIPTION_TIERS: readonly ["FREEMIUM", "STANDARD", "PREMIUM"]`
  - `type SubscriptionTier = "FREEMIUM" | "STANDARD" | "PREMIUM"`
  - `TIER_LABELS: Record<SubscriptionTier, string>`
  - `isSubscriptionTier(value: string | undefined | null): value is SubscriptionTier`
  - `hasAtLeast(account: { tier: SubscriptionTier }, required: SubscriptionTier): boolean`
  - `describeTier(account: { tier: SubscriptionTier }): { label: string; tone: "neutral" | "info" | "success" }`

The tier union is declared here as a `const` array rather than imported from `@prisma/client`, matching the `CLASS_LEVELS` / `TERMS` pattern in `src/lib/curriculum-scope.ts`. That is what keeps this module Prisma-free and its test runnable without a database. Task 2 adds a Prisma enum with exactly these three members.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-subscription.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SUBSCRIPTION_TIERS,
  TIER_LABELS,
  describeTier,
  hasAtLeast,
  isSubscriptionTier,
} from "../src/lib/subscription";

test("the tiers are ordered cheapest to richest", () => {
  assert.deepEqual(SUBSCRIPTION_TIERS, ["FREEMIUM", "STANDARD", "PREMIUM"]);
});

test("a tier satisfies itself", () => {
  // Equal must pass, or every gate would demand a strict upgrade.
  for (const tier of SUBSCRIPTION_TIERS) {
    assert.equal(hasAtLeast({ tier }, tier), true, tier);
  }
});

test("a richer tier satisfies a poorer requirement", () => {
  assert.equal(hasAtLeast({ tier: "PREMIUM" }, "FREEMIUM"), true);
  assert.equal(hasAtLeast({ tier: "PREMIUM" }, "STANDARD"), true);
  assert.equal(hasAtLeast({ tier: "STANDARD" }, "FREEMIUM"), true);
});

test("a poorer tier does not satisfy a richer requirement", () => {
  assert.equal(hasAtLeast({ tier: "FREEMIUM" }, "STANDARD"), false);
  assert.equal(hasAtLeast({ tier: "FREEMIUM" }, "PREMIUM"), false);
  assert.equal(hasAtLeast({ tier: "STANDARD" }, "PREMIUM"), false);
});

test("every tier has a label", () => {
  for (const tier of SUBSCRIPTION_TIERS) {
    assert.equal(typeof TIER_LABELS[tier], "string");
    assert.ok(TIER_LABELS[tier].length > 0, tier);
  }
});

test("describeTier gives each tier a distinct tone", () => {
  const tones = SUBSCRIPTION_TIERS.map((tier) => describeTier({ tier }).tone);
  assert.equal(new Set(tones).size, SUBSCRIPTION_TIERS.length);
});

test("describeTier carries the label through", () => {
  assert.equal(describeTier({ tier: "PREMIUM" }).label, TIER_LABELS.PREMIUM);
});

test("isSubscriptionTier accepts only the three tiers", () => {
  // A hand-edited ?tier= must never reach Prisma as a where clause on an enum.
  assert.equal(isSubscriptionTier("STANDARD"), true);
  assert.equal(isSubscriptionTier("standard"), false);
  assert.equal(isSubscriptionTier("GOLD"), false);
  assert.equal(isSubscriptionTier(""), false);
  assert.equal(isSubscriptionTier(undefined), false);
  assert.equal(isSubscriptionTier(null), false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
node --import tsx --test --test-force-exit scripts/test-subscription.mts
```

Expected: FAIL — cannot find module `../src/lib/subscription`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/subscription.ts`:

```ts
/**
 * The subscription tier seam.
 *
 * Deliberately database-free — the tier union is declared here rather than
 * imported from `@prisma/client`, the same way `curriculum-scope.ts` declares
 * CLASS_LEVELS. That is what lets the rules be unit tested without a database.
 * The Prisma `SubscriptionTier` enum carries exactly these three members.
 *
 * What each tier UNLOCKS is deliberately not defined here yet. When that
 * decision is made it becomes one table in this file; call sites only ever ask
 * `hasAtLeast`, so none of them change.
 *
 * See docs/superpowers/specs/2026-08-27-admin-console-structure-design.md
 */

export const SUBSCRIPTION_TIERS = ["FREEMIUM", "STANDARD", "PREMIUM"] as const;

export type SubscriptionTier = (typeof SUBSCRIPTION_TIERS)[number];

export const TIER_LABELS: Record<SubscriptionTier, string> = {
  FREEMIUM: "Freemium",
  STANDARD: "Standard",
  PREMIUM: "Premium",
};

/** Rank, not identity — comparisons must survive a tier being inserted later. */
const TIER_RANK: Record<SubscriptionTier, number> = {
  FREEMIUM: 0,
  STANDARD: 1,
  PREMIUM: 2,
};

const TIER_TONE: Record<SubscriptionTier, "neutral" | "info" | "success"> = {
  FREEMIUM: "neutral",
  STANDARD: "info",
  PREMIUM: "success",
};

export function isSubscriptionTier(
  value: string | undefined | null,
): value is SubscriptionTier {
  return (
    typeof value === "string" &&
    (SUBSCRIPTION_TIERS as readonly string[]).includes(value)
  );
}

/**
 * The single predicate every future entitlement gate calls.
 *
 * Equal tiers pass: a STANDARD feature is available to a STANDARD subscriber.
 */
export function hasAtLeast(
  account: { tier: SubscriptionTier },
  required: SubscriptionTier,
): boolean {
  return TIER_RANK[account.tier] >= TIER_RANK[required];
}

export function describeTier(account: { tier: SubscriptionTier }): {
  label: string;
  tone: "neutral" | "info" | "success";
} {
  return { label: TIER_LABELS[account.tier], tone: TIER_TONE[account.tier] };
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
node --import tsx --test --test-force-exit scripts/test-subscription.mts
```

Expected: PASS — 8 tests.

- [ ] **Step 5: Register the test script**

In `package.json`, append ` scripts/test-subscription.mts` to the end of the `test` script value (it is one long space-separated list of `.mts` paths).

- [ ] **Step 6: Verify the full suite still passes**

```bash
npm test
```

Expected: PASS, including the new `test-subscription.mts` file.

- [ ] **Step 7: Commit**

```bash
git add src/lib/subscription.ts scripts/test-subscription.mts package.json
git commit -m "feat(subscription): add tier seam with FREEMIUM/STANDARD/PREMIUM"
```

---

## Task 2: Schema and migrations

**Files:**
- Modify: `prisma/schema.prisma` (the `User` model, plus a new enum)
- Create: `prisma/migrations/20260827000000_subscription_tier/migration.sql`
- Create: `prisma/migrations/20260827000100_student_account_status/migration.sql`

**Interfaces:**
- Consumes: the tier names from Task 1.
- Produces: `User.tier`, `User.tierUpdatedAt`, `User.isActive`, `User.suspendedAt`, `User.suspendedReason`, `User.sessionsValidFrom`; the Prisma enum `SubscriptionTier`.

Both migrations are additive with defaults, so they are safe against live data.

- [ ] **Step 1: Add the enum to the schema**

In `prisma/schema.prisma`, next to the other enums (near `enum Track`), add:

```prisma
enum SubscriptionTier {
  FREEMIUM
  STANDARD
  PREMIUM
}
```

- [ ] **Step 2: Add the columns to the User model**

In `prisma/schema.prisma`, inside `model User`, add after the `school` relation line and before the `// Relations` comment:

```prisma
  // Subscription. Tier is the denormalised read column: when billing lands, a
  // Subscription model becomes the source of truth that WRITES this, and every
  // gate written against hasAtLeast() keeps working unchanged.
  tier          SubscriptionTier @default(FREEMIUM)
  tierUpdatedAt DateTime?

  // Account status. Suspension is reversible; deletion is not.
  isActive        Boolean   @default(true)
  suspendedAt     DateTime?
  suspendedReason String?
  /** Tokens issued before this instant are rejected. Powers force sign-out. */
  sessionsValidFrom DateTime?
```

Then, in the same model's index block at the bottom, add alongside the existing `@@index([classLevel, track])`:

```prisma
  @@index([tier])
  @@index([isActive])
```

- [ ] **Step 3: Verify the schema is valid and the client generates**

```bash
npx prisma validate
npx prisma generate
```

Expected: both succeed. `npx prisma generate` writes the client locally and does **not** need a database connection.

- [ ] **Step 4: Write the first migration file with LF endings**

Create the directory `prisma/migrations/20260827000000_subscription_tier/` and write `migration.sql`:

```sql
-- CreateEnum
CREATE TYPE "SubscriptionTier" AS ENUM ('FREEMIUM', 'STANDARD', 'PREMIUM');

-- AlterTable
ALTER TABLE "User" ADD COLUMN "tier" "SubscriptionTier" NOT NULL DEFAULT 'FREEMIUM';
ALTER TABLE "User" ADD COLUMN "tierUpdatedAt" TIMESTAMP(3);

-- CreateIndex
CREATE INDEX "User_tier_idx" ON "User"("tier");
```

- [ ] **Step 5: Write the second migration file with LF endings**

Create `prisma/migrations/20260827000100_student_account_status/migration.sql`:

```sql
-- AlterTable
ALTER TABLE "User" ADD COLUMN "isActive" BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE "User" ADD COLUMN "suspendedAt" TIMESTAMP(3);
ALTER TABLE "User" ADD COLUMN "suspendedReason" TEXT;
ALTER TABLE "User" ADD COLUMN "sessionsValidFrom" TIMESTAMP(3);

-- CreateIndex
CREATE INDEX "User_isActive_idx" ON "User"("isActive");
```

- [ ] **Step 6: Verify both files are LF, not CRLF**

```bash
grep -c $'\r' prisma/migrations/20260827000000_subscription_tier/migration.sql
grep -c $'\r' prisma/migrations/20260827000100_student_account_status/migration.sql
```

Expected: `0` for both (grep exits 1 when the count is 0, which is fine). If either is non-zero, rewrite the file with LF endings — `core.autocrlf=true` will otherwise silently drift the Prisma checksum and every later `migrate` command will report a modified migration.

- [ ] **Step 7: Apply both migrations through the Supabase SQL Editor**

`prisma migrate deploy` cannot reach the database from this machine. Open the Supabase SQL Editor and run the contents of the two `migration.sql` files, **in order**, as two separate executions. Do not run them as one batch — a half-applied batch is the failure mode being avoided.

- [ ] **Step 8: Verify the catalog, not the success message**

Run in the SQL Editor:

```sql
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'User'
  AND column_name IN ('tier','tierUpdatedAt','isActive','suspendedAt','suspendedReason','sessionsValidFrom')
ORDER BY column_name;

SELECT e.enumlabel
FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
WHERE t.typname = 'SubscriptionTier'
ORDER BY e.enumsortorder;

SELECT indexname FROM pg_indexes
WHERE tablename = 'User' AND indexname IN ('User_tier_idx','User_isActive_idx');
```

Expected: **6 column rows**; enum labels `FREEMIUM, STANDARD, PREMIUM` in that order; **2 index rows**. If any count is short, the batch half-applied — re-run only the missing statements and re-verify. Do not proceed until all three queries return the expected counts.

- [ ] **Step 9: Reconcile `_prisma_migrations`**

Compute each file's checksum locally:

```bash
sha256sum prisma/migrations/20260827000000_subscription_tier/migration.sql
sha256sum prisma/migrations/20260827000100_student_account_status/migration.sql
```

Then, in the SQL Editor, insert a row per migration, substituting the two checksums:

```sql
INSERT INTO "_prisma_migrations"
  (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
VALUES
  (gen_random_uuid()::text, '<checksum-1>', now(), '20260827000000_subscription_tier', NULL, NULL, now(), 1),
  (gen_random_uuid()::text, '<checksum-2>', now(), '20260827000100_student_account_status', NULL, NULL, now(), 1);
```

Verify:

```sql
SELECT migration_name, applied_steps_count, rolled_back_at
FROM "_prisma_migrations" ORDER BY started_at DESC LIMIT 3;
```

Expected: both new migration names present, `applied_steps_count = 1`, `rolled_back_at` null.

- [ ] **Step 10: Confirm the generated client exposes the new fields**

```bash
npx tsc --noEmit -p tsconfig.json
```

Expected: PASS. (If the client is stale, re-run `npx prisma generate`.)

- [ ] **Step 11: Commit**

```bash
git add prisma/schema.prisma prisma/migrations
git commit -m "feat(db): add subscription tier and student account status columns"
```

---

## Task 3: Account status rules

**Files:**
- Create: `src/lib/account-status.ts`
- Create: `scripts/test-account-status.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type AccountStatusFields = { isActive: boolean; sessionsValidFrom: Date | null }`
  - `isSessionRevoked(account: AccountStatusFields, tokenIssuedAtSeconds: number | undefined): boolean`
  - `describeAccountStatus(account: { isActive: boolean }): { label: string; tone: "success" | "warning" }`
  - `ACCOUNT_STATUSES: readonly ["active", "suspended"]`
  - `type AccountStatus = "active" | "suspended"`
  - `isAccountStatus(value: string | undefined | null): value is AccountStatus`

This is the pure half of suspension enforcement. Task 12 wires it into `src/lib/auth.ts`; keeping the rule here is what makes the enforcement testable without booting NextAuth.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-account-status.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  describeAccountStatus,
  isAccountStatus,
  isSessionRevoked,
} from "../src/lib/account-status";

// Token issued-at claims are seconds since the epoch, not milliseconds.
const ISSUED = Math.floor(new Date("2026-08-27T10:00:00Z").getTime() / 1000);
const BEFORE = new Date("2026-08-27T09:00:00Z");
const AFTER = new Date("2026-08-27T11:00:00Z");

test("an active account with no revocation stamp keeps its session", () => {
  assert.equal(
    isSessionRevoked({ isActive: true, sessionsValidFrom: null }, ISSUED),
    false,
  );
});

test("a suspended account loses its session regardless of issue time", () => {
  assert.equal(
    isSessionRevoked({ isActive: false, sessionsValidFrom: null }, ISSUED),
    true,
  );
});

test("a token issued before the revocation stamp is dead", () => {
  assert.equal(
    isSessionRevoked({ isActive: true, sessionsValidFrom: AFTER }, ISSUED),
    true,
  );
});

test("a token issued after the revocation stamp survives", () => {
  // Signing in again after a force sign-out has to work, or the account is
  // permanently locked out rather than merely signed out.
  assert.equal(
    isSessionRevoked({ isActive: true, sessionsValidFrom: BEFORE }, ISSUED),
    false,
  );
});

test("a token issued exactly at the stamp is dead", () => {
  const exact = Math.floor(AFTER.getTime() / 1000);
  // Second-granularity claims mean a same-second token could be the revoked
  // one. Treat the boundary as revoked rather than let it through.
  assert.equal(
    isSessionRevoked({ isActive: true, sessionsValidFrom: AFTER }, exact),
    true,
  );
});

test("a token with no issued-at claim is treated as revoked when a stamp exists", () => {
  // Cannot prove it is newer than the revocation, so it does not get the
  // benefit of the doubt.
  assert.equal(
    isSessionRevoked({ isActive: true, sessionsValidFrom: AFTER }, undefined),
    true,
  );
});

test("a token with no issued-at claim survives when no stamp exists", () => {
  assert.equal(
    isSessionRevoked({ isActive: true, sessionsValidFrom: null }, undefined),
    false,
  );
});

test("status descriptions distinguish active from suspended", () => {
  assert.equal(describeAccountStatus({ isActive: true }).tone, "success");
  assert.equal(describeAccountStatus({ isActive: false }).tone, "warning");
  assert.notEqual(
    describeAccountStatus({ isActive: true }).label,
    describeAccountStatus({ isActive: false }).label,
  );
});

test("isAccountStatus accepts only the two statuses", () => {
  assert.equal(isAccountStatus("active"), true);
  assert.equal(isAccountStatus("suspended"), true);
  assert.equal(isAccountStatus("ACTIVE"), false);
  assert.equal(isAccountStatus("deleted"), false);
  assert.equal(isAccountStatus(undefined), false);
  assert.equal(isAccountStatus(null), false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
node --import tsx --test --test-force-exit scripts/test-account-status.mts
```

Expected: FAIL — cannot find module `../src/lib/account-status`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/account-status.ts`:

```ts
/**
 * Student account status rules, as pure functions.
 *
 * Suspension has to bite on a session that is already live, not only at the
 * next sign-in: student sessions are JWT with a 60s profile refresh
 * (PROFILE_TTL_MS in auth.ts), so a suspended student would otherwise keep
 * browsing on a token nobody re-checks. `isSessionRevoked` is the rule the jwt
 * callback applies on that refresh; keeping it here is what makes it testable
 * without booting NextAuth.
 *
 * See docs/superpowers/specs/2026-08-27-admin-console-structure-design.md
 */

export const ACCOUNT_STATUSES = ["active", "suspended"] as const;

export type AccountStatus = (typeof ACCOUNT_STATUSES)[number];

export type AccountStatusFields = {
  isActive: boolean;
  sessionsValidFrom: Date | null;
};

export function isAccountStatus(
  value: string | undefined | null,
): value is AccountStatus {
  return (
    typeof value === "string" &&
    (ACCOUNT_STATUSES as readonly string[]).includes(value)
  );
}

/**
 * Whether a live token should be rejected.
 *
 * @param tokenIssuedAtSeconds the JWT `iat` claim — SECONDS since the epoch,
 *   not milliseconds. Undefined when the claim is absent.
 */
export function isSessionRevoked(
  account: AccountStatusFields,
  tokenIssuedAtSeconds: number | undefined,
): boolean {
  if (!account.isActive) return true;
  if (account.sessionsValidFrom === null) return false;

  // No issued-at claim means the token cannot prove it is newer than the
  // revocation, so it does not get the benefit of the doubt.
  if (tokenIssuedAtSeconds === undefined) return true;

  const validFromSeconds = Math.floor(account.sessionsValidFrom.getTime() / 1000);

  // `<=` not `<`: iat has second granularity, so a token stamped in the same
  // second as the revocation could be the one being revoked.
  return tokenIssuedAtSeconds <= validFromSeconds;
}

export function describeAccountStatus(account: { isActive: boolean }): {
  label: string;
  tone: "success" | "warning";
} {
  return account.isActive
    ? { label: "Active", tone: "success" }
    : { label: "Suspended", tone: "warning" };
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
node --import tsx --test --test-force-exit scripts/test-account-status.mts
```

Expected: PASS — 9 tests.

- [ ] **Step 5: Register the test and run the suite**

Append ` scripts/test-account-status.mts` to the `test` script in `package.json`, then:

```bash
npm test
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/account-status.ts scripts/test-account-status.mts package.json
git commit -m "feat(account): add suspension and session revocation rules"
```

---

## Task 4: Grouped navigation

**Files:**
- Modify: `src/lib/admin-nav.ts` (full rewrite of the exported shape)
- Modify: `src/components/admin/admin-nav.tsx`
- Create: `src/components/admin/admin-nav-more.tsx`
- Create: `scripts/test-admin-nav.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type AdminNavItem = { name: string; href: string; icon: IconType; ownerOnly?: boolean }`
  - `type AdminNavGroup = { label: string; items: readonly AdminNavItem[] }`
  - `ADMIN_NAV_GROUPS: readonly AdminNavGroup[]`
  - `MOBILE_NAV_HREFS: readonly string[]` — the three real routes on the mobile bar
  - `visibleGroups(isOwner: boolean): AdminNavGroup[]`
  - `visibleItems(isOwner: boolean): AdminNavItem[]` — flattened
  - `mobileBarItems(isOwner: boolean): AdminNavItem[]`
  - `moreSheetGroups(isOwner: boolean): AdminNavGroup[]`

`ADMIN_NAV` (the old flat array) is removed. `src/components/admin/admin-nav.tsx` is its only consumer.

The `/admin/students` and `/admin/audit` pages do not exist until Tasks 9 and 14. The nav's own rule is that every entry has a page behind it, so those two entries are added in **Step 7 of Task 9** and **Step 6 of Task 14** respectively — this task ships the grouped structure with only the routes that already exist.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-nav.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ADMIN_NAV_GROUPS,
  mobileBarItems,
  moreSheetGroups,
  visibleGroups,
  visibleItems,
} from "../src/lib/admin-nav";

test("every nav item has a non-empty name and href", () => {
  // An earlier version listed routeless links — three links straight to a 404.
  for (const group of ADMIN_NAV_GROUPS) {
    assert.ok(group.label.length > 0);
    assert.ok(group.items.length > 0, `${group.label} is empty`);
    for (const item of group.items) {
      assert.ok(item.name.length > 0);
      assert.ok(item.href.startsWith("/admin"), item.href);
    }
  }
});

test("hrefs are unique across all groups", () => {
  const hrefs = ADMIN_NAV_GROUPS.flatMap((g) => g.items.map((i) => i.href));
  assert.equal(new Set(hrefs).size, hrefs.length);
});

test("a non-owner never sees an owner-only item", () => {
  const items = visibleItems(false);
  assert.equal(items.some((i) => i.ownerOnly), false);
});

test("an owner sees every item", () => {
  const all = ADMIN_NAV_GROUPS.flatMap((g) => g.items);
  assert.equal(visibleItems(true).length, all.length);
});

test("a group that becomes empty for a non-owner is dropped entirely", () => {
  // A group label with nothing under it reads as a broken section.
  for (const group of visibleGroups(false)) {
    assert.ok(group.items.length > 0, group.label);
  }
});

test("the mobile bar is always four slots", () => {
  // Three routes plus More. A bottom bar cannot hold grouped navigation.
  assert.equal(mobileBarItems(true).length, 3);
  assert.equal(mobileBarItems(false).length, 3);
});

test("no route is orphaned on mobile", () => {
  for (const isOwner of [true, false]) {
    const reachable = new Set([
      ...mobileBarItems(isOwner).map((i) => i.href),
      ...moreSheetGroups(isOwner).flatMap((g) => g.items.map((i) => i.href)),
    ]);
    for (const item of visibleItems(isOwner)) {
      assert.ok(reachable.has(item.href), `${item.href} unreachable on mobile`);
    }
  }
});

test("the More sheet never repeats what is already on the bar", () => {
  const bar = new Set(mobileBarItems(true).map((i) => i.href));
  for (const group of moreSheetGroups(true)) {
    for (const item of group.items) {
      assert.equal(bar.has(item.href), false, item.href);
    }
  }
});

test("the More sheet hides owner-only items from a non-owner", () => {
  const hrefs = moreSheetGroups(false).flatMap((g) => g.items.map((i) => i.href));
  assert.equal(hrefs.includes("/admin/team"), false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-nav.mts
```

Expected: FAIL — `ADMIN_NAV_GROUPS` is not exported.

- [ ] **Step 3: Rewrite `src/lib/admin-nav.ts`**

Replace the whole file:

```ts
import {
  LuBookOpen,
  LuDatabase,
  LuLayoutDashboard,
  LuUsers,
} from "react-icons/lu";
import type { IconType } from "react-icons";

export type AdminNavItem = {
  name: string;
  href: string;
  icon: IconType;
  /** Hidden from non-owners. The page and its routes also enforce this. */
  ownerOnly?: boolean;
};

export type AdminNavGroup = {
  label: string;
  items: readonly AdminNavItem[];
};

// Every entry must have a page behind it. An earlier version listed Subjects,
// Users and Lessons with no routes — three links straight to a 404. Curriculum
// and Billing are deliberately absent for the same reason.
//
// Import is not a top-level entry: it is an action inside Questions. The route
// /admin/questions/import is unchanged.
export const ADMIN_NAV_GROUPS: readonly AdminNavGroup[] = [
  {
    label: "Overview",
    items: [{ name: "Dashboard", href: "/admin", icon: LuLayoutDashboard }],
  },
  {
    label: "Content",
    items: [
      { name: "Questions", href: "/admin/questions", icon: LuDatabase },
      { name: "Lessons", href: "/admin/lessons", icon: LuBookOpen },
    ],
  },
  {
    label: "People",
    items: [
      { name: "Team", href: "/admin/team", icon: LuUsers, ownerOnly: true },
    ],
  },
];

// The three real routes on the mobile bar; the fourth slot is "More". Task 9
// swaps Lessons for Students here once /admin/students exists — an href with
// no routed entry is skipped, so this list only ever names live routes.
export const MOBILE_NAV_HREFS: readonly string[] = [
  "/admin",
  "/admin/questions",
  "/admin/lessons",
];

function permitted(item: AdminNavItem, isOwner: boolean): boolean {
  return !item.ownerOnly || isOwner;
}

/**
 * Groups an actor may see, with empty groups dropped — a group label with
 * nothing under it reads as a broken section rather than an absent one.
 */
export function visibleGroups(isOwner: boolean): AdminNavGroup[] {
  return ADMIN_NAV_GROUPS.map((group) => ({
    label: group.label,
    items: group.items.filter((item) => permitted(item, isOwner)),
  })).filter((group) => group.items.length > 0);
}

export function visibleItems(isOwner: boolean): AdminNavItem[] {
  return visibleGroups(isOwner).flatMap((group) => [...group.items]);
}

/**
 * The bar keeps a fixed shape whoever is looking, so the layout does not shift
 * between an owner and a regular admin. Entries not yet routed are skipped.
 */
export function mobileBarItems(isOwner: boolean): AdminNavItem[] {
  const items = visibleItems(isOwner);
  return MOBILE_NAV_HREFS.map((href) =>
    items.find((item) => item.href === href),
  ).filter((item): item is AdminNavItem => item !== undefined);
}

/** Everything the actor may see that the bar does not already show. */
export function moreSheetGroups(isOwner: boolean): AdminNavGroup[] {
  const onBar = new Set(mobileBarItems(isOwner).map((item) => item.href));
  return visibleGroups(isOwner)
    .map((group) => ({
      label: group.label,
      items: group.items.filter((item) => !onBar.has(item.href)),
    }))
    .filter((group) => group.items.length > 0);
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-nav.mts
```

Expected: PASS — 9 tests.

- [ ] **Step 5: Create the More sheet component**

Create `src/components/admin/admin-nav-more.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LuEllipsis, LuX } from "react-icons/lu";
import { cn } from "@/lib/utils";
import { moreSheetGroups } from "@/lib/admin-nav";

const LABEL_CLS = "text-[11px] font-semibold uppercase tracking-wider text-muted";

export function AdminNavMore({ isOwner }: { isOwner: boolean }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const groups = moreSheetGroups(isOwner);

  // A route change must close the sheet, or it covers the page just opened.
  useEffect(() => setOpen(false), [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const containsCurrent = groups.some((group) =>
    group.items.some((item) => item.href === pathname),
  );

  if (groups.length === 0) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={cn(
          "flex flex-col items-center gap-0.5 rounded-lg px-3 py-1 text-xs font-semibold transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
          containsCurrent ? "text-primary" : "text-muted",
        )}
      >
        <LuEllipsis className="h-5 w-5" />
        More
      </button>

      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-black/50"
          />
          <div
            role="dialog"
            aria-label="More admin sections"
            className="absolute inset-x-0 bottom-0 max-h-[70vh] overflow-y-auto rounded-t-2xl border-t border-border bg-card p-4 pb-8"
          >
            <div className="mb-3 flex items-center justify-between">
              <span className={LABEL_CLS}>More</span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close menu"
                className="rounded-lg p-1.5 text-muted hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
              >
                <LuX className="h-5 w-5" />
              </button>
            </div>

            <div className="flex flex-col gap-4">
              {groups.map((group) => (
                <div key={group.label}>
                  <p className={LABEL_CLS}>{group.label}</p>
                  <div className="mt-1.5 space-y-0.5">
                    {group.items.map((item) => (
                      <Link
                        key={item.href}
                        href={item.href}
                        aria-current={item.href === pathname ? "page" : undefined}
                        className={cn(
                          "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                          item.href === pathname
                            ? "bg-secondary text-foreground"
                            : "text-muted hover:bg-secondary hover:text-foreground",
                        )}
                      >
                        <item.icon className="h-4 w-4 flex-shrink-0" />
                        {item.name}
                      </Link>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 6: Rewrite `src/components/admin/admin-nav.tsx`**

Replace the whole file:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { mobileBarItems, visibleGroups } from "@/lib/admin-nav";
import { AdminNavMore } from "@/components/admin/admin-nav-more";

const LABEL_CLS = "text-[11px] font-semibold uppercase tracking-wider text-muted";

export function AdminNav({
  variant,
  isOwner,
}: {
  variant: "sidebar" | "mobile";
  isOwner: boolean;
}) {
  const pathname = usePathname();

  // Exact match only. Prefix matching would light "Questions" while the user
  // is on "Questions › Import", and "Overview" on every admin page.
  const isActive = (href: string) => pathname === href;

  if (variant === "mobile") {
    return (
      <nav
        aria-label="Admin"
        className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-card lg:hidden"
      >
        <div className="flex items-center justify-around py-2">
          {mobileBarItems(isOwner).map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item.href) ? "page" : undefined}
              className={cn(
                "flex flex-col items-center gap-0.5 rounded-lg px-3 py-1 text-xs font-semibold transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                isActive(item.href) ? "text-primary" : "text-muted",
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.name}
            </Link>
          ))}
          <AdminNavMore isOwner={isOwner} />
        </div>
      </nav>
    );
  }

  return (
    <nav aria-label="Admin" className="space-y-5 p-3">
      {visibleGroups(isOwner).map((group) => (
        <div key={group.label}>
          <p className={cn(LABEL_CLS, "px-3 pb-1.5")}>{group.label}</p>
          <div className="space-y-0.5">
            {group.items.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive(item.href) ? "page" : undefined}
                className={cn(
                  "relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-semibold transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                  isActive(item.href)
                    ? "bg-secondary text-foreground"
                    : "text-muted hover:bg-secondary hover:text-foreground",
                )}
              >
                {isActive(item.href) && (
                  // A left rule rather than the student app's soft pill — the
                  // admin reads as an instrument panel.
                  <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-primary" />
                )}
                <item.icon className="h-4 w-4 flex-shrink-0" />
                {item.name}
              </Link>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}
```

- [ ] **Step 7: Add the Import action to the Questions page header**

In `src/app/admin/(console)/questions/questions-client.tsx`, find the `PageHeader` in `AdminQuestionsPageInner` (not the fallback) and give it an `action` containing both existing entry points, so removing Import from the nav does not strand the route:

```tsx
action={
  <div className="flex flex-wrap gap-2">
    <Link href="/admin/questions/import" className={buttonClass({ variant: "secondary" })}>
      Import
    </Link>
    <Link href="/admin/questions/new" className={buttonClass()}>
      New question
    </Link>
  </div>
}
```

If a "New question" link already exists in that header, keep the existing one and add only the Import link beside it. `Link` and `buttonClass` are already imported in this file.

- [ ] **Step 8: Add the Upload action to the Lessons page header**

In `src/app/admin/(console)/lessons/page.tsx`, add to the existing `PageHeader`:

```tsx
action={
  <Link href="/admin/lessons/upload" className={buttonClass()}>
    Upload note
  </Link>
}
```

`Link` and `buttonClass` are already imported in this file.

- [ ] **Step 9: Verify the build and the suite**

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm test
```

Expected: all three PASS. Confirm by eye that `ADMIN_NAV` has no remaining references:

```bash
grep -rn "ADMIN_NAV\b" src/
```

Expected: no output.

- [ ] **Step 10: Commit**

```bash
git add src/lib/admin-nav.ts src/components/admin/admin-nav.tsx \
  src/components/admin/admin-nav-more.tsx scripts/test-admin-nav.mts \
  package.json "src/app/admin/(console)/lessons/page.tsx" \
  "src/app/admin/(console)/questions/questions-client.tsx"
git commit -m "feat(admin): group the console navigation into labelled sections"
```

---

## Task 5: Shared table and empty-state primitives

**Files:**
- Create: `src/components/admin/admin-table.tsx`
- Create: `src/components/admin/empty-state.tsx`
- Modify: `src/app/admin/(console)/page.tsx`
- Modify: `src/app/admin/(console)/lessons/page.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TH_CLS: string` — the shared uppercase micro-label class
  - `AdminTable({ caption, children, className })` — wraps `<table>` in the bordered card
  - `AdminTh({ children, align, scope })` — `align` is `"left" | "right"`, default `"left"`
  - `AdminTr({ children })`
  - `AdminTd({ children, align, className })`
  - `EmptyState({ title, message, action, variant })` — `variant` is `"dashed" | "plain"`, default `"dashed"`

This is a pure refactor: no behaviour changes. Its verification is that the type check, lint and existing suite all still pass, and that no `TH_CLS` definitions remain outside the primitive.

- [ ] **Step 1: Create the table primitive**

Create `src/components/admin/admin-table.tsx`:

```tsx
import { cn } from "@/lib/utils";

/**
 * The admin's table chrome, in one place.
 *
 * Overview, Lessons and Questions each declared their own copy of this class
 * and their own border treatment, so a change to one silently diverged from
 * the others.
 */
export const TH_CLS =
  "text-[11px] font-semibold uppercase tracking-wider text-muted";

export function AdminTable({
  caption,
  children,
  className,
}: {
  /** Screen-reader description of what the table lists. Required. */
  caption: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "overflow-x-auto rounded-lg border border-border-strong bg-card",
        className,
      )}
    >
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function AdminTh({
  children,
  align = "left",
  scope = "col",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  scope?: "col" | "row";
}) {
  return (
    <th
      scope={scope}
      className={cn(
        "px-4 py-2.5",
        align === "right" ? "text-right" : "text-left",
        TH_CLS,
      )}
    >
      {children}
    </th>
  );
}

export function AdminTr({ children }: { children: React.ReactNode }) {
  return (
    <tr className="border-b border-border-strong last:border-0">{children}</tr>
  );
}

export function AdminTd({
  children,
  align = "left",
  className,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <td
      className={cn(
        "px-4 py-2.5",
        align === "right" ? "text-right" : "text-left",
        className,
      )}
    >
      {children}
    </td>
  );
}
```

- [ ] **Step 2: Create the empty-state primitive**

Create `src/components/admin/empty-state.tsx`:

```tsx
import { cn } from "@/lib/utils";

/**
 * One empty state for the console.
 *
 * "Choose a subject", "no questions yet" and "no students match" were three
 * different treatments of the same moment.
 */
export function EmptyState({
  title,
  message,
  action,
  variant = "dashed",
  className,
}: {
  title: string;
  message?: string;
  action?: React.ReactNode;
  variant?: "dashed" | "plain";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg px-4 py-10 text-center",
        variant === "dashed"
          ? "border border-dashed border-border-strong bg-card"
          : "",
        className,
      )}
    >
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {message && (
        <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">{message}</p>
      )}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}
```

- [ ] **Step 3: Migrate the Overview page onto the primitives**

In `src/app/admin/(console)/page.tsx`:

1. Delete the local `const HEADING_CLS = ...` declaration and import the shared class plus components:
   ```tsx
   import { AdminTable, AdminTd, AdminTh, AdminTr, TH_CLS } from "@/components/admin/admin-table";
   ```
2. Replace every use of `HEADING_CLS` with `TH_CLS`.
3. In the `StatTable` helper, replace the hand-rolled `<div className="mt-2 overflow-hidden rounded-lg ..."><table ...><caption .../>` wrapper with `<AdminTable caption={caption} className="mt-2">`, and replace the `<th>` / `<tr>` / `<td>` elements in its `<thead>` and `<tbody>` with `AdminTh` / `AdminTr` / `AdminTd`, passing `align="right"` where the original used `text-right`.

Keep every `aria-label`, `href` and `tabular-nums` class exactly as it is — this step must not change what the page renders.

- [ ] **Step 4: Migrate the Lessons page onto the primitives**

In `src/app/admin/(console)/lessons/page.tsx`:

1. Delete the local `const TH_CLS = ...` declaration and import the shared one from `@/components/admin/admin-table` alongside the table components.
2. Replace the table markup with `AdminTable` / `AdminTh` / `AdminTr` / `AdminTd` as above.
3. Replace the "Choose a subject to list its topics." paragraph with:
   ```tsx
   <EmptyState
     title="Choose a subject"
     message="Pick a subject above to list its topics and see which have an authored lesson note."
   />
   ```
   importing `EmptyState` from `@/components/admin/empty-state`.

- [ ] **Step 5: Verify nothing regressed**

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm test
grep -rn "TH_CLS\s*=" src/app/ src/components/admin/
```

Expected: the first three PASS. The `grep` returns **three** lines — the definition in `src/components/admin/admin-table.tsx`, plus the two Questions-page files (`questions-client.tsx` and `questions/import/import-client.tsx`) that keep their local copies because migrating them is explicitly out of scope for this plan. What this step actually checks is that **Overview and Lessons no longer declare their own** — neither `src/app/admin/(console)/page.tsx` nor `src/app/admin/(console)/lessons/page.tsx` may appear in the output. Those two Questions files collapse onto the shared constant in the later Questions-migration round.

- [ ] **Step 6: Commit**

```bash
git add src/components/admin/admin-table.tsx src/components/admin/empty-state.tsx \
  "src/app/admin/(console)/page.tsx" "src/app/admin/(console)/lessons/page.tsx"
git commit -m "refactor(admin): extract shared table and empty-state primitives"
```

---

## Task 6: Pagination and detail shell primitives

**Files:**
- Create: `src/components/admin/pagination.tsx`
- Create: `src/components/admin/detail-shell.tsx`
- Create: `scripts/test-admin-pagination.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type PageWindow = { page: number; totalPages: number; from: number; to: number; hasPrev: boolean; hasNext: boolean }`
  - `pageWindow(args: { page: number; pageSize: number; total: number }): PageWindow` — exported from `src/components/admin/pagination.tsx`
  - `Pagination({ window, basePath, params })` — `params` is a `Record<string, string>` of the current filters to preserve
  - `DetailShell({ breadcrumb, title, subtitle, actions, children })` — `breadcrumb` is `{ label: string; href: string }`

`pageWindow` is pure and carries the test; the component around it is thin.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-pagination.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { pageWindow } from "../src/components/admin/pagination";

test("a full first page reports its range one-indexed", () => {
  const w = pageWindow({ page: 1, pageSize: 25, total: 80 });
  assert.equal(w.from, 1);
  assert.equal(w.to, 25);
  assert.equal(w.totalPages, 4);
  assert.equal(w.hasPrev, false);
  assert.equal(w.hasNext, true);
});

test("the last page stops at the total, not at a full page boundary", () => {
  const w = pageWindow({ page: 4, pageSize: 25, total: 80 });
  assert.equal(w.from, 76);
  assert.equal(w.to, 80);
  assert.equal(w.hasNext, false);
});

test("an empty result set is one page showing zero of zero", () => {
  // totalPages 0 would make "Page 1 of 0" render, which reads as a bug.
  const w = pageWindow({ page: 1, pageSize: 25, total: 0 });
  assert.equal(w.totalPages, 1);
  assert.equal(w.from, 0);
  assert.equal(w.to, 0);
  assert.equal(w.hasPrev, false);
  assert.equal(w.hasNext, false);
});

test("a page beyond the end is clamped to the last page", () => {
  // ?page=999 is one hand-edited URL away and must not render an empty table
  // with a live Next button.
  const w = pageWindow({ page: 999, pageSize: 25, total: 80 });
  assert.equal(w.page, 4);
  assert.equal(w.hasNext, false);
});

test("a page below one is clamped up", () => {
  const w = pageWindow({ page: -3, pageSize: 25, total: 80 });
  assert.equal(w.page, 1);
  assert.equal(w.hasPrev, false);
});

test("a single partial page has neither neighbour", () => {
  const w = pageWindow({ page: 1, pageSize: 25, total: 7 });
  assert.equal(w.totalPages, 1);
  assert.equal(w.to, 7);
  assert.equal(w.hasPrev, false);
  assert.equal(w.hasNext, false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-pagination.mts
```

Expected: FAIL — cannot find module.

- [ ] **Step 3: Create the pagination component**

Create `src/components/admin/pagination.tsx`:

```tsx
import Link from "next/link";
import { LuChevronLeft, LuChevronRight } from "react-icons/lu";
import { cn } from "@/lib/utils";

export type PageWindow = {
  /** Clamped into [1, totalPages]. */
  page: number;
  totalPages: number;
  /** One-indexed range of rows shown; 0/0 when there are none. */
  from: number;
  to: number;
  hasPrev: boolean;
  hasNext: boolean;
};

/**
 * Pure page arithmetic.
 *
 * Clamps rather than trusts: `?page=` is one hand-edited URL away, and an
 * out-of-range page must not render an empty table with a live Next button.
 * An empty result set reports one page, not zero, so "Page 1 of 0" never
 * renders.
 */
export function pageWindow({
  page,
  pageSize,
  total,
}: {
  page: number;
  pageSize: number;
  total: number;
}): PageWindow {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const clamped = Math.min(Math.max(1, Math.floor(page)), totalPages);

  return {
    page: clamped,
    totalPages,
    from: total === 0 ? 0 : (clamped - 1) * pageSize + 1,
    to: total === 0 ? 0 : Math.min(clamped * pageSize, total),
    hasPrev: clamped > 1,
    hasNext: clamped < totalPages,
  };
}

const LINK_CLS =
  "inline-flex items-center gap-1 rounded-lg border border-border-strong bg-card px-3 py-1.5 text-sm font-semibold text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60";

function hrefFor(
  basePath: string,
  params: Record<string, string>,
  page: number,
): string {
  const next = new URLSearchParams(params);
  // Page 1 is the default, so it stays out of the URL and the canonical link
  // for a filter does not depend on how the user arrived at it.
  if (page > 1) next.set("page", String(page));
  else next.delete("page");
  const query = next.toString();
  return query ? `${basePath}?${query}` : basePath;
}

export function Pagination({
  window: win,
  basePath,
  params,
  className,
}: {
  window: PageWindow;
  basePath: string;
  /** Current filters, preserved across page changes. */
  params: Record<string, string>;
  className?: string;
}) {
  if (win.totalPages <= 1) return null;

  return (
    <nav
      aria-label="Pagination"
      className={cn("mt-4 flex items-center justify-between gap-3", className)}
    >
      <p className="text-sm text-muted">
        Showing{" "}
        <span className="tabular-nums text-foreground">
          {win.from}–{win.to}
        </span>{" "}
        · page{" "}
        <span className="tabular-nums text-foreground">{win.page}</span> of{" "}
        <span className="tabular-nums text-foreground">{win.totalPages}</span>
      </p>

      <div className="flex gap-2">
        {win.hasPrev ? (
          <Link href={hrefFor(basePath, params, win.page - 1)} className={LINK_CLS}>
            <LuChevronLeft className="h-4 w-4" /> Previous
          </Link>
        ) : (
          <span className={cn(LINK_CLS, "cursor-not-allowed opacity-50")} aria-disabled>
            <LuChevronLeft className="h-4 w-4" /> Previous
          </span>
        )}
        {win.hasNext ? (
          <Link href={hrefFor(basePath, params, win.page + 1)} className={LINK_CLS}>
            Next <LuChevronRight className="h-4 w-4" />
          </Link>
        ) : (
          <span className={cn(LINK_CLS, "cursor-not-allowed opacity-50")} aria-disabled>
            Next <LuChevronRight className="h-4 w-4" />
          </span>
        )}
      </div>
    </nav>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-pagination.mts
```

Expected: PASS — 6 tests.

- [ ] **Step 5: Create the detail shell**

Create `src/components/admin/detail-shell.tsx`:

```tsx
import Link from "next/link";
import { LuChevronLeft } from "react-icons/lu";

/**
 * Chrome for a single-record page: where you came from, what you are looking
 * at, what you can do to it.
 */
export function DetailShell({
  breadcrumb,
  title,
  subtitle,
  actions,
  children,
}: {
  breadcrumb: { label: string; href: string };
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Link
        href={breadcrumb.href}
        className="inline-flex items-center gap-1 text-sm font-semibold text-muted transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
      >
        <LuChevronLeft className="h-4 w-4" />
        {breadcrumb.label}
      </Link>

      <div className="mt-3 mb-6 flex flex-wrap items-end justify-between gap-4 md:mb-8">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
            {title}
          </h1>
          {subtitle && <p className="mt-1.5 text-sm text-muted">{subtitle}</p>}
        </div>
        {actions}
      </div>

      <div className="flex flex-col gap-8">{children}</div>
    </div>
  );
}
```

- [ ] **Step 6: Register the test and verify**

Append ` scripts/test-admin-pagination.mts` to the `test` script in `package.json`, then:

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm test
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/components/admin/pagination.tsx src/components/admin/detail-shell.tsx \
  scripts/test-admin-pagination.mts package.json
git commit -m "feat(admin): add URL-driven pagination and detail shell primitives"
```

---

## Task 7: Student permission predicates

**Files:**
- Modify: `src/lib/admin-access.ts`
- Modify: `scripts/test-admin-access.mts`

**Interfaces:**
- Consumes: `AdminPrincipal`, `canAccessConsole`, `canManageAdmins` (existing).
- Produces:
  - `canEditStudent(actor: Pick<AdminPrincipal, "isActive"> | null): boolean`
  - `canSuspendStudent(actor: Pick<AdminPrincipal, "isActive"> | null): boolean`
  - `canDeleteStudent(actor: Pick<AdminPrincipal, "isActive" | "isOwner"> | null): boolean`
  - `canForceSignOutStudent(actor: Pick<AdminPrincipal, "isActive" | "isOwner"> | null): boolean`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-admin-access.mts`, and add the four new names to the existing `import { ... } from "../src/lib/admin-access"` block at the top:

```ts
const ACTIVE_OWNER = { isActive: true, isOwner: true };
const ACTIVE_ADMIN = { isActive: true, isOwner: false };
const DEAD_OWNER = { isActive: false, isOwner: true };
const DEAD_ADMIN = { isActive: false, isOwner: false };

test("any active admin may edit a student", () => {
  assert.equal(canEditStudent(ACTIVE_OWNER), true);
  assert.equal(canEditStudent(ACTIVE_ADMIN), true);
});

test("a deactivated admin may not edit a student", () => {
  assert.equal(canEditStudent(DEAD_ADMIN), false);
  assert.equal(canEditStudent(DEAD_OWNER), false);
  assert.equal(canEditStudent(null), false);
});

test("any active admin may suspend a student", () => {
  // Suspension is reversible, so it is not held back to the owner.
  assert.equal(canSuspendStudent(ACTIVE_OWNER), true);
  assert.equal(canSuspendStudent(ACTIVE_ADMIN), true);
  assert.equal(canSuspendStudent(DEAD_ADMIN), false);
  assert.equal(canSuspendStudent(null), false);
});

test("only an active owner may delete a student", () => {
  // Deletion cascades across progress, attempts, mastery and flashcards.
  assert.equal(canDeleteStudent(ACTIVE_OWNER), true);
  assert.equal(canDeleteStudent(ACTIVE_ADMIN), false);
  assert.equal(canDeleteStudent(DEAD_OWNER), false);
  assert.equal(canDeleteStudent(null), false);
});

test("only an active owner may force a student sign-out", () => {
  assert.equal(canForceSignOutStudent(ACTIVE_OWNER), true);
  assert.equal(canForceSignOutStudent(ACTIVE_ADMIN), false);
  assert.equal(canForceSignOutStudent(DEAD_OWNER), false);
  assert.equal(canForceSignOutStudent(null), false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-access.mts
```

Expected: FAIL — `canEditStudent` is not exported.

- [ ] **Step 3: Add the predicates**

Append to `src/lib/admin-access.ts`, after `canDeactivate`:

```ts
/**
 * Student capabilities.
 *
 * Two levels, not a role enum: the reversible actions are every active admin's,
 * the irreversible ones are the owner's. Hiding a control in the UI is
 * presentation — the routes call these too.
 */

export function canEditStudent(
  actor: Pick<AdminPrincipal, "isActive"> | null,
): boolean {
  return canAccessConsole(actor);
}

/** Reversible, so it is not held back to the owner. */
export function canSuspendStudent(
  actor: Pick<AdminPrincipal, "isActive"> | null,
): boolean {
  return canAccessConsole(actor);
}

/** Cascades across progress, attempts, mastery and flashcards. Owner only. */
export function canDeleteStudent(
  actor: Pick<AdminPrincipal, "isActive" | "isOwner"> | null,
): boolean {
  return canManageAdmins(actor);
}

export function canForceSignOutStudent(
  actor: Pick<AdminPrincipal, "isActive" | "isOwner"> | null,
): boolean {
  return canManageAdmins(actor);
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-access.mts
```

Expected: PASS — the original tests plus 5 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/lib/admin-access.ts scripts/test-admin-access.mts
git commit -m "feat(admin): add student capability predicates"
```

---

## Task 8: Student filter and validation

**Files:**
- Create: `src/lib/admin-student.ts`
- Create: `scripts/test-admin-student.mts`
- Modify: `src/lib/validators.ts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `SubscriptionTier`, `isSubscriptionTier` (Task 1); `AccountStatus`, `isAccountStatus` (Task 3); `CLASS_LEVELS`, `ClassLevel` from `@/lib/curriculum-scope`.
- Produces:
  - `STUDENT_PAGE_SIZE = 25`
  - `TRACKS: readonly ["SCIENCE", "ARTS", "COMMERCIAL"]`, `type Track`, `isTrack(value)`
  - `type RawStudentParams = { q?: string; class?: string; track?: string; tier?: string; status?: string; page?: string }`
  - `type StudentFilter = { search: string | null; classLevel: ClassLevel | null; track: Track | null; tier: SubscriptionTier | null; status: AccountStatus | null; page: number }`
  - `normaliseStudentFilter(params: RawStudentParams): StudentFilter`
  - `studentFilterParams(filter: StudentFilter): Record<string, string>` — round-trips a filter back into query params for `Pagination`
  - `fullName(student: { firstName: string; lastName: string }): string`
  - In `validators.ts`: `studentProfileSchema`, `studentStatusSchema`, `studentTierSchema`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-student.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STUDENT_PAGE_SIZE,
  fullName,
  isTrack,
  normaliseStudentFilter,
  studentFilterParams,
} from "../src/lib/admin-student";
import { studentProfileSchema, studentStatusSchema, studentTierSchema } from "../src/lib/validators";

test("empty params give an unfiltered first page", () => {
  const f = normaliseStudentFilter({});
  assert.deepEqual(f, {
    search: null,
    classLevel: null,
    track: null,
    tier: null,
    status: null,
    page: 1,
  });
});

test("recognised values pass through", () => {
  const f = normaliseStudentFilter({
    q: "ada",
    class: "SS2",
    track: "SCIENCE",
    tier: "PREMIUM",
    status: "suspended",
    page: "3",
  });
  assert.equal(f.search, "ada");
  assert.equal(f.classLevel, "SS2");
  assert.equal(f.track, "SCIENCE");
  assert.equal(f.tier, "PREMIUM");
  assert.equal(f.status, "suspended");
  assert.equal(f.page, 3);
});

test("unrecognised enum values are dropped, not passed to Prisma", () => {
  // A hand-edited URL would otherwise become a where clause on an enum column
  // and throw at query time.
  const f = normaliseStudentFilter({
    class: "SS9",
    track: "MUSIC",
    tier: "GOLD",
    status: "deleted",
  });
  assert.equal(f.classLevel, null);
  assert.equal(f.track, null);
  assert.equal(f.tier, null);
  assert.equal(f.status, null);
});

test("enum matching is case sensitive", () => {
  const f = normaliseStudentFilter({ track: "science", tier: "premium" });
  assert.equal(f.track, null);
  assert.equal(f.tier, null);
});

test("search is trimmed, and whitespace-only is no search at all", () => {
  assert.equal(normaliseStudentFilter({ q: "  ada  " }).search, "ada");
  assert.equal(normaliseStudentFilter({ q: "   " }).search, null);
  assert.equal(normaliseStudentFilter({ q: "" }).search, null);
});

test("a non-numeric or out-of-range page falls back to one", () => {
  assert.equal(normaliseStudentFilter({ page: "abc" }).page, 1);
  assert.equal(normaliseStudentFilter({ page: "0" }).page, 1);
  assert.equal(normaliseStudentFilter({ page: "-4" }).page, 1);
  assert.equal(normaliseStudentFilter({ page: "" }).page, 1);
  assert.equal(normaliseStudentFilter({ page: "2.7" }).page, 2);
});

test("filter params round-trip, omitting the empties", () => {
  const f = normaliseStudentFilter({ q: "ada", tier: "STANDARD" });
  assert.deepEqual(studentFilterParams(f), { q: "ada", tier: "STANDARD" });
});

test("page is never written into the round-tripped params", () => {
  // Pagination owns the page key; duplicating it here would fight it.
  const f = normaliseStudentFilter({ q: "ada", page: "5" });
  assert.equal("page" in studentFilterParams(f), false);
});

test("isTrack accepts only the three tracks", () => {
  assert.equal(isTrack("COMMERCIAL"), true);
  assert.equal(isTrack("commercial"), false);
  assert.equal(isTrack(undefined), false);
});

test("page size is a round number of rows", () => {
  assert.equal(STUDENT_PAGE_SIZE, 25);
});

test("fullName joins the two halves with a single space", () => {
  assert.equal(fullName({ firstName: "Ada", lastName: "Obi" }), "Ada Obi");
});

test("the profile schema requires both names", () => {
  const bad = studentProfileSchema.safeParse({ firstName: "", lastName: "Obi" });
  assert.equal(bad.success, false);
});

test("the profile schema rejects a malformed email but allows none at all", () => {
  // Phone-only accounts exist, so email must be optional yet validated.
  assert.equal(
    studentProfileSchema.safeParse({ firstName: "Ada", lastName: "Obi", email: "nope" }).success,
    false,
  );
  assert.equal(
    studentProfileSchema.safeParse({ firstName: "Ada", lastName: "Obi" }).success,
    true,
  );
});

test("the profile schema rejects an unknown class level or track", () => {
  assert.equal(
    studentProfileSchema.safeParse({ firstName: "Ada", lastName: "Obi", classLevel: "SS9" }).success,
    false,
  );
  assert.equal(
    studentProfileSchema.safeParse({ firstName: "Ada", lastName: "Obi", track: "MUSIC" }).success,
    false,
  );
});

test("the status schema requires a reason when suspending", () => {
  // An audit row reading "suspended, no reason given" helps nobody later.
  assert.equal(studentStatusSchema.safeParse({ isActive: false }).success, false);
  assert.equal(
    studentStatusSchema.safeParse({ isActive: false, reason: "Payment dispute" }).success,
    true,
  );
});

test("the status schema needs no reason to reactivate", () => {
  assert.equal(studentStatusSchema.safeParse({ isActive: true }).success, true);
});

test("the tier schema accepts only the three tiers", () => {
  assert.equal(studentTierSchema.safeParse({ tier: "STANDARD" }).success, true);
  assert.equal(studentTierSchema.safeParse({ tier: "GOLD" }).success, false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-student.mts
```

Expected: FAIL — cannot find module `../src/lib/admin-student`.

- [ ] **Step 3: Create `src/lib/admin-student.ts`**

```ts
import { CLASS_LEVELS, type ClassLevel } from "@/lib/curriculum-scope";
import { isSubscriptionTier, type SubscriptionTier } from "@/lib/subscription";
import { isAccountStatus, type AccountStatus } from "@/lib/account-status";

/**
 * Narrowing the admin student list. Pure — no Prisma, no React — so the
 * filtering rules can be tested without a database or a browser, the way
 * admin-lesson-browse.ts is.
 *
 * See docs/superpowers/specs/2026-08-27-admin-console-structure-design.md
 */

export const STUDENT_PAGE_SIZE = 25;

// Mirrors the Prisma `Track` enum. Declared here rather than imported from
// @prisma/client so this module stays database-free and testable.
export const TRACKS = ["SCIENCE", "ARTS", "COMMERCIAL"] as const;

export type Track = (typeof TRACKS)[number];

export function isTrack(value: string | undefined | null): value is Track {
  return typeof value === "string" && (TRACKS as readonly string[]).includes(value);
}

function isClassLevel(value: string | undefined | null): value is ClassLevel {
  return (
    typeof value === "string" && (CLASS_LEVELS as readonly string[]).includes(value)
  );
}

export interface RawStudentParams {
  q?: string;
  class?: string;
  track?: string;
  tier?: string;
  status?: string;
  page?: string;
}

export interface StudentFilter {
  search: string | null;
  classLevel: ClassLevel | null;
  track: Track | null;
  tier: SubscriptionTier | null;
  status: AccountStatus | null;
  page: number;
}

/**
 * Coerce raw query strings into a filter that is safe to hand to Prisma.
 *
 * An unrecognised class level, track, tier or status is dropped rather than
 * passed through as a `where` clause on an enum column, which would throw. The
 * page falls back to 1 rather than to NaN; `pageWindow` clamps the upper end
 * once the total is known.
 */
export function normaliseStudentFilter(params: RawStudentParams): StudentFilter {
  const search = params.q?.trim();
  const page = Number.parseInt(params.page ?? "", 10);

  return {
    search: search ? search : null,
    classLevel: isClassLevel(params.class) ? params.class : null,
    track: isTrack(params.track) ? params.track : null,
    tier: isSubscriptionTier(params.tier) ? params.tier : null,
    status: isAccountStatus(params.status) ? params.status : null,
    page: Number.isFinite(page) && page >= 1 ? page : 1,
  };
}

/**
 * The filter as query params, for links that must preserve it.
 *
 * `page` is deliberately absent: Pagination owns that key, and writing it here
 * too would have the two fight over it.
 */
export function studentFilterParams(filter: StudentFilter): Record<string, string> {
  const params: Record<string, string> = {};
  if (filter.search) params.q = filter.search;
  if (filter.classLevel) params.class = filter.classLevel;
  if (filter.track) params.track = filter.track;
  if (filter.tier) params.tier = filter.tier;
  if (filter.status) params.status = filter.status;
  return params;
}

export function fullName(student: {
  firstName: string;
  lastName: string;
}): string {
  return `${student.firstName} ${student.lastName}`;
}
```

- [ ] **Step 4: Add the schemas to `src/lib/validators.ts`**

Append at the end of the schema declarations (before the `// Type exports` block), and add the import of `SUBSCRIPTION_TIERS` at the top of the file:

```ts
// ─── Admin students ───────────────────────────────

// Email is optional because phone-only accounts exist, but a supplied value is
// still validated — an admin correcting a typo must not be able to store a
// second malformed one.
export const studentProfileSchema = z.object({
  firstName: z.string().trim().min(1, "First name is required"),
  lastName: z.string().trim().min(1, "Last name is required"),
  email: z.string().trim().toLowerCase().email("Enter a valid email").optional(),
  phone: z.string().trim().min(7, "Enter a valid phone number").optional(),
  classLevel: z.enum(["SS1", "SS2", "SS3"]).optional(),
  track: z.enum(["SCIENCE", "ARTS", "COMMERCIAL"]).optional(),
  state: z.string().trim().min(1).optional(),
  schoolId: z.string().trim().min(1).optional(),
});

// A reason is required to suspend and meaningless to reactivate. An audit row
// reading "suspended, no reason given" helps nobody three months later.
export const studentStatusSchema = z
  .object({
    isActive: z.boolean(),
    reason: z.string().trim().min(3).max(500).optional(),
  })
  .refine((value) => value.isActive || Boolean(value.reason), {
    message: "A reason is required when suspending an account",
    path: ["reason"],
  });

export const studentTierSchema = z.object({
  tier: z.enum(SUBSCRIPTION_TIERS),
});
```

Add to the imports at the top of `validators.ts`:

```ts
import { SUBSCRIPTION_TIERS } from "@/lib/subscription";
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-student.mts
```

Expected: PASS — 17 tests.

**All three schemas were probed against the installed Zod 4.4.3 before this task was written — they behave exactly as the tests above require.** Confirmed directly: `z.enum()` accepts the `readonly` tuple as-is (no spread needed); `z.string().trim().toLowerCase().email()` both normalises (`"  Ada@Example.COM "` → `"ada@example.com"`) and rejects malformed input while staying `.optional()`; and the `.refine()` on `studentStatusSchema` rejects a suspension with no reason while allowing a reactivation without one. `z.string().email()` is deprecated in Zod 4 but still functional, and is what `validators.ts` already uses at lines 14 and 22 — stay consistent with that rather than switching to `z.email()`.

- [ ] **Step 6: Register the test and verify**

Append ` scripts/test-admin-student.mts` to the `test` script in `package.json`, then:

```bash
npx tsc --noEmit -p tsconfig.json
npm test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lib/admin-student.ts src/lib/validators.ts scripts/test-admin-student.mts package.json
git commit -m "feat(admin): add student filter normalisation and validation schemas"
```

---

## Task 9: Student list page

**Files:**
- Create: `src/lib/admin-student-data.ts`
- Create: `src/components/admin/student-filter-bar.tsx`
- Create: `src/app/admin/(console)/students/page.tsx`
- Modify: `src/lib/admin-nav.ts` (add the Students entry)

**Interfaces:**
- Consumes: `normaliseStudentFilter`, `studentFilterParams`, `fullName`, `STUDENT_PAGE_SIZE`, `TRACKS` (Task 8); `pageWindow`, `Pagination` (Task 6); `AdminTable` family, `EmptyState` (Task 5); `describeTier` (Task 1); `describeAccountStatus` (Task 3).
- Produces:
  - `type StudentRow = { id: string; firstName: string; lastName: string; email: string | null; phone: string | null; classLevel: ClassLevel | null; track: Track | null; tier: SubscriptionTier; isActive: boolean; createdAt: Date; lastActiveAt: Date | null }`
  - `listStudents(filter: StudentFilter): Promise<{ rows: StudentRow[]; total: number }>`

- [ ] **Step 1: Create the data module**

Create `src/lib/admin-student-data.ts`:

```ts
import { db } from "@/lib/db";
import { STUDENT_PAGE_SIZE, type StudentFilter } from "@/lib/admin-student";
import type { SubscriptionTier } from "@/lib/subscription";
import type { ClassLevel } from "@/lib/curriculum-scope";
import type { Track } from "@/lib/admin-student";

export interface StudentRow {
  id: string;
  firstName: string;
  lastName: string;
  email: string | null;
  phone: string | null;
  classLevel: ClassLevel | null;
  track: Track | null;
  tier: SubscriptionTier;
  isActive: boolean;
  createdAt: Date;
  /** Most recent learning event; null for an account that never studied. */
  lastActiveAt: Date | null;
}

/**
 * `where` is built from an already-normalised filter — normaliseStudentFilter
 * has dropped anything that is not a real enum member, so nothing here can
 * throw on a hand-edited URL.
 */
function whereFor(filter: StudentFilter) {
  return {
    role: "STUDENT" as const,
    ...(filter.classLevel ? { classLevel: filter.classLevel } : {}),
    ...(filter.track ? { track: filter.track } : {}),
    ...(filter.tier ? { tier: filter.tier } : {}),
    ...(filter.status ? { isActive: filter.status === "active" } : {}),
    ...(filter.search
      ? {
          OR: [
            { firstName: { contains: filter.search, mode: "insensitive" as const } },
            { lastName: { contains: filter.search, mode: "insensitive" as const } },
            { email: { contains: filter.search, mode: "insensitive" as const } },
            { phone: { contains: filter.search } },
          ],
        }
      : {}),
  };
}

export async function listStudents(
  filter: StudentFilter,
): Promise<{ rows: StudentRow[]; total: number }> {
  const where = whereFor(filter);

  // Counted FIRST, not in parallel with the fetch: the row query has to skip by
  // a page number that is already clamped to the real page count, or ?page=999
  // skips past the end and renders "no matches" over a full result set. One
  // extra round trip is the price of the page being correct.
  const total = await db.user.count({ where });
  const totalPages = Math.max(1, Math.ceil(total / STUDENT_PAGE_SIZE));
  const page = Math.min(Math.max(1, filter.page), totalPages);

  const users = await db.user.findMany({
      where,
      select: {
        id: true,
        firstName: true,
        lastName: true,
        email: true,
        phone: true,
        classLevel: true,
        track: true,
        tier: true,
        isActive: true,
        createdAt: true,
        learningEvents: {
          select: { occurredAt: true },
          orderBy: { occurredAt: "desc" },
          take: 1,
        },
      },
      orderBy: { createdAt: "desc" },
      skip: (page - 1) * STUDENT_PAGE_SIZE,
      take: STUDENT_PAGE_SIZE,
  });

  return {
    total,
    rows: users.map((user) => ({
      id: user.id,
      firstName: user.firstName,
      lastName: user.lastName,
      email: user.email,
      phone: user.phone,
      classLevel: user.classLevel as ClassLevel | null,
      track: user.track as Track | null,
      tier: user.tier as SubscriptionTier,
      isActive: user.isActive,
      createdAt: user.createdAt,
      lastActiveAt: user.learningEvents[0]?.occurredAt ?? null,
    })),
  };
}
```

**Field names already verified against the schema — use exactly these:** `LearningEvent` has **no `createdAt`**; its timestamp is `occurredAt` and its User FK is `studentId` (not `userId`). The `User` relation field is `learningEvents`. The `_count` relation names `attempts`, `topicMastery` and `flashcardReviews` are correct as written.

- [ ] **Step 2: Create the filter bar**

Create `src/components/admin/student-filter-bar.tsx`:

```tsx
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { CLASS_LEVELS } from "@/lib/curriculum-scope";
import { SUBSCRIPTION_TIERS, TIER_LABELS } from "@/lib/subscription";
import { ACCOUNT_STATUSES } from "@/lib/account-status";
import { TRACKS, type StudentFilter } from "@/lib/admin-student";

const SELECT_CLS =
  "px-3 py-2 rounded-lg border border-border bg-card text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60";
const LABEL_CLS = "text-[11px] font-semibold uppercase tracking-wider text-muted";

const STATUS_LABELS: Record<string, string> = {
  active: "Active",
  suspended: "Suspended",
};

export function StudentFilterBar({ filter }: { filter: StudentFilter }) {
  const router = useRouter();
  const [search, setSearch] = useState(filter.search ?? "");

  // Any change resets to page 1: staying on page 7 of a narrower result set
  // shows an empty table.
  function go(next: Partial<Record<"q" | "class" | "track" | "tier" | "status", string>>) {
    const params = new URLSearchParams({
      ...(filter.search ? { q: filter.search } : {}),
      ...(filter.classLevel ? { class: filter.classLevel } : {}),
      ...(filter.track ? { track: filter.track } : {}),
      ...(filter.tier ? { tier: filter.tier } : {}),
      ...(filter.status ? { status: filter.status } : {}),
    });
    for (const [key, value] of Object.entries(next)) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    const query = params.toString();
    router.replace(query ? `/admin/students?${query}` : "/admin/students");
  }

  // Debounced so typing a name is not one navigation per keystroke.
  useEffect(() => {
    const current = filter.search ?? "";
    if (search === current) return;
    const id = setTimeout(() => go({ q: search }), 350);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  return (
    <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-border-strong bg-card p-4">
      <div className="flex flex-col gap-1">
        <label htmlFor="student-search" className={LABEL_CLS}>
          Search
        </label>
        <input
          id="student-search"
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Name, email or phone"
          className={SELECT_CLS}
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="student-class" className={LABEL_CLS}>
          Class
        </label>
        <select
          id="student-class"
          value={filter.classLevel ?? ""}
          onChange={(e) => go({ class: e.target.value })}
          className={SELECT_CLS}
        >
          <option value="">All classes</option>
          {CLASS_LEVELS.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="student-track" className={LABEL_CLS}>
          Track
        </label>
        <select
          id="student-track"
          value={filter.track ?? ""}
          onChange={(e) => go({ track: e.target.value })}
          className={SELECT_CLS}
        >
          <option value="">All tracks</option>
          {TRACKS.map((track) => (
            <option key={track} value={track}>
              {track.charAt(0) + track.slice(1).toLowerCase()}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="student-tier" className={LABEL_CLS}>
          Plan
        </label>
        <select
          id="student-tier"
          value={filter.tier ?? ""}
          onChange={(e) => go({ tier: e.target.value })}
          className={SELECT_CLS}
        >
          <option value="">All plans</option>
          {SUBSCRIPTION_TIERS.map((tier) => (
            <option key={tier} value={tier}>
              {TIER_LABELS[tier]}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="student-status" className={LABEL_CLS}>
          Status
        </label>
        <select
          id="student-status"
          value={filter.status ?? ""}
          onChange={(e) => go({ status: e.target.value })}
          className={SELECT_CLS}
        >
          <option value="">Any status</option>
          {ACCOUNT_STATUSES.map((status) => (
            <option key={status} value={status}>
              {STATUS_LABELS[status]}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create the list page**

Create `src/app/admin/(console)/students/page.tsx`:

```tsx
import Link from "next/link";
import { requireAdminPage } from "@/lib/admin-session";
import { PageHeader } from "@/components/ui/page-header";
import { AdminTable, AdminTd, AdminTh, AdminTr } from "@/components/admin/admin-table";
import { EmptyState } from "@/components/admin/empty-state";
import { Pagination, pageWindow } from "@/components/admin/pagination";
import { StudentFilterBar } from "@/components/admin/student-filter-bar";
import {
  STUDENT_PAGE_SIZE,
  fullName,
  normaliseStudentFilter,
  studentFilterParams,
  type RawStudentParams,
} from "@/lib/admin-student";
import { listStudents } from "@/lib/admin-student-data";
import { describeTier } from "@/lib/subscription";
import { describeAccountStatus } from "@/lib/account-status";
import { Badge } from "@/components/admin/badge";

export const dynamic = "force-dynamic";

const DATE = new Intl.DateTimeFormat("en-NG", {
  day: "numeric",
  month: "short",
  year: "numeric",
});

export default async function AdminStudentsPage({
  searchParams,
}: {
  searchParams: Promise<RawStudentParams>;
}) {
  // The layout's check does not re-run on client-side navigation between admin
  // routes, so each page carries its own.
  await requireAdminPage();

  const filter = normaliseStudentFilter(await searchParams);
  const { rows, total } = await listStudents(filter);
  const win = pageWindow({
    page: filter.page,
    pageSize: STUDENT_PAGE_SIZE,
    total,
  });

  return (
    <div>
      <PageHeader
        title="Students"
        description={`${total} ${total === 1 ? "account" : "accounts"} match the current filters.`}
      />

      <StudentFilterBar filter={filter} />

      {rows.length === 0 ? (
        <EmptyState
          title="No students match"
          message="Try widening the filters, or clear the search box."
        />
      ) : (
        <>
          <AdminTable caption="Student accounts">
            <thead>
              <tr className="border-b border-border-strong">
                <AdminTh>Name</AdminTh>
                <AdminTh>Contact</AdminTh>
                <AdminTh>Class</AdminTh>
                <AdminTh>Track</AdminTh>
                <AdminTh>Plan</AdminTh>
                <AdminTh>Status</AdminTh>
                <AdminTh align="right">Joined</AdminTh>
                <AdminTh align="right">Last active</AdminTh>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const tier = describeTier(row);
                const status = describeAccountStatus(row);
                return (
                  <AdminTr key={row.id}>
                    <AdminTd>
                      <Link
                        href={`/admin/students/${row.id}`}
                        className="font-medium text-foreground hover:text-primary hover:underline"
                      >
                        {fullName(row)}
                      </Link>
                    </AdminTd>
                    <AdminTd className="text-muted">
                      {row.email ?? row.phone ?? "—"}
                    </AdminTd>
                    <AdminTd className="text-muted">{row.classLevel ?? "—"}</AdminTd>
                    <AdminTd className="text-muted">
                      {row.track
                        ? row.track.charAt(0) + row.track.slice(1).toLowerCase()
                        : "—"}
                    </AdminTd>
                    <AdminTd>
                      <Badge tone={tier.tone}>{tier.label}</Badge>
                    </AdminTd>
                    <AdminTd>
                      <Badge tone={status.tone}>{status.label}</Badge>
                    </AdminTd>
                    <AdminTd align="right" className="tabular-nums text-muted">
                      {DATE.format(row.createdAt)}
                    </AdminTd>
                    <AdminTd align="right" className="tabular-nums text-muted">
                      {row.lastActiveAt ? DATE.format(row.lastActiveAt) : "Never"}
                    </AdminTd>
                  </AdminTr>
                );
              })}
            </tbody>
          </AdminTable>

          <Pagination
            window={win}
            basePath="/admin/students"
            params={studentFilterParams(filter)}
          />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create the badge used above**

Create `src/components/admin/badge.tsx`:

```tsx
import { cn } from "@/lib/utils";

const TONE_CLS: Record<string, string> = {
  neutral: "border-border-strong bg-secondary text-muted",
  info: "border-border-strong bg-secondary text-foreground",
  // Mirrors StatusBanner's treatment (border-<tone>/30 + bg-<tone>-soft) so a
  // badge and a banner of the same tone read as the same colour.
  success: "border-success/30 bg-success-soft text-success",
  warning: "border-warning/30 bg-warning-soft text-warning",
};

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "info" | "success" | "warning";
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-block rounded-lg border px-2 py-0.5 text-xs font-semibold",
        TONE_CLS[tone] ?? TONE_CLS.neutral,
      )}
    >
      {children}
    </span>
  );
}
```

**Tokens already verified — use exactly these classes.** `--color-success`, `--color-success-soft`, `--color-warning` and `--color-warning-soft` are all defined in `src/app/globals.css` (light, dark, and the explicit `[data-theme]` block), so `text-warning`, `bg-warning-soft` and `border-warning/30` all resolve. Note that `StatusBanner` accepts only `error | success | info` — it has **no** `warning` tone, so do not pass one to it. `Badge` is the component that carries `warning`.

- [ ] **Step 5: Verify the page renders**

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm run dev
```

Visit `http://localhost:3000/admin/students` signed in as an admin. Confirm: the table lists students; changing a select updates the URL and the results; typing in search debounces then filters; the back button restores the previous filter; `?page=999` shows the last page with a disabled Next; `?tier=GOLD` is ignored rather than erroring.

- [ ] **Step 6: Add the Students nav entry**

In `src/lib/admin-nav.ts`, add to the `People` group, **before** the Team entry:

```ts
      { name: "Students", href: "/admin/students", icon: LuGraduationCap },
```

and add `LuGraduationCap` to the `react-icons/lu` import.

Then promote Students onto the mobile bar in place of Lessons — students are the higher-value mobile destination, and Lessons stays reachable through the "More" sheet:

```ts
export const MOBILE_NAV_HREFS: readonly string[] = [
  "/admin",
  "/admin/questions",
  "/admin/students",
];
```

- [ ] **Step 7: Verify the nav tests still pass**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-nav.mts
npm test
```

Expected: PASS. `mobileBarItems` still returns three entries, now ending in Students, and the "no route is orphaned on mobile" test confirms Lessons moved into the More sheet rather than disappearing.

- [ ] **Step 8: Commit**

```bash
git add src/lib/admin-student-data.ts src/components/admin/student-filter-bar.tsx \
  src/components/admin/badge.tsx "src/app/admin/(console)/students/page.tsx" \
  src/lib/admin-nav.ts
git commit -m "feat(admin): add the student list with URL-driven filters"
```

---

## Task 10: Student detail page

**Files:**
- Modify: `src/lib/admin-student-data.ts`
- Create: `src/app/admin/(console)/students/[id]/page.tsx`

**Interfaces:**
- Consumes: everything from Task 9, plus `DetailShell` (Task 6).
- Produces:
  - `type StudentDetail = StudentRow & { state: string | null; schoolId: string | null; schoolName: string | null; suspendedAt: Date | null; suspendedReason: string | null; tierUpdatedAt: Date | null; attemptCount: number; masteredTopicCount: number; flashcardReviewCount: number }`
  - `getStudentDetail(id: string): Promise<StudentDetail | null>`
  - `getStudentDeletionImpact(id: string): Promise<Record<string, number>>`

- [ ] **Step 1: Add the detail query**

Append to `src/lib/admin-student-data.ts`:

```ts
export interface StudentDetail extends StudentRow {
  state: string | null;
  schoolId: string | null;
  schoolName: string | null;
  suspendedAt: Date | null;
  suspendedReason: string | null;
  tierUpdatedAt: Date | null;
  attemptCount: number;
  masteredTopicCount: number;
  flashcardReviewCount: number;
}

export async function getStudentDetail(id: string): Promise<StudentDetail | null> {
  const user = await db.user.findFirst({
    where: { id, role: "STUDENT" },
    select: {
      id: true,
      firstName: true,
      lastName: true,
      email: true,
      phone: true,
      classLevel: true,
      track: true,
      state: true,
      schoolId: true,
      school: { select: { name: true } },
      tier: true,
      tierUpdatedAt: true,
      isActive: true,
      suspendedAt: true,
      suspendedReason: true,
      createdAt: true,
      learningEvents: {
        select: { occurredAt: true },
        orderBy: { occurredAt: "desc" },
        take: 1,
      },
      _count: {
        select: {
          attempts: true,
          topicMastery: true,
          flashcardReviews: true,
        },
      },
    },
  });

  if (!user) return null;

  return {
    id: user.id,
    firstName: user.firstName,
    lastName: user.lastName,
    email: user.email,
    phone: user.phone,
    classLevel: user.classLevel as ClassLevel | null,
    track: user.track as Track | null,
    state: user.state,
    schoolId: user.schoolId,
    schoolName: user.school?.name ?? null,
    tier: user.tier as SubscriptionTier,
    tierUpdatedAt: user.tierUpdatedAt,
    isActive: user.isActive,
    suspendedAt: user.suspendedAt,
    suspendedReason: user.suspendedReason,
    createdAt: user.createdAt,
    lastActiveAt: user.learningEvents[0]?.occurredAt ?? null,
    attemptCount: user._count.attempts,
    masteredTopicCount: user._count.topicMastery,
    flashcardReviewCount: user._count.flashcardReviews,
  };
}

/**
 * What deleting this account would destroy, per relation.
 *
 * Following /admin/api/questions/[id]/usage: an admin about to delete is shown
 * the actual counts, not a generic warning.
 */
export async function getStudentDeletionImpact(
  id: string,
): Promise<Record<string, number>> {
  const [attempts, responses, progress, mastery, events, reviews, decks] =
    await Promise.all([
      db.assessmentAttempt.count({ where: { studentId: id } }),
      db.questionResponse.count({ where: { attempt: { studentId: id } } }),
      db.studentProgress.count({ where: { studentId: id } }),
      db.topicMastery.count({ where: { studentId: id } }),
      db.learningEvent.count({ where: { studentId: id } }),
      db.flashcardReview.count({ where: { studentId: id } }),
      db.flashcardDeck.count({ where: { createdBy: id } }),
    ]);

  return {
    "Assessment attempts": attempts,
    "Question responses": responses,
    "Progress records": progress,
    "Topic mastery records": mastery,
    "Learning events": events,
    "Flashcard reviews": reviews,
    "Authored flashcard decks": decks,
  };
}
```

**Relation field names already verified against the schema — use exactly these.** This codebase names its User foreign keys `studentId`, **not** `userId`, on every learning-domain model (`AssessmentAttempt`, `StudentProgress`, `TopicMastery`, `LearningEvent`, `FlashcardReview`). Only `Account` and `Session` use `userId`. `QuestionResponse` has no direct User link — it reaches one through `attempt`. `FlashcardDeck`'s author FK is **`createdBy`**, not `authorId`.

- [ ] **Step 2: Create the detail page**

Create `src/app/admin/(console)/students/[id]/page.tsx`:

```tsx
import { notFound } from "next/navigation";
import { requireAdminPage } from "@/lib/admin-session";
import { DetailShell } from "@/components/admin/detail-shell";
import { Badge } from "@/components/admin/badge";
import { StudentProfileForm } from "@/components/admin/student-profile-form";
import { StudentTierControl } from "@/components/admin/student-tier-control";
import { StudentDangerZone } from "@/components/admin/student-danger-zone";
import {
  getStudentDeletionImpact,
  getStudentDetail,
} from "@/lib/admin-student-data";
import { fullName } from "@/lib/admin-student";
import { describeTier } from "@/lib/subscription";
import { describeAccountStatus } from "@/lib/account-status";
import {
  canDeleteStudent,
  canEditStudent,
  canForceSignOutStudent,
  canSuspendStudent,
} from "@/lib/admin-access";

export const dynamic = "force-dynamic";

const HEADING_CLS = "text-[11px] font-semibold uppercase tracking-wider text-muted";
const DATE = new Intl.DateTimeFormat("en-NG", {
  day: "numeric",
  month: "short",
  year: "numeric",
});

export default async function AdminStudentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const admin = await requireAdminPage();
  const { id } = await params;

  const student = await getStudentDetail(id);
  if (!student) notFound();

  // Seven COUNT queries, and only the owner can ever act on them. Computing
  // them for every admin viewing any student would tax every page view for a
  // control most viewers cannot even see.
  const impact = canDeleteStudent(admin)
    ? await getStudentDeletionImpact(id)
    : {};

  const tier = describeTier(student);
  const status = describeAccountStatus(student);

  return (
    <DetailShell
      breadcrumb={{ label: "Students", href: "/admin/students" }}
      title={fullName(student)}
      subtitle={student.email ?? student.phone ?? "No contact details"}
      actions={
        <div className="flex gap-2">
          <Badge tone={tier.tone}>{tier.label}</Badge>
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
      }
    >
      {!student.isActive && (
        <section>
          <h2 className={HEADING_CLS}>Suspension</h2>
          <p className="mt-2 text-sm text-foreground">
            Suspended{" "}
            {student.suspendedAt ? DATE.format(student.suspendedAt) : "at an unknown time"}
            {student.suspendedReason ? ` — ${student.suspendedReason}` : ""}
          </p>
        </section>
      )}

      <section>
        <h2 className={HEADING_CLS}>Profile</h2>
        <StudentProfileForm
          student={student}
          canEdit={canEditStudent(admin)}
          className="mt-2"
        />
      </section>

      <section>
        <h2 className={HEADING_CLS}>Plan</h2>
        <StudentTierControl
          studentId={student.id}
          tier={student.tier}
          tierUpdatedAt={student.tierUpdatedAt ? DATE.format(student.tierUpdatedAt) : null}
          canEdit={canEditStudent(admin)}
          className="mt-2"
        />
      </section>

      <section>
        <h2 className={HEADING_CLS}>Activity</h2>
        <dl className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Attempts" value={student.attemptCount} />
          <Stat label="Topics tracked" value={student.masteredTopicCount} />
          <Stat label="Flashcard reviews" value={student.flashcardReviewCount} />
          <Stat
            label="Last active"
            text={student.lastActiveAt ? DATE.format(student.lastActiveAt) : "Never"}
          />
        </dl>
      </section>

      <section>
        <h2 className={HEADING_CLS}>Danger zone</h2>
        <StudentDangerZone
          studentId={student.id}
          studentName={fullName(student)}
          isActive={student.isActive}
          impact={impact}
          canSuspend={canSuspendStudent(admin)}
          canForceSignOut={canForceSignOutStudent(admin)}
          canDelete={canDeleteStudent(admin)}
          className="mt-2"
        />
      </section>
    </DetailShell>
  );
}

function Stat({
  label,
  value,
  text,
}: {
  label: string;
  value?: number;
  text?: string;
}) {
  return (
    <div className="rounded-lg border border-border-strong bg-card px-4 py-3">
      <dt className={HEADING_CLS}>{label}</dt>
      <dd className="mt-1 text-lg font-bold tabular-nums text-foreground">
        {text ?? value}
      </dd>
    </div>
  );
}
```

- [ ] **Step 3: Stub the three client components so the page compiles**

The forms are wired to real routes in Tasks 11–13. Create them now as read-only renderings so this task ends with a page that loads.

Create `src/components/admin/student-profile-form.tsx`:

```tsx
"use client";

import { cn } from "@/lib/utils";

const HEADING_CLS = "text-[11px] font-semibold uppercase tracking-wider text-muted";

export function StudentProfileForm({
  student,
  canEdit,
  className,
}: {
  student: {
    id: string;
    firstName: string;
    lastName: string;
    email: string | null;
    phone: string | null;
    classLevel: string | null;
    track: string | null;
    state: string | null;
    schoolName: string | null;
  };
  canEdit: boolean;
  className?: string;
}) {
  const fields: Array<[string, string]> = [
    ["First name", student.firstName],
    ["Last name", student.lastName],
    ["Email", student.email ?? "—"],
    ["Phone", student.phone ?? "—"],
    ["Class", student.classLevel ?? "—"],
    ["Track", student.track ?? "—"],
    ["State", student.state ?? "—"],
    ["School", student.schoolName ?? "—"],
  ];

  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-4 rounded-lg border border-border-strong bg-card p-4 sm:grid-cols-4",
        className,
      )}
    >
      {fields.map(([label, value]) => (
        <div key={label}>
          <p className={HEADING_CLS}>{label}</p>
          <p className="mt-1 text-sm text-foreground">{value}</p>
        </div>
      ))}
      {!canEdit && (
        <p className="col-span-full text-xs text-muted">
          You do not have permission to edit this profile.
        </p>
      )}
    </div>
  );
}
```

Create `src/components/admin/student-tier-control.tsx`:

```tsx
"use client";

import { cn } from "@/lib/utils";
import { TIER_LABELS } from "@/lib/subscription";
import type { SubscriptionTier } from "@/lib/subscription";

export function StudentTierControl({
  tier,
  tierUpdatedAt,
  className,
}: {
  studentId: string;
  tier: SubscriptionTier;
  tierUpdatedAt: string | null;
  canEdit: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn("rounded-lg border border-border-strong bg-card p-4", className)}
    >
      <p className="text-sm text-foreground">
        Currently on <strong>{TIER_LABELS[tier]}</strong>
        {tierUpdatedAt ? `, set ${tierUpdatedAt}` : ""}.
      </p>
    </div>
  );
}
```

Create `src/components/admin/student-danger-zone.tsx`:

```tsx
"use client";

import { cn } from "@/lib/utils";

export function StudentDangerZone({
  isActive,
  className,
}: {
  studentId: string;
  studentName: string;
  isActive: boolean;
  impact: Record<string, number>;
  canSuspend: boolean;
  canForceSignOut: boolean;
  canDelete: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn("rounded-lg border border-border-strong bg-card p-4", className)}
    >
      <p className="text-sm text-muted">
        This account is currently {isActive ? "active" : "suspended"}.
      </p>
    </div>
  );
}
```

- [ ] **Step 4: Verify the page loads**

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm run dev
```

Click a student from `/admin/students`. Confirm: the detail page renders, the breadcrumb returns to the list, and a made-up id (`/admin/students/nope`) renders the 404 rather than throwing.

- [ ] **Step 5: Commit**

```bash
git add src/lib/admin-student-data.ts "src/app/admin/(console)/students/[id]/page.tsx" \
  src/components/admin/student-profile-form.tsx \
  src/components/admin/student-tier-control.tsx \
  src/components/admin/student-danger-zone.tsx
git commit -m "feat(admin): add the student detail page"
```

---

## Task 11: Profile edit and tier override routes

**Files:**
- Create: `src/app/admin/api/students/[id]/route.ts` (`PATCH` only for now)
- Create: `src/app/admin/api/students/[id]/tier/route.ts`
- Modify: `src/lib/admin-audit.ts`
- Modify: `src/lib/admin-student-data.ts`
- Modify: `src/components/admin/student-profile-form.tsx`
- Modify: `src/components/admin/student-tier-control.tsx`

**Interfaces:**
- Consumes: `studentProfileSchema`, `studentTierSchema` (Task 8); `canEditStudent` (Task 7).
- Produces:
  - `updateStudentProfile(id, data): Promise<void>`
  - `setStudentTier(id, tier): Promise<void>`
  - `AuditAction` extended with `"student.update" | "student.tier"`

- [ ] **Step 1: Extend the audit action union**

In `src/lib/admin-audit.ts`, add to the `AuditAction` union:

```ts
  | "student.update"
  | "student.suspend"
  | "student.reactivate"
  | "student.tier"
  | "student.force_signout"
  | "student.delete"
```

All six are added now so later tasks do not have to touch this file again.

- [ ] **Step 2: Add the write helpers**

Append to `src/lib/admin-student-data.ts`:

```ts
import type { StudentProfileInput } from "@/lib/validators";

export async function updateStudentProfile(
  id: string,
  data: StudentProfileInput,
): Promise<void> {
  await db.user.update({ where: { id }, data });
}

export async function setStudentTier(
  id: string,
  tier: SubscriptionTier,
): Promise<void> {
  await db.user.update({
    where: { id },
    data: { tier, tierUpdatedAt: new Date() },
  });
}
```

And add to the type exports at the bottom of `src/lib/validators.ts`:

```ts
export type StudentProfileInput = z.infer<typeof studentProfileSchema>;
```

- [ ] **Step 3: Create the profile route**

Create `src/app/admin/api/students/[id]/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { canEditStudent } from "@/lib/admin-access";
import { recordAudit } from "@/lib/admin-audit";
import { studentProfileSchema } from "@/lib/validators";
import { getStudentDetail, updateStudentProfile } from "@/lib/admin-student-data";
import { fullName } from "@/lib/admin-student";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;

  if (!canEditStudent(guard.actor)) {
    return NextResponse.json({ error: "Not permitted" }, { status: 403 });
  }

  const { id } = await params;

  const parsed = studentProfileSchema.safeParse(await req.json());
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  const before = await getStudentDetail(id);
  if (!before) {
    return NextResponse.json({ error: "Student not found" }, { status: 404 });
  }

  try {
    await updateStudentProfile(id, parsed.data);
  } catch (error) {
    console.error("Student profile update failed:", error);

    // Only P2002 (unique constraint) is actually the admin's mistake. Telling
    // them "the email is already in use" after a connection drop or a bad
    // schoolId sends them chasing a duplicate that does not exist — so the
    // blame is only assigned when the database says so.
    //
    // Narrowed with the guard this codebase already uses (see
    // admin/api/admins/route.ts:64 and lib/user-account.ts:70). Duck-typing on
    // a `code` property would accept any object that happens to carry one and
    // report a duplicate-key conflict for something that was not one.
    if (
      error instanceof Prisma.PrismaClientKnownRequestError &&
      error.code === "P2002"
    ) {
      const target = error.meta?.target;
      const field =
        Array.isArray(target) && target.includes("phone") ? "phone number" : "email";
      return NextResponse.json(
        { error: `That ${field} already belongs to another account.` },
        { status: 409 },
      );
    }

    return NextResponse.json(
      { error: "Could not save. Please try again." },
      { status: 500 },
    );
  }

  // Only the fields that actually moved, so the audit row stays readable.
  const changes = Object.entries(parsed.data)
    .filter(([key, value]) => (before as Record<string, unknown>)[key] !== value)
    .map(([key, value]) => `${key}: ${String((before as Record<string, unknown>)[key] ?? "—")} → ${String(value ?? "—")}`)
    .join("; ");

  await recordAudit({
    actorId: guard.actor.id,
    action: "student.update",
    entity: "User",
    entityId: id,
    summary: `Updated ${fullName(before)}${changes ? ` — ${changes}` : " — no fields changed"}`,
  });

  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 4: Create the tier route**

Create `src/app/admin/api/students/[id]/tier/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { canEditStudent } from "@/lib/admin-access";
import { recordAudit } from "@/lib/admin-audit";
import { studentTierSchema } from "@/lib/validators";
import { getStudentDetail, setStudentTier } from "@/lib/admin-student-data";
import { fullName } from "@/lib/admin-student";
import { TIER_LABELS } from "@/lib/subscription";

export const dynamic = "force-dynamic";

// The manual override that stands in for billing. When a provider is wired, a
// Subscription row becomes the source of truth that writes User.tier and this
// route becomes the comp/correction path rather than the only path.
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;

  if (!canEditStudent(guard.actor)) {
    return NextResponse.json({ error: "Not permitted" }, { status: 403 });
  }

  const { id } = await params;

  const parsed = studentTierSchema.safeParse(await req.json());
  if (!parsed.success) {
    return NextResponse.json({ error: "Validation failed" }, { status: 400 });
  }

  const before = await getStudentDetail(id);
  if (!before) {
    return NextResponse.json({ error: "Student not found" }, { status: 404 });
  }

  await setStudentTier(id, parsed.data.tier);

  await recordAudit({
    actorId: guard.actor.id,
    action: "student.tier",
    entity: "User",
    entityId: id,
    summary: `Changed ${fullName(before)} from ${TIER_LABELS[before.tier]} to ${TIER_LABELS[parsed.data.tier]}`,
  });

  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 5: Wire the profile form**

Replace `src/components/admin/student-profile-form.tsx` with an editable version:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { StatusBanner } from "@/components/admin/status-banner";
import { CLASS_LEVELS } from "@/lib/curriculum-scope";
import { TRACKS } from "@/lib/admin-student";

const LABEL_CLS = "text-[11px] font-semibold uppercase tracking-wider text-muted";
const INPUT_CLS =
  "px-3 py-2 rounded-lg border border-border bg-card text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60";

export function StudentProfileForm({
  student,
  canEdit,
  className,
}: {
  student: {
    id: string;
    firstName: string;
    lastName: string;
    email: string | null;
    phone: string | null;
    classLevel: string | null;
    track: string | null;
    state: string | null;
  };
  canEdit: boolean;
  className?: string;
}) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);

    const form = new FormData(event.currentTarget);
    // Empty strings are "not supplied", not "set to empty" — the schema marks
    // these fields optional, and sending "" would fail its validators.
    const body: Record<string, string> = {};
    for (const [key, value] of form.entries()) {
      const text = String(value).trim();
      if (text) body[key] = text;
    }

    try {
      const res = await fetch(`/admin/api/students/${student.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not save");
        return;
      }
      setSaved(true);
      router.refresh();
    } catch {
      setError("Could not reach the server");
    } finally {
      setSaving(false);
    }
  }

  if (!canEdit) {
    return (
      <p className={cn("text-sm text-muted", className)}>
        You do not have permission to edit this profile.
      </p>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className={cn("rounded-lg border border-border-strong bg-card p-4", className)}
    >
      {error && <StatusBanner tone="error" title={error} className="mb-4" />}
      {saved && !error && (
        <StatusBanner tone="success" title="Profile saved" className="mb-4" />
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field name="firstName" label="First name" defaultValue={student.firstName} required />
        <Field name="lastName" label="Last name" defaultValue={student.lastName} required />
        <Field name="email" label="Email" type="email" defaultValue={student.email ?? ""} />
        <Field name="phone" label="Phone" defaultValue={student.phone ?? ""} />
        <Field name="state" label="State" defaultValue={student.state ?? ""} />

        <div className="flex flex-col gap-1">
          <label htmlFor="classLevel" className={LABEL_CLS}>Class</label>
          <select id="classLevel" name="classLevel" defaultValue={student.classLevel ?? ""} className={INPUT_CLS}>
            <option value="">Not set</option>
            {CLASS_LEVELS.map((level) => (
              <option key={level} value={level}>{level}</option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="track" className={LABEL_CLS}>Track</label>
          <select id="track" name="track" defaultValue={student.track ?? ""} className={INPUT_CLS}>
            <option value="">Not set</option>
            {TRACKS.map((track) => (
              <option key={track} value={track}>
                {track.charAt(0) + track.slice(1).toLowerCase()}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4">
        <Button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save profile"}
        </Button>
      </div>
    </form>
  );
}

function Field({
  name,
  label,
  defaultValue,
  type = "text",
  required,
}: {
  name: string;
  label: string;
  defaultValue: string;
  type?: string;
  required?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={name} className={LABEL_CLS}>{label}</label>
      <input
        id={name}
        name={name}
        type={type}
        defaultValue={defaultValue}
        required={required}
        className={INPUT_CLS}
      />
    </div>
  );
}
```

Check `src/components/admin/status-banner.tsx` for the exact `tone` values it accepts; if it has no `"error"` tone, use whichever tone it uses for failures.

Then remove `schoolName` from the `StudentProfileForm` props passed in `students/[id]/page.tsx`, since the form no longer renders it.

- [ ] **Step 6: Wire the tier control**

Replace the body of `src/components/admin/student-tier-control.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { StatusBanner } from "@/components/admin/status-banner";
import { SUBSCRIPTION_TIERS, TIER_LABELS, type SubscriptionTier } from "@/lib/subscription";

const INPUT_CLS =
  "px-3 py-2 rounded-lg border border-border bg-card text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60";

export function StudentTierControl({
  studentId,
  tier,
  tierUpdatedAt,
  canEdit,
  className,
}: {
  studentId: string;
  tier: SubscriptionTier;
  tierUpdatedAt: string | null;
  canEdit: boolean;
  className?: string;
}) {
  const router = useRouter();
  const [next, setNext] = useState<SubscriptionTier>(tier);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/admin/api/students/${studentId}/tier`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier: next }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error ?? "Could not change the plan");
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={cn("rounded-lg border border-border-strong bg-card p-4", className)}>
      {error && <StatusBanner tone="error" title={error} className="mb-4" />}

      <p className="text-sm text-foreground">
        Currently on <strong>{TIER_LABELS[tier]}</strong>
        {tierUpdatedAt ? `, set ${tierUpdatedAt}` : ""}.
      </p>

      {canEdit && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label htmlFor="tier" className="sr-only">Plan</label>
          <select
            id="tier"
            value={next}
            onChange={(e) => setNext(e.target.value as SubscriptionTier)}
            className={INPUT_CLS}
          >
            {SUBSCRIPTION_TIERS.map((value) => (
              <option key={value} value={value}>{TIER_LABELS[value]}</option>
            ))}
          </select>
          <Button onClick={save} disabled={saving || next === tier}>
            {saving ? "Saving…" : "Change plan"}
          </Button>
          <p className="w-full text-xs text-muted">
            Manual override until billing is wired. Every change is recorded in
            the audit log.
          </p>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 7: Verify end to end**

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm test
npm run dev
```

On a student's detail page: change a name and save — the page refreshes with the new value; enter a malformed email — a 400 message appears rather than a crash; set an email that already belongs to another account — the duplicate message appears; change the plan — the badge updates. Then confirm both actions wrote audit rows:

```sql
SELECT action, summary, "createdAt" FROM "AdminAudit"
ORDER BY "createdAt" DESC LIMIT 5;
```

Expected: a `student.update` row naming the changed fields, and a `student.tier` row naming both tiers.

- [ ] **Step 8: Commit**

```bash
git add src/lib/admin-audit.ts src/lib/admin-student-data.ts src/lib/validators.ts \
  "src/app/admin/api/students/[id]/route.ts" \
  "src/app/admin/api/students/[id]/tier/route.ts" \
  src/components/admin/student-profile-form.tsx \
  src/components/admin/student-tier-control.tsx \
  "src/app/admin/(console)/students/[id]/page.tsx"
git commit -m "feat(admin): edit student profiles and override subscription tier"
```

---

## Task 12: Suspension and its enforcement

**Files:**
- Create: `src/app/admin/api/students/[id]/status/route.ts`
- Modify: `src/lib/admin-student-data.ts`
- Modify: `src/lib/auth.ts`
- Modify: `src/components/admin/student-danger-zone.tsx`

**Interfaces:**
- Consumes: `studentStatusSchema` (Task 8); `canSuspendStudent` (Task 7); `isSessionRevoked` (Task 3).
- Produces: `setStudentActive(id, isActive, reason): Promise<void>`

This is the task where suspension starts to bite. Read `node_modules/next/dist/docs/` and the next-auth v5 types for the `jwt` callback's return contract **before** Step 3.

- [ ] **Step 1: Add the write helper**

Append to `src/lib/admin-student-data.ts`:

```ts
export async function setStudentActive(
  id: string,
  isActive: boolean,
  reason: string | null,
): Promise<void> {
  await db.user.update({
    where: { id },
    data: isActive
      ? { isActive: true, suspendedAt: null, suspendedReason: null }
      : { isActive: false, suspendedAt: new Date(), suspendedReason: reason },
  });
}
```

Reactivation clears the stamp and reason: a stale "suspended 3 months ago" on a live account misleads the next admin who reads it.

- [ ] **Step 2: Create the status route**

Create `src/app/admin/api/students/[id]/status/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { canSuspendStudent } from "@/lib/admin-access";
import { recordAudit } from "@/lib/admin-audit";
import { studentStatusSchema } from "@/lib/validators";
import { getStudentDetail, setStudentActive } from "@/lib/admin-student-data";
import { fullName } from "@/lib/admin-student";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;

  if (!canSuspendStudent(guard.actor)) {
    return NextResponse.json({ error: "Not permitted" }, { status: 403 });
  }

  const { id } = await params;

  const parsed = studentStatusSchema.safeParse(await req.json());
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  const target = await getStudentDetail(id);
  if (!target) {
    return NextResponse.json({ error: "Student not found" }, { status: 404 });
  }

  const { isActive, reason } = parsed.data;
  await setStudentActive(id, isActive, reason ?? null);

  await recordAudit({
    actorId: guard.actor.id,
    action: isActive ? "student.reactivate" : "student.suspend",
    entity: "User",
    entityId: id,
    summary: isActive
      ? `Reactivated ${fullName(target)}`
      : `Suspended ${fullName(target)} — ${reason}`,
  });

  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 3: Block suspended accounts at sign-in**

In `src/lib/auth.ts`, inside `authorize()`, after the `if (!user || !user.passwordHash) return null;` line, add:

```ts
        // A suspended account must not be able to start a new session. The jwt
        // callback below handles the sessions that are already live.
        if (!user.isActive) return null;
```

- [ ] **Step 4: Cut off live sessions on the profile refresh**

In `src/lib/auth.ts`:

1. Add `isActive: true` and `sessionsValidFrom: true` to the `PROFILE_SELECT` object.

   **Do NOT add them to `CachedProfile` or to the `cache.profile = { … }` assignment.** They are read fresh on every refresh purely to decide revocation, and are then discarded. Caching them would be worse than useless: a cached `isActive: true` is exactly the stale value the whole mechanism exists to avoid trusting, and `sessionsValidFrom` is a `Date`, which does not belong in a JWT payload. `CachedProfile` holds display data only — the existing assignment lists its fields explicitly, so leave that list alone and the two new fields will correctly stay out of the token.
2. Import the rule: `import { isSessionRevoked } from "@/lib/account-status";`
3. Inside the `jwt` callback's `try` block, handle BOTH the missing-user case and the revoked case. Replace the bare `if (profile) {` with:

```ts
        // A user row that no longer exists has no session. `findUnique`
        // returning null is authoritative here: a database outage THROWS and
        // is caught below, keeping the cached profile, so null means the row
        // is genuinely gone — deleted. Without this, a deleted student's token
        // stays valid until it expires, while a merely suspended student's is
        // revoked within the TTL — the more severe action getting the weaker
        // enforcement.
        if (!profile) return null;

        // Suspension and force sign-out have to bite on a token that is
        // already live, not only at the next sign-in. This runs at most once
        // per PROFILE_TTL_MS, so the delay is bounded by that.
        if (isSessionRevoked(profile, token.iat)) {
          return null;
        }
```

and de-indent the `cache.profile = { … }` / `cache.profileAt = …` assignments out of the old `if (profile)` block, since the early return now guarantees `profile` is non-null.

**Why the ordering matters:** the `!profile` check must sit INSIDE the existing `try`, so that a thrown database error still reaches the `catch` and keeps the cached profile. Moving it outside would turn an outage into a mass sign-out.

**Contract already verified against the installed beta — returning `null` is correct.** `node_modules/@auth/core/index.d.ts:331` types the callback as `jwt?: (params: {...}) => Awaitable<JWT | null>`, so `return null` is permitted and is how an invalid token is signalled.

`token.iat` needs **no cast**: `DefaultJWT` declares `iat?: number` (`node_modules/@auth/core/jwt.d.ts:82`), so it is already `number | undefined` — exactly the second parameter `isSessionRevoked` expects.

The existing `catch` around the profile fetch must stay exactly as it is — it keeps the cached profile on a database failure rather than throwing. A database outage must sign nobody out.

- [ ] **Step 5: Wire the suspend control**

Replace `src/components/admin/student-danger-zone.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { StatusBanner } from "@/components/admin/status-banner";

const INPUT_CLS =
  "w-full px-3 py-2 rounded-lg border border-border bg-card text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60";

export function StudentDangerZone({
  studentId,
  studentName,
  isActive,
  canSuspend,
  className,
}: {
  studentId: string;
  studentName: string;
  isActive: boolean;
  impact: Record<string, number>;
  canSuspend: boolean;
  canForceSignOut: boolean;
  canDelete: boolean;
  className?: string;
}) {
  const router = useRouter();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function setActive(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/admin/api/students/${studentId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          next ? { isActive: true } : { isActive: false, reason },
        ),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error ?? "Could not change the status");
        return;
      }
      setReason("");
      router.refresh();
    } catch {
      setError("Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  if (!canSuspend) {
    return (
      <p className={cn("text-sm text-muted", className)}>
        You do not have permission to change this account.
      </p>
    );
  }

  return (
    <div
      className={cn(
        "rounded-lg border border-border-strong bg-card p-4",
        className,
      )}
    >
      {error && <StatusBanner tone="error" title={error} className="mb-4" />}

      {isActive ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted">
            Suspending blocks {studentName} from signing in and ends any live
            session within a minute. It is reversible and destroys nothing.
          </p>
          <label htmlFor="suspend-reason" className="sr-only">
            Reason for suspension
          </label>
          <input
            id="suspend-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (recorded in the audit log)"
            className={INPUT_CLS}
          />
          <div>
            <Button
              onClick={() => setActive(false)}
              disabled={busy || reason.trim().length < 3}
            >
              {busy ? "Suspending…" : "Suspend account"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted">
            {studentName} is suspended and cannot sign in.
          </p>
          <div>
            <Button onClick={() => setActive(true)} disabled={busy}>
              {busy ? "Reactivating…" : "Reactivate account"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Verify enforcement by hand**

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm test
npm run dev
```

**The two-browser test cannot be run from here** — it needs a student's password, which nobody working this plan has. It is handed to the repository owner as a follow-up (see "Owner follow-up" below). In its place, prove the same chain in two halves: the data half by execution, the wiring half by inspection.

**6a. Data half — execute it against the real database.** Write a THROWAWAY script (do not commit it; delete it when done) that wraps everything in an interactive transaction and throws at the end so it ROLLS BACK, leaving no student actually suspended:

```ts
// scripts/zz-suspension-check.mts — temporary, delete after running
import { PrismaClient } from "@prisma/client";
import { isSessionRevoked } from "../src/lib/account-status";

const db = new PrismaClient();
const PROFILE = { isActive: true, sessionsValidFrom: true } as const;
const IAT = Math.floor(Date.now() / 1000);

await db
  .$transaction(async (tx) => {
    const s = await tx.user.findFirstOrThrow({ where: { role: "STUDENT" }, select: { id: true } });

    const before = await tx.user.findUniqueOrThrow({ where: { id: s.id }, select: PROFILE });
    console.log("active   ->", isSessionRevoked(before, IAT), "(expect false)");

    await tx.user.update({
      where: { id: s.id },
      data: { isActive: false, suspendedAt: new Date(), suspendedReason: "rollback probe" },
    });
    const after = await tx.user.findUniqueOrThrow({ where: { id: s.id }, select: PROFILE });
    console.log("suspended->", isSessionRevoked(after, IAT), "(expect true)");

    await tx.user.update({
      where: { id: s.id },
      data: { isActive: true, suspendedAt: null, suspendedReason: null, sessionsValidFrom: new Date() },
    });
    const revoked = await tx.user.findUniqueOrThrow({ where: { id: s.id }, select: PROFILE });
    console.log("signed-out->", isSessionRevoked(revoked, IAT), "(expect true)");
    console.log("fresh token->", isSessionRevoked(revoked, IAT + 120), "(expect false)");

    throw new Error("ROLLBACK");
  })
  .catch((e) => {
    if (e.message !== "ROLLBACK") throw e;
    console.log("rolled back — no student left suspended");
  })
  .finally(() => db.$disconnect());
```

Expected: `false`, `true`, `true`, `false`, then the rollback line. That proves the columns, the `PROFILE_SELECT` shape and the revocation rule agree against real data — including that a token issued after a force sign-out survives, so an account is not permanently locked out. Confirm afterwards that the student is still active:

```sql
SELECT "isActive", "suspendedReason" FROM "User" WHERE role = 'STUDENT';
```

**6b. Wiring half — quote the three lines in your report.** The parts only NextAuth can exercise:
1. `authorize()` returns `null` when `!user.isActive`.
2. `PROFILE_SELECT` contains BOTH `isActive: true` and `sessionsValidFrom: true`.
3. The `jwt` callback calls `isSessionRevoked(profile, token.iat)` and `return null`s on true — and the existing `catch` that keeps the cached profile on a database failure is UNCHANGED, so an outage signs nobody out.

**6c. Audit rows.** Suspend and reactivate a student through the admin UI (this is reversible and leaves the account active), then:

```sql
SELECT action, summary FROM "AdminAudit" WHERE action LIKE 'student.%' ORDER BY "createdAt" DESC LIMIT 5;
```

**Owner follow-up (cannot be automated):** with a known student login, sign in as that student in browser A, suspend them from the admin in browser B, and confirm browser A is signed out within 60 seconds; then confirm they cannot sign in again, and that reactivating restores access. This is the only step that exercises the live token path end to end.

- [ ] **Step 7: Commit**

```bash
git add src/lib/auth.ts src/lib/admin-student-data.ts \
  "src/app/admin/api/students/[id]/status/route.ts" \
  src/components/admin/student-danger-zone.tsx
git commit -m "feat(admin): suspend students and enforce it on live sessions"
```

---

## Task 13: Force sign-out and account deletion

**Files:**
- Create: `src/app/admin/api/students/[id]/force-signout/route.ts`
- Modify: `src/app/admin/api/students/[id]/route.ts` (add `DELETE`)
- Modify: `src/lib/admin-student-data.ts`
- Modify: `src/components/admin/student-danger-zone.tsx`

**Interfaces:**
- Consumes: `canForceSignOutStudent`, `canDeleteStudent` (Task 7); `getStudentDeletionImpact` (Task 10).
- Produces: `revokeStudentSessions(id): Promise<void>`, `deleteStudent(id): Promise<void>`

- [ ] **Step 1: Add the write helpers**

Append to `src/lib/admin-student-data.ts`:

```ts
/**
 * Stamps the revocation instant. Every token issued at or before it is rejected
 * on the next profile refresh — see isSessionRevoked in account-status.ts.
 *
 * This is not a password reset: the account keeps its password and the student
 * simply signs in again. Real password reset needs a reset-token model and an
 * email subsystem, neither of which exists yet.
 */
export async function revokeStudentSessions(id: string): Promise<void> {
  await db.user.update({
    where: { id },
    data: { sessionsValidFrom: new Date() },
  });
}

export async function deleteStudent(id: string): Promise<void> {
  await db.user.delete({ where: { id } });
}
```

- [ ] **Step 2: Create the force sign-out route**

Create `src/app/admin/api/students/[id]/force-signout/route.ts`:

```ts
import { NextResponse } from "next/server";
import { requireOwnerApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { getStudentDetail, revokeStudentSessions } from "@/lib/admin-student-data";
import { fullName } from "@/lib/admin-student";

export const dynamic = "force-dynamic";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = await requireOwnerApi();
  if (!guard.ok) return guard.response;

  const { id } = await params;

  const target = await getStudentDetail(id);
  if (!target) {
    return NextResponse.json({ error: "Student not found" }, { status: 404 });
  }

  await revokeStudentSessions(id);

  await recordAudit({
    actorId: guard.actor.id,
    action: "student.force_signout",
    entity: "User",
    entityId: id,
    summary: `Signed ${fullName(target)} out of every device`,
  });

  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 3: Add the DELETE handler**

Append to `src/app/admin/api/students/[id]/route.ts`, and add `requireOwnerApi` to its imports from `@/lib/admin-session`:

```ts
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  // Owner only, and enforced here regardless of what the UI showed.
  const guard = await requireOwnerApi();
  if (!guard.ok) return guard.response;

  const { id } = await params;

  const target = await getStudentDetail(id);
  if (!target) {
    return NextResponse.json({ error: "Student not found" }, { status: 404 });
  }

  const name = fullName(target);
  const impact = await getStudentDeletionImpact(id);
  const destroyed = Object.entries(impact)
    .filter(([, count]) => count > 0)
    .map(([label, count]) => `${label}: ${count}`)
    .join("; ");

  // Audited BEFORE the delete: the cascade takes the account with it, and a
  // failed audit write must not be what leaves the deletion unrecorded.
  await recordAudit({
    actorId: guard.actor.id,
    action: "student.delete",
    entity: "User",
    entityId: id,
    summary: `Deleted ${name} (${target.email ?? target.phone ?? "no contact"})${
      destroyed ? ` — destroyed ${destroyed}` : " — no associated records"
    }`,
  });

  await deleteStudent(id);

  return NextResponse.json({ ok: true });
}
```

Add the imports this handler needs at the top of the file:

```ts
import { deleteStudent, getStudentDeletionImpact } from "@/lib/admin-student-data";
```

Use the `deleteStudent` helper from Step 1 rather than calling `db.user.delete` inline. Both work, but routing every student write through `admin-student-data.ts` is what keeps this plan's lib split intact — a route file importing `db` directly is the first crack in it. The route should not import `db` at all.

- [ ] **Step 4: Add both controls to the danger zone**

In `src/components/admin/student-danger-zone.tsx`, add below the suspend/reactivate block, inside the same wrapper `div`:

```tsx
      {(canForceSignOut || canDelete) && (
        <div className="mt-4 border-t border-border-strong pt-4 flex flex-col gap-4">
          {canForceSignOut && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-muted">
                Force sign-out ends every live session within a minute. The
                password is unchanged — {studentName} can sign straight back in.
                Use it when a session is on a lost or shared device.
              </p>
              <div>
                <Button
                  variant="secondary"
                  onClick={forceSignOut}
                  disabled={busy}
                >
                  {busy ? "Working…" : "Force sign-out"}
                </Button>
              </div>
            </div>
          )}

          {canDelete && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-muted">
                Deleting {studentName} is permanent and destroys:
              </p>
              <ul className="text-sm text-muted">
                {Object.entries(impact)
                  .filter(([, count]) => count > 0)
                  .map(([label, count]) => (
                    <li key={label}>
                      <span className="tabular-nums text-foreground">{count}</span>{" "}
                      {label.toLowerCase()}
                    </li>
                  ))}
                {Object.values(impact).every((count) => count === 0) && (
                  <li>No associated records.</li>
                )}
              </ul>
              <p className="text-sm text-muted">
                Suspending instead keeps all of it and is reversible. To delete,
                type <strong>{studentName}</strong> below.
              </p>
              <label htmlFor="delete-confirm" className="sr-only">
                Type the student name to confirm deletion
              </label>
              <input
                id="delete-confirm"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={studentName}
                className={INPUT_CLS}
              />
              <div>
                <Button
                  onClick={remove}
                  disabled={busy || confirmText !== studentName}
                >
                  {busy ? "Deleting…" : "Delete account permanently"}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
```

Add the state and the two handlers alongside the existing ones, and stop returning early when `!canSuspend` — an owner who somehow lacks suspend rights must still see their own controls. Replace the early return with a per-block guard:

```tsx
  const [confirmText, setConfirmText] = useState("");

  async function forceSignOut() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/admin/api/students/${studentId}/force-signout`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error ?? "Could not sign the student out");
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/admin/api/students/${studentId}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error ?? "Could not delete the account");
        return;
      }
      // The record is gone; staying on its detail page would 404 on refresh.
      router.push("/admin/students");
    } catch {
      setError("Could not reach the server");
    } finally {
      setBusy(false);
    }
  }
```

and wrap the suspend/reactivate markup in `{canSuspend && ( ... )}` instead of the early return.

- [ ] **Step 5: Verify by hand**

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm test
npm run dev
```

Signed in as the **owner**:

1. Force sign-out a student who is signed in elsewhere — they are signed out within 60 seconds, and signing in again works immediately.
2. Open the delete control — the impact list shows real counts; the button stays disabled until the name is typed exactly.
3. Delete a throwaway test account — you are returned to the list and it is gone.

**The non-owner 403 path — verify by inspection, not by signing in.** This database currently holds exactly one admin, the owner, so there is no non-owner account to sign in as. Do **not** create one to run this check: adding an admin to the live database is a side effect outside this task's scope.

Verify instead, and record each in the report:

1. Both owner-only route files call `requireOwnerApi()` as their FIRST statement and return `guard.response` when `!guard.ok` — read `src/app/admin/api/students/[id]/force-signout/route.ts` and the `DELETE` handler in `src/app/admin/api/students/[id]/route.ts` and quote the lines.
2. The detail page passes `canForceSignOutStudent(admin)` and `canDeleteStudent(admin)` into `StudentDangerZone`, and that component renders each control only when its flag is true.
3. `scripts/test-admin-access.mts` already pins both predicates as false for a non-owner and false for a deactivated owner (added in Task 7, reviewed clean). Re-run that file and cite the result.

Together these show the refusal is enforced server-side regardless of what the UI shows. If the owner later creates a second admin at `/admin/team`, the live `curl` check is worth running then:

```bash
curl -i -X DELETE http://localhost:3000/admin/api/students/<id> \
  -H "Cookie: scholarscrib.admin-session=<non-owner session cookie>"
# Expected: 403
```

Then confirm the audit rows exist for both actions.

- [ ] **Step 6: Commit**

```bash
git add "src/app/admin/api/students/[id]/force-signout/route.ts" \
  "src/app/admin/api/students/[id]/route.ts" \
  src/lib/admin-student-data.ts src/components/admin/student-danger-zone.tsx
git commit -m "feat(admin): add owner-only force sign-out and student deletion"
```

---

## Task 14: Audit log viewer

**Files:**
- Create: `src/lib/admin-audit-filter.ts`
- Create: `src/lib/admin-audit-data.ts`
- Create: `src/app/admin/(console)/audit/page.tsx`
- Create: `scripts/test-admin-audit-filter.mts`
- Modify: `src/lib/admin-nav.ts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `AdminTable` family, `EmptyState` (Task 5); `pageWindow`, `Pagination` (Task 6); `AuditAction` (Task 11).
- Produces:
  - `AUDIT_PAGE_SIZE = 50`
  - `type RawAuditParams = { actor?: string; action?: string; entity?: string; from?: string; to?: string; page?: string }`
  - `type AuditFilter = { actorId: string | null; action: string | null; entity: string | null; from: Date | null; to: Date | null; page: number }`
  - `normaliseAuditFilter(params: RawAuditParams): AuditFilter`
  - `auditFilterParams(filter: AuditFilter): Record<string, string>`
  - `listAuditEntries(filter): Promise<{ rows: AuditRow[]; total: number }>`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-audit-filter.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AUDIT_PAGE_SIZE,
  auditFilterParams,
  normaliseAuditFilter,
} from "../src/lib/admin-audit-filter";

test("empty params give an unfiltered first page", () => {
  const f = normaliseAuditFilter({});
  assert.deepEqual(f, {
    actorId: null,
    action: null,
    entity: null,
    from: null,
    to: null,
    page: 1,
  });
});

test("a known action passes through", () => {
  assert.equal(normaliseAuditFilter({ action: "student.delete" }).action, "student.delete");
});

test("an unknown action is dropped", () => {
  // The column is free text, so an unknown value would not throw — it would
  // silently return nothing, which reads as "no such activity" rather than
  // "no such action".
  assert.equal(normaliseAuditFilter({ action: "student.launder" }).action, null);
});

test("valid dates parse and invalid ones are dropped", () => {
  const f = normaliseAuditFilter({ from: "2026-08-01", to: "2026-08-27" });
  assert.equal(f.from?.toISOString().slice(0, 10), "2026-08-01");
  assert.equal(f.to?.toISOString().slice(0, 10), "2026-08-27");
  assert.equal(normaliseAuditFilter({ from: "not-a-date" }).from, null);
  assert.equal(normaliseAuditFilter({ from: "" }).from, null);
});

test("a reversed range is dropped rather than returning nothing", () => {
  // from > to can only ever match zero rows; an empty table would look like
  // "nothing happened" instead of "your dates are backwards".
  const f = normaliseAuditFilter({ from: "2026-08-27", to: "2026-08-01" });
  assert.equal(f.from, null);
  assert.equal(f.to, null);
});

test("a non-numeric page falls back to one", () => {
  assert.equal(normaliseAuditFilter({ page: "abc" }).page, 1);
  assert.equal(normaliseAuditFilter({ page: "-2" }).page, 1);
});

test("filter params round-trip without the page", () => {
  const f = normaliseAuditFilter({ action: "student.tier", from: "2026-08-01", page: "4" });
  const params = auditFilterParams(f);
  assert.equal(params.action, "student.tier");
  assert.equal(params.from, "2026-08-01");
  assert.equal("page" in params, false);
});

test("the page size is larger than the student list's", () => {
  // Audit rows are one line each; a 25-row page would mean constant paging.
  assert.equal(AUDIT_PAGE_SIZE, 50);
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-audit-filter.mts
```

Expected: FAIL — cannot find module.

- [ ] **Step 3: Create the filter module**

Create `src/lib/admin-audit-filter.ts`:

```ts
import type { AuditAction } from "@/lib/admin-audit";

/**
 * Narrowing the audit log. Pure — no Prisma — so the date and enum handling
 * can be tested without a database.
 */

export const AUDIT_PAGE_SIZE = 50;

// Kept in step with the AuditAction union in admin-audit.ts. Listed here as
// values because a type cannot be iterated to build a <select>.
export const AUDIT_ACTIONS: readonly AuditAction[] = [
  "question.create",
  "question.update",
  "question.delete",
  "question.import",
  "lesson.import",
  "admin.create",
  "admin.deactivate",
  "admin.reactivate",
  "student.update",
  "student.suspend",
  "student.reactivate",
  "student.tier",
  "student.force_signout",
  "student.delete",
];

export const AUDIT_ENTITIES = ["Question", "Lesson", "Admin", "User"] as const;

export interface RawAuditParams {
  actor?: string;
  action?: string;
  entity?: string;
  from?: string;
  to?: string;
  page?: string;
}

export interface AuditFilter {
  actorId: string | null;
  action: string | null;
  entity: string | null;
  from: Date | null;
  to: Date | null;
  page: number;
}

function parseDate(value: string | undefined): Date | null {
  if (!value?.trim()) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * A reversed range is dropped rather than passed through: `from > to` can only
 * ever match zero rows, and an empty table reads as "nothing happened" rather
 * than "your dates are backwards".
 */
export function normaliseAuditFilter(params: RawAuditParams): AuditFilter {
  const page = Number.parseInt(params.page ?? "", 10);

  let from = parseDate(params.from);
  let to = parseDate(params.to);
  if (from && to && from > to) {
    from = null;
    to = null;
  }

  const action =
    params.action && (AUDIT_ACTIONS as readonly string[]).includes(params.action)
      ? params.action
      : null;

  const entity =
    params.entity && (AUDIT_ENTITIES as readonly string[]).includes(params.entity)
      ? params.entity
      : null;

  return {
    actorId: params.actor?.trim() ? params.actor : null,
    action,
    entity,
    from,
    to,
    page: Number.isFinite(page) && page >= 1 ? page : 1,
  };
}

export function auditFilterParams(filter: AuditFilter): Record<string, string> {
  const params: Record<string, string> = {};
  if (filter.actorId) params.actor = filter.actorId;
  if (filter.action) params.action = filter.action;
  if (filter.entity) params.entity = filter.entity;
  if (filter.from) params.from = filter.from.toISOString().slice(0, 10);
  if (filter.to) params.to = filter.to.toISOString().slice(0, 10);
  return params;
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
node --import tsx --test --test-force-exit scripts/test-admin-audit-filter.mts
```

Expected: PASS — 8 tests.

- [ ] **Step 5: Create the data module and the page**

Create `src/lib/admin-audit-data.ts`:

```ts
import { db } from "@/lib/db";
import { AUDIT_PAGE_SIZE, type AuditFilter } from "@/lib/admin-audit-filter";

export interface AuditRow {
  id: string;
  action: string;
  entity: string;
  entityId: string | null;
  summary: string;
  createdAt: Date;
  actorLabel: string;
}

export async function listAuditEntries(
  filter: AuditFilter,
): Promise<{ rows: AuditRow[]; total: number }> {
  const where = {
    ...(filter.actorId ? { actorId: filter.actorId } : {}),
    ...(filter.action ? { action: filter.action } : {}),
    ...(filter.entity ? { entity: filter.entity } : {}),
    ...(filter.from || filter.to
      ? {
          createdAt: {
            ...(filter.from ? { gte: filter.from } : {}),
            // The `to` date is a day, so include everything within it rather
            // than stopping at midnight and silently dropping that day's rows.
            //
            // Both bounds are UTC: `new Date("2026-08-01")` parses as UTC
            // midnight, so in WAT (+01:00) a day runs 01:00 to 00:59 local.
            // An action logged at 00:30 local therefore falls under the
            // previous day's filter. Acceptable for a coarse date-range
            // filter over a log that also shows each row's exact timestamp;
            // fixing it properly means resolving the admin's timezone rather
            // than assuming one, which is not worth it here.
            ...(filter.to
              ? { lte: new Date(filter.to.getTime() + 24 * 60 * 60 * 1000 - 1) }
              : {}),
          },
        }
      : {}),
  };

  // Counted FIRST, not in parallel with the fetch — same rule as listStudents.
  // Skipping by an unclamped page walks past the end of the log and renders
  // "no matching activity" over a log full of entries, which reads as "nothing
  // happened" rather than "your page number is out of range".
  const total = await db.adminAudit.count({ where });
  const totalPages = Math.max(1, Math.ceil(total / AUDIT_PAGE_SIZE));
  const page = Math.min(Math.max(1, filter.page), totalPages);

  const entries = await db.adminAudit.findMany({
    where,
    select: {
      id: true,
      action: true,
      entity: true,
      entityId: true,
      summary: true,
      createdAt: true,
      actor: { select: { email: true, username: true } },
    },
    orderBy: { createdAt: "desc" },
    skip: (page - 1) * AUDIT_PAGE_SIZE,
    take: AUDIT_PAGE_SIZE,
  });

  return {
    total,
    rows: entries.map((entry) => ({
      id: entry.id,
      action: entry.action,
      entity: entry.entity,
      entityId: entry.entityId,
      summary: entry.summary,
      createdAt: entry.createdAt,
      actorLabel: entry.actor.email ?? entry.actor.username ?? "Unknown admin",
    })),
  };
}

/** Actors who have actually acted, for the filter dropdown. */
export async function listAuditActors(): Promise<
  Array<{ id: string; label: string }>
> {
  const admins = await db.admin.findMany({
    where: { audits: { some: {} } },
    select: { id: true, email: true, username: true },
    orderBy: { createdAt: "asc" },
  });
  return admins.map((admin) => ({
    id: admin.id,
    label: admin.email ?? admin.username ?? admin.id,
  }));
}
```

Create `src/app/admin/(console)/audit/page.tsx`:

```tsx
import { requireAdminPage } from "@/lib/admin-session";
import { PageHeader } from "@/components/ui/page-header";
import { AdminTable, AdminTd, AdminTh, AdminTr } from "@/components/admin/admin-table";
import { EmptyState } from "@/components/admin/empty-state";
import { Pagination, pageWindow } from "@/components/admin/pagination";
import { AuditFilterBar } from "@/components/admin/audit-filter-bar";
import {
  AUDIT_PAGE_SIZE,
  auditFilterParams,
  normaliseAuditFilter,
  type RawAuditParams,
} from "@/lib/admin-audit-filter";
import { listAuditActors, listAuditEntries } from "@/lib/admin-audit-data";

export const dynamic = "force-dynamic";

const STAMP = new Intl.DateTimeFormat("en-NG", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export default async function AdminAuditPage({
  searchParams,
}: {
  searchParams: Promise<RawAuditParams>;
}) {
  await requireAdminPage();

  const filter = normaliseAuditFilter(await searchParams);
  const [{ rows, total }, actors] = await Promise.all([
    listAuditEntries(filter),
    listAuditActors(),
  ]);
  const win = pageWindow({ page: filter.page, pageSize: AUDIT_PAGE_SIZE, total });

  return (
    <div>
      <PageHeader
        title="Audit log"
        description={`${total} recorded ${total === 1 ? "action" : "actions"}.`}
      />

      <AuditFilterBar filter={filter} actors={actors} />

      {rows.length === 0 ? (
        <EmptyState
          title="No matching activity"
          message="Widen the date range or clear the filters."
        />
      ) : (
        <>
          <AdminTable caption="Recorded admin actions">
            <thead>
              <tr className="border-b border-border-strong">
                <AdminTh>When</AdminTh>
                <AdminTh>Who</AdminTh>
                <AdminTh>Action</AdminTh>
                <AdminTh>What</AdminTh>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <AdminTr key={row.id}>
                  <AdminTd className="whitespace-nowrap tabular-nums text-muted">
                    {STAMP.format(row.createdAt)}
                  </AdminTd>
                  <AdminTd className="text-muted">{row.actorLabel}</AdminTd>
                  <AdminTd>
                    <code className="rounded bg-secondary px-1.5 py-0.5 text-xs text-foreground">
                      {row.action}
                    </code>
                  </AdminTd>
                  <AdminTd className="text-foreground">{row.summary}</AdminTd>
                </AdminTr>
              ))}
            </tbody>
          </AdminTable>

          <Pagination
            window={win}
            basePath="/admin/audit"
            params={auditFilterParams(filter)}
          />
        </>
      )}
    </div>
  );
}
```

Create `src/components/admin/audit-filter-bar.tsx` following the exact shape of `student-filter-bar.tsx` from Task 9 — same `SELECT_CLS`, `LABEL_CLS`, and the same `go()` helper that rebuilds the query string and calls `router.replace("/admin/audit?...")`. It renders four controls: an actor `<select>` over the `actors` prop (`value={actor.id}`, `label` as the text), an action `<select>` over `AUDIT_ACTIONS`, and two `<input type="date">` controls bound to `from` and `to`, each defaulting to the filter's current value formatted as `YYYY-MM-DD`.

- [ ] **Step 6: Add the nav entry**

In `src/lib/admin-nav.ts`, add a fourth group after `People`:

```ts
  {
    label: "System",
    items: [{ name: "Audit log", href: "/admin/audit", icon: LuScrollText }],
  },
```

and add `LuScrollText` to the `react-icons/lu` import.

- [ ] **Step 7: Register the test and verify everything**

Append ` scripts/test-admin-audit-filter.mts` to the `test` script in `package.json`, then:

```bash
npx tsc --noEmit -p tsconfig.json
npm run lint
npm test
npm run dev
```

Visit `/admin/audit`. Confirm: rows from every earlier task appear; the actor and action filters narrow the list; a date range works; `?action=nonsense` is ignored rather than returning an empty table; `?from=2026-12-01&to=2026-01-01` is ignored rather than returning nothing; paging preserves the filters; the "Audit log" entry appears in the sidebar and in the mobile More sheet.

- [ ] **Step 8: Commit**

```bash
git add src/lib/admin-audit-filter.ts src/lib/admin-audit-data.ts \
  "src/app/admin/(console)/audit/page.tsx" \
  src/components/admin/audit-filter-bar.tsx \
  scripts/test-admin-audit-filter.mts src/lib/admin-nav.ts package.json
git commit -m "feat(admin): add the audit log viewer"
```

---

## Final verification

- [ ] **Run the whole suite and both checks**

```bash
npm test
npx tsc --noEmit -p tsconfig.json
npm run typecheck:tests
npm run lint
npm run build
```

Expected: all five PASS.

- [ ] **Confirm every new test is registered**

```bash
grep -o "scripts/test-[a-z-]*\.mts" package.json | sort > /tmp/registered.txt
ls scripts/test-*.mts | sed 's|\\|/|g' | sort > /tmp/present.txt
diff /tmp/registered.txt /tmp/present.txt
```

Expected: no differences. Any file listed only in `present.txt` is a test that never runs.

- [ ] **Confirm no dead nav links**

```bash
grep -o 'href: "/admin[^"]*"' src/lib/admin-nav.ts
```

For each href, confirm a `page.tsx` exists under `src/app/admin/(console)/` at the matching path.

- [ ] **Confirm the guards are on every new page and route**

```bash
grep -L "requireAdminPage\|requireOwnerPage" src/app/admin/\(console\)/students/page.tsx \
  "src/app/admin/(console)/students/[id]/page.tsx" \
  "src/app/admin/(console)/audit/page.tsx"
grep -rL "requireAdminApi\|requireOwnerApi" src/app/admin/api/students/
```

Expected: no output from either — every file matched.

---

## Self-review notes

**Spec coverage.** Every section of the design maps to a task: server-first rendering (Tasks 9, 10, 14), pure/data lib split (Tasks 8, 9, 14), grouped nav with no dead links (Task 4, plus the entries added in Tasks 9 and 14), shared primitives (Tasks 5, 6), Import demoted to a page action (Task 4 Steps 7–8), tier schema and seam (Tasks 1, 2), manual tier override (Task 11), `Subscription` table deferred (no task, by design), student schema (Task 2), two-touch-point suspension enforcement (Task 12), permissions without a role enum (Task 7), all seven routes (Tasks 11, 12, 13), list columns and filters (Task 9), detail page with danger zone (Tasks 10, 13), deletion impact with type-to-confirm (Tasks 10, 13), force sign-out replacing password reset (Task 13), audit actions and viewer (Tasks 11, 14), error-handling table (every route task), all six test scripts (Tasks 1, 3, 4, 6, 8, 14 plus the extension in Task 7), and the migration constraints (Task 2 Steps 6–9).

**Known follow-ups, deliberately not in this plan.** `questions-client.tsx` is not migrated to server-first rendering — the spec lists it as a non-goal. `FilterBar` was specified as a generalised primitive but is delivered as two purpose-built bars (`student-filter-bar`, `audit-filter-bar`) that share the class constants; generalising them is worth doing once a third consumer exists, and not before.
