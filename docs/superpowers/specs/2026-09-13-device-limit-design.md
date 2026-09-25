# Device limit and anti account-sharing — design

Date: 2026-09-13
Status: approved in brainstorming, pending spec review

## Problem

Students can share one set of login details, so several students use one paid
account. The goal is to make sharing inconvenient without ever blocking a
legitimate student (shared family phones, school computers, carrier NAT).

## Scope

In scope:

1. A concurrent device limit on paid accounts, plus a Devices section in
   Settings.
2. Copy that makes clear progress and analytics are built from one student's
   answers.
3. A public `/terms` page with a one-account-per-student clause.

Out of scope (deferred): sharing detection / admin flagging, group or referral
pricing, device fingerprinting, IP or region locking, per-login OTP.

## Decisions

| Question | Decision |
|---|---|
| What happens past the limit | New sign-in always succeeds; the least-recently-used device is signed out |
| Which accounts are limited | Paid tiers only (anything above FREEMIUM) |
| Limit | 2 devices at once |
| Freemium account with >2 devices upgrades | Nothing at payment; the limit is enforced at the next sign-in |
| What a "device" is | One sign-in (one JWT). Phone + laptop = 2 |
| Mechanism | `UserDevice` table + `deviceId` on the JWT, checked in the existing 60s profile refresh |

Rejected: database session strategy (adds a DB lookup to every request) and
bumping `sessionsValidFrom` (signs out every device, cannot power a device list).

## 1. Data model

New Prisma model:

```prisma
model UserDevice {
  id         String    @id @default(cuid())
  userId     String
  user       User      @relation(fields: [userId], references: [id], onDelete: Cascade)
  label      String    // derived from the user agent, e.g. "Chrome on Android"
  createdAt  DateTime  @default(now())
  lastSeenAt DateTime  @default(now())
  revokedAt  DateTime?

  @@index([userId, revokedAt])
}
```

`User` gains `devices UserDevice[]`. The migration is applied through the
Supabase SQL editor (migrate cannot reach the direct URL from the dev machine),
and the catalog is verified afterwards.

`DEVICE_LIMIT = 2` lives in code, next to the trim logic.

## 2. Auth flow (`src/lib/auth.ts`)

### At sign-in (`isSignIn`, Credentials and Google alike)

1. Create a `UserDevice` row; store its id on the token as `deviceId`. The
   label comes from the request user agent where available, else "Unknown
   device".
2. Resolve the tier (already done in this callback). If it is paid, load the
   user's non-revoked devices and pass them to the pure function
   `devicesToRevoke(devices, newDeviceId, limit)`, which returns every device
   except the new one and the `limit - 1` most recent others by `lastSeenAt`.
   Set `revokedAt = now()` on those.

Step 2 runs in a transaction with the insert so two simultaneous sign-ins
cannot both survive over the limit.

### On each profile refresh (at most every `PROFILE_TTL_MS` = 60s)

- `PROFILE_SELECT` is extended to read this token's device row
  (`id`, `revokedAt`, `lastSeenAt`), so no extra round trip.
- Row revoked or missing → device revoked (see section 3).
- `lastSeenAt` is updated only when older than 15 minutes, fire-and-forget
  like the tier refresh, so an active device does not write every minute.

### Rules

- Revocation is checked for every tier (so manual sign-out from Settings works
  for freemium). The limit is enforced only for paid tiers, only at sign-in.
- Tokens issued before this ships carry no `deviceId`. They stay valid and are
  registered at their next sign-in. No mass sign-out on deploy.
- A database error keeps the cached token, as today. A device is never signed
  out because of an outage.
- Suspension and deletion keep returning `null`, unchanged.

## 3. Telling the student why they were signed out

Returning `null` clears the cookie, and the proxy only sees "no token", so it
cannot give a reason. For device revocation only, the jwt callback instead
returns a stripped token `{ deviceRevoked: true }`: no `sub`, no profile.

- `session` callback: no `sub`, so no `session.user.id`. Existing layout and
  page guards (`if (!session?.user?.id) redirect("/login")`) still hold.
- `src/proxy.ts`: a token with `deviceRevoked` is treated as signed out. The
  response deletes the session cookie and redirects to `/login?reason=device`
  (API routes get a 401 and the cookie is cleared). Auth routes with this token
  render normally rather than bouncing to `/dashboard`.
- Login page: when `reason=device`, show a notice: "You were signed out because
  this account was signed in on another device. If that wasn't you, change your
  password."

## 4. Devices section in Settings

`src/components/settings/devices-section.tsx`, rendered in
`src/app/(dashboard)/settings/page.tsx` under the subscription section, using the
existing `section.tsx` wrapper.

- Lists non-revoked devices: label, "This device" badge (matched on the
  session's `deviceId`), and "Last active …".
- **Sign out** on each other device, plus **Sign out all other devices**.
- Paid accounts see: "Your plan allows 2 devices at a time. Signing in on a new
  device signs out the one used least recently." Freemium sees the list without
  the limit copy.
- Server actions revoke by `(id, userId = session.user.id)`, so a student cannot
  revoke another user's device.
- A successful password change revokes every device except the current one.

The current `deviceId` must be available server-side: `session` exposes it as
`session.user.deviceId`.

## 5. "Progress is personal" copy

No new logic.

- Dashboard readiness / analytics card: "Built from every question answered on
  this account."
- Upgrade / checkout page, next to plan features: "One student per account.
  Your study plan, weak areas and unseen questions are built from your answers
  alone."

## 6. Terms page

- New public page `/terms`, added to `src/lib/public-routes.ts`.
- Plain-language draft covering account basics and: one account per student;
  login details must not be shared; ScholarsCrib may sign out devices, and
  suspend accounts, used by more than one person.
- Marked in the file as a draft pending legal review.
- Linked from the register page ("By creating an account you agree to the
  Terms") and the site footer.

## 7. Testing

- Unit tests (`node:test`, alongside existing `scripts/test-*.mts`):
  - `devicesToRevoke`: under limit, at limit, over limit, ties on
    `lastSeenAt`, new device always kept.
  - Proxy handling of a `deviceRevoked` token: page request, API request, auth
    route.
- Integration script: sign in three times as a paid test user and confirm the
  first device is signed out within the refresh window; a freemium user is not.
- Manual: Settings list and sign-out buttons, login notice, password change
  signs out other devices, `/terms` reachable signed out.
