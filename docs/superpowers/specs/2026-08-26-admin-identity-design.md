# Separate Admin Identity — Own Table, Own Auth, Own Door

Date: 2026-08-26
Status: Draft
Builds on: `2026-08-04-admin-phase-1-design.md` (the console, `requireAdmin()`,
`AdminAudit`), `2026-08-06-admin-lessons-browse-design.md` (the lesson import
routes that move with the rest of the admin API)

## Problem

Admin access today is a column on the student table. `User.role`
(`prisma/schema.prisma:174`) is `STUDENT | TEACHER | ADMIN`, and an admin is a
student row that happens to hold `ADMIN`. That has four consequences.

1. **One identity, one session, one door.** Admin and student sign in through
   the same `/login`, share one JWT cookie, and land on `/dashboard`
   (`src/app/(auth)/login/page.tsx:24`). Reaching the console means typing
   `/admin` by hand — nothing in the student chrome links to it. The project
   owner and a student are the same kind of thing to the system.

2. **The admin carries student baggage.** An admin row has `classLevel`,
   `track`, `progress`, `attempts`, `studyPlans`, `flashcardDecks`. Every
   student-side query treats it as a learner because structurally it is one.

3. **The page guard is in the wrong place.** `src/app/admin/layout.tsx:17`
   performs the `isAdminUser()` check in a layout. This Next's own
   authentication guide is explicit that layouts "don't re-render on
   navigation", so a client-side transition between `/admin/*` pages does not
   re-run it. The API handlers are correct — `requireAdmin()`
   (`src/lib/admin-guard.ts:32`) runs per request — but the page tree leans on
   a check that Partial Rendering can skip.

4. **`TEACHER` is dead weight.** The value exists and nothing reads it. There is
   no way to register as one and no teacher surface.

There is also no way to grant someone else access. `scripts/promote-admin.ts`
is the only path in, and it needs a shell, the repo, and database credentials.

## Goal

Admins become their own identity, in their own table, with their own login door
and their own session cookie, fully disjoint from student accounts. `User`
remains the student/teacher table. The owner — and only the owner — can create
further admins from inside the console.

## Non-goals

- **Teacher capabilities.** Authoring questions and library access are
  explicitly out of scope. This work only adds the fork in the registration
  road and marks it *Coming soon*.
- **Password reset for admins.** The owner sets passwords directly and hands
  them over. No email flow, no reset tokens.
- **OAuth for admins.** Credentials only.
- **Removing `ADMIN` from the `Role` enum.** Postgres has no `DROP VALUE`;
  removing it means a create-new-type-and-swap on a live column. The value stays
  and is never written again.

## Decisions

| Question | Decision |
|---|---|
| Where admin identity lives | A separate `Admin` model with its own auth |
| Concurrent sessions | Yes — admin and student sessions coexist in one browser |
| Admin bootstrap | A script mints the owner; the owner creates the rest in-console |
| Who may create admins | The owner only, not every admin |
| Login identifier | One field — an email or a username |
| `Role` enum | Keeps all three values; `ADMIN` is never assigned again |
| Session TTL | 8 hours |
| Admin API path | Moves under `/admin/api/*` so the cookie scope is real |

## Data model

### `Admin`

```prisma
model Admin {
  id           String    @id @default(cuid())
  email        String?   @unique
  username     String?   @unique
  passwordHash String
  isOwner      Boolean   @default(false)
  isActive     Boolean   @default(true)
  lastLoginAt  DateTime?

  createdById   String?
  createdBy     Admin?  @relation("AdminCreator", fields: [createdById], references: [id])
  createdAdmins Admin[] @relation("AdminCreator")

  audits AdminAudit[]

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
```

No `Account` or `Session` relations: the admin instance uses the credentials
provider with a JWT strategy, which never touches the Prisma adapter.

There is no separate display name. The identifier *is* the name — the chrome
shows the username, or the email's local part when only an email is set.
Anything more is a field to maintain for no behaviour.

**`isActive` rather than hard delete.** Deleting an admin would cascade away
their `AdminAudit` rows — precisely the history worth keeping at the moment you
revoke someone. Deactivation is re-read from the database on every request, so
revocation takes effect on the next one even with a live cookie.

**`isOwner`.** The bootstrap script sets it. The console writes it as a literal
`false` and never reads it from a request body.

### Constraints the schema cannot express

Both go in the migration as raw SQL:

```sql
-- Exactly one owner, ever.
CREATE UNIQUE INDEX "Admin_single_owner" ON "Admin" ("isOwner") WHERE "isOwner";

-- An admin must be reachable by at least one identifier.
ALTER TABLE "Admin" ADD CONSTRAINT "Admin_has_identifier"
  CHECK ("email" IS NOT NULL OR "username" IS NOT NULL);
```

The partial unique index matters: without it a bug or a stray script run mints a
second owner, and owner is the tier that can grant access.

### Identifier rules

- Both `email` and `username` are stored trimmed and lowercased, matching how
  student emails are normalized at registration and re-normalized on every
  login (`src/lib/auth.ts:96`).
- A username matches `^[a-z0-9._-]{3,32}$` and therefore **cannot contain `@`**.
  That keeps the two namespaces disjoint: no username can ever collide with
  another admin's email.
- On create, one value is supplied. Containing `@` routes it to `email` and is
  validated as one; otherwise it routes to `username`.
- On sign-in, one value is typed and looked up as
  `where: { OR: [{ email: v }, { username: v }] }`.

### `User`

Unchanged. `Role` keeps `STUDENT | TEACHER | ADMIN`, with a schema comment
recording that admins live in `Admin` so the unused value does not mislead.
`registerUser()` (`src/lib/user-account.ts`) stops hardcoding `role: "STUDENT"`
and takes the role from validated input.

### `AdminAudit`

`actorId` repoints from `User` to `Admin` (`prisma/schema.prisma:840-841`).
This is the only destructive step in the migration: existing rows hold `User`
ids, and the new foreign key will reject them. Ordering, as one block:

1. Create `Admin`, its indexes, and the check constraint.
2. Insert the owner row.
3. `UPDATE "AdminAudit" SET "actorId" = <owner id>` for every existing row.
4. Drop the old FK, add the new one against `Admin`.

If `AdminAudit` is empty, steps 3 and 4 collapse to just the constraint swap.

## Auth and session mechanics

### `src/lib/admin-auth.ts`

A second NextAuth instance exporting `adminAuth`, `adminHandlers`,
`adminSignIn`, `adminSignOut`:

- `basePath: "/admin/api/auth"`
- `secret: process.env.ADMIN_AUTH_SECRET` — a **new** variable, distinct from
  `AUTH_SECRET`. Sharing one would let a leaked student secret forge admin
  tokens and make the whole separation cosmetic.
- `session: { strategy: "jwt", maxAge: 60 * 60 * 8 }`
- `cookies`: `sessionToken` named `scholarscrib.admin-session` with
  `path: "/admin"`, `httpOnly`, `sameSite: "lax"`, `secure` in production. The
  `callbackUrl` and `csrfToken` cookies need the same distinct naming and path,
  or the CSRF check fails.
- `providers: [Credentials]` — normalize the identifier, `bcrypt.compare`,
  reject `isActive: false`, stamp `lastLoginAt`.
- No `PrismaAdapter`.
- `pages: { signIn: "/admin/login" }`

Verified against the installed types: `basePath` exists on the config
(`node_modules/next-auth/lib/client.d.ts:86`) and per-cookie `path` overrides
exist (`node_modules/@auth/core/types.d.ts:166`).

**Cookie scope is why the API moves.** `path: "/admin"` means the browser never
sends the admin cookie to a student route — a real boundary rather than a
naming convention. It also means the cookie is not sent to `/api/admin/*`,
which sits outside that path, so the admin API moves under `/admin/api/*`. Six
route files relocate and nine `fetch` call sites update across four files
(`src/app/admin/questions/page.tsx`, `.../questions/import/page.tsx`,
`src/components/admin/question-form.tsx`,
`src/components/admin/lesson-upload-form.tsx`).

### Route tree

```
src/app/admin/(entry)/login/page.tsx    → /admin/login   no chrome, no guard
src/app/admin/(console)/layout.tsx      → sidebar chrome
src/app/admin/(console)/page.tsx        → /admin
src/app/admin/(console)/questions/...   → unchanged URLs
src/app/admin/(console)/lessons/...     → unchanged URLs
src/app/admin/(console)/team/page.tsx   → /admin/team    owner only
src/app/admin/api/auth/[...nextauth]/route.ts
src/app/admin/api/questions/...         → relocated from /api/admin
src/app/admin/api/lessons/...           → relocated from /api/admin
```

Route groups do not appear in URLs, so every existing admin URL is preserved.
The split exists because today's `src/app/admin/layout.tsx` redirects anyone
unauthenticated — which would make `/admin/login` unreachable. Route handlers
ignore layouts, so the auth endpoints are unaffected by the guard.

**`src/app/admin/layout.tsx` is deleted, not kept alongside the groups.** A
layout at that level still wraps *both* route groups, so leaving it in place
would reapply the redirect to `/admin/login` and reintroduce exactly the
unreachable-login problem the split exists to solve. Its chrome moves into
`(console)/layout.tsx`; `(entry)` gets no layout of its own and inherits the
root one.

### `src/proxy.ts`

An `/admin` branch runs **before** the student logic, which would otherwise
bounce `/admin` to the student `/login`:

- `/admin/api/auth/*` — always pass through.
- `/admin/login` — admin token present → redirect `/admin`; else render.
- `/admin/*` — no admin token → redirect `/admin/login?callbackUrl=…`, with the
  relative-path-only validation the student login already applies
  (`src/app/(auth)/login/page.tsx:22`) so it is not an open redirect.
- Everything else — student logic untouched.

The admin token is read with:

```ts
getToken({
  req,
  secret: process.env.ADMIN_AUTH_SECRET,
  cookieName: "scholarscrib.admin-session",
  salt: "scholarscrib.admin-session",
})
```

**`salt` is not optional here.** `@auth/core` derives the decryption key from
secret *and* salt, and salt defaults to the cookie name
(`node_modules/@auth/core/jwt.d.ts:44-59`). Omitting it returns `null` silently,
which presents as an unexplained redirect loop rather than an error.

This check stays **optimistic only**. Next's proxy documentation states it
"should not be used as a full session management or authorization solution."
The wall is the data access layer.

## Authorization

### `src/lib/admin-session.ts`

The single place admin identity is resolved:

- `getAdminSession()` — read the admin JWT, then load `Admin` by id selecting
  `isActive`, `isOwner`, `email`, `username`. Returns `null` when the row is
  missing or deactivated.
- `requireAdminPage()` / `requireOwnerPage()` — redirect to `/admin/login` and
  `/admin` respectively.
- `requireAdminApi()` / `requireOwnerApi()` — return 401/403 `NextResponse`,
  replacing `requireAdmin()` in `src/lib/admin-guard.ts`, which stops consulting
  `User.role` entirely.

**Every `(console)` page calls `requireAdminPage()` itself.** The layout calls
it too, but only to resolve the name for the chrome — it is not the wall. This
closes the Partial Rendering hole described in the Problem.

### `src/lib/admin-access.ts`

The decisions themselves, as pure functions, following the pattern of
`src/lib/flashcard-ownership.ts` so they are unit-testable with no database:

```ts
canAccessConsole(admin: { isActive: boolean } | null): boolean
canManageAdmins(admin: { isActive: boolean; isOwner: boolean } | null): boolean

// Trims and lowercases, then classifies. Returns null for input that is
// neither a valid email nor a valid username, so callers cannot accidentally
// store an unvalidated identifier.
normalizeIdentifier(raw: string): { email: string } | { username: string } | null
```

`normalizeIdentifier` lives here rather than beside the credentials provider so
the create path and the sign-in path cannot drift apart — if they normalize
differently, an admin becomes uncreatable or unreachable.

## Admin management — `/admin/team`

Owner only, at every layer: the nav entry is hidden for non-owners, the page
calls `requireOwnerPage()`, and the routes call `requireOwnerApi()`.

- **List** — identifier, active status, created-by, created-at, last login.
- **Create** — one identifier field and a password. Nothing else. `isOwner` is
  written `false`; the created row records `createdById`.
- **Deactivate / reactivate** — a toggle per row. The owner's row renders no
  control and the server refuses the action regardless of what is posted.

Every create and status change writes an `AdminAudit` row.

**The sidebar's "Back to Dashboard" link is removed**
(`src/app/admin/layout.tsx:36`). An admin is no longer a student and has no
`/dashboard`. It is replaced by sign out. Viewing the student app now means
signing in as a student in the same browser — which the two-cookie design
permits, and is why concurrent sessions were chosen.

## Student side

Step 1 of the register wizard (`src/app/(auth)/register/page.tsx:23`) gains an
account-type choice: **Student** and **Teacher**, the latter rendered disabled
and badged *Coming soon*. The wizard's two-step shape is unchanged.

`registerUser()` takes a role, and the zod schema on `/api/auth/register`
accepts `STUDENT` only. A hand-crafted POST therefore cannot mint a teacher
through a door the UI has not opened.

## Bootstrap

`scripts/promote-admin.ts` is replaced by `scripts/create-admin.ts`:

```
npx tsx scripts/create-admin.ts <email-or-username> <password>
```

Sets `isOwner: true`, bcrypt at 12 rounds (matching `BCRYPT_ROUNDS` in
`src/lib/user-account.ts`), and refuses to create a second owner — enforced by
the partial unique index, not merely by the check in the script.

`ADMIN_AUTH_SECRET` is added to `.env` and documented in the README. Until it is
set, admin sign-in fails.

## Testing

`scripts/test-admin-access.mts`, wired into the `test` script in
`package.json`, covering the pure decisions:

- An active admin may access the console.
- A deactivated admin may not, cookie notwithstanding.
- A non-owner admin may not manage admins.
- An owner may.
- A `null` admin is refused by both.
- An identifier-normalization pair: mixed-case and padded input resolves to the
  same admin, and a username containing `@` is rejected.

What unit tests cannot establish, and must be verified by hand: the cookie
actually scoping to `/admin`, both sessions coexisting in one browser, the
redirect behaviour of the proxy branch, and the migration applying.

## Migration and rollout

`DIRECT_URL` does not resolve from this machine, so `prisma migrate` cannot
apply this. The migration SQL is written as one ordered block and pasted into
the Supabase SQL Editor; the migration directory is then reconciled so Prisma's
checksums match. Line endings must stay LF — `core.autocrlf=true` silently
drifts migration checksums.

Order of operations:

1. Apply the migration SQL (creates `Admin`, repoints `AdminAudit`).
2. Set `ADMIN_AUTH_SECRET`.
3. Run `create-admin.ts` to mint the owner.
4. Verify sign-in at `/admin/login`.

## Risks

- **The `AdminAudit` FK repoint is destructive** and hand-applied. It is the one
  step that cannot be trivially reversed. Existing audit rows are remapped to
  the owner, which is a loss of fidelity where multiple admins already acted —
  acceptable only because there is currently one.
- **`next-auth@5.0.0-beta.32` is a beta.** `basePath`, per-cookie `path`, and
  `getToken`'s `cookieName`/`salt` are verified against the installed type
  definitions but not at runtime. A minor bump could move them.
- **Two auth configurations now exist** and can drift. The distinct secrets and
  cookie names are what keep them independent; changing one without the other
  breaks sign-in in ways that present as silent redirects.
- **Lockout.** If `ADMIN_AUTH_SECRET` is lost or the owner password is
  forgotten, the only recovery is re-running the script against the database.
