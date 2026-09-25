# Architecture, infrastructure, and environment

## Runtime today

- Next.js 16 App Router. Route handlers are `src/app/**/route.ts`.
- Postgres via Prisma 6 (`src/lib/db.ts`). One process-wide `PrismaClient`. In development it is stored on `globalThis` so hot reload does not open extra pools.
- `DATABASE_URL` is the pooled connection. `DIRECT_URL` is declared on the Prisma datasource for migrations (not listed in `.env.example`; Prisma requires it).
- IDs are Prisma `cuid()` except `LearningEvent.seq` (bigint identity) and composite primary keys noted in the data model.
- JSON columns store lesson blocks, question options, flashcard payloads, study-plan outlines, and announcement audiences. The Python service must read and write the same JSON shapes. Lesson blocks are documented with the lesson engine in [07-learning.md](./07-learning.md).

## Request path

```
Browser
  → src/proxy.ts          optimistic cookie gate (not the security boundary)
  → route handler         auth() or requireAdminApi() re-checks identity
  → src/lib/*             IO and rules
  → src/engines/*         pure functions
  → Postgres
```

`src/proxy.ts` is the Next.js 16 proxy (there is no `middleware.ts`). Its matcher skips:

- `api/auth` (Auth.js must run logged-out)
- `api/billing/webhook` (Paystack has no session; the route checks HMAC)
- `api/cron` (bearer `CRON_SECRET`)
- static assets and image extensions

Proxy decisions:

| Caller | No session cookie | Session cookie present |
|---|---|---|
| `/admin/login` | allow | redirect `/admin` |
| other `/admin` pages | redirect `/admin/login?callbackUrl=` | allow (optimistic) |
| `/admin/api/*` | 401 `{ "error": "Unauthorized" }` | allow (optimistic) |
| `/admin/api/auth/*` | always allow | always allow |
| `/` | allow | redirect `/dashboard` |
| `/login`, `/register` | allow | allow (do not bounce; a decodable cookie is not a live session) |
| public paths (below) | allow | allow |
| other `/api/*` | 401 | allow |
| other pages | redirect `/login?callbackUrl=` | allow |

Public paths (`src/lib/public-routes.ts`): exact `/`, `/about`, `/contact`, `/terms`, `/robots.txt`, `/sitemap.xml`, `/manifest.webmanifest`, `/sw.js`, `/sw-policy.js`, `/offline`, `/signed-out`; prefixes `/learn` and `/past-questions` (segment-aware, so `/learnable` is not public); sitemap shards `/sitemap/<slug>.xml`; root Open Graph image only.

A student JWT flagged `deviceRevoked` is deleted and answered with 401 on `/api/*`, or a redirect to `/login?reason=device` on pages. In practice the dashboard layout usually catches this first, because `auth()` drops the flagged cookie before the proxy sees it.

**The proxy is not authorization.** Student routes call `auth()`. Admin routes call `requireAdminApi()` / `requireOwnerApi()`, which re-read the `Admin` row. FastAPI should do the real check in dependencies and may keep a cheap cookie-presence gate only as a convenience.

## Two identity realms

| | Student | Admin |
|---|---|---|
| Table | `User` | `Admin` |
| Module | `src/lib/auth.ts` | `src/lib/admin-auth.ts` |
| Secret | `AUTH_SECRET` | `ADMIN_AUTH_SECRET` (must be set and must differ; boot throws otherwise) |
| Cookie | `authjs.session-token` or `__Secure-authjs.session-token` on HTTPS | `scholarscrib.admin-session` |
| Cookie path | `/` | `/admin` only |
| Strategy | JWT | JWT, max age **8 hours** |
| Providers | Google (optional) + email/password | username-or-email + password |
| Base path | `/api/auth/*` | `/admin/api/auth/*` |

HTTPS cookie prefix follows `AUTH_URL` ?? `NEXTAUTH_URL` ?? the request URL (`src/lib/session-token.ts`). Chunked cookies (`.0`, `.1`, …) must be deleted together with `path=/`.

`.env.example` says not to set `AUTH_URL` / `NEXTAUTH_URL` to localhost, because that forces the secure cookie name in production by mistake.

## Caches

| Cache | Where | TTL / invalidation |
|---|---|---|
| Subject catalogue | `src/lib/catalogue.ts` `unstable_cache`, tag `catalogue` | 3600s. Busted by `revalidateTag("catalogue")` after admin question, lesson, and provider mutations |
| Achievement catalogue | `src/lib/achievements.ts` | `unstable_cache`, same pattern |
| JWT profile | inside the student JWT | 60 seconds (`PROFILE_TTL_MS`) |
| Question provider | Postgres `ProviderFetch` / `ProviderQuestion` | persistent; see [12-question-provider.md](./12-question-provider.md) |
| Rate-limit windows | Upstash Redis REST, else process memory | fixed window; see [03-auth-and-access.md](./03-auth-and-access.md) |

There is no general HTTP cache, no CDN purge API, and no application-level query cache beyond those.

## External services

| Service | Used for | Off behaviour |
|---|---|---|
| Paystack | Checkout + webhook | Missing/placeholder `PAYSTACK_SECRET_KEY` → billing disabled, checkout returns 503 |
| Cloudinary | Avatars and signed material uploads | Unconfigured → 503 on those routes |
| SDASH (`sdashapi.com`) | Past-question ingest | `QUESTION_PROVIDER_ENABLED` must be the string `"true"`; otherwise fetches are not scheduled |
| Upstash Redis | Shared rate limits | Absent → in-memory per process. Redis error or >800ms → fail over to memory (not fail-closed) |
| Web Push (VAPID) | Reminders and announcements | Missing keys or cron secret → push treated as off; cron routes return 204 |
| Google OAuth | Student sign-in | Skipped when id/secret missing or still the `.env.example` placeholders |

**Not implemented**, despite `.env.example` entries: Termii (`TERMII_API_KEY`, `TERMII_SENDER_ID`) and Resend (`RESEND_API_KEY`). Do not build phone OTP or email sending unless product asks for it.

## Environment variables

| Variable | Required | Role |
|---|---|---|
| `DATABASE_URL` | yes | Pooled Postgres |
| `DIRECT_URL` | yes for Prisma migrate | Direct Postgres (schema only) |
| `AUTH_SECRET` | yes | Student JWT |
| `ADMIN_AUTH_SECRET` | yes | Admin JWT; ≠ `AUTH_SECRET` |
| `AUTH_GOOGLE_ID` / `AUTH_GOOGLE_SECRET` | no | Google provider |
| `AUTH_URL` / `NEXTAUTH_URL` | no | Secure-cookie detection only |
| `NEXT_PUBLIC_APP_URL` | yes in prod | Paystack callback and billing redirects. Default `http://localhost:3000` |
| `NEXT_PUBLIC_APP_NAME` | no | Display name, not used by API logic |
| `PAYSTACK_SECRET_KEY` | for billing | Also the HMAC key |
| `CLOUDINARY_CLOUD_NAME` / `API_KEY` / `API_SECRET` | for uploads | Signed uploads and avatars |
| `SDASH_BASE_URL` | no | Default `https://sdashapi.com/api` |
| `SDASH_ACCESS_TOKEN` | for ingest | Bearer to SDASH |
| `QUESTION_PROVIDER_ENABLED` | no | `"true"` enables scheduling |
| `UPSTASH_REDIS_REST_URL` / `TOKEN` | no | Also accepts `KV_REST_API_*` and `SC_KV_REST_API_*` |
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | for push | Browser subscribe |
| `VAPID_PRIVATE_KEY` | for push | Server send |
| `VAPID_SUBJECT` | for push | Must start with `mailto:` or `https:` |
| `CRON_SECRET` | for cron | ≥ 16 characters; shared with the scheduler |

## Time and money

- Instants are timezone-aware UTC in Postgres (`timestamptz`), except study-plan and academic-term dates stored as `@db.Date` (calendar dates, no time).
- “Today” for streaks, plan items, and reminders is `lagosDayKey`: `YYYY-MM-DD` in `Africa/Lagos` (`src/lib/streak.ts`).
- Money is integer **kobo** (₦1 = 100 kobo). Currency is always `NGN`.
- Prices live in code (`src/lib/subscription.ts`), not in the database. Each `Subscription` row snapshots `amountKobo` at creation so a later price change does not rewrite history.

## Error body convention

Almost every route returns JSON `{ "error": "<human sentence>" }`. Validation failures from Zod add `details` (Zod flatten) or, on profile completion, a single `error` string taken from the first issue plus `details`. A few domain errors add machine fields (`reason`, `preparing`, `requiredTier`, `feature`, `outcome`). Status codes are per route in [04-student-api.md](./04-student-api.md) and [05-admin-api.md](./05-admin-api.md).

Rate limit responses are **429** with

```json
{ "error": "Too many requests. Please slow down and try again shortly." }
```

and header `Retry-After: <seconds>`.

Unauthenticated routes use **401** `{ "error": "Unauthorized" }`. Forbidden entitlements use **403** with `requiredTier` and `feature` (see auth doc).
