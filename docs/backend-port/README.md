# Backend port specification

This folder is the contract for splitting ScholarsCrib’s backend out of the Next.js app and rewriting it in FastAPI. It describes behaviour that exists in the code today: HTTP routes, the Postgres schema, auth and sessions, caches, billing, push, and the learning engines that pages call without going through a route.

The column-level source of truth remains [`prisma/schema.prisma`](../../prisma/schema.prisma). Request-body rules remain [`src/lib/validators.ts`](../../src/lib/validators.ts) and [`src/lib/push-validators.ts`](../../src/lib/push-validators.ts). When this document and the code disagree, the code wins — update the document.

## What “backend” means here

The Next app mixes three kinds of server work:

1. **HTTP route handlers** under `src/app/api/**` (students) and `src/app/admin/api/**` (admins).
2. **Server-component loaders** in `src/lib/*-data.ts` and similar modules. Pages call these directly. A separate API must expose them, or the Next frontend will keep reading Postgres itself.
3. **Pure engines** under `src/engines/**`. These have no I/O. Port the formulas exactly; the tests in `scripts/test-*.mts` are the oracle.

There are no `"use server"` actions. Contact details in `src/lib/contact.ts` are static copy, not an API.

## Suggested FastAPI layout

Keep the existing Postgres database. Prisma migrations already own the schema; the Python service should use the same tables, enums, and constraints rather than inventing a second model.

| Python package | Current TypeScript |
|---|---|
| `app/auth/` | `src/lib/auth.ts`, `admin-auth.ts`, `session-token.ts`, `account-status.ts`, `devices.ts` |
| `app/api/student/` | `src/app/api/**/route.ts` |
| `app/api/admin/` | `src/app/admin/api/**/route.ts` |
| `app/learning/` | `src/engines/learning/`, `src/lib/learning-path.ts`, `topic-mastery-store.ts` |
| `app/planner/` | `src/engines/planner/`, `src/lib/study-plan.ts` |
| `app/assessments/` | `src/lib/assessment-*.ts`, `jamb-cbt*.ts`, `question-pool.ts` |
| `app/srs/` | `src/lib/spaced-repetition.ts`, `flashcards.ts` |
| `app/billing/` | `src/lib/billing/` |
| `app/provider/` | `src/lib/question-provider/` |
| `app/push/` | `src/lib/push-*.ts`, `announcement-drain.ts` |

Background work that Next does with `after()` (provider ingest, achievement awards, plan auto-complete, announcement drain) must be a FastAPI `BackgroundTasks` job or a worker. The HTTP response must not wait for it, and the job must still run if the process stays up after the response.

## Documents

| File | Contents |
|---|---|
| [01-architecture.md](./01-architecture.md) | Request path, two identity realms, caches, env vars, what is unused |
| [02-data-model.md](./02-data-model.md) | Enums, models, invariants, cascade behaviour |
| [03-auth-and-access.md](./03-auth-and-access.md) | Student JWT, admin JWT, devices, proxy gates, entitlements, rate limits |
| [04-student-api.md](./04-student-api.md) | Every student HTTP route |
| [05-admin-api.md](./05-admin-api.md) | Every admin HTTP route and audit actions |
| [06-assessments.md](./06-assessments.md) | Question selection, quizzes, mocks, JAMB CBT, scoring, attempts |
| [07-learning.md](./07-learning.md) | Evidence ledger, mastery, path, gaps, classroom loaders |
| [08-study-plan.md](./08-study-plan.md) | Term / blended / exam planner |
| [09-flashcards.md](./09-flashcards.md) | SRS algorithm and deck routes |
| [10-billing.md](./10-billing.md) | Paystack checkout, webhook, settlement, comps |
| [11-push-and-cron.md](./11-push-and-cron.md) | Web push, reminders, announcements |
| [12-question-provider.md](./12-question-provider.md) | SDASH cache, saturation, circuit breaker |
| [13-page-loaders.md](./13-page-loaders.md) | Server data the UI reads today that has no REST route |

## Porting rules that are easy to get wrong

1. **Two auth realms.** Students and admins are different tables, different JWT secrets, and different cookies. A student token must never authorize `/admin`.
2. **JWT, not the `Session` table.** `Session` and `VerificationToken` exist because Auth.js’s Prisma adapter expects them. Student and admin sessions are JWTs. OAuth still writes `Account` rows.
3. **Suspension and force sign-out are checked on token refresh**, not only at login. See `isSessionRevoked` in [03-auth-and-access.md](./03-auth-and-access.md).
4. **Device limit is two browsers for STANDARD and above.** Freemium is unlimited. Revoking a device also deletes its push subscriptions.
5. **`User.tier` is a cache.** The source of truth is live `Subscription` rows. Settlement and admin comps write both.
6. **Paystack signatures are HMAC-SHA512 of the raw body.** Re-serializing JSON breaks verification.
7. **Quiz payloads sent to the client omit `correctAnswer` and `explanation`.** The public `GET /api/questions` list currently does **not** omit them. Preserve that only if you intend the leak; otherwise close it deliberately.
8. **Past-paper quizzes are always timed.** `untimed` applies only when `examType` is absent.
9. **Submit is idempotent.** A second submit of a completed attempt replays the stored result. A lost race returns 409 only if the attempt is no longer `IN_PROGRESS` and not `COMPLETED`.
10. **Learning events are append-only.** `TopicMastery` is a decayed fold of that ledger. Bump `SCORING_VERSION` to force a replay; do not edit old events.
11. **Catalogue cache** is a one-hour tagged cache (`catalogue`). Question, lesson, and provider mutations invalidate it. FastAPI needs an equivalent (Redis or in-process TTL plus explicit bust).
12. **Cron auth fails open to 204 when `CRON_SECRET` is unset**, so a misconfigured scheduler does not error-loop. A present but wrong bearer is 401.
13. **Free-tier numeric caps** (subjects per day, questions per day, mock count) are described in comments in `src/lib/subscription.ts` and are **not implemented**. Gates that exist are the four entitlement flags.
14. **`TERMII_*` and `RESEND_API_KEY` are unused.** Phone OTP and transactional email are not implemented. `emailVerified` is never set by application code.
15. **Lagos time** (`Africa/Lagos`) is the calendar for streaks, study-plan days, and reminder idempotency. Do not use UTC dates for those.

## Tests to keep as oracles

The behavioural tests live in `scripts/test-*.mts` and run with `npm test`. The suites that pin backend rules (not UI) include streak, rate limit, login rate limit, profile completion, device limit, account status, subscription, billing term/entitlement/signature/settlement, spaced repetition, learning-path graph/state/recommend/revision/plan/pretest/evidence/gaps, study-plan engines, provider cache/mapper/saturation/ingest, push, cron auth, announcement, admin question/import/access, and lesson progress rules. Port those assertions into pytest before changing formulas.
