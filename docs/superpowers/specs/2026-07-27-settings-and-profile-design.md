# Settings Page & User Profile Icon — Design

Date: 2026-07-27
Status: Approved

## Problem

Two gaps in the running app:

1. The sidebar links to `/settings`, but `src/app/(dashboard)/settings/` is an empty
   directory. The link 404s.
2. There is no way to sign out. No profile affordance exists anywhere, on any
   breakpoint.

## Scope

A Settings page covering profile details, academic setup, password change, and
avatar upload; plus a profile icon that carries the sign-out action.

Out of scope: email address changes (see Decisions), notification preferences,
account deletion, school selection UI.

## Placement

Desktop: a user block pinned to the sidebar footer, above the exam countdown,
opening a dropdown with Settings and Sign out.

Mobile: a new sticky top bar — ScholarsCrib mark on the left, avatar on the right.
The app currently has no mobile header at all, so this also gives mobile screens
the app name they lack. The existing bottom tab bar is untouched.

## Components

| File | Kind | Responsibility |
|---|---|---|
| `components/ui/avatar.tsx` | server-safe | Render `User.image`, else initials on a colored circle |
| `components/ui/user-menu.tsx` | client | Avatar + name + `classLevel · track`; dropdown with Settings / Sign out |
| `components/ui/mobile-header.tsx` | client | Sticky mobile top bar |
| `components/ui/sidebar.tsx` | client | Gains a user block in the footer |
| `components/settings/profile-form.tsx` | client | firstName, lastName, phone, state |
| `components/settings/academic-form.tsx` | client | classLevel, track |
| `components/settings/password-form.tsx` | client | currentPassword, newPassword |
| `components/settings/avatar-upload.tsx` | client | File picker → upload → preview |
| `app/(dashboard)/settings/page.tsx` | server | Reads user from DB, composes the four sections |

Each form section saves independently. One section failing does not strand the
others, and no section needs to know about any other.

## API

Route handlers with client `fetch`, matching the existing codebase pattern
(`/api/assessments/*`, `/api/questions/*`). Not server actions.

- `PATCH /api/user/profile` — `firstName`, `lastName`, `phone`, `state`, `classLevel`, `track`
- `POST /api/user/password` — `currentPassword`, `newPassword`
- `POST /api/user/avatar` — multipart file → Cloudinary → writes `User.image`

Every handler guards with `auth()` and validates with zod schemas added to
`lib/validators.ts`. The phone field reuses the existing Nigerian phone regex.
All handlers act on the session user's own id — the id is never taken from the
request body.

## Data flow

`(dashboard)/layout.tsx` already calls `auth()` for its route guard. The session
user is passed to `Sidebar` and `MobileHeader` as props rather than introducing a
`SessionProvider`, matching how the codebase works today (it has no provider and
no `useSession` call anywhere).

The session callback in `lib/auth.ts` currently selects `role`, `classLevel`,
`track`, `firstName`, `lastName` — but not `image`. It must also select `image`,
or the avatar goes stale immediately after an upload.

The settings page reads the user from Postgres directly as a server component, so
it always reflects the last write after the client calls `router.refresh()`.

## Decisions

**Email is read-only.** It is the identity the credentials provider authenticates
against. Allowing edits without a re-verification flow lets a user lock themselves
out of their own account. A verified email-change flow is its own feature.

**The password section is hidden for Google-only accounts.** Those users have no
`passwordHash`, so there is nothing to verify a current password against.

**Cloudinary without the SDK.** Signed upload via `fetch` plus node `crypto`,
roughly fifteen lines, no new dependency. `cloudinary` is not currently installed
and this avoids adding it.

**Unconfigured uploads fail cleanly.** The `.env` Cloudinary values are still
literal placeholders (`your-cloud-name`). When they are absent or unchanged, the
avatar route returns 503 with "Image uploads aren't configured" instead of
failing obscurely against a nonexistent account. Every other part of the page
works regardless.

## Error handling

- 401 when unauthenticated (defense in depth — the proxy already gates `/api/*`)
- 400 with zod field errors on validation failure
- 400 on wrong current password, distinct message from a validation failure
- 409 on phone number already taken (`User.phone` is `@unique`)
- 503 on unconfigured Cloudinary
- Each form surfaces the returned message inline, styled like the existing
  register-page error block

## Verification

Typecheck and build, then drive the running app against Postgres:

1. Sign in; confirm the avatar renders initials with no `image` set
2. Save each section; confirm the values persist in the database
3. Submit a wrong current password; confirm rejection
4. Submit a duplicate phone number; confirm 409
5. Attempt an avatar upload; confirm the 503 configured-check message
6. Sign out; confirm the session clears and protected routes bounce to `/login`
