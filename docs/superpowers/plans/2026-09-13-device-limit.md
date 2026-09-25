# Device Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make account sharing inconvenient: paid accounts may be signed in on 2 devices at once (a new sign-in signs out the least recently used one). Students can see and sign out their devices, and the product and a new Terms page state "one account per student".

**Architecture:** A `UserDevice` row is created at every sign-in and its id is carried on the student JWT as `deviceId`. The existing 60s profile refresh in the `jwt` callback also reads that row. A revoked row turns the token into a stripped `{ deviceRevoked: true }` token, which `proxy.ts` clears, redirecting to `/login?reason=device`. All rules live in a pure, database-free module (`src/lib/device-limit.ts`) so they are unit tested. Database access lives in `src/lib/devices.ts`.

**Tech Stack:** Next.js 16.2 (App Router, `proxy.ts`), next-auth v5 beta (JWT strategy), Prisma 6 on Supabase Postgres, `node:test` via `tsx`, Tailwind.

**Spec:** `docs/superpowers/specs/2026-09-13-device-limit-design.md`

## Global Constraints

- Branch: `feat/device-limit` (already created off `perf/scaling-hardening`).
- Device limit: `DEVICE_LIMIT = 2`, applies only to tiers above `FREEMIUM`, enforced only at sign-in.
- Revocation is checked for every tier.
- Tokens without `deviceId` (issued before this ships) stay valid. No mass sign-out on deploy.
- A database error must never sign a student out (keep the existing catch-and-keep-cached behaviour).
- `lastSeenAt` is written at most once per 15 minutes per device.
- Suspension and deletion keep returning `null` from the `jwt` callback. Only device revocation returns `{ deviceRevoked: true }`.
- Login notice copy, verbatim: "You were signed out because this account was signed in on another device. If that wasn't you, change your password."
- Settings limit copy, verbatim: "Your plan allows 2 devices at a time. Signing in on a new device signs out the one used least recently."
- Dashboard copy, verbatim: "Built from every question answered on this account."
- Checkout copy, verbatim: "One student per account. Your study plan, weak areas and unseen questions are built from your answers alone."
- Brand name in UI copy is **ScholarsCrib**.
- Migration SQL files must be LF-only (`.gitattributes` enforces it). Migrations are applied by hand in the Supabase SQL Editor, never `prisma migrate deploy` (it cannot reach the DB from the dev machine).
- Stop the dev server before `npx prisma generate` (EPERM on the query engine DLL otherwise).
- This is Next.js 16: check `node_modules/next/dist/docs/` before using an unfamiliar API. `headers()` is async.
- Unit tests: `node --import tsx --test scripts/<file>.mts`. Each new test file must be appended to the `test` script in `package.json`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011hCavgJQfS2f9ZL7tnZ7iw
  ```

## File Map

| File | Status | Responsibility |
|---|---|---|
| `src/lib/device-limit.ts` | Create | Pure rules: limit, which devices to revoke, device label from user agent, last-seen throttle, token state, proxy decision, "last active" text |
| `scripts/test-device-limit.mts` | Create | Unit tests for the above |
| `prisma/schema.prisma` | Modify | `UserDevice` model, `User.devices` relation |
| `prisma/migrations/20260913000000_user_devices/migration.sql` | Create | Table, index, FK |
| `src/lib/devices.ts` | Create | Database access: register (with trim), list, revoke one, revoke others, touch |
| `src/lib/auth.ts` | Modify | Register the device at sign-in, check it on refresh, expose `deviceId` on the session |
| `src/lib/session-token.ts` | Modify | `sessionCookieName()` so the proxy can delete the cookie |
| `src/proxy.ts` | Modify | Handle `deviceRevoked` tokens |
| `src/app/(auth)/login/page.tsx` | Modify | `reason=device` notice |
| `src/app/api/user/devices/route.ts` | Create | POST: revoke one device or all others |
| `src/components/settings/devices-section.tsx` | Create | Server component: loads devices, renders the list |
| `src/components/settings/device-list.tsx` | Create | Client component: sign-out buttons |
| `src/app/(dashboard)/settings/page.tsx` | Modify | Render `DevicesSection` |
| `src/app/api/user/password/route.ts` | Modify | Revoke other devices after a password change |
| `src/components/settings/password-form.tsx` | Modify | Success copy |
| `src/app/(dashboard)/dashboard/page.tsx` | Modify | "Built from every question…" line |
| `src/components/billing/plan-picker.tsx` | Modify | "One student per account…" line |
| `src/app/(public)/terms/page.tsx` | Create | Terms of Service draft |
| `src/lib/public-routes.ts`, `scripts/test-public-routes.mts` | Modify | `/terms` is public |
| `src/components/landing/footer.tsx` | Modify | Terms link → `/terms` |
| `src/app/(auth)/register/page.tsx` | Modify | "By creating an account you agree…" |
| `scripts/verify-device-limit.mts` | Create | Against-the-database check (not part of `npm test`) |

---

### Task 1: Pure device-limit rules

**Files:**
- Create: `src/lib/device-limit.ts`
- Create: `scripts/test-device-limit.mts`
- Modify: `package.json` (`test` script)

**Interfaces:**
- Consumes: `hasAtLeast`, `SubscriptionTier` from `src/lib/subscription.ts`
- Produces:
  - `DEVICE_LIMIT: 2`
  - `LAST_SEEN_WRITE_INTERVAL_MS: number`
  - `type DeviceSummary = { id: string; lastSeenAt: Date }`
  - `isDeviceLimited(tier: SubscriptionTier): boolean`
  - `devicesToRevoke(devices: DeviceSummary[], keepId: string, limit: number): string[]`
  - `deviceLabel(userAgent: string | null | undefined): string`
  - `shouldTouchLastSeen(lastSeenAt: Date, now: Date): boolean`
  - `type DeviceState = "untracked" | "active" | "revoked"`
  - `deviceState(deviceId: string | undefined, row: { revokedAt: Date | null } | undefined): DeviceState`
  - `type StudentTokenState = "none" | "revoked" | "active"`
  - `studentTokenState(token: Record<string, unknown> | null): StudentTokenState`
  - `type RevokedTokenAction = "unauthorized" | "redirect-with-reason" | "continue"`
  - `revokedTokenAction(args: { pathname: string; reason: string | null; isPublic: boolean }): RevokedTokenAction`
  - `formatLastActive(lastSeenAt: Date, now: Date): string`

- [ ] **Step 1: Write the failing tests**

Create `scripts/test-device-limit.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEVICE_LIMIT,
  deviceLabel,
  deviceState,
  devicesToRevoke,
  formatLastActive,
  isDeviceLimited,
  revokedTokenAction,
  shouldTouchLastSeen,
  studentTokenState,
} from "../src/lib/device-limit";

const at = (iso: string) => new Date(iso);

test("the limit is two devices", () => {
  assert.equal(DEVICE_LIMIT, 2);
});

test("only paid tiers are limited", () => {
  assert.equal(isDeviceLimited("FREEMIUM"), false);
  assert.equal(isDeviceLimited("STANDARD"), true);
  assert.equal(isDeviceLimited("PREMIUM"), true);
});

test("under or at the limit nothing is revoked", () => {
  const devices = [
    { id: "new", lastSeenAt: at("2026-09-13T10:00:00Z") },
    { id: "a", lastSeenAt: at("2026-09-13T09:00:00Z") },
  ];
  assert.deepEqual(devicesToRevoke(devices, "new", 2), []);
  assert.deepEqual(devicesToRevoke(devices.slice(0, 1), "new", 2), []);
});

test("over the limit the least recently used devices are revoked", () => {
  const devices = [
    { id: "old", lastSeenAt: at("2026-09-10T10:00:00Z") },
    { id: "recent", lastSeenAt: at("2026-09-13T09:00:00Z") },
    { id: "older", lastSeenAt: at("2026-09-11T10:00:00Z") },
    { id: "new", lastSeenAt: at("2026-09-13T10:00:00Z") },
  ];
  assert.deepEqual(devicesToRevoke(devices, "new", 2).sort(), ["old", "older"]);
});

test("the new device is kept even if its timestamp is oldest", () => {
  // Clock skew between app instances must not sign out the device that just signed in.
  const devices = [
    { id: "new", lastSeenAt: at("2026-09-01T00:00:00Z") },
    { id: "a", lastSeenAt: at("2026-09-13T09:00:00Z") },
    { id: "b", lastSeenAt: at("2026-09-13T08:00:00Z") },
  ];
  assert.deepEqual(devicesToRevoke(devices, "new", 2), ["b"]);
});

test("ties on lastSeenAt are broken deterministically", () => {
  const same = at("2026-09-13T09:00:00Z");
  const devices = [
    { id: "new", lastSeenAt: same },
    { id: "a", lastSeenAt: same },
    { id: "b", lastSeenAt: same },
  ];
  const first = devicesToRevoke(devices, "new", 2);
  const second = devicesToRevoke([...devices].reverse(), "new", 2);
  assert.equal(first.length, 1);
  assert.deepEqual(first, second);
});

test("device labels name the browser and platform", () => {
  assert.equal(
    deviceLabel(
      "Mozilla/5.0 (Linux; Android 13; SM-A135F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
    ),
    "Chrome on Android",
  );
  assert.equal(
    deviceLabel(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    ),
    "Safari on iPhone",
  );
  assert.equal(
    deviceLabel(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    ),
    "Edge on Windows",
  );
  assert.equal(
    deviceLabel("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"),
    "Firefox on Linux",
  );
  assert.equal(
    deviceLabel(
      "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36",
    ),
    "Samsung Internet on Android",
  );
  assert.equal(
    deviceLabel(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    ),
    "Safari on macOS",
  );
});

test("an unrecognised or missing user agent gets a generic label", () => {
  assert.equal(deviceLabel(null), "Unknown device");
  assert.equal(deviceLabel(""), "Unknown device");
  assert.equal(deviceLabel("curl/8.0"), "Unknown device");
});

test("lastSeenAt is written at most every 15 minutes", () => {
  const now = at("2026-09-13T10:00:00Z");
  assert.equal(shouldTouchLastSeen(at("2026-09-13T09:50:00Z"), now), false);
  assert.equal(shouldTouchLastSeen(at("2026-09-13T09:44:00Z"), now), true);
});

test("device state on refresh", () => {
  assert.equal(deviceState(undefined, undefined), "untracked");
  assert.equal(deviceState("d1", { revokedAt: null }), "active");
  assert.equal(deviceState("d1", { revokedAt: at("2026-09-13T10:00:00Z") }), "revoked");
  // Row gone (e.g. deleted by an admin tool) is treated as revoked.
  assert.equal(deviceState("d1", undefined), "revoked");
});

test("student token state", () => {
  assert.equal(studentTokenState(null), "none");
  assert.equal(studentTokenState({ deviceRevoked: true }), "revoked");
  assert.equal(studentTokenState({ sub: "u1" }), "active");
});

test("a revoked token on an API route gets a 401", () => {
  assert.equal(
    revokedTokenAction({ pathname: "/api/attempts", reason: null, isPublic: false }),
    "unauthorized",
  );
});

test("a revoked token on an app page is sent to login with the reason", () => {
  assert.equal(
    revokedTokenAction({ pathname: "/dashboard", reason: null, isPublic: false }),
    "redirect-with-reason",
  );
});

test("a revoked token on /login without the reason gains it, once", () => {
  // The layout guard redirects to a bare /login; the notice must still show.
  assert.equal(
    revokedTokenAction({ pathname: "/login", reason: null, isPublic: false }),
    "redirect-with-reason",
  );
  assert.equal(
    revokedTokenAction({ pathname: "/login", reason: "device", isPublic: false }),
    "continue",
  );
});

test("a revoked token on public pages and /register just continues", () => {
  assert.equal(revokedTokenAction({ pathname: "/", reason: null, isPublic: true }), "continue");
  assert.equal(revokedTokenAction({ pathname: "/terms", reason: null, isPublic: true }), "continue");
  assert.equal(revokedTokenAction({ pathname: "/register", reason: null, isPublic: false }), "continue");
});

test("last active text", () => {
  const now = at("2026-09-13T10:00:00Z");
  assert.equal(formatLastActive(at("2026-09-13T09:55:00Z"), now), "Active recently");
  assert.equal(formatLastActive(at("2026-09-13T09:20:00Z"), now), "Last active 40 minutes ago");
  assert.equal(formatLastActive(at("2026-09-13T09:00:00Z"), now), "Last active 1 hour ago");
  assert.equal(formatLastActive(at("2026-09-13T05:00:00Z"), now), "Last active 5 hours ago");
  assert.equal(formatLastActive(at("2026-09-12T09:00:00Z"), now), "Last active 1 day ago");
  assert.equal(formatLastActive(at("2026-09-06T10:00:00Z"), now), "Last active 7 days ago");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --import tsx --test scripts/test-device-limit.mts`
Expected: FAIL: cannot find module `../src/lib/device-limit`.

- [ ] **Step 3: Implement `src/lib/device-limit.ts`**

```ts
/**
 * Device limit rules, as pure functions.
 *
 * A "device" is one sign-in: one UserDevice row, whose id rides on the student
 * JWT as `deviceId`. Paid accounts may hold DEVICE_LIMIT of them at once; a new
 * sign-in past that signs out the least recently used. This is anti-sharing,
 * not security — it only has to make one account used by several students
 * inconvenient, never block a real student.
 *
 * Kept free of database and next-auth imports: proxy.ts imports it on every
 * request, and the tests run without either.
 *
 * See docs/superpowers/specs/2026-09-13-device-limit-design.md
 */

import { hasAtLeast, type SubscriptionTier } from "@/lib/subscription";

export const DEVICE_LIMIT = 2;

/** An active device writes lastSeenAt no more often than this. */
export const LAST_SEEN_WRITE_INTERVAL_MS = 15 * 60_000;

export type DeviceSummary = { id: string; lastSeenAt: Date };

/** Freemium is unlimited: a shared free account costs nothing. */
export function isDeviceLimited(tier: SubscriptionTier): boolean {
  return hasAtLeast({ tier }, "STANDARD");
}

/**
 * Which of a user's active devices to sign out so at most `limit` remain.
 * `keepId` — the device signing in right now — always survives, whatever its
 * timestamp says: instances' clocks can disagree.
 */
export function devicesToRevoke(
  devices: DeviceSummary[],
  keepId: string,
  limit: number,
): string[] {
  const others = devices
    .filter((d) => d.id !== keepId)
    .sort(
      (a, b) =>
        b.lastSeenAt.getTime() - a.lastSeenAt.getTime() ||
        (a.id < b.id ? 1 : a.id > b.id ? -1 : 0),
    );
  return others.slice(Math.max(limit - 1, 0)).map((d) => d.id);
}

// Order matters: Edge and Samsung Internet also say "Chrome", Chrome also says
// "Safari", and iPhone/iPad user agents also say "Mac OS X".
const BROWSERS: [RegExp, string][] = [
  [/Edg\//, "Edge"],
  [/OPR\/|Opera/, "Opera"],
  [/SamsungBrowser\//, "Samsung Internet"],
  [/Firefox\/|FxiOS\//, "Firefox"],
  [/Chrome\/|CriOS\//, "Chrome"],
  [/Safari\//, "Safari"],
];

const PLATFORMS: [RegExp, string][] = [
  [/Android/, "Android"],
  [/iPhone/, "iPhone"],
  [/iPad/, "iPad"],
  [/Windows/, "Windows"],
  [/Mac OS X|Macintosh/, "macOS"],
  [/CrOS/, "ChromeOS"],
  [/Linux/, "Linux"],
];

export function deviceLabel(userAgent: string | null | undefined): string {
  if (!userAgent) return "Unknown device";
  const browser = BROWSERS.find(([re]) => re.test(userAgent))?.[1];
  const platform = PLATFORMS.find(([re]) => re.test(userAgent))?.[1];
  if (browser && platform) return `${browser} on ${platform}`;
  return browser ?? platform ?? "Unknown device";
}

export function shouldTouchLastSeen(lastSeenAt: Date, now: Date): boolean {
  return now.getTime() - lastSeenAt.getTime() > LAST_SEEN_WRITE_INTERVAL_MS;
}

export type DeviceState = "untracked" | "active" | "revoked";

/**
 * A token with no deviceId predates this feature and stays valid until its
 * next sign-in registers it. A deviceId whose row is gone counts as revoked.
 */
export function deviceState(
  deviceId: string | undefined,
  row: { revokedAt: Date | null } | undefined,
): DeviceState {
  if (!deviceId) return "untracked";
  if (!row || row.revokedAt) return "revoked";
  return "active";
}

export type StudentTokenState = "none" | "revoked" | "active";

export function studentTokenState(
  token: Record<string, unknown> | null,
): StudentTokenState {
  if (!token) return "none";
  return token.deviceRevoked === true ? "revoked" : "active";
}

export type RevokedTokenAction = "unauthorized" | "redirect-with-reason" | "continue";

/** What proxy.ts does with a request carrying a `deviceRevoked` token. */
export function revokedTokenAction(args: {
  pathname: string;
  reason: string | null;
  isPublic: boolean;
}): RevokedTokenAction {
  if (args.pathname.startsWith("/api/")) return "unauthorized";
  if (args.pathname === "/login") {
    return args.reason === "device" ? "continue" : "redirect-with-reason";
  }
  if (args.pathname === "/register" || args.isPublic) return "continue";
  return "redirect-with-reason";
}

/**
 * Coarse on purpose: lastSeenAt is only written every 15 minutes, so anything
 * finer would be false precision.
 */
export function formatLastActive(lastSeenAt: Date, now: Date): string {
  const minutes = Math.floor((now.getTime() - lastSeenAt.getTime()) / 60_000);
  if (minutes < 15) return "Active recently";
  if (minutes < 60) return `Last active ${minutes} minutes ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Last active ${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `Last active ${days} day${days === 1 ? "" : "s"} ago`;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --import tsx --test scripts/test-device-limit.mts`
Expected: all tests PASS.

- [ ] **Step 5: Register the test file**

In `package.json`, in the `test` script, replace `scripts/test-account-status.mts ` with `scripts/test-account-status.mts scripts/test-device-limit.mts ` (the first occurrence of that string is the `test` script).

Run: `npm test`
Expected: PASS, and the output includes the device-limit tests.

- [ ] **Step 6: Commit**

```bash
git add src/lib/device-limit.ts scripts/test-device-limit.mts package.json
git commit -m "Add pure device-limit rules"   # plus the attribution lines
```

---

### Task 2: `UserDevice` schema, migration and data access

**Files:**
- Modify: `prisma/schema.prisma` (the `User` model, near `sessionsValidFrom`; add a new model after `Session`)
- Create: `prisma/migrations/20260913000000_user_devices/migration.sql`
- Create: `src/lib/devices.ts`

**Interfaces:**
- Consumes: `DEVICE_LIMIT`, `devicesToRevoke` from Task 1; `db` from `src/lib/db.ts`
- Produces:
  - `registerDevice(args: { userId: string; label: string; limited: boolean }): Promise<string>`: returns the new device id
  - `listActiveDevices(userId: string): Promise<{ id: string; label: string; lastSeenAt: Date }[]>`: most recent first
  - `revokeDevice(userId: string, deviceId: string): Promise<boolean>`: false if not found, not owned, or already revoked
  - `revokeOtherDevices(userId: string, keepDeviceId: string | undefined): Promise<number>`
  - `touchDevice(deviceId: string): Promise<void>`
  - Prisma: `User.devices` relation with fields `id, userId, label, createdAt, lastSeenAt, revokedAt`

- [ ] **Step 1: Add the model**

In `prisma/schema.prisma`, inside `model User`, directly under `sessionsValidFrom DateTime?`, add:

```prisma
  devices         UserDevice[]
```

After the closing `}` of `model Session`, add:

```prisma
/// One signed-in browser. Its id rides on the student JWT as `deviceId`; the
/// jwt callback's profile refresh rejects a token whose row is revoked. See
/// src/lib/device-limit.ts.
model UserDevice {
  id         String    @id @default(cuid())
  userId     String
  user       User      @relation(fields: [userId], references: [id], onDelete: Cascade)
  label      String
  createdAt  DateTime  @default(now())
  lastSeenAt DateTime  @default(now())
  revokedAt  DateTime?

  @@index([userId, revokedAt])
}
```

- [ ] **Step 2: Write the migration SQL and check it against the schema diff**

Create `prisma/migrations/20260913000000_user_devices/migration.sql`:

```sql
-- CreateTable
CREATE TABLE "UserDevice" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "label" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastSeenAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "revokedAt" TIMESTAMP(3),

    CONSTRAINT "UserDevice_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "UserDevice_userId_revokedAt_idx" ON "UserDevice"("userId", "revokedAt");

-- AddForeignKey
ALTER TABLE "UserDevice" ADD CONSTRAINT "UserDevice_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
```

Confirm it matches what Prisma would generate. This works offline:

```bash
git show HEAD:prisma/schema.prisma > "$TMPDIR/schema-before.prisma"
npx prisma migrate diff --from-schema-datamodel "$TMPDIR/schema-before.prisma" --to-schema-datamodel prisma/schema.prisma --script
```

Expected: the same three statements. If they differ, use Prisma's output.

Confirm LF only: `tr -cd '\r' < prisma/migrations/20260913000000_user_devices/migration.sql | wc -c` → `0`.

- [ ] **Step 3: Regenerate the client**

Stop the dev server if it is running, then run `npx prisma generate`.
Expected: "Generated Prisma Client". Then `npx tsc --noEmit` → no errors.

- [ ] **Step 4: Implement `src/lib/devices.ts`**

```ts
import { db } from "./db";
import { DEVICE_LIMIT, devicesToRevoke } from "./device-limit";

/**
 * Creates the device row for a sign-in and, for a limited account, signs out
 * the least recently used devices past DEVICE_LIMIT.
 *
 * One transaction, with the user row locked, so two simultaneous sign-ins
 * cannot both read "one other device" and both survive. The generous waits are
 * for the Supabase pooler, which can take many seconds to hand out a connection.
 */
export async function registerDevice(args: {
  userId: string;
  label: string;
  limited: boolean;
}): Promise<string> {
  return db.$transaction(
    async (tx) => {
      await tx.$queryRaw`SELECT 1 FROM "User" WHERE "id" = ${args.userId} FOR UPDATE`;

      const device = await tx.userDevice.create({
        data: { userId: args.userId, label: args.label },
        select: { id: true },
      });

      if (args.limited) {
        const active = await tx.userDevice.findMany({
          where: { userId: args.userId, revokedAt: null },
          select: { id: true, lastSeenAt: true },
        });
        const revoke = devicesToRevoke(active, device.id, DEVICE_LIMIT);
        if (revoke.length > 0) {
          await tx.userDevice.updateMany({
            where: { id: { in: revoke } },
            data: { revokedAt: new Date() },
          });
        }
      }

      return device.id;
    },
    { maxWait: 15_000, timeout: 20_000 },
  );
}

export function listActiveDevices(userId: string) {
  return db.userDevice.findMany({
    where: { userId, revokedAt: null },
    select: { id: true, label: true, lastSeenAt: true },
    orderBy: { lastSeenAt: "desc" },
  });
}

/** Scoped by userId, so a student can only ever sign out their own devices. */
export async function revokeDevice(userId: string, deviceId: string): Promise<boolean> {
  const { count } = await db.userDevice.updateMany({
    where: { id: deviceId, userId, revokedAt: null },
    data: { revokedAt: new Date() },
  });
  return count > 0;
}

/**
 * Signs out every device except `keepDeviceId`. Tokens minted before device
 * tracking have no row and are not reached by this; they lapse at their normal
 * session expiry or their next sign-in.
 */
export async function revokeOtherDevices(
  userId: string,
  keepDeviceId: string | undefined,
): Promise<number> {
  const { count } = await db.userDevice.updateMany({
    where: {
      userId,
      revokedAt: null,
      ...(keepDeviceId ? { id: { not: keepDeviceId } } : {}),
    },
    data: { revokedAt: new Date() },
  });
  return count;
}

export async function touchDevice(deviceId: string): Promise<void> {
  await db.userDevice.update({
    where: { id: deviceId },
    data: { lastSeenAt: new Date() },
  });
}
```

- [ ] **Step 5: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Apply the migration to Supabase (manual, needs the user)**

The migration cannot be applied from this machine with `prisma migrate`. Prepare, **and hand to the user to run in the Supabase SQL Editor**, one statement at a time:

1. The three statements from `migration.sql`.
2. The history row. Compute the checksum first with `sha256sum prisma/migrations/20260913000000_user_devices/migration.sql`:
   ```sql
   INSERT INTO "_prisma_migrations" (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
   VALUES (gen_random_uuid()::text, '<sha256>', now(), '20260913000000_user_devices', NULL, NULL, now(), 1);
   ```

Then verify through the catalog. Do not trust the editor's success message:

```sql
SELECT indexname FROM pg_indexes WHERE tablename = 'UserDevice';
-- expect UserDevice_pkey, UserDevice_userId_revokedAt_idx
SELECT conname FROM pg_constraint WHERE conrelid = '"UserDevice"'::regclass;
-- expect UserDevice_pkey, UserDevice_userId_fkey
```

Do not continue to Task 3's manual checks until both queries show the expected rows.

- [ ] **Step 7: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/20260913000000_user_devices src/lib/devices.ts
git commit -m "Add UserDevice table and device data access"   # plus the attribution lines
```

---

### Task 3: Register and check devices in the auth callbacks

**Files:**
- Modify: `src/lib/auth.ts`

**Interfaces:**
- Consumes: `registerDevice`, `touchDevice` (Task 2); `deviceLabel`, `deviceState`, `isDeviceLimited`, `shouldTouchLastSeen` (Task 1)
- Produces:
  - JWT claims: `deviceId?: string`; revoked form `{ deviceRevoked: true }`
  - `session.user.deviceId?: string` (read as `(session.user as { deviceId?: string }).deviceId`)

- [ ] **Step 1: Imports and the device-aware select**

At the top of `src/lib/auth.ts`, add:

```ts
import { headers } from "next/headers";
import type { JWT } from "next-auth/jwt";
import {
  deviceLabel,
  deviceState,
  isDeviceLimited,
  shouldTouchLastSeen,
} from "@/lib/device-limit";
import { registerDevice, touchDevice } from "@/lib/devices";
```

Below `PROFILE_SELECT`, add:

```ts
// The token's own device row rides along on the profile read, so checking it
// costs no extra round trip. An absent deviceId matches no row, and
// deviceState() treats that token as untracked rather than revoked.
function profileSelect(deviceId: string | undefined) {
  return {
    ...PROFILE_SELECT,
    devices: {
      where: { id: deviceId ?? "" },
      select: { revokedAt: true, lastSeenAt: true },
    },
  } as const;
}

/** The signing-in request's user agent. Outside a request scope there is none. */
async function requestUserAgent(): Promise<string | null> {
  try {
    return (await headers()).get("user-agent");
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: Expose `deviceId` on the session**

In the `session` callback, replace:

```ts
        if (cached) applyProfile(session.user, cached);
```

with:

```ts
        if (cached) applyProfile(session.user, cached);
        (session.user as SessionUser & { deviceId?: string }).deviceId = (
          token as { deviceId?: string }
        ).deviceId;
```

- [ ] **Step 3: Check the device on refresh, register it at sign-in**

In the `jwt` callback:

(a) Extend the `cache` type and short-circuit a revoked token. Replace:

```ts
      const cache = token as {
        profile?: CachedProfile;
        profileAt?: number;
        sessionStartedAt?: number;
      };
      const isSignIn = Boolean(user);
```

with:

```ts
      const cache = token as {
        profile?: CachedProfile;
        profileAt?: number;
        sessionStartedAt?: number;
        deviceId?: string;
        deviceRevoked?: boolean;
      };
      const isSignIn = Boolean(user);

      // Stays revoked until proxy.ts clears the cookie. A fresh sign-in mints
      // a new token, so it never arrives here carrying the flag.
      if (cache.deviceRevoked && !isSignIn) return token;
```

(b) Use the device-aware select. Replace:

```ts
          select: PROFILE_SELECT,
```

with:

```ts
          select: profileSelect(cache.deviceId),
```

(c) Directly after the existing `if (isSessionRevoked(profile, startedAt)) { return null; }` block, add:

```ts
        // Signed out from Settings, or displaced by a newer sign-in past the
        // device limit. Not `null`: the stripped token is how proxy.ts learns
        // why, so the login page can say so. Suspension above stays `null`.
        const device = profile.devices[0];
        const state = deviceState(cache.deviceId, device);
        if (!isSignIn && state === "revoked") {
          return { deviceRevoked: true } as JWT;
        }
        if (state === "active" && device && shouldTouchLastSeen(device.lastSeenAt, new Date())) {
          // Fire-and-forget like the tier refresh below.
          touchDevice(cache.deviceId!).catch(() => {});
        }
```

(d) Directly after `cache.profileAt = Date.now();` (still inside the outer `try`), add:

```ts
        if (isSignIn) {
          try {
            cache.deviceId = await registerDevice({
              userId: token.sub,
              label: deviceLabel(await requestUserAgent()),
              limited: isDeviceLimited(resolved.tier),
            });
          } catch {
            // A failed registration must not fail the sign-in. The token is
            // then untracked, like one minted before this feature, and the
            // next sign-in registers it.
          }
        }
```

- [ ] **Step 4: Typecheck, lint and run the unit tests**

Run: `npx tsc --noEmit`, then `npm run lint`, then `npm test`.
Expected: no type errors, no new lint errors, tests PASS.

- [ ] **Step 5: Manual check (dev server, migrated DB)**

1. `npm run dev`. Sign in with a FREEMIUM test student in a normal window.
2. In the Supabase SQL Editor: `SELECT id, label, "revokedAt" FROM "UserDevice" WHERE "userId" = '<id>';` → one row with a sensible label, `revokedAt` null.
3. Browse for a minute. No sign-out.

(Tier and limit behaviour is covered in Task 8.)

- [ ] **Step 6: Commit**

```bash
git add src/lib/auth.ts
git commit -m "Register devices at sign-in and reject revoked ones on refresh"   # plus the attribution lines
```

---

### Task 4: Proxy handling and the login notice

**Files:**
- Modify: `src/lib/session-token.ts`
- Modify: `src/proxy.ts`
- Modify: `src/app/(auth)/login/page.tsx`

**Interfaces:**
- Consumes: `studentTokenState`, `revokedTokenAction` (Task 1); `usesSecureCookie` (existing)
- Produces: `sessionCookieName(requestUrl: string): string`

- [ ] **Step 1: Cookie name helper**

In `src/lib/session-token.ts`, after `usesSecureCookie`, add:

```ts
/** The student session cookie's name, by the same rule Auth.js uses. */
export function sessionCookieName(requestUrl: string): string {
  return `${usesSecureCookie(requestUrl) ? "__Secure-" : ""}authjs.session-token`;
}
```

- [ ] **Step 2: Handle revoked tokens in the proxy**

In `src/proxy.ts`, change the imports:

```ts
import { getSessionToken, sessionCookieName } from "@/lib/session-token";
import { revokedTokenAction, studentTokenState } from "@/lib/device-limit";
```

Replace:

```ts
  const token = await getSessionToken(req);
```

with:

```ts
  const token = await getSessionToken(req);

  // Signed out elsewhere (device limit, or from Settings). The token still
  // decodes, so without this branch every check below would call it a session.
  if (studentTokenState(token) === "revoked") {
    const action = revokedTokenAction({
      pathname,
      reason: req.nextUrl.searchParams.get("reason"),
      isPublic: isPublicPath(pathname),
    });
    const res =
      action === "unauthorized"
        ? NextResponse.json({ error: "Unauthorized" }, { status: 401 })
        : action === "redirect-with-reason"
          ? NextResponse.redirect(new URL("/login?reason=device", req.url))
          : NextResponse.next();
    res.cookies.delete(sessionCookieName(req.url));
    return res;
  }
```

- [ ] **Step 3: Login notice**

In `src/app/(auth)/login/page.tsx`, after:

```ts
  const justRegistered = searchParams.get("registered") === "true";
```

add:

```ts
  const signedOutElsewhere = searchParams.get("reason") === "device";
```

Directly before `{error && (`, add:

```tsx
      {signedOutElsewhere && (
        <div
          role="status"
          className="mt-6 rounded-xl border border-primary/25 bg-primary-soft p-3.5 text-sm font-medium text-foreground animate-fade-in"
        >
          You were signed out because this account was signed in on another
          device. If that wasn&apos;t you, change your password.
        </div>
      )}
```

- [ ] **Step 4: Typecheck and tests**

Run: `npx tsc --noEmit` and `npm test`.
Expected: no errors, PASS.

- [ ] **Step 5: Manual check**

1. Sign in as any student in window A. Find the device id in `UserDevice`.
2. Revoke it by hand: `UPDATE "UserDevice" SET "revokedAt" = now() WHERE id = '<id>';`
3. Wait 60s, then navigate to `/dashboard` in window A. Expected: you land on `/login?reason=device` with the notice showing.
4. In devtools → Application → Cookies, the `authjs.session-token` cookie is gone.
5. Sign in again. Expected: dashboard loads, and a new `UserDevice` row exists.

- [ ] **Step 6: Commit**

```bash
git add src/lib/session-token.ts src/proxy.ts "src/app/(auth)/login/page.tsx"
git commit -m "Sign out revoked devices with an explanation on the login page"   # plus the attribution lines
```

---

### Task 5: Devices section in Settings, and password change signs out other devices

**Files:**
- Create: `src/app/api/user/devices/route.ts`
- Create: `src/components/settings/devices-section.tsx`
- Create: `src/components/settings/device-list.tsx`
- Modify: `src/app/(dashboard)/settings/page.tsx`
- Modify: `src/app/api/user/password/route.ts`
- Modify: `src/components/settings/password-form.tsx`

**Interfaces:**
- Consumes: `listActiveDevices`, `revokeDevice`, `revokeOtherDevices` (Task 2); `formatLastActive`, `isDeviceLimited`, `DEVICE_LIMIT` (Task 1); `session.user.deviceId` (Task 3); `currentEntitlement(userId)` from `src/lib/billing/subscription-data.ts` (returns `{ tier, expiresAt }`)
- Produces: `POST /api/user/devices` with body `{ deviceId: string }` or `{ allOthers: true }`. Responses: `200 { revoked: number }`, `400` / `401` / `404 { error }`.

- [ ] **Step 1: API route**

Create `src/app/api/user/devices/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { auth } from "@/lib/auth";
import { revokeDevice, revokeOtherDevices } from "@/lib/devices";

export const dynamic = "force-dynamic";

const bodySchema = z.union([
  z.object({ deviceId: z.string().min(1) }),
  z.object({ allOthers: z.literal(true) }),
]);

export async function POST(req: NextRequest) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const userId = session.user.id;
  const currentDeviceId = (session.user as { deviceId?: string }).deviceId;

  const parsed = bodySchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }

  try {
    if ("allOthers" in parsed.data) {
      const revoked = await revokeOtherDevices(userId, currentDeviceId);
      return NextResponse.json({ revoked });
    }

    // Signing out this device is the ordinary sign-out button's job.
    if (parsed.data.deviceId === currentDeviceId) {
      return NextResponse.json(
        { error: "Use Sign out to leave this device" },
        { status: 400 },
      );
    }

    const ok = await revokeDevice(userId, parsed.data.deviceId);
    if (!ok) {
      return NextResponse.json({ error: "Device not found" }, { status: 404 });
    }
    return NextResponse.json({ revoked: 1 });
  } catch (error) {
    console.error("Device sign-out failed:", error);
    return NextResponse.json(
      { error: "Something went wrong. Please try again." },
      { status: 500 },
    );
  }
}
```

- [ ] **Step 2: Client list**

Create `src/components/settings/device-list.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { buttonClass } from "@/components/ui/button";
import { FormMessage } from "./section";

export type DeviceRow = {
  id: string;
  label: string;
  lastActive: string;
  isCurrent: boolean;
};

export function DeviceList({ devices }: { devices: DeviceRow[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function signOut(body: { deviceId: string } | { allOthers: true }, key: string) {
    setBusy(key);
    setError("");
    try {
      const res = await fetch("/api/user/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error ?? "Could not sign out that device.");
        return;
      }
      router.refresh();
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  const others = devices.filter((d) => !d.isCurrent);

  return (
    <div>
      <FormMessage error={error} />

      <ul className="divide-y divide-border">
        {devices.map((device) => (
          <li key={device.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
            <div>
              <p className="text-sm font-semibold text-foreground">
                {device.label}
                {device.isCurrent && (
                  <span className="ml-2 rounded-full bg-success-soft px-2 py-0.5 text-xs font-semibold text-success">
                    This device
                  </span>
                )}
              </p>
              <p className="text-xs text-muted">{device.lastActive}</p>
            </div>
            {!device.isCurrent && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => signOut({ deviceId: device.id }, device.id)}
                className={buttonClass("outline", "sm")}
              >
                {busy === device.id ? "Signing out…" : "Sign out"}
              </button>
            )}
          </li>
        ))}
      </ul>

      {others.length > 1 && (
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => signOut({ allOthers: true }, "all")}
          className={`mt-4 ${buttonClass("outline", "md")}`}
        >
          {busy === "all" ? "Signing out…" : "Sign out all other devices"}
        </button>
      )}
    </div>
  );
}
```

Before using `buttonClass("outline", "sm")`, open `src/components/ui/button.tsx` and confirm `"outline"` and `"sm"` are valid variant and size names (`plan-picker.tsx` uses `variant="outline"`). If `"sm"` does not exist, use the smallest size it defines.

- [ ] **Step 3: Server section**

Create `src/components/settings/devices-section.tsx`:

```tsx
import { currentEntitlement } from "@/lib/billing/subscription-data";
import { listActiveDevices } from "@/lib/devices";
import { DEVICE_LIMIT, formatLastActive, isDeviceLimited } from "@/lib/device-limit";
import { Section } from "./section";
import { DeviceList } from "./device-list";

/**
 * "Last active" is formatted here on the server: formatting a timestamp in a
 * client component renders differently on the server and in the browser.
 */
export async function DevicesSection({
  userId,
  currentDeviceId,
}: {
  userId: string;
  currentDeviceId: string | undefined;
}) {
  const [{ tier }, devices] = await Promise.all([
    currentEntitlement(userId),
    listActiveDevices(userId),
  ]);
  const now = new Date();

  return (
    <Section
      title="Devices"
      description={
        isDeviceLimited(tier)
          ? `Your plan allows ${DEVICE_LIMIT} devices at a time. Signing in on a new device signs out the one used least recently.`
          : "Browsers where you are signed in."
      }
    >
      <DeviceList
        devices={devices.map((d) => ({
          id: d.id,
          label: d.label,
          lastActive:
            d.id === currentDeviceId ? "Active now" : formatLastActive(d.lastSeenAt, now),
          isCurrent: d.id === currentDeviceId,
        }))}
      />
    </Section>
  );
}
```

- [ ] **Step 4: Render it in Settings**

In `src/app/(dashboard)/settings/page.tsx`, add the import:

```ts
import { DevicesSection } from "@/components/settings/devices-section";
```

and directly after `<SubscriptionSection userId={session.user.id} />` add:

```tsx
        <DevicesSection
          userId={session.user.id}
          currentDeviceId={(session.user as { deviceId?: string }).deviceId}
        />
```

- [ ] **Step 5: Password change signs out other devices**

In `src/app/api/user/password/route.ts`, add the import:

```ts
import { revokeOtherDevices } from "@/lib/devices";
```

Replace:

```ts
    return NextResponse.json({ message: "Password changed" });
```

with:

```ts
    // Whoever else knew the old password should not stay signed in with it.
    // The password is already changed; a failure here is logged, not reported.
    await revokeOtherDevices(
      session.user.id,
      (session.user as { deviceId?: string }).deviceId,
    ).catch((error) => console.error("Revoking other devices failed:", error));

    return NextResponse.json({ message: "Password changed" });
```

In `src/components/settings/password-form.tsx`, replace `setSuccess("Password changed.");` with:

```ts
      setSuccess("Password changed. Your other devices have been signed out.");
```

- [ ] **Step 6: Typecheck, lint, tests**

Run: `npx tsc --noEmit`, `npm run lint`, `npm test`.
Expected: clean, PASS.

- [ ] **Step 7: Manual check**

1. Sign in as the same student in two browsers (or one normal and one private window), A and B.
2. In A open `/settings`. Expected: a Devices section with two rows, A marked "This device", and B showing a "Sign out" button.
3. Click Sign out on B. The list refreshes to one row. Within about 60s, navigating in B lands on `/login?reason=device`.
4. Sign B back in. In A, change the password. Within about 60s B is signed out.
5. Freemium student: description reads "Browsers where you are signed in." Paid student: the limit sentence.

- [ ] **Step 8: Commit**

```bash
git add src/app/api/user/devices src/components/settings/devices-section.tsx src/components/settings/device-list.tsx "src/app/(dashboard)/settings/page.tsx" src/app/api/user/password/route.ts src/components/settings/password-form.tsx
git commit -m "Let students see and sign out their devices"   # plus the attribution lines
```

---

### Task 6: "Progress is personal" copy

**Files:**
- Modify: `src/app/(dashboard)/dashboard/page.tsx` (the "Your progress" section, around line 207)
- Modify: `src/components/billing/plan-picker.tsx` (before the plan grid, around line 136)

**Interfaces:** none.

- [ ] **Step 1: Dashboard line**

In `src/app/(dashboard)/dashboard/page.tsx`, inside the `<section>` whose heading is "Your progress", find the closing `</div>` of the `mb-3 flex items-center justify-between` header row. Change that row's class from `mb-3` to `mb-1`, and directly after its closing `</div>` add:

```tsx
        <p className="mb-3 text-xs text-muted">
          Built from every question answered on this account.
        </p>
```

- [ ] **Step 2: Checkout line**

In `src/components/billing/plan-picker.tsx`, directly before `<div className="mt-4 grid gap-4 sm:grid-cols-2">`, add:

```tsx
      <p className="mt-4 text-sm text-muted">
        One student per account. Your study plan, weak areas and unseen
        questions are built from your answers alone.
      </p>
```

- [ ] **Step 3: Check**

Run: `npx tsc --noEmit`. Then open `/dashboard` and `/settings/billing` and confirm each line appears once and the spacing looks right at phone width (devtools, 400px).

- [ ] **Step 4: Commit**

```bash
git add "src/app/(dashboard)/dashboard/page.tsx" src/components/billing/plan-picker.tsx
git commit -m "Say that progress is built from one student's answers"   # plus the attribution lines
```

---

### Task 7: Terms of Service page

**Files:**
- Create: `src/app/(public)/terms/page.tsx`
- Modify: `src/lib/public-routes.ts`
- Modify: `scripts/test-public-routes.mts`
- Modify: `src/components/landing/footer.tsx:45`
- Modify: `src/app/(auth)/register/page.tsx` (around line 381)

**Interfaces:**
- Consumes: `buildMetadata({ title, description, path })` from `src/lib/seo/metadata.ts`; `isPublicPath`

- [ ] **Step 1: Failing test**

In `scripts/test-public-routes.mts`, add:

```ts
test("the terms page is public", () => {
  // Linked from the register page, which a signed-out visitor is on.
  assert.equal(isPublicPath("/terms"), true);
});
```

Run: `node --import tsx --test scripts/test-public-routes.mts`
Expected: FAIL on "the terms page is public".

- [ ] **Step 2: Make it public**

In `src/lib/public-routes.ts`, in `PUBLIC_EXACT_PATHS`, after `"/contact",` add `"/terms",`.

Run the same command. Expected: PASS.

- [ ] **Step 3: The page**

Create `src/app/(public)/terms/page.tsx`:

```tsx
import Link from "next/link";
import { buildMetadata } from "@/lib/seo/metadata";

// DRAFT — plain-language terms written by the product team. Not yet reviewed
// by a lawyer; have it reviewed before relying on it in a dispute.

const TITLE = "Terms of Service — ScholarsCrib";
const DESCRIPTION =
  "The terms for using ScholarsCrib, including one account per student.";

export const metadata = buildMetadata({
  title: TITLE,
  description: DESCRIPTION,
  path: "/terms",
});

const SECTIONS: { heading: string; body: React.ReactNode }[] = [
  {
    heading: "1. About these terms",
    body: (
      <p>
        These terms apply when you create a ScholarsCrib account or use
        ScholarsCrib. By creating an account you agree to them. If you are
        under 18, a parent or guardian should read them with you.
      </p>
    ),
  },
  {
    heading: "2. One account per student",
    body: (
      <>
        <p>
          Each ScholarsCrib account belongs to one student. Your study plan,
          weak areas, unseen questions and progress are built from your own
          answers, so an account shared between students stops working
          properly for all of them.
        </p>
        <p>
          You must not share your login details or let another student use
          your account. You are responsible for keeping your password private.
        </p>
      </>
    ),
  },
  {
    heading: "3. Devices",
    body: (
      <p>
        A paid plan can be signed in on up to two devices at a time. Signing
        in on another device signs out the one used least recently. You can
        see and sign out your devices in Settings.
      </p>
    ),
  },
  {
    heading: "4. When we may act on an account",
    body: (
      <p>
        If an account appears to be used by more than one person, or is used
        in a way that breaks these terms, we may sign out its devices, ask
        the owner to change their password, or suspend the account. We will
        tell you what happened and how to contact us.
      </p>
    ),
  },
  {
    heading: "5. Paid plans",
    body: (
      <p>
        Payments are processed by Paystack. A plan gives access to its
        features for the period you paid for, and is for the account holder
        only.
      </p>
    ),
  },
  {
    heading: "6. Using ScholarsCrib fairly",
    body: (
      <p>
        Do not copy or resell questions, lessons or other content, try to get
        around limits or security, or interfere with the service for other
        students.
      </p>
    ),
  },
  {
    heading: "7. Changes to these terms",
    body: (
      <p>
        We may update these terms. When we make an important change we will
        let you know in the app before it takes effect.
      </p>
    ),
  },
  {
    heading: "8. Contact",
    body: (
      <p>
        Questions about these terms? <Link href="/contact" className="font-semibold text-primary hover:underline">Contact us</Link>.
      </p>
    ),
  },
];

export default function TermsPage() {
  return (
    <article className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <h1 className="text-3xl font-bold tracking-tight text-foreground">
        Terms of Service
      </h1>
      <p className="mt-2 text-sm text-muted">Last updated 13 September 2026</p>

      <div className="mt-10 space-y-8">
        {SECTIONS.map((section) => (
          <section key={section.heading}>
            <h2 className="text-lg font-bold text-foreground">{section.heading}</h2>
            <div className="mt-2 space-y-3 text-sm leading-relaxed text-foreground/85">
              {section.body}
            </div>
          </section>
        ))}
      </div>
    </article>
  );
}
```

Open `src/app/(public)/about/page.tsx` and `src/app/(public)/layout.tsx` first. If the public layout already sets a page container or top padding, match it and drop the duplicated classes on `<article>`.

- [ ] **Step 4: Link it**

In `src/components/landing/footer.tsx`, replace:

```ts
      { label: "Terms of Service", href: "/" },
```

with:

```ts
      { label: "Terms of Service", href: "/terms" },
```

In `src/app/(auth)/register/page.tsx`, directly after the `<p className="mt-6 text-center text-sm text-muted">` paragraph containing "Already have an account?", add:

```tsx
      <p className="mt-3 text-center text-xs text-muted">
        By creating an account you agree to the{" "}
        <Link href="/terms" className="font-semibold text-primary hover:underline">
          Terms of Service
        </Link>
        .
      </p>
```

(`Link` is already imported there.)

- [ ] **Step 5: Check**

Run: `npx tsc --noEmit` and `npm test` → clean, PASS.
Signed out, open `/terms` (no redirect), `/register` (link present) and the footer on `/` (link goes to `/terms`).

- [ ] **Step 6: Commit**

```bash
git add "src/app/(public)/terms" src/lib/public-routes.ts scripts/test-public-routes.mts src/components/landing/footer.tsx "src/app/(auth)/register/page.tsx"
git commit -m "Add Terms of Service with one account per student"   # plus the attribution lines
```

---

### Task 8: Verify the limit end to end

**Files:**
- Create: `scripts/verify-device-limit.mts`

**Interfaces:**
- Consumes: `registerDevice`, `listActiveDevices`, `revokeOtherDevices` (Task 2)

- [ ] **Step 1: Database verification script**

Create `scripts/verify-device-limit.mts`. It needs the migrated database and is **not** added to `npm test`:

```ts
// Checks the device limit against the real database.
//   npx tsx scripts/verify-device-limit.mts <paidUserId> <freemiumUserId>
// Revokes every device of both users at the end, which signs them out
// everywhere. Use test accounts only.
import assert from "node:assert/strict";
import { db } from "../src/lib/db";
import { listActiveDevices, registerDevice, revokeOtherDevices } from "../src/lib/devices";

const [paidUserId, freeUserId] = process.argv.slice(2);
if (!paidUserId || !freeUserId) {
  console.error("usage: verify-device-limit.mts <paidUserId> <freemiumUserId>");
  process.exit(1);
}

async function main() {
  await revokeOtherDevices(paidUserId, undefined);
  await revokeOtherDevices(freeUserId, undefined);

  const first = await registerDevice({ userId: paidUserId, label: "verify 1", limited: true });
  const second = await registerDevice({ userId: paidUserId, label: "verify 2", limited: true });
  const third = await registerDevice({ userId: paidUserId, label: "verify 3", limited: true });
  const paid = (await listActiveDevices(paidUserId)).map((d) => d.id).sort();
  assert.deepEqual(paid, [second, third].sort(), "paid: the first device is signed out");

  // Simultaneous sign-ins must not both slip past the limit.
  await Promise.all([
    registerDevice({ userId: paidUserId, label: "race a", limited: true }),
    registerDevice({ userId: paidUserId, label: "race b", limited: true }),
    registerDevice({ userId: paidUserId, label: "race c", limited: true }),
  ]);
  assert.equal((await listActiveDevices(paidUserId)).length, 2, "paid: concurrent sign-ins respect the limit");

  for (let i = 0; i < 3; i++) {
    await registerDevice({ userId: freeUserId, label: `free ${i}`, limited: false });
  }
  assert.equal((await listActiveDevices(freeUserId)).length, 3, "freemium: not limited");

  await revokeOtherDevices(paidUserId, undefined);
  await revokeOtherDevices(freeUserId, undefined);
  console.log("device limit verified; first device", first, "was revoked");
}

main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => db.$disconnect());
```

- [ ] **Step 2: Run it**

Ask the user for a paid test student id and a freemium test student id (or create them). Then run:
`npx tsx scripts/verify-device-limit.mts <paidUserId> <freemiumUserId>`
Expected: `device limit verified; …`, exit code 0.

- [ ] **Step 3: Manual end to end in browsers**

With a paid test student:
1. Sign in on browser A, then browser B. Both work.
2. Sign in on browser C (e.g. a private window). It works.
3. Within about 60s, navigating in A (the least recently used) lands on `/login?reason=device` with the notice. B and C stay signed in.
4. `/settings` on C lists B and C only.

With a freemium test student: sign in on three browsers. All three stay signed in.

Record the results (pass/fail per step) in the PR description.

- [ ] **Step 4: Full check**

Run: `npx tsc --noEmit`, `npm run lint`, `npm test`, `npm run build`.
Expected: all clean. `npm run build` exercises the proxy and route handlers under the production compiler.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify-device-limit.mts
git commit -m "Add database check for the device limit"   # plus the attribution lines
```
