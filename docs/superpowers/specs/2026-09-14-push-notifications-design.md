# Push notifications (self-hosted Web Push) — design

Date: 2026-09-14
Status: approved in brainstorming, pending spec review

## Problem

Students only hear from ScholarsCrib when they open it. There is no way to
remind a student that today's study-plan topics or due flashcards are waiting,
that their streak is about to break, or to tell students about something
important (a new feature, a mock exam, downtime).

## Scope

In scope:

1. Opt-in Web Push on the existing PWA (`public/sw.js`), per device.
2. Two automatic reminders: a morning digest (study plan + due flashcards) and
   an evening streak-at-risk reminder.
3. Admin broadcast announcements to a filtered audience, delivered by push and
   shown as a dismissible dashboard banner for everyone in the audience.
4. Settings → Notifications: per-type toggles and a per-device master switch.

Out of scope (deferred): exam-countdown reminders, student-chosen reminder
hours, a notification inbox, click/open analytics, a draft→approve step for
announcements, scheduled (future-dated) announcements, native apps, email
fallback, OneSignal or any third-party push service.

## Decisions

| Question | Decision |
|---|---|
| Provider | Self-hosted Web Push with VAPID keys and the `web-push` package; no third-party SDK in the browser |
| Hosting constraint | Vercel **Hobby**: crons are daily-only and functions are capped at 60s |
| Scheduler | Supabase `pg_cron` + `pg_net` POSTs to secret-protected Vercel routes |
| First-release reminders | Study plan + flashcards (one morning digest), streak at risk (evening) |
| Timing | Fixed windows (07:00 and 19:00 Africa/Lagos), per-type on/off toggles |
| In-app fallback for announcements | One dismissible dashboard banner (newest applicable) |
| Who may send announcements | Any active admin (`canAccessConsole`), always audited |
| Where students are asked | After saving a study plan, and in Settings. Never on page load |

Rejected:

- **OneSignal.** Conflicts with the hand-tuned service worker, adds a
  third-party script for a metered-data audience, needs our data synced out to
  target reminders, and moves minors' data to another processor.
- **Vercel Hobby crons.** Daily only and imprecise within the hour; cannot
  drain a broadcast queue.
- **Sending a broadcast inside the admin request.** Thousands of sends cannot
  fit in 60s and cannot resume after a timeout.
- **GitHub Actions schedules.** Routinely delayed 10–30 minutes.

## 1. Data model

Six new models and two enums. Migration applied through the Supabase SQL
Editor, then verified against the catalog (the editor can report success on a
half-applied batch). Write migration files with LF line endings.

```prisma
/// One row per browser/device a student opted in on.
model PushSubscription {
  id            String    @id @default(cuid())
  userId        String
  user          User      @relation(fields: [userId], references: [id], onDelete: Cascade)
  /// Unique: re-subscribing the same browser upserts, and a shared phone
  /// that changes hands moves the row to the new user.
  endpoint      String    @unique
  p256dh        String
  auth          String
  userAgent     String?
  createdAt     DateTime  @default(now())
  lastSuccessAt DateTime?
  /// Consecutive non-410 failures. The subscription is deleted at 5.
  failureCount  Int       @default(0)

  @@index([userId])
}

/// Per-student toggles. A missing row means every toggle is on; rows are
/// created the first time the student changes a toggle.
model NotificationPreference {
  userId          String   @id
  user            User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  /// Morning digest (study plan + flashcards).
  studyReminders  Boolean  @default(true)
  streakReminders Boolean  @default(true)
  /// Push only. The dashboard banner shows regardless.
  announcements   Boolean  @default(true)
  updatedAt       DateTime @updatedAt
}

model Announcement {
  id             String             @id @default(cuid())
  /// ≤ 60 characters.
  title          String
  /// ≤ 180 characters.
  body           String
  /// Internal path only ("/classroom/biology"); never absolute, never "//".
  url            String?
  /// AudienceFilter, validated with zod.
  audience       Json
  status         AnnouncementStatus @default(QUEUED)
  /// The banner stops showing after this. Defaults to createdAt + 7 days.
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
  /// Deliberately not a foreign key: a subscription can be deleted mid-send.
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
  /// The push service said the subscription no longer exists (404/410).
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
}

/// Makes reminder runs idempotent: a retried or overlapping cron call cannot
/// send the same reminder twice.
model ReminderLog {
  userId String
  user   User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  /// "morning" | "streak"
  kind   String
  /// lagosDayKey() from src/lib/streak.ts.
  dayKey String
  sentAt DateTime @default(now())

  @@id([userId, kind, dayKey])
}
```

`User` gains the back-relations `pushSubscriptions`, `notificationPreference`,
`announcementDismissals` and `reminderLogs`; `Admin` gains `announcements`.
`AdminAudit.action` gains `announcement.send`, `announcement.cancel` and
`announcement.test`.

**`AudienceFilter`** (`src/lib/push-audience.ts`). Every field is optional;
fields combine with AND, values within a field with OR; `{}` means all students.

```ts
type AudienceFilter = {
  examTargets?: ExamType[];
  classLevels?: ClassLevel[];
  tracks?: Track[];
  tiers?: SubscriptionTier[];
  userIds?: string[];
};
```

`examTargets` matches the `targetExam` of the student's **active `StudyPlan`**.
It is the only stored exam choice: `src/lib/exam-target.ts` derives a
countdown from class level and stores nothing. A student with no active plan,
or a plan with no `targetExam`, does not match an `examTargets` filter. The
admin form says so next to the field ("students with a study plan for…").

## 2. Opt-in and subscription lifecycle

### Capability

`pushCapability(env)` in `src/lib/push-capability.ts` is pure and unit tested.
It takes a snapshot of the browser (has `serviceWorker`, has `PushManager`,
`Notification.permission`, iOS, running standalone, has a subscription) and
returns one of:

| State | UI |
|---|---|
| `unsupported` | Nothing |
| `ios-needs-install` | "Add ScholarsCrib to your Home Screen to get reminders" with steps |
| `denied` | "Notifications are blocked. Turn them on in your browser settings." No prompt |
| `default` | Opt-in button |
| `subscribed` | Toggles shown as on |

### Where students are asked

Never on page load. The browser permission dialog only appears after the
student taps **Turn on**.

1. **After a study plan is saved:** a soft card, "Get a morning reminder of
   today's topics?", with **Turn on** / **Not now**. "Not now" hides the card
   for 14 days (localStorage, wrapped in try/catch).
2. **Settings → Notifications:** a per-device master switch, plus the three
   toggles stored in `NotificationPreference` (`PATCH
   /api/user/notification-preferences`). The toggles apply to all of the
   student's devices.

### Subscribe flow

1. Await `navigator.serviceWorker.ready`, then post a `GET_VERSION` message to
   the active worker. `SHELL_VERSION` is bumped to `v2` with this feature. If
   the active worker is still `v1`, show "Close all ScholarsCrib tabs and
   reopen to turn on reminders" instead of subscribing a worker that has no
   push handler.
2. `registration.pushManager.subscribe({ userVisibleOnly: true,
   applicationServerKey })` with `NEXT_PUBLIC_VAPID_PUBLIC_KEY`.
3. `POST /api/push/subscription` with the subscription JSON. The route
   requires a session, is rate limited, validates with zod and **upserts by
   endpoint**, reassigning `userId` if the endpoint belonged to someone else.

### Keeping subscriptions correct

- **On app load** with `subscribed`, re-POST the current subscription (a cheap
  upsert), because browsers rotate endpoints.
- **`pushsubscriptionchange`** in `sw.js` re-subscribes with the same key and
  POSTs the new endpoint.
- **Sign-out:** `DELETE /api/push/subscription` for this endpoint, then
  `subscription.unsubscribe()`, so the next student on a shared device does not
  receive the previous student's reminders.
- **Force sign-out** (admin) deletes all of that user's subscriptions. Account
  deletion is covered by `onDelete: Cascade`.
- **Master switch off** unsubscribes and deletes this device only.

### Service worker (`public/sw.js`)

- `push`: parse `{ title, body, url, tag }`, call `showNotification` with
  `/icon-192.png`, and use `tag` so repeats collapse.
- `notificationclick`: close the notification. If `url` is a same-origin path,
  focus an existing ScholarsCrib client and navigate it there, otherwise
  `clients.openWindow(url)`. Anything else opens `/dashboard`.
- `pushsubscriptionchange`: as above.
- `message` `GET_VERSION`: reply with `SHELL_VERSION`.
- Still no `skipWaiting()` / `clients.claim()`. The new handlers take over when
  all tabs close, which subscribe step 1 detects.

## 3. Automatic reminders

`src/lib/push-reminders.ts` holds the pure selection and content functions;
`src/lib/push-reminders-data.ts` holds the bulk database loaders. This follows
the `admin-access.ts` / `admin-session.ts` split.

### Morning digest, `POST /api/cron/push/morning`

- **Eligible:** `isActive` students with ≥ 1 `PushSubscription`,
  `studyReminders` on (or no preference row), and no `ReminderLog(morning,
  today)`.
- **Inputs per student:** `StudyPlanItem` rows on the active plan where
  `scheduledDate` is today (Lagos) and `status = PENDING`; a count of
  `FlashcardReview` rows where `dueAt` ≤ end of today (Lagos).
- **`buildMorningDigest({ planItems, dueCards })`:**

  | Plan items | Due cards | Notification | URL |
  |---|---|---|---|
  | ≥ 1 | ≥ 1 | "Today: 3 topics · 60 min, and 12 flashcards due" | `/study-plan` |
  | ≥ 1 | 0 | "Today's plan: Photosynthesis + 2 more (60 min)" | `/study-plan` |
  | 0 | ≥ 1 | "12 flashcards are due for review" | `/flashcards` |
  | 0 | 0 | none | — |

- **Tag:** `morning-<dayKey>`.

### Streak reminder, `POST /api/cron/push/streak`

- **Eligible:** `isActive`, has a subscription, `streakReminders` on, a streak
  of **≥ 2 days ending yesterday**, and **no completed attempt today**. The
  streak is computed with `lagosDayKey` and `currentStreak` from
  `src/lib/streak.ts`, over the same completed-attempt days that
  `src/lib/achievements.ts` uses, so the number matches the badge.
- **Content:** "Keep your 6-day streak: one quick practice before midnight",
  linking to `/practice`.
- **Tag:** `streak-<dayKey>`.

### Staying within 60 seconds

- Each call has a **40s budget** measured from request start. It works through
  students in pages of 200 and stops when the budget is spent.
- `pg_cron` calls the morning route every 5 minutes from 07:00 to 07:55 Lagos
  and the streak route from 19:00 to 19:55 Lagos. Students already in
  `ReminderLog` are skipped, so each call continues where the last stopped, and
  a call with nothing left returns in milliseconds.
- **Per batch:** `createMany` the `ReminderLog` rows with `skipDuplicates`,
  re-read which rows this call actually inserted, and send only to those users
  (all their subscriptions, concurrency 20). If a call dies mid-batch, at most
  that batch misses the reminder for the day. A miss is preferred to a
  duplicate.
- Sends go through the same result mapping as broadcasts (§4): 404/410 deletes
  the subscription; other failures increment `failureCount` and are not retried
  (tomorrow is the retry).

Nothing is ever sent outside 07:00–08:00 and 19:00–20:00 Lagos, so there are no
quiet-hours settings. A student gets at most one morning and one streak
reminder per day, plus any announcements.

## 4. Admin broadcasts

### Console, `/admin/announcements`

Any admin who passes `canAccessConsole`. Added to the admin nav.

- **List:** title, audience summary, status, sent / failed / recipients,
  creator, created time.
- **New announcement form:**
  - Title (≤ 60), body (≤ 180), optional link (must match `^/(?!/)`), banner
    expiry (default 7 days, max 30).
  - Audience multi-selects: exam target, class level, track, tier. Blank means
    all students.
  - A live phone-style notification preview.
  - A live recipient count from `POST /admin/api/announcements/preview`:
    "Reaches 1,240 devices (912 students). 3,400 more students in this audience
    will see only the banner."
- **Send test:** a student email or phone (normalised with
  `normalizeIdentifier`). Sends immediately to that student's devices only, is
  not queued, creates no `Announcement`, and is audited as
  `announcement.test`. Admin accounts are separate from students and have no
  devices, so admins test with their own student account.
- **Confirm:** an in-page modal (never `window.confirm`) showing the recipient
  count. At 500 devices or more the admin must type `SEND`.
- **Cancel:** allowed while `QUEUED` or `SENDING`. Sets the announcement
  `CANCELLED`, sets remaining `PENDING` deliveries `CANCELLED`, and hides the
  banner. Audited as `announcement.cancel`.

### Queueing, `POST /admin/api/announcements`

1. Validate with zod and create the `Announcement` (`QUEUED`).
2. Insert deliveries with **one `INSERT … SELECT`**: `PushSubscription` joined
   to `User` rows that match the audience and are `isActive`, left-joined to
   `NotificationPreference` where `announcements` is true or the row is
   missing. Recipients are never loaded into Node.
3. Set `recipientCount`, write the `announcement.send` audit entry, and respond.
4. Call the drain once inside `after()` from `next/server` so sending starts
   immediately. `after()` runs within the route's max duration (see
   `node_modules/next/dist/docs/01-app/03-api-reference/04-functions/after.md`),
   and the per-minute cron continues whatever it does not finish.

### Draining, `POST /api/cron/push/drain`

`pg_cron` calls it every minute; each call has a 40s budget.

1. **Claim** up to 100 rows in one statement:
   `UPDATE "AnnouncementDelivery" SET "claimedAt" = now() WHERE id IN (SELECT id
   … WHERE status = 'PENDING' AND ("claimedAt" IS NULL OR "claimedAt" < now() -
   interval '5 minutes') FOR UPDATE SKIP LOCKED LIMIT 100) RETURNING …`.
   Overlapping calls never claim the same row. The announcement moves `QUEUED →
   SENDING` on its first claim.
2. **Send** with concurrency 20. Subscriptions that no longer exist are marked
   `GONE` without sending.
3. **Map each result** with the pure `classifySendResult(statusCode | error)`
   (`src/lib/push-send-result.ts`):

   | Result | Delivery | Subscription |
   |---|---|---|
   | 201 | `SENT` | `lastSuccessAt = now`, `failureCount = 0` |
   | 404, 410 | `GONE` | deleted |
   | 400, 413 | `FAILED` (logged) | unchanged |
   | 429, 5xx, network error | `attempts += 1`, claim released; `FAILED` at 3 attempts | `failureCount += 1` on final failure; deleted at 5 |

4. **Complete:** when an announcement has no `PENDING` rows, write `sentCount`
   and `failedCount` (GONE counts as failed), set `SENT` and `completedAt`.

### Dashboard banner

- The dashboard layout (a server component) loads non-cancelled, unexpired
  announcements the student has not dismissed, newest first, and shows the
  first one where `matchesAudience(student, audience)` is true.
- `matchesAudience` is pure and mirrors the SQL predicate in the queueing step.
  Both are exercised against the same fixtures.
- Shows title, body and an optional **Open** link. **×** calls `POST
  /api/announcements/[id]/dismiss`, which upserts `AnnouncementDismissal`.
- Shown regardless of the student's push settings.

### Payload

`buildPushPayload({ title, body, url, tag })` (`src/lib/push-payload.ts`)
enforces the length limits, same-origin paths, and the tag formats
`morning-<dayKey>`, `streak-<dayKey>` and `announcement-<id>`. The payload is
well under the 4KB Web Push limit and contains no personal data: no names, no
scores.

## 5. Configuration, security, failure handling, testing

### Configuration

| Variable | Where | Purpose |
|---|---|---|
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | Vercel + `.env.local` | Browser subscribe key |
| `VAPID_PRIVATE_KEY` | Vercel + `.env.local` | Signs pushes (server only) |
| `VAPID_SUBJECT` | Vercel + `.env.local` | `mailto:` contact for push services |
| `CRON_SECRET` | Vercel + Supabase Vault | Authenticates `pg_cron` calls |

- Generate keys once with `npx web-push generate-vapid-keys`. **Rotating the
  VAPID keys invalidates every subscription.** Do not rotate them except in
  response to a leak.
- **If any variable is missing, the feature is off:** the opt-in UI is hidden,
  cron routes return `204` and log a warning, and the admin page shows "Push is
  not configured". Nothing throws.
- New dependency: `web-push` (server only).

### `pg_cron` jobs

Lagos is UTC+1 with no DST. Applied through the SQL Editor. The secret and the
base URL live in Supabase Vault, never literally in the job definition.

```sql
create extension if not exists pg_cron;
create extension if not exists pg_net;

-- once, in the Vault UI or:
-- select vault.create_secret('<CRON_SECRET>', 'push_cron_secret');
-- select vault.create_secret('https://<production-host>', 'push_base_url');

create or replace function public.call_push_cron(path text)
returns void
language plpgsql
security definer
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

select cron.schedule('push-morning', '*/5 6 * * *', $$select public.call_push_cron('/api/cron/push/morning')$$);
select cron.schedule('push-streak',  '*/5 18 * * *', $$select public.call_push_cron('/api/cron/push/streak')$$);
select cron.schedule('push-drain',   '* * * * *',   $$select public.call_push_cron('/api/cron/push/drain')$$);
```

Verify after applying: `select jobname, schedule, active from cron.job;`, then
after a few minutes `select * from cron.job_run_details order by start_time
desc limit 20;` and `select status_code, created from net._http_response order
by created desc limit 20;`.

### Security

- **Cron routes** (`/api/cron/push/*`): POST only, `Authorization: Bearer
  CRON_SECRET` compared with `crypto.timingSafeEqual`; anything else is `401`.
  They are added to the `proxy.ts` matcher exclusion alongside
  `api/billing/webhook`, because they carry no user session. They are **not**
  added to `public-routes.ts`, which is for world-readable pages.
- **`/api/push/subscription`:** requires a student session; `rateLimit` at 10
  per minute per user; endpoint must be `https:` and ≤ 1024 characters; `p256dh`
  and `auth` are length-checked base64url.
- **Admin routes:** the existing admin session check, plus `AdminAudit`.
- Announcement URLs are validated as internal paths on the server (zod) and
  again in `notificationclick`.
- Payloads contain no personal data, and Web Push encrypts them end to end.

### Failure handling

| Failure | Behaviour |
|---|---|
| Subscription gone (404/410) | Delete it |
| Transient (429/5xx/network) | Broadcast: retry up to 3 attempts via the drain. Reminder: no retry that day |
| 5 consecutive failures on a subscription | Delete it |
| Cron call times out mid-run | The next call resumes (`ReminderLog`, stale claims expire after 5 minutes) |
| Overlapping drain calls | `FOR UPDATE SKIP LOCKED` |
| Student still on the v1 worker | Opt-in explains closing tabs; nothing is subscribed |
| Slow Supabase pooler connect (6–35s) | Budget measured from request start; cron routes use the app's existing Prisma connection settings |
| VAPID/secret missing | Feature off, `204`, warning log |

### Testing

New `scripts/test-push-*.mts` files, added to `npm test`:

- `test-push-capability.mts`: every browser, permission and install state.
- `test-push-reminders.mts`: all four digest cases, pluralisation, truncation;
  streak eligibility for a 1-day streak, a broken streak, practised today, and
  the Lagos-midnight boundary.
- `test-push-audience.mts`: zod filter validation; `matchesAudience` across
  shared fixtures that the SQL predicate is also checked against.
- `test-push-send-result.mts`: status code and error to outcome mapping.
- `test-push-payload.mts`: length limits, internal-path validation, tag
  formats.
- `test-cron-auth.mts`: correct secret, wrong secret, missing header, wrong
  method, secret not configured.

Manual checklist (recorded in the plan's verification step):

- Subscribe and receive in Chrome desktop, Android Chrome, and an installed iOS
  PWA (iOS ≥ 16.4). The iOS Safari tab shows the install hint.
- DevTools → Application → Push delivers to the handler; clicking opens the
  right page in an existing tab.
- Upgrade from the v1 worker with a tab open: the opt-in explains closing
  tabs; after reopening, subscribing works.
- Shared device: student A signs out, student B signs in; a broadcast
  targeting only A does not reach the device.
- Admin test send reaches only the named student.
- A broadcast to more than 100 test subscriptions drains across several cron
  calls with correct final counts; cancelling mid-drain stops it.
- Banner shows, dismisses, and stays dismissed across devices.

## Build order

1. Schema, migration, configuration, `web-push` wrapper.
2. Service worker handlers, capability function, subscribe/unsubscribe routes,
   sign-out cleanup.
3. Settings → Notifications and the post-study-plan opt-in card.
4. Admin test send (proves delivery end to end).
5. Morning and streak reminders, cron auth, `pg_cron` jobs.
6. Announcements: console page, queueing, drain, cancel, dashboard banner.
