# Push, reminders, and announcements

Modules: `src/lib/push-config.ts`, `push-send.ts`, `push-payload.ts`, `push-send-result.ts`, `push-subscription-data.ts`, `push-validators.ts`, `push-reminders.ts`, `push-reminder-runner.ts`, `push-audience.ts`, `push-audience-sql.ts`, `push-capability.ts`, `cron-auth.ts`, `announcement.ts`, `announcement-data.ts`, `announcement-drain.ts`.

Client subscribe helper `src/lib/push-client.ts` uses `NEXT_PUBLIC_VAPID_PUBLIC_KEY` in the browser. The service worker is frontend. The API only stores the subscription and sends.

## When push is off

Push is on only when the VAPID public key, VAPID private key, VAPID subject, and `CRON_SECRET` are all present (`push-config.ts`). Subject must start with `mailto:` or `https:`. If push is off, the three cron routes return **204** with no body after the cron guard passes (or 204 from the guard itself when the secret is unset).

Rotating VAPID keys invalidates every stored subscription. There is no migration.

## Cron authentication

`src/lib/cron-auth.ts`. Intended caller is Supabase `pg_cron` (or any scheduler) with `Authorization: Bearer <CRON_SECRET>`.

| Condition | Status |
|---|---|
| Method is not POST | 405 |
| `CRON_SECRET` unset | **204** (skip, so a scheduler does not retry forever on a half-configured environment) |
| Secret shorter than 16 chars at read time | treated as unset |
| Bearer missing or not equal | 401 |
| Equal | proceed |

Comparison: SHA-256 both sides, then `timingSafeEqual` on the digests. Do not compare the raw secret with `==`.

In-handler budget `CRON_BUDGET_MS = 40_000`. Stop claiming more rows when the deadline is near, and return `done: false` (reminders) or a partial drain count so the next minute continues.

Suggested schedule (documented on the route files, enforced by the scheduler, not by the app):

| Route | When (Africa/Lagos) |
|---|---|
| `POST /api/cron/push/drain` | every minute |
| `POST /api/cron/push/morning` | every 5 minutes, 07:00–07:55 |
| `POST /api/cron/push/streak` | every 5 minutes, 19:00–19:55 |

## Payload

```json
{
  "title": "≤ 60 chars",
  "body": "≤ 180 chars",
  "url": "/dashboard or another internal path",
  "tag": "morning-YYYY-MM-DD | streak-YYYY-MM-DD | announcement-<id>"
}
```

`web-push` send: TTL 12 hours, request timeout 10 seconds (`push-send.ts`).

| HTTP from the push service | Outcome |
|---|---|
| 2xx | `sent` |
| 404 or 410 | `gone` — delete the subscription |
| 400 or 413 | `invalid` |
| anything else, or a throw | `retry` |

`failureCount` increments on non-gone failures. At **5**, delete the subscription. Delivery rows have their own `attempts` counter, max **3**, then `FAILED`.

## Subscriptions

`POST /api/push/subscription` upserts on `endpoint`. If another user owns that endpoint, the row moves to the caller (shared device). `deviceId` is copied from the JWT. `DELETE` removes the endpoint only when `userId` matches.

Preferences default to all true when the row is missing. The announcements toggle gates **push** audience selection. In-app banners ignore it; dismissal is `POST /api/announcements/{id}/dismiss`.

## Morning and streak runners

`runReminders(kind, { now, deadline })` in `push-reminder-runner.ts`.

Claim: `INSERT INTO ReminderLog (userId, kind, dayKey) … ON CONFLICT DO NOTHING RETURNING`. A conflict means this student was already processed for this kind today (sent or deliberately skipped). There is no same-day retry.

Page size 200 students per invocation. Send concurrency 20.

**Morning** (`kind = "morning"`): students with `studyReminders` enabled (or no preference row). Body is a digest of today’s `PENDING` plan items plus due flashcard count. If both are empty, record the log as skipped and do not send. Tag `morning-{lagosDayKey}`.

**Streak** (`kind = "streak"`): `streakReminders` enabled. `streakToRemind` (`src/lib/push-reminders.ts`):

- If the student already has a completed attempt **today**, return null (no reminder).
- Otherwise `currentStreak(days, previousDayKey(today))`. That counts the consecutive Lagos days ending **yesterday**.
- Send only when that number is ≥ 2. The body says “Keep your {n}-day streak”.

`currentStreak` itself (`src/lib/streak.ts`) is 0 when the end day you pass it is missing from the set. Achievements call it with **today**, so a streak badge requires study today. The evening reminder deliberately passes yesterday so it can still mention a streak that has not yet been extended. Tag `streak-{lagosDayKey}`.

Response: `{ processed, notified, sent, skipped, done }`. `done` is false when the deadline hit before the candidate list was exhausted.

## Announcement drain

`drainAnnouncements` (`announcement-drain.ts`):

1. Expire announcements past `expiresAt` that are still `QUEUED` or `SENDING` (do not keep sending).
2. Claim up to 100 `PENDING` deliveries with `FOR UPDATE SKIP LOCKED`, also reclaiming rows whose `claimedAt` is older than 5 minutes (worker died).
3. Set `claimedAt = now` and move the parent announcement `QUEUED → SENDING` if needed.
4. Send with concurrency 20.
5. `sent` → `SENT` and increment `sentCount`. `gone` → `GONE` and delete the subscription. Retryable failure → increment `attempts`, clear `claimedAt` if attempts < 3, else `FAILED` and increment `failedCount`.
6. When an announcement has no `PENDING` deliveries left, set `SENT` and `completedAt`.

Cancel (`POST .../cancel`) only from `QUEUED` or `SENDING`. It flips remaining `PENDING` rows to `CANCELLED`. The drain’s claim must re-check status so a cancel that lands mid-send does not count as sent. The implementation guards the final update on `status = PENDING`.

Creating an announcement inserts the delivery rows in the request and then calls `after(drain)`. Cron drain is the backstop. Both must be safe to run together (the skip-locked claim is that lock).

Test send (`/announcements/test`) does not write delivery rows and does not consult the preference flag.

## Audience

Filter object (all fields optional):

| Field | Type |
|---|---|
| `examTargets` | exam type enum values; matches `StudyPlan.targetExam` |
| `classLevels` | `SS1`–`SS3` |
| `tracks` | `SCIENCE` \| `ARTS` \| `COMMERCIAL` |
| `tiers` | subscription tiers |
| `userIds` | string ids, max 1000 |

SQL (`push-audience-sql.ts`) always starts from active students (`role = STUDENT AND isActive`). Each provided list is an `AND column = ANY(...)`. Subscriptions are joined after the student filter, and the announcements preference must not be false.

Preview counts (`students`, `subscribedStudents`, `devices`) use the same filter so the admin UI matches the send.
