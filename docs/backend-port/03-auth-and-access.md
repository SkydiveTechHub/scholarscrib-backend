# Auth, sessions, devices, entitlements, rate limits

## Student session

Implemented in `src/lib/auth.ts`. Auth.js v5, JWT strategy, Prisma adapter for OAuth account rows only.

| Setting | Value |
|---|---|
| Sign-in page | `/login` |
| New Google user page | `/complete-profile` |
| Callbacks | `jwt`, `session`. There is no `signIn` callback. |
| Profile cache TTL | 60_000 ms |
| Explicit `maxAge` | not set (Auth.js default, 30 days) |

### Providers

**Credentials** (`admin` is a different file). Body fields used by the provider: `email`, `password` with minimum length **6**. This is stricter than `loginSchema` in `validators.ts` (password min 1), which the credentials provider does not use.

Order inside `authorize`:

1. Parse email and password. Invalid → `null` (generic failure, no enumeration).
2. `checkLoginRateLimit` **before** the user lookup and bcrypt. Over limit throws `LoginRateLimited` (`code = "rate_limited"`). The client treats that code as “too many attempts”.
3. Look up user by `trim().toLowerCase()` email. Missing user, missing `passwordHash`, or `isActive === false` → `null`.
4. `bcrypt.compare`. Mismatch → `null`.
5. Return `{ id, email, name, image }`.

**Google**, only when `AUTH_GOOGLE_ID` and `AUTH_GOOGLE_SECRET` are set and are not the placeholder strings `your-google-client-id` / `your-google-client-secret`. Profile mapping is `googleProfileToUser` in `src/lib/google-profile.ts`: `sub`, email, given/family name, picture. Suspended Google users are not blocked inside the provider; the `jwt` callback revokes them via `isSessionRevoked` on the sign-in profile fetch.

Registration is a separate route, not a provider. See [04-student-api.md](./04-student-api.md). bcrypt cost is **12**.

### JWT contents

| Claim | Meaning |
|---|---|
| `sub` | User id. Absent on a device-revoked marker token. |
| `profile` | `{ role, classLevel, track, state, firstName, lastName, image, tier }` |
| `profileAt` | ms timestamp of that cache |
| `sessionStartedAt` | seconds, set at sign-in, compared with `sessionsValidFrom` |
| `deviceId` | `UserDevice.id` |
| `deviceRevoked` | `true` on a marker token that has no `sub` |

### `jwt` callback order

1. If the token is already `deviceRevoked` and this is not a new sign-in, return it unchanged.
2. Refresh the profile when this is a sign-in, `trigger === "update"`, or the cache is older than 60s.
3. User row missing → return `null` (session ends). A thrown database error keeps the cached profile.
4. `isSessionRevoked(profile, sessionStartedAt)` → return `null`.
5. If the device row is revoked and this is not a fresh sign-in → replace the token with `{ deviceRevoked: true }`.
6. If the device is active and `lastSeenAt` is older than 15 minutes, `touchDevice` fire-and-forget.
7. Resolve tier from live `ACTIVE` subscriptions. If it differs from `User.tier`, update the column and the cache.
8. On sign-in, `registerDevice`. The device cap applies when the resolved tier is at least `STANDARD`.

`updateSession` is Auth.js `unstable_update`. Profile completion and post-payment flows call it so the next request does not wait out the 60s cache. FastAPI can do the same with a short-lived claim or by re-reading the user on each request. If you re-read every request, keep the 15-minute `lastSeenAt` write throttle.

### `session` callback

If `deviceRevoked`, the session is `{ user: undefined, deviceRevoked: true }`. `user` must be explicitly undefined so Auth.js does not copy the JWT into `user`. Otherwise `user.id = sub`, profile fields are copied, and `user.deviceId` is set.

### Account status

`src/lib/account-status.ts`:

```
revoked if not isActive
else if sessionsValidFrom is null → ok
else if token has no iat/startedAt → revoked
else revoked if tokenIssuedAtSeconds <= floor(sessionsValidFrom / 1000)
```

`sessionStartedAt` on sign-in is `now`. Afterwards it is the stored value, falling back to `token.iat`.

Suspension sets `isActive=false`, `suspendedAt`, `suspendedReason`. Reactivation clears all three. Force sign-out sets `sessionsValidFrom=now` and deletes every `PushSubscription` for that user. It does not change the password or the device rows by itself; the JWT check is what kicks browsers out. Password change separately revokes other devices.

### Devices

`DEVICE_LIMIT = 2` for tiers ≥ `STANDARD`. Freemium is not limited.

`registerDevice` locks the user row with `SELECT … FOR NO KEY UPDATE`, inserts a device, then revokes overflow:

- Keep the device just created.
- Sort the other non-revoked devices by `lastSeenAt` descending, then id.
- Revoke everyone past `limit - 1` (the least recently seen).
- Delete push subscriptions whose `deviceId` was revoked.

Manual revoke (`POST /api/user/devices`):

- `{ "allOthers": true }` revokes every device except the current one.
- `{ "deviceId": "<id>" }` revokes that device. Revoking the current device is **400** with “Use Sign out to leave this device”.
- Unknown id is **404**.

### Profile completion gate

`needsProfileCompletion` is true when the cached profile actually contains `classLevel`, `track`, and `state` keys and any of them is missing or the state is not a Nigerian state. A cache that has not loaded those keys yet does not redirect (avoids bouncing on a partial token). The dashboard layout sends incomplete profiles to `/complete-profile`.

Nigerian states are the 36 states plus `"FCT Abuja"`, exact spelling, in `src/lib/constants/exam-types.ts`.

### Library auth exception

`GET /api/library` does not call `auth()`. It decodes the JWT with `getSessionToken` and requires `token.sub`. Tier for the lock check comes from the JWT profile, not a fresh DB read.

## Admin session

`src/lib/admin-auth.ts`, `src/lib/admin-session.ts`, `src/lib/admin-access.ts`.

- Cookie name `scholarscrib.admin-session`, path `/admin`, `httpOnly`, `sameSite=lax`, `secure` in production.
- JWT max age 8 hours. Payload is `sub` = admin id only. **Do not** trust `isOwner` or `isActive` from the token; they are not stored there.
- Decrypting the admin cookie requires `salt` equal to the cookie name. Auth.js derives the key from secret **and** salt, and the default salt is the cookie name.
- Credentials provider id `admin-credentials`. Identifier is an email (contains `@`) or a username matching `^[a-z0-9._-]{3,32}$`, trimmed and lowercased (`src/lib/admin-access.ts`).
- Inactive admin → `authorize` returns null. On success, `lastLoginAt` is updated.
- Password minimum **12** on create. bcrypt cost **12**.
- Sign-in page `/admin/login`.

`getAdminPrincipal` loads the admin by id on every API call. `canAccessConsole` requires a row with `isActive`. `requireAdminApi` → 401 if that fails. `requireOwnerApi` → 401 if unsigned/inactive, **403** `{ "error": "Owner access required" }` if active but not owner.

| Capability | Who |
|---|---|
| Console, questions, lessons, materials, announcements, terms, provider backfill, student profile / suspend / tier | any active admin |
| Create or deactivate admins, delete a student, force sign-out | owner only |
| Deactivate the owner account | nobody (`canDeactivate` is false for `isOwner`) |

New admins are created with `isOwner: false`. There is no API to promote an owner.

## Entitlements

`src/lib/subscription.ts` and `src/lib/entitlements.ts`.

Rank: `FREEMIUM=0 < STANDARD=1 < PREMIUM=2`. `can(tier, feature)` is true when the tier rank is at least the feature’s requirement.

| Feature key | Minimum tier | Enforced on |
|---|---|---|
| `flashcards` | STANDARD | all `/api/flashcards/**` |
| `studyPlanner` | STANDARD | all `/api/study-plan/**` |
| `premiumLibrary` | PREMIUM | library URL unlocking (rows still returned) |
| `advancedAnalytics` | PREMIUM | performance UI; confirm call sites when porting pages |

`denyUnlessEntitled`:

- no user id → **401** `{ "error": "Unauthorized" }`
- session tier not entitled → re-read `User.tier` from the database (payment may have landed inside the 60s cache)
- still not entitled → **403**

```json
{
  "error": "This feature is part of Standard.",
  "requiredTier": "STANDARD",
  "feature": "flashcards"
}
```

`displayName` is the human tier name inside that sentence.

**Not implemented:** the comments in `subscription.ts` mention freemium caps (about 3 subjects, 25 questions/day, 1 mock). No route enforces them. Do not invent those limits during the port.

Prices in kobo (monthly / yearly):

| Tier | Monthly | Yearly |
|---|---|---|
| FREEMIUM | 0 | 0 |
| STANDARD | 250000 (₦2,500) | 2400000 |
| PREMIUM | 500000 (₦5,000) | 5000000 |

`isPurchasableTier` is false for `FREEMIUM`.

Which subscription rows count as live is in [10-billing.md](./10-billing.md).

## Rate limits

`src/lib/rate-limit.ts`. Fixed window. The request that opens a window is always allowed (`count <= 1` is ok), so a limit of 0 would block from the second request, not the first.

Redis: `INCR` style via Upstash REST, key prefix `rl:`. Timeout 800ms. On timeout or error, use memory. Memory map is capped at 10_000 keys and sweeps expired windows.

`clientKey(req, scope)` is `{scope}:{ip}` using the first `x-forwarded-for` hop, else `x-real-ip`.

| Key | Limit | Window | Where |
|---|---|---|---|
| `register:{ip}` | 5 | 600s | register |
| `login:{email}:{ip}` | 5 | 900s | credentials authorize |
| `login-email:{email}` | 20 | 3600s | credentials authorize, after the pair limit |
| `password:{userId}` | 5 | 900s | change password |
| `avatar:{userId}` | 10 | 3600s | avatar |
| `generate:{userId}` | 20 | 60s | quiz generate |
| `provider:outbound` | 30 | 60s | SDASH scheduling (global, not per user) |
| `scoped-mock:{userId}` | 12 | 60s | scoped mock |
| `jamb-cbt-prepare:{userId}` | 20 | 60s | JAMB prepare |
| `jamb-cbt:{userId}` | 6 | 60s | JAMB generate |
| `flashcard-preview:{userId}` | 40 | 60s | preview |
| `billing-checkout:{userId}` | 10 | 60s | checkout |
| `push-subscribe:{userId}` | 10 | 60s | push subscribe |

Login checks the email+IP window first, then the email-only window.

## Password change side effect

`changeUserPassword` returns `ok`, `no-password` (Google-only account), or `wrong-password`. On `ok`, the route calls `revokeOtherDevices(userId, currentDeviceId)`. That revoke is best-effort: a failure there does not fail the password response.

## Cookies the Python service must set

If FastAPI replaces Auth.js rather than sharing its encrypted JWT:

- Keep issuing a student credential the Next app can still verify during a strangler period, **or** move every `auth()` call site (layouts and loaders, not only routes) in the same cutover.
- Admin cookie must stay path-scoped to `/admin` and use a different signing key.
- Deleting a session must clear every chunk and use `path=/` plus the secure flag that matches the name prefix.

Sharing Auth.js’s JWE format is painful. The practical cutover is: FastAPI owns auth, Next becomes a client that sends `Authorization: Bearer`, and the proxy’s cookie checks are deleted. Document that as a deployment step; do not silently change cookie names while Next still calls `auth()`.
