# Push Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-hosted Web Push for ScholarsCrib: opt-in per device, a morning study digest, an evening streak reminder, and admin broadcast announcements with a dashboard banner fallback.

**Architecture:** Browsers subscribe through the existing PWA service worker and the server stores subscriptions in Postgres. Supabase `pg_cron` POSTs to secret-protected Vercel routes: two reminder routes that page through students under a 40s budget, and a drain route that sends queued announcement deliveries with `FOR UPDATE SKIP LOCKED`. All rules (payloads, result mapping, audience matching, reminder content, capability, cron auth) are pure modules tested with `node:test`; database and HTTP code are thin wrappers.

**Tech Stack:** Next.js 16 App Router (read `node_modules/next/dist/docs/` before using an API), React 19, Prisma 6 on Supabase Postgres, zod 4, `web-push`, classic-script service worker, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-09-14-push-notifications-design.md`

## Global Constraints

- Vercel **Hobby**: every cron route exports `maxDuration = 60` and stops work at a **40s** budget measured from request start.
- Reminder windows: morning 07:00–07:55 Lagos (`*/5 6 * * *` UTC), streak 19:00–19:55 Lagos (`*/5 18 * * *` UTC), drain `* * * * *`. Lagos is UTC+1, no DST.
- Title ≤ **60** chars, body ≤ **180** chars, URLs are internal paths only (`/x`, never `//` or `\`).
- Payloads carry no personal data (no names, no scores).
- Tags: `morning-<dayKey>`, `streak-<dayKey>`, `announcement-<id>`.
- Subscription endpoint `https:` and ≤ **1024** chars; `/api/push/subscription` rate limit **10/min per user**.
- Retry: broadcast deliveries **3** attempts; a subscription is deleted after **5** consecutive failures or on 404/410. Reminders never retry the same day.
- Claim batch **100**, send concurrency **20**, reminder page size **200**, stale claim **5 minutes**.
- Service worker: `SHELL_VERSION` becomes `"v2"`; still no `skipWaiting()` / `clients.claim()`.
- Missing `NEXT_PUBLIC_VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` or `CRON_SECRET` turns the feature off; nothing throws. Cron routes answer `204`.
- Confirm modal requires typing `SEND` at **≥ 500** devices. Banner expiry default **7** days, max **30**.
- Migrations: LF line endings only; applied through the Supabase SQL Editor (Prisma cannot reach the DB from the dev machine); verify the catalog afterwards.
- Never call `window.confirm/alert`. Wrap every `localStorage` access in try/catch.
- Every new `scripts/test-*.mts` file is appended to the `test` script in `package.json`.
- If `prisma generate` fails with EPERM, a running `next dev` holds the engine DLL: stop this project's dev servers, regenerate, restart.

## File Map

| File | Responsibility |
|---|---|
| `prisma/schema.prisma`, `prisma/migrations/20260915000001_push_notifications/migration.sql` | Tables and enums |
| `src/lib/push-config.ts` | Read VAPID and cron env (pure) |
| `src/lib/push-payload.ts` | Payload building, internal-path check, tags (pure) |
| `src/lib/push-send-result.ts` | Status → outcome, delivery/subscription state transitions, `mapWithConcurrency` (pure) |
| `src/lib/push-send.ts` | `web-push` wrapper and subscription side effects (server) |
| `public/sw-policy.js`, `public/sw.js` | `notificationTarget`, push/click/subscription-change/version handlers |
| `src/lib/push-capability.ts` | Capability state, iOS detection, version check, snooze (pure) |
| `src/lib/push-client.ts` | Browser subscribe/unsubscribe/sync |
| `src/lib/push-validators.ts` | zod schemas for subscription and preferences |
| `src/lib/push-subscription-data.ts` | Subscription and preference persistence |
| `src/app/api/push/subscription/route.ts` | POST upsert, DELETE this device |
| `src/app/api/user/notification-preferences/route.ts` | PATCH toggles |
| `src/components/push/push-sync.tsx` | Re-sync on app load |
| `src/components/settings/notifications-section.tsx`, `notification-settings.tsx` | Settings UI |
| `src/components/study-plan/reminder-opt-in-card.tsx` | Soft opt-in on the study plan page |
| `src/lib/cron-auth.ts` | Cron request check (pure) + route guard |
| `src/lib/push-reminders.ts` | Digest/streak content and Lagos day bounds (pure) |
| `src/lib/push-reminder-runner.ts` | Paged, budgeted reminder runs (server) |
| `src/app/api/cron/push/{morning,streak,drain}/route.ts` | Cron entry points |
| `src/lib/push-audience.ts` | `AudienceFilter` schema, clauses, `matchesAudience`, description (pure) |
| `src/lib/push-audience-sql.ts` | Clauses → SQL predicate |
| `src/lib/announcement.ts` | Announcement input schema, confirm threshold (pure) |
| `src/lib/announcement-data.ts` | Preview, queue, cancel, list, test send, banner query |
| `src/lib/announcement-drain.ts` | Claim, send, finalize |
| `src/app/admin/api/announcements/**` | Admin API |
| `src/app/admin/(console)/announcements/page.tsx`, `src/components/admin/announcement-composer.tsx`, `announcement-list.tsx` | Admin UI |
| `src/components/announcements/announcement-banner.tsx`, `dismiss-announcement-button.tsx`, `src/app/api/announcements/[id]/dismiss/route.ts` | Banner |
| `docs/push-notifications-ops.md` | Env, VAPID, `pg_cron` SQL, verification |

---

### Task 1: Schema, migration, dependency and config

**Files:**
- Modify: `prisma/schema.prisma` (`User` ~213, `Admin` ~979, `AdminAudit` comment ~1002, new models at end)
- Create: `prisma/migrations/20260915000001_push_notifications/migration.sql`
- Create: `src/lib/push-config.ts`
- Modify: `src/lib/admin-audit.ts`, `src/lib/admin-audit-filter.ts`, `package.json`, `.env.example` (create if missing)
- Test: `scripts/test-push-config.mts`

**Interfaces:**
- Produces: Prisma models `PushSubscription`, `NotificationPreference`, `Announcement`, `AnnouncementDelivery`, `AnnouncementDismissal`, `ReminderLog`; enums `AnnouncementStatus`, `DeliveryStatus`.
- Produces: `type PushConfig = { publicKey: string; privateKey: string; subject: string }`, `readPushConfig(env?): PushConfig | null`, `readCronSecret(env?): string | null`.
- Produces: `AuditAction` gains `"announcement.send" | "announcement.cancel" | "announcement.test"`.

- [ ] **Step 1: Write the failing config test**

`scripts/test-push-config.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { readCronSecret, readPushConfig } from "../src/lib/push-config";

const full = {
  NEXT_PUBLIC_VAPID_PUBLIC_KEY: "pub",
  VAPID_PRIVATE_KEY: "priv",
  VAPID_SUBJECT: "mailto:hello@scholarscrib.com",
  CRON_SECRET: "s3cret-value-long-enough",
};

test("all three VAPID values give a config", () => {
  assert.deepEqual(readPushConfig(full), {
    publicKey: "pub",
    privateKey: "priv",
    subject: "mailto:hello@scholarscrib.com",
  });
});

test("any missing or blank VAPID value turns push off", () => {
  for (const key of ["NEXT_PUBLIC_VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"]) {
    assert.equal(readPushConfig({ ...full, [key]: undefined }), null, key);
    assert.equal(readPushConfig({ ...full, [key]: "  " }), null, key);
  }
});

test("the subject must be a mailto: or https: URL", () => {
  assert.equal(readPushConfig({ ...full, VAPID_SUBJECT: "hello@scholarscrib.com" }), null);
  assert.ok(readPushConfig({ ...full, VAPID_SUBJECT: "https://scholarscrib.com" }));
});

test("cron secret is trimmed and must be at least 16 characters", () => {
  assert.equal(readCronSecret(full), "s3cret-value-long-enough");
  assert.equal(readCronSecret({ CRON_SECRET: "short" }), null);
  assert.equal(readCronSecret({}), null);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-push-config.mts`
Expected: FAIL, cannot find module `../src/lib/push-config`.

- [ ] **Step 3: Implement `src/lib/push-config.ts`**

```ts
// Push configuration, read once per call from the environment. Pure: tests
// pass an env object. A missing value turns the whole feature off rather than
// throwing, so a deploy without keys behaves exactly like one before push.

type Env = Record<string, string | undefined>;

export type PushConfig = {
  publicKey: string;
  privateKey: string;
  subject: string;
};

function value(env: Env, key: string): string | null {
  const raw = env[key]?.trim();
  return raw ? raw : null;
}

export function readPushConfig(env: Env = process.env): PushConfig | null {
  const publicKey = value(env, "NEXT_PUBLIC_VAPID_PUBLIC_KEY");
  const privateKey = value(env, "VAPID_PRIVATE_KEY");
  const subject = value(env, "VAPID_SUBJECT");
  if (!publicKey || !privateKey || !subject) return null;
  // Push services reject a subject that is not a contact URL.
  if (!subject.startsWith("mailto:") && !subject.startsWith("https://")) return null;
  return { publicKey, privateKey, subject };
}

export function readCronSecret(env: Env = process.env): string | null {
  const secret = value(env, "CRON_SECRET");
  return secret && secret.length >= 16 ? secret : null;
}
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --import tsx --test scripts/test-push-config.mts`
Expected: PASS (4 tests).

- [ ] **Step 5: Add the models to `prisma/schema.prisma`**

Add these back-relations inside `model User`, after `devices UserDevice[]`:
```prisma
  // Push notifications (see docs/superpowers/specs/2026-09-14-push-notifications-design.md)
  pushSubscriptions      PushSubscription[]
  notificationPreference NotificationPreference?
  announcementDismissals AnnouncementDismissal[]
  reminderLogs           ReminderLog[]
```
Inside `model Admin`, after `audits AdminAudit[]`:
```prisma
  announcements Announcement[]
```
Append `| "announcement.send" | "announcement.cancel" | "announcement.test"` to the `AdminAudit.action` comment.

Append at the end of the file:
```prisma
// ─── Push notifications ─────────────────────────────────────

/// One row per browser/device a student opted in on.
model PushSubscription {
  id            String    @id @default(cuid())
  userId        String
  user          User      @relation(fields: [userId], references: [id], onDelete: Cascade)
  /// Unique: the same browser re-subscribing upserts; a shared phone moves to the new user.
  endpoint      String    @unique
  p256dh        String
  auth          String
  userAgent     String?
  createdAt     DateTime  @default(now())
  lastSuccessAt DateTime?
  /// Consecutive non-410 failures. Deleted at 5.
  failureCount  Int       @default(0)

  @@index([userId])
}

/// Missing row = every toggle on.
model NotificationPreference {
  userId          String   @id
  user            User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  studyReminders  Boolean  @default(true)
  streakReminders Boolean  @default(true)
  /// Push only. The dashboard banner shows regardless.
  announcements   Boolean  @default(true)
  updatedAt       DateTime @updatedAt
}

model Announcement {
  id             String             @id @default(cuid())
  title          String
  body           String
  url            String?
  /// AudienceFilter (src/lib/push-audience.ts)
  audience       Json
  status         AnnouncementStatus @default(QUEUED)
  expiresAt      DateTime
  recipientCount Int                @default(0)
  sentCount      Int                @default(0)
  failedCount    Int                @default(0)
  createdById    String
  createdBy      Admin              @relation(fields: [createdById], references: [id])
  createdAt      DateTime           @default(now())
  completedAt    DateTime?

  deliveries AnnouncementDelivery[]
  dismissals AnnouncementDismissal[]

  @@index([status])
  @@index([expiresAt])
}

enum AnnouncementStatus {
  QUEUED
  SENDING
  SENT
  CANCELLED
}

/// The send queue: one row per (announcement, subscription).
model AnnouncementDelivery {
  id             String         @id @default(cuid())
  announcementId String
  announcement   Announcement   @relation(fields: [announcementId], references: [id], onDelete: Cascade)
  /// Not a foreign key: a subscription can be deleted mid-send.
  subscriptionId String
  status         DeliveryStatus @default(PENDING)
  attempts       Int            @default(0)
  claimedAt      DateTime?
  error          String?

  @@unique([announcementId, subscriptionId])
  @@index([status, claimedAt])
}

enum DeliveryStatus {
  PENDING
  SENT
  FAILED
  GONE
  CANCELLED
}

model AnnouncementDismissal {
  announcementId String
  announcement   Announcement @relation(fields: [announcementId], references: [id], onDelete: Cascade)
  userId         String
  user           User         @relation(fields: [userId], references: [id], onDelete: Cascade)
  dismissedAt    DateTime     @default(now())

  @@id([announcementId, userId])
  @@index([userId])
}

/// Makes reminder runs idempotent. A row means "this student was processed for
/// this reminder today", whether or not a notification went out.
model ReminderLog {
  userId String
  user   User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  /// "morning" | "streak"
  kind   String
  /// lagosDayKey() from src/lib/streak.ts
  dayKey String
  sentAt DateTime @default(now())

  @@id([userId, kind, dayKey])
}
```

- [ ] **Step 6: Generate the migration SQL offline**

Run (Git Bash):
```bash
mkdir -p prisma/migrations/20260915000001_push_notifications
before="$(mktemp --suffix=.prisma)"
git show HEAD:prisma/schema.prisma > "$before"
npx prisma migrate diff --from-schema-datamodel "$before" --to-schema-datamodel prisma/schema.prisma --script > prisma/migrations/20260915000001_push_notifications/migration.sql
tr -cd '\r' < prisma/migrations/20260915000001_push_notifications/migration.sql | wc -c
```
Expected: the SQL contains `CREATE TYPE "AnnouncementStatus"`, `CREATE TYPE "DeliveryStatus"`, six `CREATE TABLE` statements, and foreign keys. The final command prints `0` (no CR bytes). If it is not 0, run `sed -i 's/\r$//'` on the file.

- [ ] **Step 7: Regenerate the client and add the dependency**

Run: `npx prisma generate` then `npm install web-push@3` and `npm install -D @types/web-push@3`
Expected: generate succeeds (if EPERM, see Global Constraints).

- [ ] **Step 8: Audit actions**

In `src/lib/admin-audit.ts`, add to the `AuditAction` union after `"provider.backfill"`:
```ts
  | "announcement.send"
  | "announcement.cancel"
  | "announcement.test";
```
(remove the `;` after `"provider.backfill"`). In `src/lib/admin-audit-filter.ts`, append to `AUDIT_ACTIONS`:
```ts
  "announcement.send",
  "announcement.cancel",
  "announcement.test",
```
and change `AUDIT_ENTITIES` to `["Question", "Lesson", "Admin", "User", "Announcement"] as const`.

- [ ] **Step 9: Env example and test script**

Append to `.env.example`:
```
# Push notifications — generate once with: npx web-push generate-vapid-keys
# Rotating these keys invalidates every student's subscription.
NEXT_PUBLIC_VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
VAPID_SUBJECT=mailto:hello@scholarscrib.com
# Shared with Supabase Vault (push_cron_secret). At least 16 characters.
CRON_SECRET=
```
Append ` scripts/test-push-config.mts` to the end of the `test` script in `package.json`.

- [ ] **Step 10: Verify types and tests**

Run: `npx tsc --noEmit` and `npm run typecheck:tests` and `node --import tsx --test scripts/test-push-config.mts scripts/test-admin-audit-filter.mts`
Expected: no type errors; tests PASS.

- [ ] **Step 11: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/20260915000001_push_notifications src/lib/push-config.ts src/lib/admin-audit.ts src/lib/admin-audit-filter.ts scripts/test-push-config.mts package.json package-lock.json .env.example
git commit -m "Add push notification schema and config"
```

The migration is **applied to Supabase in Task 17**, together with the `pg_cron` setup, so the feature ships as one database change.

---

### Task 2: Payloads and send-result rules

**Files:**
- Create: `src/lib/push-payload.ts`, `src/lib/push-send-result.ts`
- Test: `scripts/test-push-payload.mts`, `scripts/test-push-send-result.mts`

**Interfaces:**
- Produces (`push-payload.ts`): `TITLE_MAX = 60`, `BODY_MAX = 180`, `type PushPayload = { title: string; body: string; url: string; tag: string }`, `isInternalPath(url: unknown): url is string`, `truncate(text: string, max: number): string`, `buildPushPayload(input: { title: string; body: string; url?: string | null; tag: string }): PushPayload`, `pushTag.morning(dayKey)`, `pushTag.streak(dayKey)`, `pushTag.announcement(id)`.
- Produces (`push-send-result.ts`): `type SendOutcome = "sent" | "gone" | "invalid" | "retry"`, `classifySendResult(result: { statusCode?: number } | null | undefined): SendOutcome`, `MAX_DELIVERY_ATTEMPTS = 3`, `MAX_SUBSCRIPTION_FAILURES = 5`, `nextDelivery(outcome: SendOutcome, attempts: number): { status: "SENT" | "GONE" | "FAILED" | "PENDING"; attempts: number }`, `type SubscriptionEffect = { kind: "success" } | { kind: "delete" } | { kind: "fail"; failureCount: number } | { kind: "none" }`, `subscriptionEffect(outcome: SendOutcome, failureCount: number, final: boolean): SubscriptionEffect`, `mapWithConcurrency<T, R>(items: readonly T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]>`.

- [ ] **Step 1: Write the failing payload test**

`scripts/test-push-payload.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  BODY_MAX,
  TITLE_MAX,
  buildPushPayload,
  isInternalPath,
  pushTag,
  truncate,
} from "../src/lib/push-payload";

test("internal paths are accepted", () => {
  for (const url of ["/", "/study-plan", "/classroom/biology?tab=notes", "/flashcards#due"]) {
    assert.equal(isInternalPath(url), true, url);
  }
});

test("anything that could leave the origin is rejected", () => {
  for (const url of [
    "https://evil.com",
    "//evil.com",
    "/\\evil.com",
    "\\\\evil.com",
    "javascript:alert(1)",
    "study-plan",
    "/ spaced",
    "",
    null,
    42,
    "/" + "a".repeat(600),
  ]) {
    assert.equal(isInternalPath(url), false, String(url));
  }
});

test("truncate keeps short text and ellipsises long text at the limit", () => {
  assert.equal(truncate("hello", 10), "hello");
  const cut = truncate("a".repeat(70), TITLE_MAX);
  assert.equal(cut.length, TITLE_MAX);
  assert.ok(cut.endsWith("…"));
});

test("buildPushPayload enforces limits and falls back to the dashboard", () => {
  const payload = buildPushPayload({
    title: "t".repeat(100),
    body: "b".repeat(300),
    url: "https://evil.com",
    tag: "announcement-abc",
  });
  assert.equal(payload.title.length, TITLE_MAX);
  assert.equal(payload.body.length, BODY_MAX);
  assert.equal(payload.url, "/dashboard");
  assert.equal(payload.tag, "announcement-abc");
  assert.equal(buildPushPayload({ title: "a", body: "b", tag: "x" }).url, "/dashboard");
  assert.equal(buildPushPayload({ title: "a", body: "b", url: "/practice", tag: "x" }).url, "/practice");
});

test("the serialised payload is far below the 4KB Web Push limit", () => {
  const payload = buildPushPayload({
    title: "t".repeat(100),
    body: "b".repeat(300),
    url: "/" + "p".repeat(400),
    tag: pushTag.announcement("c".repeat(30)),
  });
  assert.ok(Buffer.byteLength(JSON.stringify(payload)) < 3000);
});

test("tag formats", () => {
  assert.equal(pushTag.morning("2026-09-15"), "morning-2026-09-15");
  assert.equal(pushTag.streak("2026-09-15"), "streak-2026-09-15");
  assert.equal(pushTag.announcement("abc"), "announcement-abc");
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-push-payload.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/push-payload.ts`**

```ts
// What goes inside an encrypted push message. Pure. The service worker trusts
// none of it (it re-checks the URL), but the server never sends anything that
// would need rejecting.

export const TITLE_MAX = 60;
export const BODY_MAX = 180;
const URL_MAX = 500;
const FALLBACK_URL = "/dashboard";

export type PushPayload = {
  title: string;
  body: string;
  url: string;
  tag: string;
};

/**
 * A same-origin path. "//host" and "/\host" are both treated by browsers as
 * protocol-relative URLs to another origin, so neither is allowed.
 */
export function isInternalPath(url: unknown): url is string {
  if (typeof url !== "string") return false;
  if (url.length === 0 || url.length > URL_MAX) return false;
  return /^\/(?![/\\])[^\s\\]*$/.test(url);
}

export function truncate(text: string, max: number): string {
  const clean = text.trim();
  if (clean.length <= max) return clean;
  return clean.slice(0, max - 1).trimEnd() + "…";
}

export function buildPushPayload(input: {
  title: string;
  body: string;
  url?: string | null;
  tag: string;
}): PushPayload {
  return {
    title: truncate(input.title, TITLE_MAX),
    body: truncate(input.body, BODY_MAX),
    url: isInternalPath(input.url) ? input.url : FALLBACK_URL,
    tag: input.tag,
  };
}

export const pushTag = {
  morning: (dayKey: string) => `morning-${dayKey}`,
  streak: (dayKey: string) => `streak-${dayKey}`,
  announcement: (id: string) => `announcement-${id}`,
};
```

Note: `truncate` can return fewer than `max` characters when `trimEnd` removes spaces; the test uses `"a"` repeated, so it is exactly `max`.

- [ ] **Step 4: Run it to confirm it passes**

Run: `node --import tsx --test scripts/test-push-payload.mts`
Expected: PASS (6 tests).

- [ ] **Step 5: Write the failing send-result test**

`scripts/test-push-send-result.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MAX_DELIVERY_ATTEMPTS,
  MAX_SUBSCRIPTION_FAILURES,
  classifySendResult,
  mapWithConcurrency,
  nextDelivery,
  subscriptionEffect,
} from "../src/lib/push-send-result";

test("status codes map to outcomes", () => {
  assert.equal(classifySendResult({ statusCode: 201 }), "sent");
  assert.equal(classifySendResult({ statusCode: 200 }), "sent");
  assert.equal(classifySendResult({ statusCode: 404 }), "gone");
  assert.equal(classifySendResult({ statusCode: 410 }), "gone");
  assert.equal(classifySendResult({ statusCode: 400 }), "invalid");
  assert.equal(classifySendResult({ statusCode: 413 }), "invalid");
  assert.equal(classifySendResult({ statusCode: 429 }), "retry");
  assert.equal(classifySendResult({ statusCode: 500 }), "retry");
  assert.equal(classifySendResult({ statusCode: 503 }), "retry");
  assert.equal(classifySendResult({}), "retry");
  assert.equal(classifySendResult(null), "retry");
});

test("limits", () => {
  assert.equal(MAX_DELIVERY_ATTEMPTS, 3);
  assert.equal(MAX_SUBSCRIPTION_FAILURES, 5);
});

test("delivery transitions", () => {
  assert.deepEqual(nextDelivery("sent", 0), { status: "SENT", attempts: 1 });
  assert.deepEqual(nextDelivery("gone", 0), { status: "GONE", attempts: 1 });
  assert.deepEqual(nextDelivery("invalid", 0), { status: "FAILED", attempts: 1 });
  assert.deepEqual(nextDelivery("retry", 0), { status: "PENDING", attempts: 1 });
  assert.deepEqual(nextDelivery("retry", 1), { status: "PENDING", attempts: 2 });
  assert.deepEqual(nextDelivery("retry", 2), { status: "FAILED", attempts: 3 });
});

test("subscription effects", () => {
  assert.deepEqual(subscriptionEffect("sent", 3, false), { kind: "success" });
  assert.deepEqual(subscriptionEffect("gone", 0, false), { kind: "delete" });
  assert.deepEqual(subscriptionEffect("invalid", 0, true), { kind: "none" });
  assert.deepEqual(subscriptionEffect("retry", 0, false), { kind: "none" });
  assert.deepEqual(subscriptionEffect("retry", 0, true), { kind: "fail", failureCount: 1 });
  assert.deepEqual(subscriptionEffect("retry", 4, true), { kind: "delete" });
});

test("mapWithConcurrency keeps order and never exceeds the limit", async () => {
  let running = 0;
  let peak = 0;
  const out = await mapWithConcurrency([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
    running += 1;
    peak = Math.max(peak, running);
    await new Promise((r) => setTimeout(r, 5 * (8 - n)));
    running -= 1;
    return n * 2;
  });
  assert.deepEqual(out, [2, 4, 6, 8, 10, 12, 14]);
  assert.ok(peak <= 3);
  assert.deepEqual(await mapWithConcurrency([], 3, async (n: number) => n), []);
});
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-push-send-result.mts`
Expected: FAIL, module not found.

- [ ] **Step 7: Implement `src/lib/push-send-result.ts`**

```ts
// What a push service's answer means for the delivery row and for the stored
// subscription. Pure, so every branch is tested without a push service.

export type SendOutcome = "sent" | "gone" | "invalid" | "retry";

export const MAX_DELIVERY_ATTEMPTS = 3;
export const MAX_SUBSCRIPTION_FAILURES = 5;

export function classifySendResult(
  result: { statusCode?: number } | null | undefined,
): SendOutcome {
  const code = result?.statusCode;
  if (code === undefined) return "retry"; // network error, DNS, timeout
  if (code >= 200 && code < 300) return "sent";
  if (code === 404 || code === 410) return "gone";
  if (code === 400 || code === 413) return "invalid";
  return "retry"; // 429, 5xx and anything unexpected
}

export function nextDelivery(
  outcome: SendOutcome,
  attempts: number,
): { status: "SENT" | "GONE" | "FAILED" | "PENDING"; attempts: number } {
  const next = attempts + 1;
  switch (outcome) {
    case "sent":
      return { status: "SENT", attempts: next };
    case "gone":
      return { status: "GONE", attempts: next };
    case "invalid":
      return { status: "FAILED", attempts: next };
    case "retry":
      return { status: next >= MAX_DELIVERY_ATTEMPTS ? "FAILED" : "PENDING", attempts: next };
  }
}

export type SubscriptionEffect =
  | { kind: "success" }
  | { kind: "delete" }
  | { kind: "fail"; failureCount: number }
  | { kind: "none" };

/**
 * `final` is true when this failure will not be retried: always for
 * reminders, and on the last attempt for broadcasts. Only final transient
 * failures count against the subscription.
 */
export function subscriptionEffect(
  outcome: SendOutcome,
  failureCount: number,
  final: boolean,
): SubscriptionEffect {
  if (outcome === "sent") return { kind: "success" };
  if (outcome === "gone") return { kind: "delete" };
  if (outcome === "invalid" || !final) return { kind: "none" };
  const next = failureCount + 1;
  return next >= MAX_SUBSCRIPTION_FAILURES ? { kind: "delete" } : { kind: "fail", failureCount: next };
}

export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  async function worker() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await fn(items[index]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}
```

- [ ] **Step 8: Run both tests**

Run: `node --import tsx --test scripts/test-push-payload.mts scripts/test-push-send-result.mts`
Expected: PASS.

- [ ] **Step 9: Register tests and commit**

Append ` scripts/test-push-payload.mts scripts/test-push-send-result.mts` to the `test` script.
```bash
git add src/lib/push-payload.ts src/lib/push-send-result.ts scripts/test-push-payload.mts scripts/test-push-send-result.mts package.json
git commit -m "Add push payload and send-result rules"
```

---

### Task 3: Server send wrapper

**Files:**
- Create: `src/lib/push-send.ts`

**Interfaces:**
- Consumes: `readPushConfig` (Task 1); `PushPayload` (Task 2); `classifySendResult`, `subscriptionEffect`, `SendOutcome`, `SubscriptionEffect` (Task 2).
- Produces: `type StoredSubscription = { id: string; endpoint: string; p256dh: string; auth: string; failureCount: number }`, `sendPush(sub: StoredSubscription, payload: PushPayload): Promise<SendOutcome>` (never throws; returns `"retry"` if push is not configured), `applySubscriptionEffect(subscriptionId: string, effect: SubscriptionEffect): Promise<void>` (never throws), `SUBSCRIPTION_SELECT` (Prisma select for `StoredSubscription`).

This task is a thin I/O wrapper whose rules were tested in Task 2, so it is verified by typecheck here and end to end in Task 13 (admin test send).

- [ ] **Step 1: Implement `src/lib/push-send.ts`**

```ts
import webpush from "web-push";
import { db } from "@/lib/db";
import { readPushConfig } from "@/lib/push-config";
import type { PushPayload } from "@/lib/push-payload";
import {
  classifySendResult,
  type SendOutcome,
  type SubscriptionEffect,
} from "@/lib/push-send-result";

export type StoredSubscription = {
  id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
  failureCount: number;
};

export const SUBSCRIPTION_SELECT = {
  id: true,
  endpoint: true,
  p256dh: true,
  auth: true,
  failureCount: true,
} as const;

let configuredWith: string | null = null;

function ensureConfigured(): boolean {
  const config = readPushConfig();
  if (!config) return false;
  const key = `${config.subject}|${config.publicKey}`;
  if (configuredWith !== key) {
    webpush.setVapidDetails(config.subject, config.publicKey, config.privateKey);
    configuredWith = key;
  }
  return true;
}

/** Never throws: every failure becomes an outcome the caller records. */
export async function sendPush(
  sub: StoredSubscription,
  payload: PushPayload,
): Promise<SendOutcome> {
  if (!ensureConfigured()) return "retry";
  try {
    const result = await webpush.sendNotification(
      { endpoint: sub.endpoint, keys: { p256dh: sub.p256dh, auth: sub.auth } },
      JSON.stringify(payload),
      // A reminder that arrives a day late is noise; 12h covers a phone that
      // was off overnight.
      { TTL: 60 * 60 * 12, timeout: 10_000 },
    );
    return classifySendResult(result);
  } catch (error) {
    const statusCode = (error as { statusCode?: number }).statusCode;
    const outcome = classifySendResult({ statusCode });
    if (outcome === "invalid") {
      console.error("push: payload rejected", statusCode, (error as { body?: string }).body);
    }
    return outcome;
  }
}

export async function applySubscriptionEffect(
  subscriptionId: string,
  effect: SubscriptionEffect,
): Promise<void> {
  try {
    if (effect.kind === "success") {
      await db.pushSubscription.updateMany({
        where: { id: subscriptionId },
        data: { lastSuccessAt: new Date(), failureCount: 0 },
      });
    } else if (effect.kind === "delete") {
      await db.pushSubscription.deleteMany({ where: { id: subscriptionId } });
    } else if (effect.kind === "fail") {
      await db.pushSubscription.updateMany({
        where: { id: subscriptionId },
        data: { failureCount: effect.failureCount },
      });
    }
  } catch (error) {
    console.error("push: could not record subscription outcome", error);
  }
}
```

`updateMany`/`deleteMany` are used so a subscription deleted concurrently is not an error.

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/lib/push-send.ts
git commit -m "Add web-push send wrapper"
```

---

### Task 4: Service worker push handlers

**Files:**
- Modify: `public/sw-policy.js` (add `notificationTarget` before the final `scope.chooseStrategy = …` line)
- Modify: `public/sw.js` (`SHELL_VERSION`, new listeners appended at the end)
- Test: `scripts/test-pwa-policy.mts` (append tests)

**Interfaces:**
- Produces: `self.notificationTarget(url: unknown, origin: string): string` (absolute URL, same origin, `/dashboard` fallback).
- Produces: service worker message protocol: client posts `{ type: "GET_VERSION" }` with a `MessagePort` in `ports[0]`; the worker replies `{ version: "v2" }`.
- Produces: the worker POSTs `/api/push/subscription` with `PushSubscription.toJSON()` on `pushsubscriptionchange`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-pwa-policy.mts`:
```ts
const notificationTarget = context.notificationTarget as (
  url: unknown,
  origin: string,
) => string;

test("notification clicks open same-origin paths", () => {
  assert.equal(notificationTarget("/study-plan", ORIGIN), `${ORIGIN}/study-plan`);
  assert.equal(
    notificationTarget("/classroom/biology?tab=notes", ORIGIN),
    `${ORIGIN}/classroom/biology?tab=notes`,
  );
});

test("notification clicks never leave the origin", () => {
  for (const url of [
    "https://evil.com/x",
    "//evil.com",
    "/\\evil.com",
    "javascript:alert(1)",
    "",
    null,
    undefined,
    7,
  ]) {
    assert.equal(notificationTarget(url, ORIGIN), `${ORIGIN}/dashboard`, String(url));
  }
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-pwa-policy.mts`
Expected: the two new tests FAIL (`notificationTarget is not a function`); existing tests pass.

- [ ] **Step 3: Add `notificationTarget` to `public/sw-policy.js`**

Insert immediately before `scope.chooseStrategy = chooseStrategy;`:
```js
  // Where a notification click goes. Mirrors isInternalPath() in
  // src/lib/push-payload.ts: the server never sends anything else, but the
  // worker does not trust the payload. "//host" and "/\host" are
  // protocol-relative to another origin in every browser.
  var INTERNAL_PATH = /^\/(?![/\\])[^\s\\]*$/;

  function notificationTarget(url, origin) {
    if (typeof url === "string" && url.length <= 500 && INTERNAL_PATH.test(url)) {
      return origin + url;
    }
    return origin + "/dashboard";
  }

  scope.notificationTarget = notificationTarget;
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `node --import tsx --test scripts/test-pwa-policy.mts scripts/test-pwa-manifest.mts`
Expected: PASS.

- [ ] **Step 5: Bump the worker version**

In `public/sw.js` change `var SHELL_VERSION = "v1";` to `var SHELL_VERSION = "v2";`. If `scripts/test-pwa-policy.mts` or `scripts/test-pwa-manifest.mts` asserts `"v1"`, update that assertion to `"v2"`.

- [ ] **Step 6: Append the handlers to the end of `public/sw.js`**

```js
// ─── Push notifications ─────────────────────────────────────
// See docs/superpowers/specs/2026-09-14-push-notifications-design.md.

// The page asks which worker is in control before subscribing: a v1 worker
// has no push handler, and a subscription it owned would receive pushes that
// silently show nothing.
self.addEventListener("message", function (event) {
  var data = event.data;
  if (!data || data.type !== "GET_VERSION") return;
  var port = event.ports && event.ports[0];
  if (port) port.postMessage({ version: SHELL_VERSION });
});

self.addEventListener("push", function (event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = {};
  }
  var title = typeof data.title === "string" && data.title ? data.title : "ScholarsCrib";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: typeof data.body === "string" ? data.body : "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: typeof data.tag === "string" && data.tag ? data.tag : undefined,
      data: { url: self.notificationTarget(data.url, self.location.origin) },
    }),
  );
});

// Opens the target rather than navigating an existing tab: navigating would
// discard a half-finished quiz in whichever tab happened to be focused. A tab
// already showing the exact target is focused instead of duplicated.
self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var stored = event.notification.data && event.notification.data.url;
  var target =
    typeof stored === "string" && stored.indexOf(self.location.origin + "/") === 0
      ? stored
      : self.notificationTarget(null, self.location.origin);

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then(function (clients) {
        for (var i = 0; i < clients.length; i += 1) {
          if (clients[i].url === target && "focus" in clients[i]) {
            return clients[i].focus();
          }
        }
        return self.clients.openWindow(target);
      })
      .catch(function () {
        return undefined;
      }),
  );
});

// Browsers may rotate a subscription. Re-subscribe with the same key and tell
// the server; the request carries the session cookie (same-origin).
self.addEventListener("pushsubscriptionchange", function (event) {
  var old = event.oldSubscription;
  var key = old && old.options && old.options.applicationServerKey;
  if (!key) return;
  event.waitUntil(
    self.registration.pushManager
      .subscribe({ userVisibleOnly: true, applicationServerKey: key })
      .then(function (subscription) {
        return fetch("/api/push/subscription", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(subscription.toJSON()),
        });
      })
      .catch(function () {
        // The next app load re-syncs (src/components/push/push-sync.tsx).
      }),
  );
});
```

- [ ] **Step 7: Syntax check**

Run: `node --check public/sw.js && node --check public/sw-policy.js`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add public/sw.js public/sw-policy.js scripts/test-pwa-policy.mts scripts/test-pwa-manifest.mts
git commit -m "Handle push, notification clicks and subscription changes in the service worker"
```

Deviation from the spec, recorded on purpose: the spec says a click "focuses an existing tab and navigates it". This plan opens a new window unless a tab already shows the exact target, because navigating a focused tab could discard an exam in progress, which is the rule `sw.js` is built around.

---

### Task 5: Push capability rules

**Files:**
- Create: `src/lib/push-capability.ts`
- Test: `scripts/test-push-capability.mts`

**Interfaces:**
- Produces: `type PushPermission = "default" | "granted" | "denied"`, `type PushEnvSnapshot = { configured: boolean; hasServiceWorker: boolean; hasPushManager: boolean; hasNotification: boolean; permission: PushPermission; isIOS: boolean; isStandalone: boolean; hasSubscription: boolean }`, `type PushCapability = "unsupported" | "ios-needs-install" | "denied" | "default" | "subscribed"`, `pushCapability(s: PushEnvSnapshot): PushCapability`, `detectIOS(ua: { userAgent: string; maxTouchPoints: number }): boolean`, `REQUIRED_WORKER_VERSION = "v2"`, `isPushWorkerVersion(version: string | null): boolean`, `OPT_IN_SNOOZE_MS = 14 * 24 * 60 * 60 * 1000`, `isOptInSnoozed(snoozedAt: number | null, now: number): boolean`.

- [ ] **Step 1: Write the failing test**

`scripts/test-push-capability.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  OPT_IN_SNOOZE_MS,
  REQUIRED_WORKER_VERSION,
  detectIOS,
  isOptInSnoozed,
  isPushWorkerVersion,
  pushCapability,
  type PushEnvSnapshot,
} from "../src/lib/push-capability";

const base: PushEnvSnapshot = {
  configured: true,
  hasServiceWorker: true,
  hasPushManager: true,
  hasNotification: true,
  permission: "default",
  isIOS: false,
  isStandalone: false,
  hasSubscription: false,
};
const cap = (patch: Partial<PushEnvSnapshot>) => pushCapability({ ...base, ...patch });

test("a capable browser that has not been asked can be asked", () => {
  assert.equal(cap({}), "default");
});

test("not configured on the server is unsupported everywhere", () => {
  assert.equal(cap({ configured: false }), "unsupported");
  assert.equal(cap({ configured: false, isIOS: true }), "unsupported");
});

test("missing browser APIs are unsupported", () => {
  assert.equal(cap({ hasServiceWorker: false }), "unsupported");
  assert.equal(cap({ hasPushManager: false }), "unsupported");
  assert.equal(cap({ hasNotification: false }), "unsupported");
});

test("iOS in a Safari tab needs installing, even though PushManager is absent there", () => {
  assert.equal(cap({ isIOS: true, isStandalone: false, hasPushManager: false }), "ios-needs-install");
  assert.equal(cap({ isIOS: true, isStandalone: true }), "default");
});

test("denied permission is never prompted", () => {
  assert.equal(cap({ permission: "denied" }), "denied");
  assert.equal(cap({ permission: "denied", hasSubscription: true }), "denied");
});

test("granted with a subscription is subscribed; granted without one can re-subscribe", () => {
  assert.equal(cap({ permission: "granted", hasSubscription: true }), "subscribed");
  assert.equal(cap({ permission: "granted", hasSubscription: false }), "default");
});

test("iOS detection covers iPhone and iPadOS reporting as a Mac", () => {
  assert.equal(detectIOS({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", maxTouchPoints: 5 }), true);
  assert.equal(detectIOS({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", maxTouchPoints: 5 }), true);
  assert.equal(detectIOS({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", maxTouchPoints: 0 }), false);
  assert.equal(detectIOS({ userAgent: "Mozilla/5.0 (Linux; Android 14)", maxTouchPoints: 5 }), false);
});

test("only the push-capable worker version passes", () => {
  assert.equal(REQUIRED_WORKER_VERSION, "v2");
  assert.equal(isPushWorkerVersion("v2"), true);
  assert.equal(isPushWorkerVersion("v1"), false);
  assert.equal(isPushWorkerVersion(null), false);
});

test("Not now snoozes the opt-in card for 14 days", () => {
  const now = Date.UTC(2026, 8, 15);
  assert.equal(OPT_IN_SNOOZE_MS, 14 * 24 * 60 * 60 * 1000);
  assert.equal(isOptInSnoozed(null, now), false);
  assert.equal(isOptInSnoozed(now - OPT_IN_SNOOZE_MS + 1, now), true);
  assert.equal(isOptInSnoozed(now - OPT_IN_SNOOZE_MS, now), false);
  assert.equal(isOptInSnoozed(Number.NaN, now), false);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-push-capability.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/push-capability.ts`**

```ts
// Which push UI a browser should see. Pure: the browser snapshot is gathered
// in src/lib/push-client.ts and passed in, so every state is testable.

export type PushPermission = "default" | "granted" | "denied";

export type PushEnvSnapshot = {
  /** NEXT_PUBLIC_VAPID_PUBLIC_KEY is set. */
  configured: boolean;
  hasServiceWorker: boolean;
  hasPushManager: boolean;
  hasNotification: boolean;
  permission: PushPermission;
  isIOS: boolean;
  /** Running as an installed app (display-mode: standalone). */
  isStandalone: boolean;
  hasSubscription: boolean;
};

export type PushCapability =
  | "unsupported"
  | "ios-needs-install"
  | "denied"
  | "default"
  | "subscribed";

export function pushCapability(s: PushEnvSnapshot): PushCapability {
  if (!s.configured) return "unsupported";
  // Checked before the API checks: iOS Safari tabs have no PushManager at
  // all, and "install the app" is the one useful thing to tell them.
  if (s.isIOS && !s.isStandalone) return "ios-needs-install";
  if (!s.hasServiceWorker || !s.hasPushManager || !s.hasNotification) return "unsupported";
  if (s.permission === "denied") return "denied";
  if (s.permission === "granted" && s.hasSubscription) return "subscribed";
  return "default";
}

/** iPadOS 13+ reports itself as a Mac; touch points give it away. */
export function detectIOS(ua: { userAgent: string; maxTouchPoints: number }): boolean {
  if (/iPad|iPhone|iPod/.test(ua.userAgent)) return true;
  return /Macintosh/.test(ua.userAgent) && ua.maxTouchPoints > 1;
}

/** Must match SHELL_VERSION in public/sw.js. */
export const REQUIRED_WORKER_VERSION = "v2";

export function isPushWorkerVersion(version: string | null): boolean {
  return version === REQUIRED_WORKER_VERSION;
}

export const OPT_IN_SNOOZE_MS = 14 * 24 * 60 * 60 * 1000;

export function isOptInSnoozed(snoozedAt: number | null, now: number): boolean {
  if (snoozedAt === null || !Number.isFinite(snoozedAt)) return false;
  return now - snoozedAt < OPT_IN_SNOOZE_MS;
}
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `node --import tsx --test scripts/test-push-capability.mts`
Expected: PASS (9 tests).

- [ ] **Step 5: Register and commit**

Append ` scripts/test-push-capability.mts` to the `test` script.
```bash
git add src/lib/push-capability.ts scripts/test-push-capability.mts package.json
git commit -m "Add push capability rules"
```

---

### Task 6: Subscription and preference API

**Files:**
- Create: `src/lib/push-validators.ts`, `src/lib/push-subscription-data.ts`
- Create: `src/app/api/push/subscription/route.ts`, `src/app/api/user/notification-preferences/route.ts`
- Test: `scripts/test-push-validators.mts`

**Interfaces:**
- Produces (`push-validators.ts`): `pushSubscriptionSchema` (`{ endpoint: string; keys: { p256dh: string; auth: string } }`, extra keys such as `expirationTime` stripped), `unsubscribeSchema` (`{ endpoint: string }`), `notificationPreferencesSchema` (partial `{ studyReminders?: boolean; streakReminders?: boolean; announcements?: boolean }`, at least one key), `type NotificationPreferences = { studyReminders: boolean; streakReminders: boolean; announcements: boolean }`.
- Produces (`push-subscription-data.ts`): `saveSubscription(userId: string, sub: PushSubscriptionInput, userAgent: string | null): Promise<void>`, `deleteSubscription(userId: string, endpoint: string): Promise<void>`, `deleteAllSubscriptions(userId: string): Promise<void>`, `getPreferences(userId: string): Promise<NotificationPreferences>`, `updatePreferences(userId: string, patch: Partial<NotificationPreferences>): Promise<NotificationPreferences>`, `countSubscriptions(userId: string): Promise<number>`.
- Produces HTTP: `POST /api/push/subscription` body = `PushSubscription.toJSON()` → `{ ok: true }`; `DELETE /api/push/subscription` body `{ endpoint }` → `{ ok: true }`; `PATCH /api/user/notification-preferences` → `NotificationPreferences`.

- [ ] **Step 1: Write the failing validator test**

`scripts/test-push-validators.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  notificationPreferencesSchema,
  pushSubscriptionSchema,
  unsubscribeSchema,
} from "../src/lib/push-validators";

const p256dh = "B" + "A".repeat(86); // 87 chars, base64url of 65 bytes
const auth = "A".repeat(22); // base64url of 16 bytes
const valid = {
  endpoint: "https://fcm.googleapis.com/fcm/send/abc123",
  expirationTime: null,
  keys: { p256dh, auth },
};

test("a browser subscription JSON is accepted and trimmed to what we store", () => {
  const parsed = pushSubscriptionSchema.parse(valid);
  assert.deepEqual(parsed, { endpoint: valid.endpoint, keys: { p256dh, auth } });
});

test("endpoints must be https and at most 1024 characters", () => {
  assert.equal(pushSubscriptionSchema.safeParse({ ...valid, endpoint: "http://fcm.googleapis.com/x" }).success, false);
  assert.equal(pushSubscriptionSchema.safeParse({ ...valid, endpoint: "not a url" }).success, false);
  assert.equal(
    pushSubscriptionSchema.safeParse({ ...valid, endpoint: "https://x.com/" + "a".repeat(1020) }).success,
    false,
  );
});

test("keys must be base64url of plausible length", () => {
  assert.equal(pushSubscriptionSchema.safeParse({ ...valid, keys: { p256dh: "short", auth } }).success, false);
  assert.equal(pushSubscriptionSchema.safeParse({ ...valid, keys: { p256dh, auth: "!!!!!!!!!!!!!!!!!!!!!!" } }).success, false);
  assert.equal(pushSubscriptionSchema.safeParse({ endpoint: valid.endpoint }).success, false);
});

test("unsubscribe needs an endpoint", () => {
  assert.equal(unsubscribeSchema.safeParse({ endpoint: valid.endpoint }).success, true);
  assert.equal(unsubscribeSchema.safeParse({}).success, false);
});

test("preference patches need at least one boolean", () => {
  assert.deepEqual(notificationPreferencesSchema.parse({ streakReminders: false }), { streakReminders: false });
  assert.equal(notificationPreferencesSchema.safeParse({}).success, false);
  assert.equal(notificationPreferencesSchema.safeParse({ studyReminders: "yes" }).success, false);
  assert.equal(notificationPreferencesSchema.safeParse({ somethingElse: true }).success, false);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-push-validators.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/push-validators.ts`**

```ts
import { z } from "zod";

const BASE64URL = /^[A-Za-z0-9_-]+={0,2}$/;

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

export const pushSubscriptionSchema = z.object({
  endpoint: z.string().max(1024).refine(isHttpsUrl, "Endpoint must be an https URL"),
  keys: z.object({
    p256dh: z.string().min(80).max(100).regex(BASE64URL),
    auth: z.string().min(16).max(32).regex(BASE64URL),
  }),
});

export type PushSubscriptionInput = z.infer<typeof pushSubscriptionSchema>;

export const unsubscribeSchema = z.object({
  endpoint: z.string().min(1).max(1024),
});

export const notificationPreferencesSchema = z
  .object({
    studyReminders: z.boolean(),
    streakReminders: z.boolean(),
    announcements: z.boolean(),
  })
  .partial()
  .strict()
  .refine((patch) => Object.keys(patch).length > 0, "Nothing to update");

export type NotificationPreferences = {
  studyReminders: boolean;
  streakReminders: boolean;
  announcements: boolean;
};
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `node --import tsx --test scripts/test-push-validators.mts`
Expected: PASS (5 tests).

- [ ] **Step 5: Implement `src/lib/push-subscription-data.ts`**

```ts
import { db } from "@/lib/db";
import type {
  NotificationPreferences,
  PushSubscriptionInput,
} from "@/lib/push-validators";

const DEFAULT_PREFERENCES: NotificationPreferences = {
  studyReminders: true,
  streakReminders: true,
  announcements: true,
};

/**
 * Upsert by endpoint. An endpoint that belonged to another student moves to
 * this one: on a shared family phone, whoever signed in last owns the device.
 */
export async function saveSubscription(
  userId: string,
  sub: PushSubscriptionInput,
  userAgent: string | null,
): Promise<void> {
  const data = {
    userId,
    p256dh: sub.keys.p256dh,
    auth: sub.keys.auth,
    userAgent: userAgent?.slice(0, 300) ?? null,
    failureCount: 0,
  };
  await db.pushSubscription.upsert({
    where: { endpoint: sub.endpoint },
    create: { endpoint: sub.endpoint, ...data },
    update: data,
  });
}

/** Scoped to the caller: one student cannot remove another's device. */
export async function deleteSubscription(userId: string, endpoint: string): Promise<void> {
  await db.pushSubscription.deleteMany({ where: { userId, endpoint } });
}

export async function deleteAllSubscriptions(userId: string): Promise<void> {
  await db.pushSubscription.deleteMany({ where: { userId } });
}

export async function countSubscriptions(userId: string): Promise<number> {
  return db.pushSubscription.count({ where: { userId } });
}

export async function getPreferences(userId: string): Promise<NotificationPreferences> {
  const row = await db.notificationPreference.findUnique({
    where: { userId },
    select: { studyReminders: true, streakReminders: true, announcements: true },
  });
  return row ?? DEFAULT_PREFERENCES;
}

export async function updatePreferences(
  userId: string,
  patch: Partial<NotificationPreferences>,
): Promise<NotificationPreferences> {
  return db.notificationPreference.upsert({
    where: { userId },
    create: { userId, ...DEFAULT_PREFERENCES, ...patch },
    update: patch,
    select: { studyReminders: true, streakReminders: true, announcements: true },
  });
}
```

- [ ] **Step 6: Implement `src/app/api/push/subscription/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { rateLimit, tooManyRequests } from "@/lib/rate-limit";
import { pushSubscriptionSchema, unsubscribeSchema } from "@/lib/push-validators";
import { deleteSubscription, saveSubscription } from "@/lib/push-subscription-data";

export const dynamic = "force-dynamic";

async function requireStudent() {
  const session = await auth();
  return session?.user?.id ?? null;
}

// POST /api/push/subscription — store or refresh this device's subscription
export async function POST(req: NextRequest) {
  const userId = await requireStudent();
  if (!userId) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const limit = await rateLimit({ key: `push-subscribe:${userId}`, limit: 10, windowSeconds: 60 });
  if (!limit.ok) return tooManyRequests(limit.retryAfterSeconds);

  const parsed = pushSubscriptionSchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid subscription" }, { status: 400 });
  }

  try {
    await saveSubscription(userId, parsed.data, req.headers.get("user-agent"));
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Saving push subscription failed:", error);
    return NextResponse.json({ error: "Could not save subscription" }, { status: 500 });
  }
}

// DELETE /api/push/subscription — forget this device (sign-out, master switch off)
export async function DELETE(req: NextRequest) {
  const userId = await requireStudent();
  if (!userId) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const parsed = unsubscribeSchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }

  try {
    await deleteSubscription(userId, parsed.data.endpoint);
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Deleting push subscription failed:", error);
    return NextResponse.json({ error: "Could not remove subscription" }, { status: 500 });
  }
}
```

`rateLimit` takes `{ key, limit, windowSeconds }` and returns `{ ok, remaining, retryAfterSeconds }` (`src/lib/rate-limit.ts`).

- [ ] **Step 7: Implement `src/app/api/user/notification-preferences/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { notificationPreferencesSchema } from "@/lib/push-validators";
import { updatePreferences } from "@/lib/push-subscription-data";

export const dynamic = "force-dynamic";

// PATCH /api/user/notification-preferences — toggles apply to every device
export async function PATCH(req: NextRequest) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const parsed = notificationPreferencesSchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }

  try {
    return NextResponse.json(await updatePreferences(session.user.id, parsed.data));
  } catch (error) {
    console.error("Updating notification preferences failed:", error);
    return NextResponse.json({ error: "Something went wrong. Please try again." }, { status: 500 });
  }
}
```

- [ ] **Step 8: Verify**

Run: `npx tsc --noEmit` and `node --import tsx --test scripts/test-push-validators.mts`
Expected: no errors; PASS.

- [ ] **Step 9: Register and commit**

Append ` scripts/test-push-validators.mts` to the `test` script.
```bash
git add src/lib/push-validators.ts src/lib/push-subscription-data.ts src/app/api/push src/app/api/user/notification-preferences scripts/test-push-validators.mts package.json
git commit -m "Add push subscription and notification preference routes"
```

---

### Task 7: Browser client, re-sync and sign-out cleanup

**Files:**
- Create: `src/lib/push-client.ts`, `src/components/push/push-sync.tsx`
- Modify: `src/app/(dashboard)/layout.tsx` (render `<PushSync />`)
- Modify: `src/components/ui/user-menu.tsx:82-83` (sign-out)
- Modify: `src/lib/admin-student-data.ts:260-265` (`revokeStudentSessions`)

**Interfaces:**
- Consumes: `pushCapability`, `detectIOS`, `isPushWorkerVersion`, `PushEnvSnapshot`, `PushCapability` (Task 5); HTTP routes (Task 6); worker `GET_VERSION` protocol (Task 4).
- Produces (`push-client.ts`, browser only): `readPushState(): Promise<PushCapability>`, `type SubscribeResult = "subscribed" | "denied" | "needs-reload" | "unsupported" | "failed"`, `subscribeThisDevice(): Promise<SubscribeResult>`, `unsubscribeThisDevice(): Promise<void>` (never throws, ≤ 3s), `syncThisDevice(): Promise<void>` (never throws).

- [ ] **Step 1: Implement `src/lib/push-client.ts`**

```ts
"use client";

import {
  detectIOS,
  isPushWorkerVersion,
  pushCapability,
  type PushCapability,
  type PushEnvSnapshot,
} from "@/lib/push-capability";

const PUBLIC_KEY = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ?? "";

function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4))
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function hasPushApis(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

async function currentSubscription(): Promise<PushSubscription | null> {
  if (!hasPushApis()) return null;
  const registration = await navigator.serviceWorker.getRegistration("/");
  return (await registration?.pushManager.getSubscription()) ?? null;
}

export async function readPushState(): Promise<PushCapability> {
  const apis = hasPushApis();
  const snapshot: PushEnvSnapshot = {
    configured: PUBLIC_KEY.length > 0,
    hasServiceWorker: typeof navigator !== "undefined" && "serviceWorker" in navigator,
    hasPushManager: typeof window !== "undefined" && "PushManager" in window,
    hasNotification: typeof window !== "undefined" && "Notification" in window,
    permission: apis ? (Notification.permission as PushEnvSnapshot["permission"]) : "default",
    isIOS: detectIOS({ userAgent: navigator.userAgent, maxTouchPoints: navigator.maxTouchPoints }),
    isStandalone: window.matchMedia("(display-mode: standalone)").matches,
    hasSubscription: apis ? (await currentSubscription().catch(() => null)) !== null : false,
  };
  return pushCapability(snapshot);
}

function workerVersion(registration: ServiceWorkerRegistration): Promise<string | null> {
  const worker = registration.active;
  if (!worker) return Promise.resolve(null);
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    const timer = setTimeout(() => resolve(null), 1500);
    channel.port1.onmessage = (event) => {
      clearTimeout(timer);
      resolve(typeof event.data?.version === "string" ? event.data.version : null);
    };
    worker.postMessage({ type: "GET_VERSION" }, [channel.port2]);
  });
}

async function postSubscription(subscription: PushSubscription): Promise<boolean> {
  const res = await fetch("/api/push/subscription", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(subscription.toJSON()),
  });
  return res.ok;
}

export type SubscribeResult = "subscribed" | "denied" | "needs-reload" | "unsupported" | "failed";

/** Call only from a click handler: the permission prompt needs a user gesture. */
export async function subscribeThisDevice(): Promise<SubscribeResult> {
  if (!hasPushApis() || !PUBLIC_KEY) return "unsupported";
  try {
    const registration = await navigator.serviceWorker.ready;
    // A v1 worker has no push handler; a subscription it owns would receive
    // pushes that show nothing.
    if (!isPushWorkerVersion(await workerVersion(registration))) return "needs-reload";

    const permission = await Notification.requestPermission();
    if (permission === "denied") return "denied";
    if (permission !== "granted") return "failed";

    const subscription =
      (await registration.pushManager.getSubscription()) ??
      (await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(PUBLIC_KEY),
      }));
    return (await postSubscription(subscription)) ? "subscribed" : "failed";
  } catch (error) {
    console.error("Push subscribe failed", error);
    return "failed";
  }
}

/**
 * Removes this device on the server first (while the session still exists),
 * then in the browser. Bounded to 3s so sign-out never hangs on it.
 */
export async function unsubscribeThisDevice(): Promise<void> {
  const work = (async () => {
    const subscription = await currentSubscription();
    if (!subscription) return;
    await fetch("/api/push/subscription", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    }).catch(() => undefined);
    await subscription.unsubscribe().catch(() => undefined);
  })().catch(() => undefined);
  await Promise.race([work, new Promise((resolve) => setTimeout(resolve, 3000))]);
}

/** Browsers rotate endpoints; a cheap upsert on load keeps the server current. */
export async function syncThisDevice(): Promise<void> {
  try {
    if (!hasPushApis() || Notification.permission !== "granted") return;
    const subscription = await currentSubscription();
    if (subscription) await postSubscription(subscription);
  } catch {
    // Offline or signed out mid-request. The next load tries again.
  }
}
```

If `tsc` rejects `Uint8Array<ArrayBuffer>` (older `@types/node`/lib), change the return type to `Uint8Array` and keep the body.

- [ ] **Step 2: Implement `src/components/push/push-sync.tsx`**

```tsx
"use client";

import { useEffect } from "react";
import { syncThisDevice } from "@/lib/push-client";

/** Renders nothing. Re-sends this device's subscription once per app load. */
export function PushSync() {
  useEffect(() => {
    void syncThisDevice();
  }, []);
  return null;
}
```

- [ ] **Step 3: Mount it in `src/app/(dashboard)/layout.tsx`**

Add `import { PushSync } from "@/components/push/push-sync";` and render `<PushSync />` as the first child inside `<div className="min-h-full">`.

- [ ] **Step 4: Clean up on sign-out in `src/components/ui/user-menu.tsx`**

Replace:
```tsx
    const { signOut } = await import("next-auth/react");
    signOut({ callbackUrl: "/login" });
```
with:
```tsx
    // Before the session ends: the next student on a shared phone must not
    // receive this student's reminders.
    const [{ signOut }, { unsubscribeThisDevice }] = await Promise.all([
      import("next-auth/react"),
      import("@/lib/push-client"),
    ]);
    await unsubscribeThisDevice();
    signOut({ callbackUrl: "/login" });
```
Then run `grep -rn "signOut(" src --include=*.tsx` and apply the same change to any other student-facing sign-out of *this* device (not `device-list.tsx`, which signs out *other* devices, and not the admin sign-out).

- [ ] **Step 5: Force sign-out removes every subscription**

In `src/lib/admin-student-data.ts` replace `revokeStudentSessions` with:
```ts
export async function revokeStudentSessions(id: string): Promise<void> {
  await db.$transaction([
    db.user.update({
      where: { id },
      data: { sessionsValidFrom: new Date() },
    }),
    // A signed-out student's phones must stop receiving their reminders too.
    db.pushSubscription.deleteMany({ where: { userId: id } }),
  ]);
}
```

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit` and `npm run lint`
Expected: no errors.

Manual: `npm run dev`, sign in, open DevTools → Application → Service Workers. After closing and reopening all tabs the active worker is the new script; in the console `navigator.serviceWorker.ready` resolves.

- [ ] **Step 7: Commit**

```bash
git add src/lib/push-client.ts src/components/push/push-sync.tsx "src/app/(dashboard)/layout.tsx" src/components/ui/user-menu.tsx src/lib/admin-student-data.ts
git commit -m "Subscribe, re-sync and clean up push subscriptions in the browser"
```

---

### Task 8: Settings → Notifications and the study plan opt-in card

**Files:**
- Create: `src/components/settings/notifications-section.tsx` (server), `src/components/settings/notification-settings.tsx` (client)
- Create: `src/components/study-plan/reminder-opt-in-card.tsx` (client)
- Modify: `src/app/(dashboard)/settings/page.tsx` (render after `DevicesSection`)
- Modify: `src/app/(dashboard)/study-plan/page.tsx` (render the card above `StudyPlanView` when a plan exists)

**Interfaces:**
- Consumes: `readPushState`, `subscribeThisDevice`, `unsubscribeThisDevice`, `SubscribeResult` (Task 7); `getPreferences`, `NotificationPreferences` (Task 6); `isOptInSnoozed` (Task 5); `Section`, `FormMessage` from `src/components/settings/section.tsx`; `buttonClass` from `@/components/ui/button`.
- Produces: `<NotificationsSection userId />`, `<NotificationSettings initial={NotificationPreferences} />`, `<ReminderOptInCard />`.

UI work: verified by typecheck, lint and the manual checks in Step 5.

- [ ] **Step 1: Implement `src/components/settings/notification-settings.tsx`**

`PUSH_STATE_COPY` is exported because the opt-in card (Step 3) shows the same messages.

```tsx
"use client";

import { useEffect, useState } from "react";
import { buttonClass } from "@/components/ui/button";
import { FormMessage } from "./section";
import type { PushCapability } from "@/lib/push-capability";
import type { NotificationPreferences } from "@/lib/push-validators";
import {
  readPushState,
  subscribeThisDevice,
  unsubscribeThisDevice,
} from "@/lib/push-client";

export const PUSH_STATE_COPY: Record<string, string> = {
  unsupported: "This browser can't show ScholarsCrib notifications.",
  "ios-needs-install":
    "On iPhone and iPad, reminders need the app installed: tap Share, then Add to Home Screen, then open ScholarsCrib from your Home Screen.",
  denied: "Notifications are blocked for ScholarsCrib. Turn them on in your browser's site settings.",
  "needs-reload": "Almost there: close every ScholarsCrib tab, reopen the app, then try again.",
  failed: "Couldn't turn on notifications. Please try again.",
};

const TOGGLES: { key: keyof NotificationPreferences; label: string; hint: string }[] = [
  { key: "studyReminders", label: "Morning study reminder", hint: "Around 7am: today's plan and flashcards due." },
  { key: "streakReminders", label: "Streak reminder", hint: "Around 7pm, only if your streak is about to end." },
  { key: "announcements", label: "Announcements", hint: "Important news from ScholarsCrib." },
];

export function NotificationSettings({ initial }: { initial: NotificationPreferences }) {
  const [state, setState] = useState<PushCapability | null>(null);
  const [prefs, setPrefs] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    readPushState().then(setState, () => setState("unsupported"));
  }, []);

  async function enable() {
    setBusy(true);
    setError("");
    const result = await subscribeThisDevice();
    if (result !== "subscribed") setError(PUSH_STATE_COPY[result] ?? PUSH_STATE_COPY.failed);
    setState(await readPushState());
    setBusy(false);
  }

  async function disable() {
    setBusy(true);
    setError("");
    await unsubscribeThisDevice();
    setState(await readPushState());
    setBusy(false);
  }

  async function toggle(key: keyof NotificationPreferences) {
    const previous = prefs;
    const next = { ...prefs, [key]: !prefs[key] };
    setPrefs(next);
    setError("");
    try {
      const res = await fetch("/api/user/notification-preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: next[key] }),
      });
      if (!res.ok) throw new Error(String(res.status));
    } catch {
      setPrefs(previous);
      setError("Couldn't save that change. Please try again.");
    }
  }

  if (state === null) return <p className="text-sm text-muted">Checking this device…</p>;

  return (
    <div>
      <FormMessage error={error} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">Notifications on this device</p>
          <p className="text-sm text-muted">
            {state === "subscribed" ? "On" : state === "default" ? "Off" : PUSH_STATE_COPY[state]}
          </p>
        </div>
        {state === "subscribed" && (
          <button type="button" disabled={busy} onClick={disable} className={buttonClass("outline", "sm")}>
            {busy ? "Turning off…" : "Turn off"}
          </button>
        )}
        {state === "default" && (
          <button type="button" disabled={busy} onClick={enable} className={buttonClass("primary", "sm")}>
            {busy ? "Turning on…" : "Turn on"}
          </button>
        )}
      </div>

      <ul className="mt-5 space-y-4 border-t border-border pt-5">
        {TOGGLES.map((toggleItem) => (
          <li key={toggleItem.key} className="flex items-start justify-between gap-4">
            <label htmlFor={`pref-${toggleItem.key}`} className="min-w-0">
              <span className="block text-sm font-semibold text-foreground">{toggleItem.label}</span>
              <span className="block text-sm text-muted">{toggleItem.hint}</span>
            </label>
            <input
              id={`pref-${toggleItem.key}`}
              type="checkbox"
              role="switch"
              checked={prefs[toggleItem.key]}
              onChange={() => toggle(toggleItem.key)}
              className="mt-1 h-5 w-5 shrink-0 accent-primary"
            />
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-muted">These choices apply to all your devices.</p>
    </div>
  );
}
```

- [ ] **Step 2: Implement `src/components/settings/notifications-section.tsx`**

```tsx
import { getPreferences } from "@/lib/push-subscription-data";
import { Section } from "./section";
import { NotificationSettings } from "./notification-settings";

export async function NotificationsSection({ userId }: { userId: string }) {
  // Push not configured on this deployment: say nothing rather than offer a
  // switch that cannot work.
  if (!process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY) return null;

  let preferences;
  try {
    preferences = await getPreferences(userId);
  } catch (error) {
    // Degrade this section, not all of Settings (e.g. before the migration).
    console.error("Loading notification preferences failed:", error);
    return (
      <Section title="Notifications">
        <p className="text-sm text-muted">Notification settings could not be loaded. Please try again later.</p>
      </Section>
    );
  }

  return (
    <Section title="Notifications" description="Reminders and announcements on your phone or computer.">
      <NotificationSettings initial={preferences} />
    </Section>
  );
}
```

Add to `src/app/(dashboard)/settings/page.tsx`: `import { NotificationsSection } from "@/components/settings/notifications-section";` and `<NotificationsSection userId={session.user.id} />` directly after the `<DevicesSection … />` element.

- [ ] **Step 3: Implement `src/components/study-plan/reminder-opt-in-card.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { buttonClass } from "@/components/ui/button";
import { isOptInSnoozed } from "@/lib/push-capability";
import { readPushState, subscribeThisDevice } from "@/lib/push-client";
import { PUSH_STATE_COPY } from "@/components/settings/notification-settings";

const SNOOZE_KEY = "scholarscrib.reminder-opt-in-snoozed-at";

function readSnooze(): number | null {
  try {
    const raw = window.localStorage.getItem(SNOOZE_KEY);
    return raw === null ? null : Number(raw);
  } catch {
    return null;
  }
}

function writeSnooze(at: number) {
  try {
    window.localStorage.setItem(SNOOZE_KEY, String(at));
  } catch {
    // Private mode: the card simply comes back next visit.
  }
}

/** Shown on the study plan page. Never prompts on its own: only on "Turn on". */
export function ReminderOptInCard() {
  const [visible, setVisible] = useState(false);
  const [iosHint, setIosHint] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (isOptInSnoozed(readSnooze(), Date.now())) return;
    readPushState().then((state) => {
      if (state === "default") setVisible(true);
      if (state === "ios-needs-install") {
        setIosHint(true);
        setVisible(true);
      }
    }, () => undefined);
  }, []);

  if (!visible) return null;

  function notNow() {
    writeSnooze(Date.now());
    setVisible(false);
  }

  async function turnOn() {
    setBusy(true);
    const result = await subscribeThisDevice();
    setBusy(false);
    if (result === "subscribed") {
      setVisible(false);
      return;
    }
    setMessage(PUSH_STATE_COPY[result] ?? PUSH_STATE_COPY.failed);
  }

  return (
    <div className="card mb-6 p-4 sm:p-5">
      <p className="text-sm font-bold text-foreground">Get a morning reminder of today&apos;s topics?</p>
      <p className="mt-1 text-sm text-muted">
        {iosHint ? PUSH_STATE_COPY["ios-needs-install"] : message || "One notification around 7am. Change it any time in Settings."}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {!iosHint && (
          <button type="button" disabled={busy} onClick={turnOn} className={buttonClass("primary", "sm")}>
            {busy ? "Turning on…" : "Turn on"}
          </button>
        )}
        <button type="button" onClick={notNow} className={buttonClass("outline", "sm")}>
          Not now
        </button>
      </div>
    </div>
  );
}
```

In `src/app/(dashboard)/study-plan/page.tsx`, import the card and render it immediately before `<StudyPlanView … />`, only when `plan` is not null, wrapping both in a fragment:
```tsx
  return (
    <>
      {plan && <ReminderOptInCard />}
      <StudyPlanView
        …existing props unchanged…
      />
    </>
  );
```
If `feat/study-plan-term-mode` has merged and restructured this page, render the card in the same place relative to whatever component now shows an existing plan.

Deviation from the spec, recorded on purpose: the spec says the card appears "after a study plan is saved". It is rendered whenever the student has a plan and has not opted in or snoozed, which includes right after saving, and does not depend on the plan form's internals (which the term-mode branch is rewriting).

- [ ] **Step 4: Verify types and lint**

Run: `npx tsc --noEmit` and `npm run lint`
Expected: no errors.

- [ ] **Step 5: Manual check**

With VAPID env set and `npm run dev`: Settings shows Notifications; **Turn on** shows the browser prompt; after allowing, the state reads "On" and a `PushSubscription` row exists (Prisma Studio or SQL Editor). Toggling a switch persists after reload. **Turn off** removes the row. On the study plan page with a plan, the card shows; **Not now** hides it across reloads.

- [ ] **Step 6: Commit**

```bash
git add src/components/settings/notifications-section.tsx src/components/settings/notification-settings.tsx src/components/study-plan/reminder-opt-in-card.tsx "src/app/(dashboard)/settings/page.tsx" "src/app/(dashboard)/study-plan/page.tsx"
git commit -m "Add notification settings and the study plan reminder opt-in"
```

---

### Task 9: Cron authentication

**Files:**
- Create: `src/lib/cron-auth.ts`
- Modify: `src/proxy.ts` (matcher, ~line 113)
- Test: `scripts/test-cron-auth.mts`

**Interfaces:**
- Consumes: `readCronSecret` (Task 1).
- Produces: `type CronCheck = "ok" | "unauthorized" | "method-not-allowed" | "not-configured"`, `checkCronRequest(input: { method: string; authorization: string | null; secret: string | null }): CronCheck`, `cronGuard(req: Request): Response | null` (null = proceed), `CRON_BUDGET_MS = 40_000`, `deadlineFrom(startedAt: number): number`.

- [ ] **Step 1: Write the failing test**

`scripts/test-cron-auth.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { CRON_BUDGET_MS, checkCronRequest, deadlineFrom } from "../src/lib/cron-auth";

const secret = "a-very-long-cron-secret";

test("POST with the right bearer secret is allowed", () => {
  assert.equal(checkCronRequest({ method: "POST", authorization: `Bearer ${secret}`, secret }), "ok");
});

test("wrong, missing or malformed credentials are unauthorized", () => {
  for (const authorization of [null, "", secret, `Bearer ${secret}x`, `Bearer ${secret.slice(1)}`, `Basic ${secret}`, "Bearer "]) {
    assert.equal(checkCronRequest({ method: "POST", authorization, secret }), "unauthorized", String(authorization));
  }
});

test("only POST is accepted", () => {
  assert.equal(checkCronRequest({ method: "GET", authorization: `Bearer ${secret}`, secret }), "method-not-allowed");
});

test("no configured secret means the feature is off, whatever is sent", () => {
  assert.equal(checkCronRequest({ method: "POST", authorization: "Bearer anything", secret: null }), "not-configured");
});

test("the budget is 40 seconds from the start of the request", () => {
  assert.equal(CRON_BUDGET_MS, 40_000);
  assert.equal(deadlineFrom(1_000), 41_000);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-cron-auth.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/cron-auth.ts`**

```ts
import { createHash, timingSafeEqual } from "node:crypto";
import { readCronSecret } from "@/lib/push-config";

export type CronCheck = "ok" | "unauthorized" | "method-not-allowed" | "not-configured";

/** Vercel Hobby caps functions at 60s; stop starting new work well before. */
export const CRON_BUDGET_MS = 40_000;

export function deadlineFrom(startedAt: number): number {
  return startedAt + CRON_BUDGET_MS;
}

// Hashing first makes both buffers the same length, so timingSafeEqual never
// throws and the comparison leaks nothing about the secret's length.
function sameSecret(a: string, b: string): boolean {
  const left = createHash("sha256").update(a).digest();
  const right = createHash("sha256").update(b).digest();
  return timingSafeEqual(left, right);
}

export function checkCronRequest(input: {
  method: string;
  authorization: string | null;
  secret: string | null;
}): CronCheck {
  if (input.method !== "POST") return "method-not-allowed";
  if (!input.secret) return "not-configured";
  const header = input.authorization ?? "";
  if (!header.startsWith("Bearer ")) return "unauthorized";
  return sameSecret(header.slice("Bearer ".length), input.secret) ? "ok" : "unauthorized";
}

/** Returns a response to send, or null to proceed. */
export function cronGuard(req: Request): Response | null {
  const check = checkCronRequest({
    method: req.method,
    authorization: req.headers.get("authorization"),
    secret: readCronSecret(),
  });
  if (check === "ok") return null;
  if (check === "not-configured") {
    console.warn("cron: CRON_SECRET is not configured; skipping");
    return new Response(null, { status: 204 });
  }
  if (check === "method-not-allowed") {
    return Response.json({ error: "Method not allowed" }, { status: 405 });
  }
  return Response.json({ error: "Unauthorized" }, { status: 401 });
}
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `node --import tsx --test scripts/test-cron-auth.mts`
Expected: PASS (5 tests).

- [ ] **Step 5: Exclude cron routes from the session proxy**

In `src/proxy.ts`, extend the matcher comment and pattern:
```ts
    // api/billing/webhook is excluded because Paystack is not a signed-in
    // user: …(keep the existing comment)…
    // api/cron is excluded for the same reason: Supabase pg_cron calls it with
    // a bearer secret that the route checks itself (src/lib/cron-auth.ts).
    "/((?!api/auth|api/billing/webhook|api/cron|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
```
Do **not** add cron paths to `src/lib/public-routes.ts`.

- [ ] **Step 6: Verify the proxy tests still pass**

Run: `node --import tsx --test scripts/test-public-routes.mts scripts/test-cron-auth.mts` and `npx tsc --noEmit`
Expected: PASS; no errors.

- [ ] **Step 7: Register and commit**

Append ` scripts/test-cron-auth.mts` to the `test` script.
```bash
git add src/lib/cron-auth.ts src/proxy.ts scripts/test-cron-auth.mts package.json
git commit -m "Authenticate cron routes with a bearer secret"
```

---

### Task 10: Reminder content and eligibility rules

**Files:**
- Create: `src/lib/push-reminders.ts`
- Test: `scripts/test-push-reminders.mts`

**Interfaces:**
- Consumes: `currentStreak`, `previousDayKey` from `src/lib/streak.ts`.
- Produces: `type ReminderKind = "morning" | "streak"`, `type ReminderMessage = { title: string; body: string; url: string }`, `type DigestPlanItem = { topicName: string | null; subjectName: string; durationMinutes: number }`, `buildMorningDigest(input: { planItems: DigestPlanItem[]; dueCards: number }): ReminderMessage | null`, `streakToRemind(dayKeys: Iterable<string>, today: string): number | null`, `buildStreakReminder(streak: number): ReminderMessage`, `lagosDayStart(dayKey: string): Date`, `lagosDayEnd(dayKey: string): Date`, `planDateFor(dayKey: string): Date`, `REMINDER_PAGE_SIZE = 200`.

- [ ] **Step 1: Write the failing test**

`scripts/test-push-reminders.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  REMINDER_PAGE_SIZE,
  buildMorningDigest,
  buildStreakReminder,
  lagosDayEnd,
  lagosDayStart,
  planDateFor,
  streakToRemind,
  type DigestPlanItem,
} from "../src/lib/push-reminders";
import { lagosDayKey } from "../src/lib/streak";

const item = (topicName: string | null, subjectName: string, durationMinutes: number): DigestPlanItem => ({
  topicName,
  subjectName,
  durationMinutes,
});

test("nothing planned and nothing due sends nothing", () => {
  assert.equal(buildMorningDigest({ planItems: [], dueCards: 0 }), null);
});

test("plan and cards together", () => {
  const message = buildMorningDigest({
    planItems: [item("Photosynthesis", "Biology", 30), item("Vectors", "Physics", 20), item(null, "English", 10)],
    dueCards: 12,
  });
  assert.deepEqual(message, {
    title: "Today's study plan",
    body: "Today: 3 topics · 60 min, and 12 flashcards due",
    url: "/study-plan",
  });
});

test("singular forms", () => {
  const message = buildMorningDigest({ planItems: [item("Vectors", "Physics", 25)], dueCards: 1 });
  assert.equal(message?.body, "Today: 1 topic · 25 min, and 1 flashcard due");
});

test("plan only names the first topic, or its subject when there is no topic", () => {
  assert.equal(
    buildMorningDigest({ planItems: [item("Photosynthesis", "Biology", 30), item("Cells", "Biology", 30)], dueCards: 0 })?.body,
    "Today's plan: Photosynthesis + 1 more (60 min)",
  );
  assert.equal(
    buildMorningDigest({ planItems: [item(null, "Mathematics", 45)], dueCards: 0 })?.body,
    "Today's plan: Mathematics (45 min)",
  );
  assert.equal(buildMorningDigest({ planItems: [item(null, "Mathematics", 45)], dueCards: 0 })?.url, "/study-plan");
});

test("cards only", () => {
  assert.deepEqual(buildMorningDigest({ planItems: [], dueCards: 12 }), {
    title: "Flashcards due",
    body: "12 flashcards are due for review",
    url: "/flashcards",
  });
  assert.equal(buildMorningDigest({ planItems: [], dueCards: 1 })?.body, "1 flashcard is due for review");
});

test("a very long topic name stays within the body limit", () => {
  const message = buildMorningDigest({ planItems: [item("x".repeat(400), "Biology", 30)], dueCards: 0 });
  assert.ok(message && message.body.length <= 180);
});

test("streak reminder needs at least two days ending yesterday and nothing today", () => {
  const today = "2026-09-15";
  assert.equal(streakToRemind(["2026-09-14", "2026-09-13", "2026-09-12"], today), 3);
  assert.equal(streakToRemind(["2026-09-14", "2026-09-13"], today), 2);
  assert.equal(streakToRemind(["2026-09-14"], today), null, "a 1-day streak is not worth a nudge");
  assert.equal(streakToRemind(["2026-09-15", "2026-09-14", "2026-09-13"], today), null, "already practised today");
  assert.equal(streakToRemind(["2026-09-13", "2026-09-12"], today), null, "already broken yesterday");
  assert.equal(streakToRemind([], today), null);
});

test("streak copy", () => {
  assert.deepEqual(buildStreakReminder(6), {
    title: "Keep your streak going",
    body: "Keep your 6-day streak: one quick practice before midnight",
    url: "/practice",
  });
});

test("Lagos day bounds are UTC+1", () => {
  assert.equal(lagosDayStart("2026-09-15").toISOString(), "2026-09-14T23:00:00.000Z");
  assert.equal(lagosDayEnd("2026-09-15").toISOString(), "2026-09-15T23:00:00.000Z");
  // 23:30 UTC on the 14th is 00:30 on the 15th in Lagos: inside the day.
  const lateNight = new Date("2026-09-14T23:30:00.000Z");
  assert.equal(lagosDayKey(lateNight), "2026-09-15");
  assert.ok(lateNight >= lagosDayStart("2026-09-15") && lateNight < lagosDayEnd("2026-09-15"));
});

test("plan dates are stored as UTC midnight of the day key", () => {
  assert.equal(planDateFor("2026-09-15").toISOString(), "2026-09-15T00:00:00.000Z");
});

test("page size", () => {
  assert.equal(REMINDER_PAGE_SIZE, 200);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-push-reminders.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/push-reminders.ts`**

```ts
// What the morning and evening reminders say, and who gets the evening one.
// Pure — no database — so the copy and the streak rule are tested directly.

import { BODY_MAX, truncate } from "@/lib/push-payload";
import { currentStreak, previousDayKey } from "@/lib/streak";

export type ReminderKind = "morning" | "streak";

export type ReminderMessage = { title: string; body: string; url: string };

export type DigestPlanItem = {
  topicName: string | null;
  subjectName: string;
  durationMinutes: number;
};

export const REMINDER_PAGE_SIZE = 200;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function buildMorningDigest(input: {
  planItems: DigestPlanItem[];
  dueCards: number;
}): ReminderMessage | null {
  const { planItems, dueCards } = input;
  const minutes = planItems.reduce((sum, i) => sum + i.durationMinutes, 0);

  if (planItems.length > 0 && dueCards > 0) {
    return {
      title: "Today's study plan",
      body: `Today: ${plural(planItems.length, "topic", "topics")} · ${minutes} min, and ${plural(dueCards, "flashcard", "flashcards")} due`,
      url: "/study-plan",
    };
  }

  if (planItems.length > 0) {
    const first = planItems[0];
    const more = planItems.length > 1 ? ` + ${planItems.length - 1} more` : "";
    const suffix = `${more} (${minutes} min)`;
    const prefix = "Today's plan: ";
    const label = truncate(first.topicName ?? first.subjectName, BODY_MAX - prefix.length - suffix.length);
    return { title: "Today's study plan", body: `${prefix}${label}${suffix}`, url: "/study-plan" };
  }

  if (dueCards > 0) {
    return {
      title: "Flashcards due",
      body: `${plural(dueCards, "flashcard", "flashcards")} ${dueCards === 1 ? "is" : "are"} due for review`,
      url: "/flashcards",
    };
  }

  return null;
}

/**
 * The streak to mention tonight, or null for no reminder. Uses the same
 * currentStreak() as the achievements badge so the numbers agree.
 */
export function streakToRemind(dayKeys: Iterable<string>, today: string): number | null {
  const days = new Set(dayKeys);
  if (days.has(today)) return null;
  const streak = currentStreak(days, previousDayKey(today));
  return streak >= 2 ? streak : null;
}

export function buildStreakReminder(streak: number): ReminderMessage {
  return {
    title: "Keep your streak going",
    body: `Keep your ${streak}-day streak: one quick practice before midnight`,
    url: "/practice",
  };
}

const HOUR_MS = 60 * 60 * 1000;

/** Lagos is UTC+1 all year: a Lagos day starts at 23:00 UTC the day before. */
export function lagosDayStart(dayKey: string): Date {
  const [y, m, d] = dayKey.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d) - HOUR_MS);
}

export function lagosDayEnd(dayKey: string): Date {
  return new Date(lagosDayStart(dayKey).getTime() + 24 * HOUR_MS);
}

/** StudyPlanItem.scheduledDate is @db.Date, stored as UTC midnight of the civil day. */
export function planDateFor(dayKey: string): Date {
  return new Date(`${dayKey}T00:00:00.000Z`);
}
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `node --import tsx --test scripts/test-push-reminders.mts`
Expected: PASS (11 tests).

- [ ] **Step 5: Confirm the plan date convention**

Run: `grep -rn "scheduledDate" src/lib/study-plan*.ts src/lib/study-plan/ 2>/dev/null | head -20`
Expected: dates written as UTC midnight of the civil day (`new Date("YYYY-MM-DDT00:00:00Z")` or `Date.UTC`). If the code stores a different instant, change `planDateFor` and its test to match that code, not the other way round.

- [ ] **Step 6: Register and commit**

Append ` scripts/test-push-reminders.mts` to the `test` script.
```bash
git add src/lib/push-reminders.ts scripts/test-push-reminders.mts package.json
git commit -m "Add morning digest and streak reminder rules"
```

---

### Task 11: Reminder runner and cron routes

**Files:**
- Create: `src/lib/push-reminder-runner.ts`
- Create: `src/app/api/cron/push/morning/route.ts`, `src/app/api/cron/push/streak/route.ts`

**Interfaces:**
- Consumes: `buildMorningDigest`, `streakToRemind`, `buildStreakReminder`, `lagosDayStart`, `lagosDayEnd`, `planDateFor`, `REMINDER_PAGE_SIZE`, `ReminderKind`, `ReminderMessage` (Task 10); `lagosDayKey`, `previousDayKey` (`src/lib/streak.ts`); `buildPushPayload`, `pushTag` (Task 2); `subscriptionEffect`, `mapWithConcurrency` (Task 2); `sendPush`, `applySubscriptionEffect`, `SUBSCRIPTION_SELECT` (Task 3); `readPushConfig` (Task 1); `cronGuard`, `deadlineFrom` (Task 9).
- Produces: `runReminders(kind: ReminderKind, options: { now: Date; deadline: number }): Promise<{ processed: number; notified: number; sent: number; done: boolean }>`; `POST /api/cron/push/morning` and `/streak` → that result as JSON, or `204` when push is not configured.

Verified by typecheck here and by a local dry run in Step 5; its rules were unit-tested in Tasks 2 and 10.

- [ ] **Step 1: Implement `src/lib/push-reminder-runner.ts`**

```ts
import { Prisma } from "@prisma/client";
import { db } from "@/lib/db";
import { lagosDayKey, previousDayKey } from "@/lib/streak";
import { buildPushPayload, pushTag } from "@/lib/push-payload";
import { mapWithConcurrency, subscriptionEffect } from "@/lib/push-send-result";
import { SUBSCRIPTION_SELECT, applySubscriptionEffect, sendPush } from "@/lib/push-send";
import {
  REMINDER_PAGE_SIZE,
  buildMorningDigest,
  buildStreakReminder,
  lagosDayEnd,
  lagosDayStart,
  planDateFor,
  streakToRemind,
  type DigestPlanItem,
  type ReminderKind,
  type ReminderMessage,
} from "@/lib/push-reminders";

const SEND_CONCURRENCY = 20;
const STREAK_LOOKBACK_DAYS = 400;
const DAY_MS = 24 * 60 * 60 * 1000;

type RunResult = { processed: number; notified: number; sent: number; done: boolean };

/** Students who could get this reminder and have not been processed today. */
async function findCandidates(kind: ReminderKind, dayKey: string): Promise<string[]> {
  const preference: Prisma.UserWhereInput =
    kind === "morning"
      ? { OR: [{ notificationPreference: null }, { notificationPreference: { studyReminders: true } }] }
      : { OR: [{ notificationPreference: null }, { notificationPreference: { streakReminders: true } }] };

  const narrowing: Prisma.UserWhereInput =
    kind === "streak"
      ? {
          // Only students who practised yesterday can have a streak at risk.
          attempts: {
            some: {
              status: "COMPLETED",
              completedAt: {
                gte: lagosDayStart(previousDayKey(dayKey)),
                lt: lagosDayStart(dayKey),
              },
            },
          },
        }
      : {};

  const rows = await db.user.findMany({
    where: {
      role: "STUDENT",
      isActive: true,
      pushSubscriptions: { some: {} },
      reminderLogs: { none: { kind, dayKey } },
      AND: [preference, narrowing],
    },
    select: { id: true },
    orderBy: { id: "asc" },
    take: REMINDER_PAGE_SIZE,
  });
  return rows.map((r) => r.id);
}

/**
 * Claims the page: only users whose log row THIS call inserted are returned,
 * so an overlapping or retried call can never notify the same student twice.
 */
async function claim(kind: ReminderKind, dayKey: string, userIds: string[]): Promise<string[]> {
  if (userIds.length === 0) return [];
  const rows = await db.$queryRaw<{ userId: string }[]>`
    INSERT INTO "ReminderLog" ("userId", "kind", "dayKey", "sentAt")
    SELECT u, ${kind}, ${dayKey}, now() FROM unnest(${userIds}::text[]) AS u
    ON CONFLICT DO NOTHING
    RETURNING "userId"`;
  return rows.map((r) => r.userId);
}

async function morningMessages(userIds: string[], dayKey: string): Promise<Map<string, ReminderMessage>> {
  const [items, due] = await Promise.all([
    db.studyPlanItem.findMany({
      where: {
        status: "PENDING",
        scheduledDate: planDateFor(dayKey),
        studyPlan: { isActive: true, studentId: { in: userIds } },
      },
      select: {
        durationMinutes: true,
        topic: { select: { name: true } },
        subject: { select: { name: true } },
        studyPlan: { select: { studentId: true } },
      },
      orderBy: { id: "asc" },
    }),
    db.flashcardReview.groupBy({
      by: ["studentId"],
      where: { studentId: { in: userIds }, dueAt: { lt: lagosDayEnd(dayKey) } },
      _count: { _all: true },
    }),
  ]);

  const planByUser = new Map<string, DigestPlanItem[]>();
  for (const row of items) {
    const list = planByUser.get(row.studyPlan.studentId) ?? [];
    list.push({
      topicName: row.topic?.name ?? null,
      subjectName: row.subject.name,
      durationMinutes: row.durationMinutes,
    });
    planByUser.set(row.studyPlan.studentId, list);
  }
  const dueByUser = new Map(due.map((d) => [d.studentId, d._count._all]));

  const messages = new Map<string, ReminderMessage>();
  for (const userId of userIds) {
    const message = buildMorningDigest({
      planItems: planByUser.get(userId) ?? [],
      dueCards: dueByUser.get(userId) ?? 0,
    });
    if (message) messages.set(userId, message);
  }
  return messages;
}

async function streakMessages(userIds: string[], dayKey: string, now: Date): Promise<Map<string, ReminderMessage>> {
  const attempts = await db.assessmentAttempt.findMany({
    where: {
      studentId: { in: userIds },
      status: "COMPLETED",
      completedAt: { gte: new Date(now.getTime() - STREAK_LOOKBACK_DAYS * DAY_MS) },
    },
    select: { studentId: true, completedAt: true },
  });

  const daysByUser = new Map<string, Set<string>>();
  for (const a of attempts) {
    if (!a.completedAt) continue;
    const set = daysByUser.get(a.studentId) ?? new Set<string>();
    set.add(lagosDayKey(a.completedAt));
    daysByUser.set(a.studentId, set);
  }

  const messages = new Map<string, ReminderMessage>();
  for (const userId of userIds) {
    const streak = streakToRemind(daysByUser.get(userId) ?? [], dayKey);
    if (streak !== null) messages.set(userId, buildStreakReminder(streak));
  }
  return messages;
}

export async function runReminders(
  kind: ReminderKind,
  options: { now: Date; deadline: number },
): Promise<RunResult> {
  const dayKey = lagosDayKey(options.now);
  const result: RunResult = { processed: 0, notified: 0, sent: 0, done: false };

  while (Date.now() < options.deadline) {
    const candidates = await findCandidates(kind, dayKey);
    if (candidates.length === 0) {
      result.done = true;
      break;
    }

    const claimed = await claim(kind, dayKey, candidates);
    result.processed += claimed.length;
    if (claimed.length === 0) continue; // another call took this page

    const messages =
      kind === "morning"
        ? await morningMessages(claimed, dayKey)
        : await streakMessages(claimed, dayKey, options.now);
    if (messages.size === 0) continue;
    result.notified += messages.size;

    const subscriptions = await db.pushSubscription.findMany({
      where: { userId: { in: [...messages.keys()] } },
      select: { ...SUBSCRIPTION_SELECT, userId: true },
    });

    const outcomes = await mapWithConcurrency(subscriptions, SEND_CONCURRENCY, async (sub) => {
      const message = messages.get(sub.userId)!;
      const payload = buildPushPayload({
        ...message,
        tag: kind === "morning" ? pushTag.morning(dayKey) : pushTag.streak(dayKey),
      });
      const outcome = await sendPush(sub, payload);
      // Reminders are never retried the same day, so every failure is final.
      await applySubscriptionEffect(sub.id, subscriptionEffect(outcome, sub.failureCount, true));
      return outcome;
    });
    result.sent += outcomes.filter((o) => o === "sent").length;
  }

  return result;
}
```

- [ ] **Step 2: Implement the morning route `src/app/api/cron/push/morning/route.ts`**

```ts
import { NextResponse } from "next/server";
import { cronGuard, deadlineFrom } from "@/lib/cron-auth";
import { readPushConfig } from "@/lib/push-config";
import { runReminders } from "@/lib/push-reminder-runner";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

// POST /api/cron/push/morning — called every 5 minutes 07:00–07:55 Lagos by pg_cron
export async function POST(req: Request) {
  const startedAt = Date.now();
  const denied = cronGuard(req);
  if (denied) return denied;
  if (!readPushConfig()) {
    console.warn("cron: VAPID keys are not configured; skipping morning reminders");
    return new Response(null, { status: 204 });
  }

  try {
    const result = await runReminders("morning", { now: new Date(), deadline: deadlineFrom(startedAt) });
    return NextResponse.json(result);
  } catch (error) {
    console.error("Morning reminders failed:", error);
    return NextResponse.json({ error: "Morning reminders failed" }, { status: 500 });
  }
}
```

- [ ] **Step 3: Implement the streak route `src/app/api/cron/push/streak/route.ts`**

```ts
import { NextResponse } from "next/server";
import { cronGuard, deadlineFrom } from "@/lib/cron-auth";
import { readPushConfig } from "@/lib/push-config";
import { runReminders } from "@/lib/push-reminder-runner";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

// POST /api/cron/push/streak — called every 5 minutes 19:00–19:55 Lagos by pg_cron
export async function POST(req: Request) {
  const startedAt = Date.now();
  const denied = cronGuard(req);
  if (denied) return denied;
  if (!readPushConfig()) {
    console.warn("cron: VAPID keys are not configured; skipping streak reminders");
    return new Response(null, { status: 204 });
  }

  try {
    const result = await runReminders("streak", { now: new Date(), deadline: deadlineFrom(startedAt) });
    return NextResponse.json(result);
  } catch (error) {
    console.error("Streak reminders failed:", error);
    return NextResponse.json({ error: "Streak reminders failed" }, { status: 500 });
  }
}
```

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors. (Prisma sends a JS `string[]` parameter as a Postgres `text[]`, which `unnest` expands.)

- [ ] **Step 5: Local dry run (dev database, your own student account)**

With the migration applied (Task 17 can be done early against a dev project) and your account subscribed in Task 8:
```bash
curl -i -X POST http://localhost:3000/api/cron/push/morning -H "Authorization: Bearer $CRON_SECRET"
curl -i -X POST http://localhost:3000/api/cron/push/morning -H "Authorization: Bearer $CRON_SECRET"
curl -i -X POST http://localhost:3000/api/cron/push/morning
```
Expected: first call `200` with `processed ≥ 1` and a notification if you have plan items or due cards today; second call `200` with `processed: 0, done: true` (idempotent); third call `401`.

- [ ] **Step 6: Commit**

```bash
git add src/lib/push-reminder-runner.ts src/app/api/cron/push/morning src/app/api/cron/push/streak
git commit -m "Send morning and streak reminders from cron routes"
```

---

### Task 12: Audience filter

**Files:**
- Create: `src/lib/push-audience.ts`, `src/lib/push-audience-sql.ts`
- Test: `scripts/test-push-audience.mts`

**Interfaces:**
- Produces (`push-audience.ts`): `audienceFilterSchema`, `type AudienceFilter = { examTargets?: ("WAEC"|"JAMB"|"NECO")[]; classLevels?: ("SS1"|"SS2"|"SS3")[]; tracks?: ("SCIENCE"|"ARTS"|"COMMERCIAL")[]; tiers?: ("FREEMIUM"|"STANDARD"|"PREMIUM")[]; userIds?: string[] }`, `type AudienceClause = { field: "examTarget" | "classLevel" | "track" | "tier" | "userId"; values: string[] }`, `audienceClauses(filter: AudienceFilter): AudienceClause[]`, `type AudienceStudent = { id: string; role: string; isActive: boolean; classLevel: string | null; track: string | null; tier: string; activeExamTargets: string[] }`, `matchesAudience(student: AudienceStudent, filter: AudienceFilter): boolean`, `describeAudience(filter: AudienceFilter): string`, `parseStoredAudience(json: unknown): AudienceFilter | null`.
- Produces (`push-audience-sql.ts`): `audienceWhereSql(filter: AudienceFilter): Prisma.Sql`, a predicate over a `"User"` row aliased `u`.

`matchesAudience` and `audienceWhereSql` are both built from `audienceClauses`, one clause at a time, so they cannot drift apart silently: the test checks each clause's matching rule on shared fixtures and that the SQL has exactly one condition and the same values per clause.

- [ ] **Step 1: Write the failing test**

`scripts/test-push-audience.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  audienceClauses,
  audienceFilterSchema,
  describeAudience,
  matchesAudience,
  parseStoredAudience,
  type AudienceFilter,
  type AudienceStudent,
} from "../src/lib/push-audience";
import { audienceWhereSql } from "../src/lib/push-audience-sql";

const student = (patch: Partial<AudienceStudent> = {}): AudienceStudent => ({
  id: "u1",
  role: "STUDENT",
  isActive: true,
  classLevel: "SS3",
  track: "SCIENCE",
  tier: "STANDARD",
  activeExamTargets: ["JAMB"],
  ...patch,
});

const FIXTURES: { name: string; filter: AudienceFilter; matches: AudienceStudent[]; misses: AudienceStudent[] }[] = [
  { name: "empty = every active student", filter: {}, matches: [student(), student({ classLevel: null, track: null, activeExamTargets: [] })], misses: [student({ role: "TEACHER" }), student({ isActive: false })] },
  { name: "class level", filter: { classLevels: ["SS2", "SS3"] }, matches: [student()], misses: [student({ classLevel: "SS1" }), student({ classLevel: null })] },
  { name: "track", filter: { tracks: ["ARTS"] }, matches: [student({ track: "ARTS" })], misses: [student(), student({ track: null })] },
  { name: "tier", filter: { tiers: ["FREEMIUM"] }, matches: [student({ tier: "FREEMIUM" })], misses: [student()] },
  { name: "exam target via active plan", filter: { examTargets: ["JAMB"] }, matches: [student(), student({ activeExamTargets: ["WAEC", "JAMB"] })], misses: [student({ activeExamTargets: [] }), student({ activeExamTargets: ["WAEC"] })] },
  { name: "user ids", filter: { userIds: ["u1", "u9"] }, matches: [student()], misses: [student({ id: "u2" })] },
  { name: "fields combine with AND", filter: { classLevels: ["SS3"], tiers: ["PREMIUM"] }, matches: [student({ tier: "PREMIUM" })], misses: [student(), student({ classLevel: "SS2", tier: "PREMIUM" })] },
];

for (const fixture of FIXTURES) {
  test(`matchesAudience: ${fixture.name}`, () => {
    for (const s of fixture.matches) assert.equal(matchesAudience(s, fixture.filter), true, JSON.stringify(s));
    for (const s of fixture.misses) assert.equal(matchesAudience(s, fixture.filter), false, JSON.stringify(s));
  });
}

test("clauses skip empty lists and de-duplicate values", () => {
  assert.deepEqual(audienceClauses({ classLevels: [], tiers: ["PREMIUM", "PREMIUM"] }), [
    { field: "tier", values: ["PREMIUM"] },
  ]);
});

test("the SQL predicate has one parameterised condition per clause with the same values", () => {
  for (const fixture of FIXTURES) {
    const sql = audienceWhereSql(fixture.filter);
    const clauses = audienceClauses(fixture.filter);
    // Base conditions: role and isActive. Plus one IN (...) per clause.
    assert.equal((sql.sql.match(/ IN \(/g) ?? []).length, clauses.length, fixture.name);
    assert.deepEqual(
      sql.values,
      ["STUDENT", ...clauses.flatMap((c) => c.values)],
      fixture.name,
    );
    assert.ok(!/SS\d|JAMB|PREMIUM|u1/.test(sql.sql), "values must be parameters, never inlined");
  }
});

test("schema rejects unknown values and keys", () => {
  assert.equal(audienceFilterSchema.safeParse({ classLevels: ["SS4"] }).success, false);
  assert.equal(audienceFilterSchema.safeParse({ examTargets: ["CUSTOM"] }).success, false);
  assert.equal(audienceFilterSchema.safeParse({ schools: ["x"] }).success, false);
  assert.equal(audienceFilterSchema.safeParse({}).success, true);
});

test("stored JSON is re-validated", () => {
  assert.deepEqual(parseStoredAudience({ tiers: ["FREEMIUM"] }), { tiers: ["FREEMIUM"] });
  assert.equal(parseStoredAudience({ tiers: ["GOLD"] }), null);
  assert.equal(parseStoredAudience("nope"), null);
});

test("descriptions", () => {
  assert.equal(describeAudience({}), "All students");
  assert.equal(describeAudience({ classLevels: ["SS3"], examTargets: ["JAMB"] }), "JAMB plan · SS3");
  assert.equal(describeAudience({ userIds: ["a", "b"] }), "2 specific students");
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-push-audience.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/push-audience.ts`**

```ts
// Who an announcement is for. Pure. The SQL twin lives in
// push-audience-sql.ts and is built from the same clauses.

import { z } from "zod";

export const audienceFilterSchema = z
  .object({
    examTargets: z.array(z.enum(["WAEC", "JAMB", "NECO"])).max(3).optional(),
    classLevels: z.array(z.enum(["SS1", "SS2", "SS3"])).max(3).optional(),
    tracks: z.array(z.enum(["SCIENCE", "ARTS", "COMMERCIAL"])).max(3).optional(),
    tiers: z.array(z.enum(["FREEMIUM", "STANDARD", "PREMIUM"])).max(3).optional(),
    userIds: z.array(z.string().min(1).max(64)).max(1000).optional(),
  })
  .strict();

export type AudienceFilter = z.infer<typeof audienceFilterSchema>;

export type AudienceClause = {
  field: "examTarget" | "classLevel" | "track" | "tier" | "userId";
  values: string[];
};

/** Order is fixed; the SQL builder and its test rely on it. */
export function audienceClauses(filter: AudienceFilter): AudienceClause[] {
  const entries: [AudienceClause["field"], string[] | undefined][] = [
    ["examTarget", filter.examTargets],
    ["classLevel", filter.classLevels],
    ["track", filter.tracks],
    ["tier", filter.tiers],
    ["userId", filter.userIds],
  ];
  return entries
    .filter(([, values]) => values && values.length > 0)
    .map(([field, values]) => ({ field, values: [...new Set(values)] }));
}

export type AudienceStudent = {
  id: string;
  role: string;
  isActive: boolean;
  classLevel: string | null;
  track: string | null;
  tier: string;
  /** targetExam of each active StudyPlan. */
  activeExamTargets: string[];
};

function clauseMatches(student: AudienceStudent, clause: AudienceClause): boolean {
  switch (clause.field) {
    case "examTarget":
      return student.activeExamTargets.some((t) => clause.values.includes(t));
    case "classLevel":
      return student.classLevel !== null && clause.values.includes(student.classLevel);
    case "track":
      return student.track !== null && clause.values.includes(student.track);
    case "tier":
      return clause.values.includes(student.tier);
    case "userId":
      return clause.values.includes(student.id);
  }
}

export function matchesAudience(student: AudienceStudent, filter: AudienceFilter): boolean {
  if (student.role !== "STUDENT" || !student.isActive) return false;
  return audienceClauses(filter).every((clause) => clauseMatches(student, clause));
}

export function parseStoredAudience(json: unknown): AudienceFilter | null {
  const parsed = audienceFilterSchema.safeParse(json);
  return parsed.success ? parsed.data : null;
}

export function describeAudience(filter: AudienceFilter): string {
  const parts: string[] = [];
  if (filter.userIds?.length) {
    parts.push(filter.userIds.length === 1 ? "1 specific student" : `${filter.userIds.length} specific students`);
  }
  if (filter.examTargets?.length) parts.push(`${filter.examTargets.join("/")} plan`);
  if (filter.classLevels?.length) parts.push(filter.classLevels.join("/"));
  if (filter.tracks?.length) parts.push(filter.tracks.map((t) => t[0] + t.slice(1).toLowerCase()).join("/"));
  if (filter.tiers?.length) parts.push(filter.tiers.map((t) => t[0] + t.slice(1).toLowerCase()).join("/"));
  return parts.length ? parts.join(" · ") : "All students";
}
```

- [ ] **Step 4: Implement `src/lib/push-audience-sql.ts`**

```ts
import { Prisma } from "@prisma/client";
import { audienceClauses, type AudienceClause, type AudienceFilter } from "@/lib/push-audience";

function clauseSql(clause: AudienceClause): Prisma.Sql {
  const values = Prisma.join(clause.values);
  switch (clause.field) {
    case "examTarget":
      return Prisma.sql`EXISTS (SELECT 1 FROM "StudyPlan" sp WHERE sp."studentId" = u."id" AND sp."isActive" AND sp."targetExam"::text IN (${values}))`;
    case "classLevel":
      return Prisma.sql`u."classLevel"::text IN (${values})`;
    case "track":
      return Prisma.sql`u."track"::text IN (${values})`;
    case "tier":
      return Prisma.sql`u."tier"::text IN (${values})`;
    case "userId":
      return Prisma.sql`u."id" IN (${values})`;
  }
}

/** Mirrors matchesAudience(): role, active, then every clause with AND. */
export function audienceWhereSql(filter: AudienceFilter): Prisma.Sql {
  const conditions = [
    Prisma.sql`u."role"::text = ${"STUDENT"}`,
    Prisma.sql`u."isActive"`,
    ...audienceClauses(filter).map(clauseSql),
  ];
  return Prisma.join(conditions, " AND ");
}
```

- [ ] **Step 5: Run it to confirm it passes**

Run: `node --import tsx --test scripts/test-push-audience.mts`
Expected: PASS (12 tests).

- [ ] **Step 6: Register and commit**

Append ` scripts/test-push-audience.mts` to the `test` script.
```bash
git add src/lib/push-audience.ts src/lib/push-audience-sql.ts scripts/test-push-audience.mts package.json
git commit -m "Add announcement audience filter and its SQL predicate"
```

---

### Task 13: Announcement drain

**Files:**
- Create: `src/lib/announcement-drain.ts`
- Create: `src/app/api/cron/push/drain/route.ts`

**Interfaces:**
- Consumes: `buildPushPayload`, `pushTag` (Task 2); `nextDelivery`, `subscriptionEffect`, `mapWithConcurrency`, `SendOutcome` (Task 2); `sendPush`, `applySubscriptionEffect`, `SUBSCRIPTION_SELECT` (Task 3); `cronGuard`, `deadlineFrom` (Task 9); `readPushConfig` (Task 1).
- Produces: `drainAnnouncements(options: { deadline: number }): Promise<{ claimed: number; sent: number; failed: number; gone: number; retrying: number }>`; `POST /api/cron/push/drain` → that result, or `204` when not configured.

The state transitions were unit-tested in Task 2 (`nextDelivery`, `subscriptionEffect`); this task is claim/record I/O, verified by typecheck and by the multi-call drain check in Task 17.

- [ ] **Step 1: Implement `src/lib/announcement-drain.ts`**

```ts
import { db } from "@/lib/db";
import { buildPushPayload, pushTag } from "@/lib/push-payload";
import {
  mapWithConcurrency,
  nextDelivery,
  subscriptionEffect,
} from "@/lib/push-send-result";
import { SUBSCRIPTION_SELECT, applySubscriptionEffect, sendPush } from "@/lib/push-send";

const CLAIM_BATCH = 100;
const SEND_CONCURRENCY = 20;

type ClaimedRow = {
  id: string;
  announcementId: string;
  subscriptionId: string;
  attempts: number;
  title: string;
  body: string;
  url: string | null;
};

type DrainResult = { claimed: number; sent: number; failed: number; gone: number; retrying: number };

/**
 * Claims up to 100 pending deliveries. SKIP LOCKED means two overlapping
 * calls (cron + the admin route's after()) never claim the same row. A claim
 * older than 5 minutes belonged to a call that died, or is a retry waiting
 * its turn, and may be claimed again.
 */
async function claimBatch(): Promise<ClaimedRow[]> {
  return db.$queryRaw<ClaimedRow[]>`
    WITH picked AS (
      SELECT d."id"
      FROM "AnnouncementDelivery" d
      JOIN "Announcement" a ON a."id" = d."announcementId"
      WHERE d."status" = 'PENDING'
        AND a."status" IN ('QUEUED', 'SENDING')
        AND (d."claimedAt" IS NULL OR d."claimedAt" < now() - interval '5 minutes')
      ORDER BY d."id"
      LIMIT ${CLAIM_BATCH}
      FOR UPDATE OF d SKIP LOCKED
    )
    UPDATE "AnnouncementDelivery" d
    SET "claimedAt" = now()
    FROM picked, "Announcement" a
    WHERE d."id" = picked."id" AND a."id" = d."announcementId"
    RETURNING d."id", d."announcementId", d."subscriptionId", d."attempts", a."title", a."body", a."url"`;
}

async function finalize(announcementIds: string[]): Promise<void> {
  for (const id of announcementIds) {
    const pending = await db.announcementDelivery.count({
      where: { announcementId: id, status: "PENDING" },
    });
    if (pending > 0) continue;
    const counts = await db.announcementDelivery.groupBy({
      by: ["status"],
      where: { announcementId: id },
      _count: { _all: true },
    });
    const count = (s: string) => counts.find((c) => c.status === s)?._count._all ?? 0;
    // Guarded on status so a cancel that raced this call keeps CANCELLED.
    await db.announcement.updateMany({
      where: { id, status: { in: ["QUEUED", "SENDING"] } },
      data: {
        status: "SENT",
        completedAt: new Date(),
        sentCount: count("SENT"),
        failedCount: count("FAILED") + count("GONE"),
      },
    });
  }
}

export async function drainAnnouncements(options: { deadline: number }): Promise<DrainResult> {
  const result: DrainResult = { claimed: 0, sent: 0, failed: 0, gone: 0, retrying: 0 };
  const touched = new Set<string>();

  while (Date.now() < options.deadline) {
    const batch = await claimBatch();
    if (batch.length === 0) break;
    result.claimed += batch.length;

    const announcementIds = [...new Set(batch.map((row) => row.announcementId))];
    announcementIds.forEach((id) => touched.add(id));
    await db.announcement.updateMany({
      where: { id: { in: announcementIds }, status: "QUEUED" },
      data: { status: "SENDING" },
    });

    const subscriptions = await db.pushSubscription.findMany({
      where: { id: { in: batch.map((row) => row.subscriptionId) } },
      select: SUBSCRIPTION_SELECT,
    });
    const byId = new Map(subscriptions.map((s) => [s.id, s]));

    await mapWithConcurrency(batch, SEND_CONCURRENCY, async (row) => {
      const sub = byId.get(row.subscriptionId);
      if (!sub) {
        // Deleted since queueing (sign-out, 410 from another send).
        await db.announcementDelivery.update({
          where: { id: row.id },
          data: { status: "GONE", attempts: row.attempts + 1, error: "subscription removed" },
        });
        result.gone += 1;
        return;
      }

      const payload = buildPushPayload({
        title: row.title,
        body: row.body,
        url: row.url,
        tag: pushTag.announcement(row.announcementId),
      });
      const outcome = await sendPush(sub, payload);
      const next = nextDelivery(outcome, row.attempts);

      // A retry keeps its claimedAt, so it waits out the 5-minute stale
      // window instead of burning all three attempts within seconds.
      await db.announcementDelivery.update({
        where: { id: row.id },
        data: {
          status: next.status,
          attempts: next.attempts,
          error: outcome === "sent" ? null : outcome,
        },
      });
      await applySubscriptionEffect(
        sub.id,
        subscriptionEffect(outcome, sub.failureCount, next.status === "FAILED"),
      );

      if (next.status === "SENT") result.sent += 1;
      else if (next.status === "GONE") result.gone += 1;
      else if (next.status === "FAILED") result.failed += 1;
      else result.retrying += 1;
    });
  }

  await finalize([...touched]);
  return result;
}
```

- [ ] **Step 2: Implement `src/app/api/cron/push/drain/route.ts`**

```ts
import { NextResponse } from "next/server";
import { cronGuard, deadlineFrom } from "@/lib/cron-auth";
import { readPushConfig } from "@/lib/push-config";
import { drainAnnouncements } from "@/lib/announcement-drain";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

// POST /api/cron/push/drain — called every minute by pg_cron
export async function POST(req: Request) {
  const startedAt = Date.now();
  const denied = cronGuard(req);
  if (denied) return denied;
  if (!readPushConfig()) {
    console.warn("cron: VAPID keys are not configured; skipping announcement drain");
    return new Response(null, { status: 204 });
  }

  try {
    return NextResponse.json(await drainAnnouncements({ deadline: deadlineFrom(startedAt) }));
  } catch (error) {
    console.error("Announcement drain failed:", error);
    return NextResponse.json({ error: "Announcement drain failed" }, { status: 500 });
  }
}
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/lib/announcement-drain.ts src/app/api/cron/push/drain
git commit -m "Drain queued announcement deliveries from a cron route"
```

Recorded against the spec: a transient failure keeps its claim for the 5-minute stale window rather than releasing it immediately, so the three attempts are spread over roughly ten minutes.

A finalization gap to know about: an announcement whose last pending rows are all parked retries is finalized by the call that eventually settles them, because that call touches the announcement. No announcement can stay `SENDING` forever: every `PENDING` row is either claimable or becomes claimable within 5 minutes, and at 3 attempts it becomes `FAILED`.

---

### Task 14: Announcement data and admin API (queue, preview, test send, cancel)

**Files:**
- Create: `src/lib/announcement.ts` (pure), `src/lib/announcement-data.ts`
- Create: `src/app/admin/api/announcements/route.ts` (GET list, POST queue)
- Create: `src/app/admin/api/announcements/preview/route.ts`, `src/app/admin/api/announcements/test/route.ts`, `src/app/admin/api/announcements/[id]/cancel/route.ts`
- Test: `scripts/test-announcement.mts`

**Interfaces:**
- Consumes: `audienceFilterSchema`, `AudienceFilter`, `describeAudience` (Task 12); `audienceWhereSql` (Task 12); `isInternalPath`, `buildPushPayload`, `pushTag`, `TITLE_MAX`, `BODY_MAX` (Task 2); `sendPush`, `applySubscriptionEffect`, `SUBSCRIPTION_SELECT` (Task 3); `subscriptionEffect`, `mapWithConcurrency` (Task 2); `requireAdminApi` (`src/lib/admin-session.ts`); `recordAudit` (Task 1 actions).
- Produces (`announcement.ts`): `announcementInputSchema` (`{ title; body; url: string | null; audience: AudienceFilter; expiresInDays: number }`), `type AnnouncementInput`, `testSendSchema` (`{ contact: string; title; body; url: string | null }`), `CONFIRM_TYPED_THRESHOLD = 500`, `needsTypedConfirm(devices: number): boolean`, `expiresAtFrom(now: Date, days: number): Date`.
- Produces (`announcement-data.ts`): `previewAudience(filter): Promise<{ students: number; subscribedStudents: number; devices: number }>`, `queueAnnouncement(input: AnnouncementInput, adminId: string, now?: Date): Promise<{ id: string; recipientCount: number }>`, `cancelAnnouncement(id: string): Promise<boolean>`, `listAnnouncements(): Promise<AnnouncementRow[]>`, `type AnnouncementRow`, `findStudentByContact(contact: string): Promise<{ id: string; firstName: string; lastName: string } | null>`, `sendTestPush(userId: string, message: { title; body; url: string | null }): Promise<{ devices: number; sent: number }>`.
- Produces HTTP: `GET /admin/api/announcements` → `AnnouncementRow[]`; `POST /admin/api/announcements` → `{ id, recipientCount }` (201) and starts a drain in `after()` using `drainAnnouncements` (Task 13); `POST /admin/api/announcements/preview` body `{ audience }` → preview counts; `POST /admin/api/announcements/test` → `{ devices, sent, student }`; `POST /admin/api/announcements/[id]/cancel` → `{ ok: true }` or 409.

- [ ] **Step 1: Write the failing test**

`scripts/test-announcement.mts`:
```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CONFIRM_TYPED_THRESHOLD,
  announcementInputSchema,
  expiresAtFrom,
  needsTypedConfirm,
  testSendSchema,
} from "../src/lib/announcement";

const valid = { title: "New mock exam", body: "JAMB mock 3 is live.", url: "/practice", audience: {}, expiresInDays: 7 };

test("a valid announcement parses, trimming text", () => {
  const parsed = announcementInputSchema.parse({ ...valid, title: "  New mock exam  " });
  assert.equal(parsed.title, "New mock exam");
  assert.equal(parsed.url, "/practice");
});

test("limits: title 60, body 180, expiry 1-30 days (default 7)", () => {
  assert.equal(announcementInputSchema.safeParse({ ...valid, title: "t".repeat(61) }).success, false);
  assert.equal(announcementInputSchema.safeParse({ ...valid, body: "b".repeat(181) }).success, false);
  assert.equal(announcementInputSchema.safeParse({ ...valid, title: "   " }).success, false);
  assert.equal(announcementInputSchema.safeParse({ ...valid, expiresInDays: 31 }).success, false);
  assert.equal(announcementInputSchema.safeParse({ ...valid, expiresInDays: 0 }).success, false);
  const { expiresInDays: _omit, ...noExpiry } = valid;
  assert.equal(announcementInputSchema.parse(noExpiry).expiresInDays, 7);
});

test("links must be internal paths; blank becomes null", () => {
  assert.equal(announcementInputSchema.safeParse({ ...valid, url: "https://evil.com" }).success, false);
  assert.equal(announcementInputSchema.safeParse({ ...valid, url: "//evil.com" }).success, false);
  assert.equal(announcementInputSchema.parse({ ...valid, url: "" }).url, null);
  assert.equal(announcementInputSchema.parse({ ...valid, url: null }).url, null);
});

test("audience is validated", () => {
  assert.equal(announcementInputSchema.safeParse({ ...valid, audience: { tiers: ["GOLD"] } }).success, false);
});

test("test send needs a contact", () => {
  assert.equal(testSendSchema.safeParse({ ...valid, contact: "" }).success, false);
  assert.equal(testSendSchema.parse({ ...valid, contact: " me@example.com " }).contact, "me@example.com");
});

test("typed confirmation from 500 devices", () => {
  assert.equal(CONFIRM_TYPED_THRESHOLD, 500);
  assert.equal(needsTypedConfirm(499), false);
  assert.equal(needsTypedConfirm(500), true);
});

test("expiry date", () => {
  assert.equal(
    expiresAtFrom(new Date("2026-09-14T10:00:00Z"), 7).toISOString(),
    "2026-09-21T10:00:00.000Z",
  );
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --import tsx --test scripts/test-announcement.mts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `src/lib/announcement.ts`**

```ts
import { z } from "zod";
import { audienceFilterSchema } from "@/lib/push-audience";
import { BODY_MAX, TITLE_MAX, isInternalPath } from "@/lib/push-payload";

const urlField = z
  .string()
  .trim()
  .nullable()
  .optional()
  .transform((value) => (value ? value : null))
  .refine((value) => value === null || isInternalPath(value), "Link must be a page on ScholarsCrib, e.g. /practice");

const messageFields = {
  title: z.string().trim().min(1).max(TITLE_MAX),
  body: z.string().trim().min(1).max(BODY_MAX),
  url: urlField,
};

export const announcementInputSchema = z.object({
  ...messageFields,
  audience: audienceFilterSchema,
  expiresInDays: z.number().int().min(1).max(30).default(7),
});

export type AnnouncementInput = z.infer<typeof announcementInputSchema>;

export const testSendSchema = z.object({
  ...messageFields,
  contact: z.string().trim().min(1).max(200),
});

export const CONFIRM_TYPED_THRESHOLD = 500;

export function needsTypedConfirm(devices: number): boolean {
  return devices >= CONFIRM_TYPED_THRESHOLD;
}

export function expiresAtFrom(now: Date, days: number): Date {
  return new Date(now.getTime() + days * 24 * 60 * 60 * 1000);
}
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `node --import tsx --test scripts/test-announcement.mts`
Expected: PASS (7 tests).

- [ ] **Step 5: Implement `src/lib/announcement-data.ts`**

```ts
import { db } from "@/lib/db";
import { audienceWhereSql } from "@/lib/push-audience-sql";
import type { AudienceFilter } from "@/lib/push-audience";
import { expiresAtFrom, type AnnouncementInput } from "@/lib/announcement";
import { buildPushPayload, pushTag } from "@/lib/push-payload";
import { mapWithConcurrency, subscriptionEffect } from "@/lib/push-send-result";
import { SUBSCRIPTION_SELECT, applySubscriptionEffect, sendPush } from "@/lib/push-send";

export async function previewAudience(
  filter: AudienceFilter,
): Promise<{ students: number; subscribedStudents: number; devices: number }> {
  const where = audienceWhereSql(filter);
  const [row] = await db.$queryRaw<{ students: number; subscribed: number; devices: number }[]>`
    SELECT
      count(*)::int AS students,
      count(*) FILTER (WHERE s.n > 0)::int AS subscribed,
      coalesce(sum(s.n), 0)::int AS devices
    FROM "User" u
    LEFT JOIN "NotificationPreference" p ON p."userId" = u."id"
    LEFT JOIN LATERAL (
      SELECT count(*)::int AS n FROM "PushSubscription" ps
      WHERE ps."userId" = u."id" AND (p."userId" IS NULL OR p."announcements")
    ) s ON true
    WHERE ${where}`;
  return { students: row.students, subscribedStudents: row.subscribed, devices: row.devices };
}

/**
 * Creates the announcement and its delivery rows in one transaction. The
 * deliveries are one INSERT … SELECT, so recipients never load into Node.
 */
export async function queueAnnouncement(
  input: AnnouncementInput,
  adminId: string,
  now: Date = new Date(),
): Promise<{ id: string; recipientCount: number }> {
  const where = audienceWhereSql(input.audience);
  return db.$transaction(async (tx) => {
    const announcement = await tx.announcement.create({
      data: {
        title: input.title,
        body: input.body,
        url: input.url,
        audience: input.audience,
        expiresAt: expiresAtFrom(now, input.expiresInDays),
        createdById: adminId,
      },
      select: { id: true },
    });

    const recipientCount = await tx.$executeRaw`
      INSERT INTO "AnnouncementDelivery" ("id", "announcementId", "subscriptionId", "status", "attempts")
      SELECT gen_random_uuid()::text, ${announcement.id}, s."id", 'PENDING', 0
      FROM "PushSubscription" s
      JOIN "User" u ON u."id" = s."userId"
      LEFT JOIN "NotificationPreference" p ON p."userId" = u."id"
      WHERE ${where} AND (p."userId" IS NULL OR p."announcements")`;

    // Nothing to send: done immediately, but the banner still shows.
    await tx.announcement.update({
      where: { id: announcement.id },
      data:
        recipientCount === 0
          ? { recipientCount, status: "SENT", completedAt: now }
          : { recipientCount },
    });
    return { id: announcement.id, recipientCount };
  });
}

export async function cancelAnnouncement(id: string): Promise<boolean> {
  return db.$transaction(async (tx) => {
    const updated = await tx.announcement.updateMany({
      where: { id, status: { in: ["QUEUED", "SENDING"] } },
      data: { status: "CANCELLED", completedAt: new Date() },
    });
    if (updated.count === 0) return false;
    await tx.announcementDelivery.updateMany({
      where: { announcementId: id, status: "PENDING" },
      data: { status: "CANCELLED" },
    });
    const counts = await tx.announcementDelivery.groupBy({
      by: ["status"],
      where: { announcementId: id },
      _count: { _all: true },
    });
    const count = (s: string) => counts.find((c) => c.status === s)?._count._all ?? 0;
    await tx.announcement.update({
      where: { id },
      data: { sentCount: count("SENT"), failedCount: count("FAILED") + count("GONE") },
    });
    return true;
  });
}

export type AnnouncementRow = {
  id: string;
  title: string;
  body: string;
  url: string | null;
  audience: unknown;
  status: "QUEUED" | "SENDING" | "SENT" | "CANCELLED";
  recipientCount: number;
  sentCount: number;
  failedCount: number;
  pendingCount: number;
  createdAt: string;
  /** Formatted on the server: formatting in the client renders differently during SSR and hydration. */
  createdAtLabel: string;
  expiresAt: string;
  createdBy: string;
};

const LAGOS_DATE_TIME = new Intl.DateTimeFormat("en-NG", {
  timeZone: "Africa/Lagos",
  dateStyle: "medium",
  timeStyle: "short",
});

export async function listAnnouncements(): Promise<AnnouncementRow[]> {
  const rows = await db.announcement.findMany({
    orderBy: { createdAt: "desc" },
    take: 50,
    include: {
      createdBy: { select: { email: true, username: true } },
      _count: { select: { deliveries: { where: { status: "PENDING" } } } },
    },
  });
  return rows.map((r) => ({
    id: r.id,
    title: r.title,
    body: r.body,
    url: r.url,
    audience: r.audience,
    status: r.status,
    recipientCount: r.recipientCount,
    sentCount: r.sentCount,
    failedCount: r.failedCount,
    pendingCount: r._count.deliveries,
    createdAt: r.createdAt.toISOString(),
    createdAtLabel: LAGOS_DATE_TIME.format(r.createdAt),
    expiresAt: r.expiresAt.toISOString(),
    createdBy: r.createdBy.username ?? r.createdBy.email ?? "admin",
  }));
}

export async function findStudentByContact(
  contact: string,
): Promise<{ id: string; firstName: string; lastName: string } | null> {
  const value = contact.trim();
  if (!value) return null;
  return db.user.findFirst({
    where: {
      role: "STUDENT",
      OR: value.includes("@") ? [{ email: value.toLowerCase() }] : [{ phone: value }],
    },
    select: { id: true, firstName: true, lastName: true },
  });
}

/** Immediate, not queued, no Announcement row. Ignores the announcements toggle on purpose. */
export async function sendTestPush(
  userId: string,
  message: { title: string; body: string; url: string | null },
): Promise<{ devices: number; sent: number }> {
  const subscriptions = await db.pushSubscription.findMany({
    where: { userId },
    select: SUBSCRIPTION_SELECT,
  });
  const payload = buildPushPayload({ ...message, tag: pushTag.announcement(`test-${Date.now()}`) });
  const outcomes = await mapWithConcurrency(subscriptions, 5, async (sub) => {
    const outcome = await sendPush(sub, payload);
    await applySubscriptionEffect(sub.id, subscriptionEffect(outcome, sub.failureCount, false));
    return outcome;
  });
  return { devices: subscriptions.length, sent: outcomes.filter((o) => o === "sent").length };
}
```

- [ ] **Step 6: Implement `src/app/admin/api/announcements/route.ts`**

```ts
import { NextRequest, NextResponse, after } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { announcementInputSchema } from "@/lib/announcement";
import { describeAudience } from "@/lib/push-audience";
import { listAnnouncements, queueAnnouncement } from "@/lib/announcement-data";
import { drainAnnouncements } from "@/lib/announcement-drain";
import { deadlineFrom } from "@/lib/cron-auth";
import { readPushConfig } from "@/lib/push-config";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

// GET /admin/api/announcements — the 50 most recent
export async function GET() {
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;
  try {
    return NextResponse.json(await listAnnouncements());
  } catch (error) {
    console.error("Listing announcements failed:", error);
    return NextResponse.json({ error: "Failed to list announcements" }, { status: 500 });
  }
}

// POST /admin/api/announcements — queue for push and show the banner
export async function POST(req: NextRequest) {
  const startedAt = Date.now();
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;

  const parsed = announcementInputSchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  try {
    const queued = await queueAnnouncement(parsed.data, guard.actor.id);

    await recordAudit({
      actorId: guard.actor.id,
      action: "announcement.send",
      entity: "Announcement",
      entityId: queued.id,
      summary: `Sent "${parsed.data.title}" to ${describeAudience(parsed.data.audience)} (${queued.recipientCount} devices)`,
    });

    // Start sending now instead of waiting up to a minute for pg_cron. after()
    // runs within this route's maxDuration; the cron drain finishes the rest.
    if (queued.recipientCount > 0 && readPushConfig()) {
      after(async () => {
        try {
          await drainAnnouncements({ deadline: deadlineFrom(startedAt) });
        } catch (error) {
          console.error("Immediate announcement drain failed:", error);
        }
      });
    }

    return NextResponse.json(queued, { status: 201 });
  } catch (error) {
    console.error("Queueing announcement failed:", error);
    return NextResponse.json({ error: "Failed to send announcement" }, { status: 500 });
  }
}
```

- [ ] **Step 7: Implement `src/app/admin/api/announcements/preview/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { requireAdminApi } from "@/lib/admin-session";
import { audienceFilterSchema } from "@/lib/push-audience";
import { previewAudience } from "@/lib/announcement-data";

export const dynamic = "force-dynamic";

const bodySchema = z.object({ audience: audienceFilterSchema });

// POST /admin/api/announcements/preview — how many students and devices an audience reaches
export async function POST(req: NextRequest) {
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;

  const parsed = bodySchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid audience" }, { status: 400 });
  }
  try {
    return NextResponse.json(await previewAudience(parsed.data.audience));
  } catch (error) {
    console.error("Audience preview failed:", error);
    return NextResponse.json({ error: "Failed to count the audience" }, { status: 500 });
  }
}
```

- [ ] **Step 8: Implement `src/app/admin/api/announcements/test/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { testSendSchema } from "@/lib/announcement";
import { findStudentByContact, sendTestPush } from "@/lib/announcement-data";
import { readPushConfig } from "@/lib/push-config";

export const dynamic = "force-dynamic";

// POST /admin/api/announcements/test — send to one student's devices right now
export async function POST(req: NextRequest) {
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;

  if (!readPushConfig()) {
    return NextResponse.json({ error: "Push is not configured" }, { status: 503 });
  }

  const parsed = testSendSchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  try {
    const student = await findStudentByContact(parsed.data.contact);
    if (!student) {
      return NextResponse.json({ error: "No student with that email or phone" }, { status: 404 });
    }
    const result = await sendTestPush(student.id, parsed.data);

    await recordAudit({
      actorId: guard.actor.id,
      action: "announcement.test",
      entity: "User",
      entityId: student.id,
      summary: `Test announcement "${parsed.data.title}" to ${student.firstName} ${student.lastName} (${result.sent}/${result.devices} devices)`,
    });

    return NextResponse.json({ ...result, student: `${student.firstName} ${student.lastName}` });
  } catch (error) {
    console.error("Test announcement failed:", error);
    return NextResponse.json({ error: "Failed to send test" }, { status: 500 });
  }
}
```

- [ ] **Step 9: Implement `src/app/admin/api/announcements/[id]/cancel/route.ts`**

```ts
import { NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { cancelAnnouncement } from "@/lib/announcement-data";

export const dynamic = "force-dynamic";

// POST /admin/api/announcements/[id]/cancel — stop sending and hide the banner
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = await requireAdminApi();
  if (!guard.ok) return guard.response;

  const { id } = await params;
  try {
    const cancelled = await cancelAnnouncement(id);
    if (!cancelled) {
      return NextResponse.json(
        { error: "Only a queued or sending announcement can be cancelled" },
        { status: 409 },
      );
    }
    await recordAudit({
      actorId: guard.actor.id,
      action: "announcement.cancel",
      entity: "Announcement",
      entityId: id,
      summary: "Cancelled an announcement",
    });
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Cancelling announcement failed:", error);
    return NextResponse.json({ error: "Failed to cancel" }, { status: 500 });
  }
}
```

A `SENT` announcement cannot be cancelled here, but its banner can still be hidden early: out of scope for this release (the spec only cancels `QUEUED`/`SENDING`).

- [ ] **Step 10: Verify**

Run: `npx tsc --noEmit` and `node --import tsx --test scripts/test-announcement.mts`
Expected: no errors; PASS.

- [ ] **Step 11: Register and commit**

Append ` scripts/test-announcement.mts` to the `test` script.
```bash
git add src/lib/announcement.ts src/lib/announcement-data.ts src/app/admin/api/announcements scripts/test-announcement.mts package.json
git commit -m "Queue, preview, test-send and cancel announcements"
```

---

### Task 15: Admin announcements page

**Files:**
- Create: `src/app/admin/(console)/announcements/page.tsx`
- Create: `src/components/admin/announcement-composer.tsx`, `src/components/admin/announcement-list.tsx`
- Modify: `src/lib/admin-nav.ts` (add the nav entry)
- Test: `scripts/test-admin-nav.mts` (existing; must still pass)

**Interfaces:**
- Consumes: `listAnnouncements`, `AnnouncementRow` (Task 14); `describeAudience`, `parseStoredAudience`, `AudienceFilter` (Task 12); `needsTypedConfirm`, `CONFIRM_TYPED_THRESHOLD` (Task 14); `TITLE_MAX`, `BODY_MAX` (Task 2); `readPushConfig` (Task 1); `requireAdminPage`; `PageHeader`, `ConfirmDialog`, `StatusBanner`, `Button`; HTTP routes from Task 14.
- Produces: `<AnnouncementComposer pushConfigured: boolean />`, `<AnnouncementList rows: AnnouncementRow[] />`, nav item `{ name: "Announcements", href: "/admin/announcements" }`.

UI work: verified by typecheck, lint, the nav test and the manual checks in Step 6.

- [ ] **Step 1: Add the nav entry**

In `src/lib/admin-nav.ts` add `LuMegaphone` to the `react-icons/lu` import and change the `People` group to:
```ts
  {
    label: "People",
    items: [
      { name: "Students", href: "/admin/students", icon: LuGraduationCap },
      { name: "Announcements", href: "/admin/announcements", icon: LuMegaphone },
      { name: "Team", href: "/admin/team", icon: LuUsers, ownerOnly: true },
    ],
  },
```
Run: `node --import tsx --test scripts/test-admin-nav.mts`
Expected: PASS (the test checks structure and unique hrefs, not a fixed count).

- [ ] **Step 2: Implement `src/app/admin/(console)/announcements/page.tsx`**

```tsx
import { requireAdminPage } from "@/lib/admin-session";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBanner } from "@/components/admin/status-banner";
import { listAnnouncements } from "@/lib/announcement-data";
import { readPushConfig } from "@/lib/push-config";
import { AnnouncementComposer } from "@/components/admin/announcement-composer";
import { AnnouncementList } from "@/components/admin/announcement-list";

export const dynamic = "force-dynamic";

export default async function AdminAnnouncementsPage() {
  // The layout's check does not re-run on client-side navigation.
  await requireAdminPage();

  const pushConfigured = readPushConfig() !== null;
  const rows = await listAnnouncements();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Announcements"
        description="Send a notification to students' devices. Everyone in the audience also sees it as a banner on their dashboard."
      />
      {!pushConfigured && (
        <StatusBanner
          tone="info"
          title="Push is not configured"
          message="Set the VAPID keys and CRON_SECRET (docs/push-notifications-ops.md). Announcements will still show as dashboard banners."
        />
      )}
      <AnnouncementComposer pushConfigured={pushConfigured} />
      <AnnouncementList rows={rows} />
    </div>
  );
}
```

- [ ] **Step 3: Implement `src/components/admin/announcement-composer.tsx`**

```tsx
"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/admin/confirm-dialog";
import { StatusBanner } from "@/components/admin/status-banner";
import { BODY_MAX, TITLE_MAX } from "@/lib/push-payload";
import { describeAudience, type AudienceFilter } from "@/lib/push-audience";
import { CONFIRM_TYPED_THRESHOLD, needsTypedConfirm } from "@/lib/announcement";

const INPUT_CLS =
  "w-full min-w-0 px-3 py-2 rounded-lg border border-border bg-card text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60";

const OPTIONS = {
  examTargets: ["WAEC", "JAMB", "NECO"],
  classLevels: ["SS1", "SS2", "SS3"],
  tracks: ["SCIENCE", "ARTS", "COMMERCIAL"],
  tiers: ["FREEMIUM", "STANDARD", "PREMIUM"],
} as const;

const LABELS: Record<keyof typeof OPTIONS, string> = {
  examTargets: "Exam (students with a study plan for…)",
  classLevels: "Class",
  tracks: "Track",
  tiers: "Plan",
};

type Preview = { students: number; subscribedStudents: number; devices: number };
type Group = keyof typeof OPTIONS;

export function AnnouncementComposer({ pushConfigured }: { pushConfigured: boolean }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [url, setUrl] = useState("");
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [selected, setSelected] = useState<Record<Group, string[]>>({
    examTargets: [],
    classLevels: [],
    tracks: [],
    tiers: [],
  });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [contact, setContact] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState<"send" | "test" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const audience = useMemo<AudienceFilter>(() => {
    const filter: Record<string, string[]> = {};
    for (const group of Object.keys(selected) as Group[]) {
      if (selected[group].length) filter[group] = selected[group];
    }
    return filter as AudienceFilter;
  }, [selected]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const res = await fetch("/admin/api/announcements/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ audience }),
          signal: controller.signal,
        });
        if (res.ok) setPreview(await res.json());
      } catch {
        // Aborted or offline: keep the last count.
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [audience]);

  function toggle(group: Group, value: string) {
    setSelected((prev) => ({
      ...prev,
      [group]: prev[group].includes(value)
        ? prev[group].filter((v) => v !== value)
        : [...prev[group], value],
    }));
  }

  const message = { title: title.trim(), body: body.trim(), url: url.trim() || null };
  const canSubmit = message.title.length > 0 && message.body.length > 0;
  const typedRequired = needsTypedConfirm(preview?.devices ?? 0);

  async function sendTest() {
    setBusy("test");
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch("/admin/api/announcements/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...message, contact }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not send the test");
        return;
      }
      setSuccess(
        data.devices === 0
          ? `${data.student} has no devices with notifications turned on.`
          : `Test sent to ${data.student}: ${data.sent} of ${data.devices} devices accepted it.`,
      );
    } catch {
      setError("Could not reach the server");
    } finally {
      setBusy(null);
    }
  }

  async function send() {
    setBusy("send");
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch("/admin/api/announcements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...message, audience, expiresInDays }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not send the announcement");
        return;
      }
      setConfirmOpen(false);
      setTyped("");
      setTitle("");
      setBody("");
      setUrl("");
      setSuccess(`Queued for ${data.recipientCount} devices. Sending has started.`);
      router.refresh();
    } catch {
      setError("Could not reach the server");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-lg border border-border-strong bg-card p-4 sm:p-5">
      <h2 className="text-base font-bold text-foreground">New announcement</h2>
      {error && <StatusBanner tone="error" title={error} className="mt-4" />}
      {success && <StatusBanner tone="success" title={success} className="mt-4" />}

      <div className="mt-4 grid gap-6 lg:grid-cols-[1fr_18rem]">
        <div className="space-y-4 min-w-0">
          <label className="block text-sm">
            <span className="font-semibold">Title</span>
            <input className={INPUT_CLS} value={title} maxLength={TITLE_MAX} onChange={(e) => setTitle(e.target.value)} />
            <span className="text-xs text-muted">{title.length}/{TITLE_MAX}</span>
          </label>
          <label className="block text-sm">
            <span className="font-semibold">Message</span>
            <textarea className={INPUT_CLS} rows={3} value={body} maxLength={BODY_MAX} onChange={(e) => setBody(e.target.value)} />
            <span className="text-xs text-muted">{body.length}/{BODY_MAX}</span>
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="font-semibold">Link (optional)</span>
              <input className={INPUT_CLS} placeholder="/practice" value={url} onChange={(e) => setUrl(e.target.value)} />
            </label>
            <label className="block text-sm">
              <span className="font-semibold">Banner shows for (days)</span>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={30}
                value={expiresInDays}
                onChange={(e) => setExpiresInDays(Math.min(30, Math.max(1, Number(e.target.value) || 7)))}
              />
            </label>
          </div>

          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold">Audience (blank = all students)</legend>
            {(Object.keys(OPTIONS) as Group[]).map((group) => (
              <div key={group}>
                <p className="text-xs text-muted">{LABELS[group]}</p>
                <div className="mt-1 flex flex-wrap gap-2">
                  {OPTIONS[group].map((value) => (
                    <label key={value} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-sm">
                      <input type="checkbox" checked={selected[group].includes(value)} onChange={() => toggle(group, value)} />
                      {value[0] + value.slice(1).toLowerCase()}
                    </label>
                  ))}
                </div>
              </div>
            ))}
            <p className="text-sm text-foreground">
              {preview
                ? `${describeAudience(audience)}: reaches ${preview.devices} devices (${preview.subscribedStudents} students). ${preview.students - preview.subscribedStudents} more students will see only the banner.`
                : "Counting…"}
            </p>
          </fieldset>
        </div>

        <aside className="space-y-4 min-w-0">
          <div aria-label="Notification preview" className="rounded-2xl border border-border bg-secondary p-3">
            <p className="text-xs text-muted">ScholarsCrib · now</p>
            <p className="mt-1 text-sm font-semibold text-foreground break-words">{title || "Title"}</p>
            <p className="text-sm text-foreground break-words">{body || "Your message"}</p>
          </div>

          <div className="space-y-2">
            <label className="block text-sm">
              <span className="font-semibold">Send a test to a student account</span>
              <input className={INPUT_CLS} placeholder="email or phone" value={contact} onChange={(e) => setContact(e.target.value)} />
            </label>
            <Button
              variant="outline"
              size="sm"
              disabled={!pushConfigured || !canSubmit || !contact.trim() || busy !== null}
              onClick={sendTest}
            >
              {busy === "test" ? "Sending test…" : "Send test"}
            </Button>
          </div>

          <Button className="w-full" disabled={!canSubmit || busy !== null} onClick={() => setConfirmOpen(true)}>
            Send announcement
          </Button>
        </aside>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Send this announcement?"
        description={`"${message.title}" goes to ${preview?.devices ?? 0} devices now and shows on dashboards for ${expiresInDays} days. This can be cancelled while sending, but notifications already delivered stay delivered.`}
        confirmLabel="Send"
        busy={busy === "send"}
        disabled={typedRequired && typed !== "SEND"}
        onConfirm={send}
        onCancel={() => {
          setConfirmOpen(false);
          setTyped("");
        }}
      >
        {typedRequired && (
          <label className="block text-sm">
            <span>
              This reaches {CONFIRM_TYPED_THRESHOLD}+ devices. Type <strong>SEND</strong> to confirm.
            </span>
            <input className={INPUT_CLS} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </label>
        )}
      </ConfirmDialog>
    </section>
  );
}
```

- [ ] **Step 4: Implement `src/components/admin/announcement-list.tsx`**

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/admin/confirm-dialog";
import { StatusBanner } from "@/components/admin/status-banner";
import { describeAudience, parseStoredAudience } from "@/lib/push-audience";
import type { AnnouncementRow } from "@/lib/announcement-data";

const STATUS_LABEL: Record<AnnouncementRow["status"], string> = {
  QUEUED: "Queued",
  SENDING: "Sending",
  SENT: "Sent",
  CANCELLED: "Cancelled",
};

export function AnnouncementList({ rows }: { rows: AnnouncementRow[] }) {
  const router = useRouter();
  const [cancelling, setCancelling] = useState<AnnouncementRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function cancel() {
    if (!cancelling) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/admin/api/announcements/${cancelling.id}/cancel`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error ?? "Could not cancel");
        return;
      }
      setCancelling(null);
      router.refresh();
    } catch {
      setError("Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  if (rows.length === 0) {
    return <p className="text-sm text-muted">No announcements yet.</p>;
  }

  return (
    <section>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-bold text-foreground">Recent</h2>
        <Button variant="ghost" size="sm" onClick={() => router.refresh()}>
          Refresh
        </Button>
      </div>
      {error && <StatusBanner tone="error" title={error} className="mt-3" />}
      <div className="mt-3 overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[40rem] text-sm">
          <thead className="bg-secondary text-left text-xs text-muted">
            <tr>
              <th className="px-3 py-2">Announcement</th>
              <th className="px-3 py-2">Audience</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Delivered</th>
              <th className="px-3 py-2">Sent by</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const audience = parseStoredAudience(row.audience);
              return (
                <tr key={row.id} className="border-t border-border align-top">
                  <td className="px-3 py-2">
                    <p className="font-semibold">{row.title}</p>
                    <p className="text-xs text-muted">{row.createdAtLabel}</p>
                  </td>
                  <td className="px-3 py-2">{audience ? describeAudience(audience) : "—"}</td>
                  <td className="px-3 py-2">{STATUS_LABEL[row.status]}</td>
                  <td className="px-3 py-2">
                    {row.status === "SENDING" || row.status === "QUEUED"
                      ? `${row.recipientCount - row.pendingCount} / ${row.recipientCount}`
                      : `${row.sentCount} sent · ${row.failedCount} failed · ${row.recipientCount} devices`}
                  </td>
                  <td className="px-3 py-2">{row.createdBy}</td>
                  <td className="px-3 py-2 text-right">
                    {(row.status === "QUEUED" || row.status === "SENDING") && (
                      <Button variant="outline" size="sm" onClick={() => setCancelling(row)}>
                        Cancel
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={cancelling !== null}
        title="Cancel this announcement?"
        description="Devices that have not received it yet won't, and the dashboard banner disappears. Notifications already delivered stay delivered."
        confirmLabel="Cancel announcement"
        busy={busy}
        onConfirm={cancel}
        onCancel={() => setCancelling(null)}
      />
    </section>
  );
}
```

- [ ] **Step 5: Verify**

Run: `npx tsc --noEmit`, `npm run lint`, `node --import tsx --test scripts/test-admin-nav.mts`
Expected: no errors; PASS.

- [ ] **Step 6: Manual check**

`npm run dev`, sign in to `/admin`: "Announcements" appears in the nav. Selecting audience options updates the count within a second. **Send test** to your own student account delivers a notification to your subscribed browser (Task 8), and a row appears in the audit log as `announcement.test`. **Send announcement** opens the dialog, and the confirm button stays disabled at ≥ 500 devices until `SEND` is typed.

- [ ] **Step 7: Commit**

```bash
git add "src/app/admin/(console)/announcements" src/components/admin/announcement-composer.tsx src/components/admin/announcement-list.tsx src/lib/admin-nav.ts scripts/test-admin-nav.mts
git commit -m "Add the admin announcements page"
```

---

### Task 16: Dashboard announcement banner

**Files:**
- Modify: `src/lib/announcement-data.ts` (add `getBannerAnnouncement`, `dismissAnnouncement`)
- Create: `src/components/announcements/announcement-banner.tsx` (server), `src/components/announcements/dismiss-announcement-button.tsx` (client)
- Create: `src/app/api/announcements/[id]/dismiss/route.ts`
- Modify: `src/app/(dashboard)/layout.tsx`

**Interfaces:**
- Consumes: `matchesAudience`, `parseStoredAudience`, `AudienceStudent` (Task 12); `isInternalPath` (Task 2).
- Produces: `getBannerAnnouncement(userId: string, now?: Date): Promise<{ id: string; title: string; body: string; url: string | null } | null>`, `dismissAnnouncement(userId: string, announcementId: string): Promise<boolean>`, `<AnnouncementBanner userId />`, `POST /api/announcements/[id]/dismiss` → `{ ok: true }` or 404.

The matching rule is tested in Task 12; this task is a loader and markup, verified by typecheck and Step 6.

- [ ] **Step 1: Add the loaders to `src/lib/announcement-data.ts`**

Add to the imports: `import { matchesAudience, parseStoredAudience } from "@/lib/push-audience";` (merge with the existing `push-audience` type import). Append:
```ts
/** How many recent live announcements to check against one student. */
const BANNER_CANDIDATES = 20;

export async function getBannerAnnouncement(
  userId: string,
  now: Date = new Date(),
): Promise<{ id: string; title: string; body: string; url: string | null } | null> {
  const [user, candidates] = await Promise.all([
    db.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        role: true,
        isActive: true,
        classLevel: true,
        track: true,
        tier: true,
        studyPlans: { where: { isActive: true }, select: { targetExam: true } },
      },
    }),
    db.announcement.findMany({
      where: {
        status: { not: "CANCELLED" },
        expiresAt: { gt: now },
        dismissals: { none: { userId } },
      },
      orderBy: { createdAt: "desc" },
      take: BANNER_CANDIDATES,
      select: { id: true, title: true, body: true, url: true, audience: true },
    }),
  ]);
  if (!user) return null;

  const student = {
    id: user.id,
    role: user.role,
    isActive: user.isActive,
    classLevel: user.classLevel,
    track: user.track,
    tier: user.tier,
    activeExamTargets: user.studyPlans.flatMap((p) => (p.targetExam ? [p.targetExam] : [])),
  };

  for (const candidate of candidates) {
    const audience = parseStoredAudience(candidate.audience);
    if (audience && matchesAudience(student, audience)) {
      return { id: candidate.id, title: candidate.title, body: candidate.body, url: candidate.url };
    }
  }
  return null;
}

export async function dismissAnnouncement(userId: string, announcementId: string): Promise<boolean> {
  const exists = await db.announcement.count({ where: { id: announcementId } });
  if (exists === 0) return false;
  await db.announcementDismissal.upsert({
    where: { announcementId_userId: { announcementId, userId } },
    create: { announcementId, userId },
    update: {},
  });
  return true;
}
```

- [ ] **Step 2: Implement `src/app/api/announcements/[id]/dismiss/route.ts`**

```ts
import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { dismissAnnouncement } from "@/lib/announcement-data";

export const dynamic = "force-dynamic";

// POST /api/announcements/[id]/dismiss — hide the banner on every device
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { id } = await params;
  try {
    const ok = await dismissAnnouncement(session.user.id, id);
    if (!ok) return NextResponse.json({ error: "Not found" }, { status: 404 });
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Dismissing announcement failed:", error);
    return NextResponse.json({ error: "Something went wrong" }, { status: 500 });
  }
}
```

- [ ] **Step 3: Implement `src/components/announcements/dismiss-announcement-button.tsx`**

```tsx
"use client";

import { useState, type ReactNode } from "react";
import { LuX } from "react-icons/lu";

/** Wraps the banner so dismissing hides it immediately, before the request lands. */
export function DismissibleAnnouncement({ id, children }: { id: string; children: ReactNode }) {
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;

  function dismiss() {
    setHidden(true);
    // Fire and forget: if it fails, the banner simply returns on the next load.
    fetch(`/api/announcements/${id}/dismiss`, { method: "POST" }).catch(() => undefined);
  }

  return (
    <div role="status" className="card mb-6 flex items-start gap-3 p-4">
      <div className="min-w-0 flex-1">{children}</div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss announcement"
        className="shrink-0 rounded-lg p-1 text-muted hover:bg-secondary hover:text-foreground"
      >
        <LuX aria-hidden className="h-4 w-4" />
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Implement `src/components/announcements/announcement-banner.tsx`**

```tsx
import Link from "next/link";
import { getBannerAnnouncement } from "@/lib/announcement-data";
import { isInternalPath } from "@/lib/push-payload";
import { buttonClass } from "@/components/ui/button";
import { DismissibleAnnouncement } from "./dismiss-announcement-button";

export async function AnnouncementBanner({ userId }: { userId: string }) {
  let announcement;
  try {
    announcement = await getBannerAnnouncement(userId);
  } catch (error) {
    // A banner is never worth a broken page (e.g. before the migration).
    console.error("Loading announcement banner failed:", error);
    return null;
  }
  if (!announcement) return null;

  return (
    <DismissibleAnnouncement id={announcement.id}>
      <p className="text-sm font-bold text-foreground break-words">{announcement.title}</p>
      <p className="mt-0.5 text-sm text-muted break-words">{announcement.body}</p>
      {isInternalPath(announcement.url) && (
        <Link href={announcement.url} className={buttonClass("outline", "sm", "mt-3")}>
          Open
        </Link>
      )}
    </DismissibleAnnouncement>
  );
}
```

- [ ] **Step 5: Render it in `src/app/(dashboard)/layout.tsx`**

Add imports:
```tsx
import { Suspense } from "react";
import { AnnouncementBanner } from "@/components/announcements/announcement-banner";
```
Inside `<div className="max-w-6xl mx-auto …">`, before `{children}`:
```tsx
          {/* Streams in: a slow pooler connection must not hold up the page. */}
          <Suspense fallback={null}>
            <AnnouncementBanner userId={session.user.id} />
          </Suspense>
```
`session.user.id` is typed `string | null | undefined`, so change the existing guard `if (!session?.user) redirect("/login");` to `if (!session?.user?.id) redirect("/login");`.

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit` and `npm run lint`
Expected: no errors.

Manual: send an announcement to all students from `/admin/announcements`; the student dashboard shows the banner on every page under the dashboard layout; **×** hides it, and it stays hidden after reload and on a second browser signed in as the same student; a student outside the audience (e.g. a different class level) never sees it; after cancelling, it disappears for everyone.

- [ ] **Step 7: Commit**

```bash
git add src/lib/announcement-data.ts src/components/announcements "src/app/api/announcements" "src/app/(dashboard)/layout.tsx"
git commit -m "Show announcements as a dismissible dashboard banner"
```

---

### Task 17: Ops documentation, database rollout and end-to-end verification

**Files:**
- Create: `docs/push-notifications-ops.md`

**Interfaces:**
- Consumes: everything above. Produces the production configuration: Vercel env vars, the applied migration, Vault secrets, the `call_push_cron` function and three `pg_cron` jobs.

This task changes shared infrastructure (production database, Vercel env, scheduled jobs). **Stop and get the user's go-ahead before Steps 3, 4 and 5**, and have the user (or the SQL Editor session they own) run the SQL.

- [ ] **Step 1: Write `docs/push-notifications-ops.md`**

````markdown
# Push notifications — operations

Design: docs/superpowers/specs/2026-09-14-push-notifications-design.md

## Environment (Vercel → Settings → Environment Variables, and .env.local)

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | from `npx web-push generate-vapid-keys` |
| `VAPID_PRIVATE_KEY` | from the same command |
| `VAPID_SUBJECT` | `mailto:hello@scholarscrib.com` |
| `CRON_SECRET` | 32+ random characters (`node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`) |

Generate VAPID keys **once**. Rotating them invalidates every student's
subscription; only rotate after a leak, and expect every student to opt in
again. `NEXT_PUBLIC_VAPID_PUBLIC_KEY` is inlined at build time: redeploy after
changing it.

Any missing variable turns the feature off: no opt-in UI, cron routes answer
204, the admin page says push is not configured.

## Database migration

`prisma migrate` cannot reach Supabase from the dev machine. Apply
`prisma/migrations/20260915000001_push_notifications/migration.sql` in the
Supabase SQL Editor inside `BEGIN; … COMMIT;`, followed by its
`_prisma_migrations` row:

```sql
INSERT INTO "_prisma_migrations"
  (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
VALUES
  (gen_random_uuid()::text, '<sha256 of migration.sql bytes>', now(),
   '20260915000001_push_notifications', NULL, NULL, now(), 1);
```

Checksum: `sha256sum prisma/migrations/20260915000001_push_notifications/migration.sql`
(the file must be LF-only: `tr -cd '\r' < … | wc -c` prints 0).

The SQL Editor can report success on a half-applied batch. Verify:

```sql
select table_name from information_schema.tables
where table_schema = 'public' and table_name in
  ('PushSubscription','NotificationPreference','Announcement',
   'AnnouncementDelivery','AnnouncementDismissal','ReminderLog')
order by 1;  -- 6 rows

select typname from pg_type where typname in ('AnnouncementStatus','DeliveryStatus');  -- 2 rows

select migration_name, finished_at from "_prisma_migrations"
where migration_name = '20260915000001_push_notifications';  -- 1 row
```

## Scheduler (pg_cron + pg_net)

Deploy the code with the env vars first, so the routes exist before anything
calls them.

```sql
create extension if not exists pg_cron;
create extension if not exists pg_net;

select vault.create_secret('<CRON_SECRET>', 'push_cron_secret');
select vault.create_secret('https://<production-host>', 'push_base_url');

create or replace function public.call_push_cron(path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  base text := (select decrypted_secret from vault.decrypted_secrets where name = 'push_base_url');
  secret text := (select decrypted_secret from vault.decrypted_secrets where name = 'push_cron_secret');
begin
  perform net.http_post(
    url := base || path,
    headers := jsonb_build_object('Authorization', 'Bearer ' || secret),
    timeout_milliseconds := 55000
  );
end;
$$;

revoke all on function public.call_push_cron(text) from public, anon, authenticated;

-- Lagos is UTC+1 with no DST: 06:xx UTC = 07:xx Lagos, 18:xx UTC = 19:xx Lagos.
select cron.schedule('push-morning', '*/5 6 * * *', $$select public.call_push_cron('/api/cron/push/morning')$$);
select cron.schedule('push-streak',  '*/5 18 * * *', $$select public.call_push_cron('/api/cron/push/streak')$$);
select cron.schedule('push-drain',   '* * * * *',   $$select public.call_push_cron('/api/cron/push/drain')$$);
```

Verify:

```sql
select jobname, schedule, active from cron.job where jobname like 'push-%';  -- 3 rows, active

-- after a few minutes:
select j.jobname, d.status, d.start_time
from cron.job_run_details d join cron.job j on j.jobid = d.jobid
where j.jobname like 'push-%' order by d.start_time desc limit 10;

select status_code, left(content, 200), created
from net._http_response order by created desc limit 10;  -- 200s, never 401
```

A 401 means the Vault secret and Vercel's `CRON_SECRET` differ. A 204 means
the deployment is missing env vars.

To pause: `select cron.unschedule('push-drain');` (and the others).
To rotate the cron secret: update Vercel, redeploy, then
`select vault.update_secret((select id from vault.secrets where name = 'push_cron_secret'), '<new>');`.

## Housekeeping

`AnnouncementDelivery` and `ReminderLog` grow daily. Clearing old rows is
safe at any time:

```sql
delete from "ReminderLog" where "sentAt" < now() - interval '30 days';
delete from "AnnouncementDelivery" d using "Announcement" a
where a.id = d."announcementId" and a."completedAt" < now() - interval '30 days';
```
````

- [ ] **Step 2: Full automated check**

Run: `npm test`, `npm run typecheck:tests`, `npx tsc --noEmit`, `npm run lint`, `npm run build`
Expected: all pass. Paste the summary lines (test counts, build success) into the PR description.

- [ ] **Step 3 (needs the user's go-ahead): Apply the migration**

Follow "Database migration" in the ops doc. Paste the three verification query results into the PR.

- [ ] **Step 4 (needs the user's go-ahead): Configure Vercel and deploy**

Set the four env vars for Production (and Preview if previews should send), deploy, then from a terminal:
```bash
curl -i -X POST https://<production-host>/api/cron/push/drain
curl -i -X POST https://<production-host>/api/cron/push/drain -H "Authorization: Bearer <CRON_SECRET>"
```
Expected: `401`, then `200` with `{"claimed":0,…}`.

- [ ] **Step 5 (needs the user's go-ahead): Install the scheduler**

Follow "Scheduler" in the ops doc and paste the verification results into the PR.

- [ ] **Step 6: Manual end-to-end checklist**

Record pass/fail for each in the PR:

- [ ] Chrome desktop: opt in from Settings; admin test send arrives; clicking opens the link in a new tab (an open quiz tab is not navigated).
- [ ] Android Chrome (installed PWA): opt in from the study plan card; test send arrives with the app icon.
- [ ] iPhone Safari tab: Settings shows the Add to Home Screen instructions; after installing (iOS ≥ 16.4), opt-in works and a test send arrives.
- [ ] DevTools → Application → Service Workers → Push with `{"title":"T","body":"B","url":"https://evil.com","tag":"x"}`: the notification shows; clicking opens `/dashboard`.
- [ ] Worker upgrade: with a tab open on the old v1 worker, **Turn on** shows the close-all-tabs message; after closing all tabs and reopening, it works.
- [ ] Shared device: student A opts in and signs out; student B signs in on the same browser; an announcement to A only does not arrive; `PushSubscription` has no row for A on that endpoint.
- [ ] Force sign-out of a student from the admin console deletes their subscriptions.
- [ ] Morning run: a subscribed test student with a pending plan item today gets one digest at ~07:00 Lagos, and `ReminderLog` has exactly one `morning` row for them; the 07:05 run does not notify again.
- [ ] Streak run: a test student with attempts on the two previous days and none today gets one reminder at ~19:00 Lagos; practising first means no reminder.
- [ ] Broadcast drain (staging or dev database only): seed 150 `PushSubscription` rows for one test student with endpoints `https://fcm.googleapis.com/fcm/send/fake-1` … `fake-150` (random valid-length `p256dh`/`auth` strings), plus your real browser subscription. Queue an announcement with `userIds: [that student]`. Deliveries drain across at least two claim batches; your browser receives it; fake endpoints end `GONE` or `FAILED` (their subscriptions are deleted on 404/410); the announcement ends `SENT` with `sentCount + failedCount = recipientCount`. Delete leftover fake rows afterwards.
- [ ] Cancel mid-drain: remaining deliveries become `CANCELLED`, the banner disappears, the list shows Cancelled.
- [ ] Banner: shows for the audience only, dismisses across devices, respects expiry.
- [ ] Notification preferences: turning off Announcements stops pushes but the banner still shows; turning off Streak reminders stops only those.

- [ ] **Step 7: Commit**

```bash
git add docs/push-notifications-ops.md
git commit -m "Document push notification operations and rollout"
```

---

## Spec Coverage

| Spec section | Tasks |
|---|---|
| Data model (§1) | 1 |
| Capability, opt-in placement, subscribe flow, lifecycle (§2) | 5, 6, 7, 8 |
| Service worker (§2) | 4 |
| Morning digest, streak reminder, 60s strategy (§3) | 10, 11 |
| Admin console, queueing, test send, confirm, cancel (§4) | 13, 14, 15 |
| Draining and result mapping (§4) | 2, 3, 13 |
| Dashboard banner (§4) | 16 |
| Payload (§4) | 2 |
| Configuration, pg_cron, security (§5) | 1, 9, 17 |
| Failure handling (§5) | 2, 3, 11, 13 |
| Testing and manual checklist (§5) | every task; 17 |

Recorded deviations from the spec (each explained in its task):
1. Notification clicks open a new window unless a tab already shows the target (Task 4), so an exam in progress is never navigated away.
2. The opt-in card shows whenever a student has a plan and has not opted in or snoozed (Task 8), rather than hooking the plan-save flow that the term-mode branch is rewriting.
3. A transient delivery failure keeps its claim for the 5-minute stale window instead of being released immediately (Task 13), spacing out the three attempts.

