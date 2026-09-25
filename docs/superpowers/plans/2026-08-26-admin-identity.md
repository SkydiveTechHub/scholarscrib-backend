# Separate Admin Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins their own database table, their own NextAuth instance, and their own cookie scoped to `/admin`, fully disjoint from student accounts.

**Architecture:** A new `Admin` model holds admin identity with a single login identifier (email *or* username). A second NextAuth instance mounted at `basePath: "/admin/api/auth"` issues a distinct, `/admin`-scoped cookie signed with its own secret, so an admin session and a student session coexist independently in one browser. Authorization decisions live in pure functions; the enforcing wall is a data-access layer called by every admin page and route handler, never a layout.

**Tech Stack:** Next.js 16.2.11 (App Router; middleware is `src/proxy.ts`), next-auth 5.0.0-beta.32, Prisma 6 + PostgreSQL (Supabase), bcryptjs, zod 4, node:test via tsx.

**Spec:** `docs/superpowers/specs/2026-08-26-admin-identity-design.md`

## Global Constraints

- **This is not the Next.js you know.** Read the relevant guide in `node_modules/next/dist/docs/` before writing routing, proxy, or auth code. Middleware is called **Proxy** and lives at `src/proxy.ts`.
- **Never trust a JWT for authorization.** Admin status is re-read from the database on every request. The token carries presentation data only. This matches the existing discipline in `src/lib/auth.ts`.
- **`prisma migrate` cannot reach the database from this machine.** `DIRECT_URL` does not resolve. Migration SQL is hand-applied through the Supabase SQL Editor, then reconciled.
- **Migration files must keep LF line endings.** `core.autocrlf=true` silently drifts Prisma migration checksums.
- **Cookie name and salt must match exactly** wherever the admin token is read: `scholarscrib.admin-session` for both.
- **`isOwner` is never read from a request body.** Only `scripts/create-admin.ts` writes `true`.
- **bcrypt cost factor is 12**, matching `BCRYPT_ROUNDS` in `src/lib/user-account.ts`.
- **Identifiers are stored trimmed and lowercased**, matching student email handling.
- Run `npx tsc --noEmit` and `npm run lint` before each commit.

---

### Task 1: Pure access decisions and identifier normalization

The whole authorization story as testable functions, with no database and no framework. Everything later in the plan depends on these names.

**Files:**
- Create: `src/lib/admin-access.ts`
- Create: `scripts/test-admin-access.mts`
- Modify: `package.json` (add the test file to the `test` script)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type AdminPrincipal = { id: string; isActive: boolean; isOwner: boolean }`
  - `canAccessConsole(admin: Pick<AdminPrincipal, "isActive"> | null): boolean`
  - `canManageAdmins(admin: Pick<AdminPrincipal, "isActive" | "isOwner"> | null): boolean`
  - `canDeactivate(target: { id: string; isOwner: boolean }, actor: Pick<AdminPrincipal, "id" | "isActive" | "isOwner">): boolean`
  - `type Identifier = { email: string } | { username: string }`
  - `normalizeIdentifier(raw: string): Identifier | null`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-access.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  canAccessConsole,
  canManageAdmins,
  canDeactivate,
  normalizeIdentifier,
} from "../src/lib/admin-access";

test("an active admin may open the console", () => {
  assert.equal(canAccessConsole({ isActive: true }), true);
});

test("a deactivated admin may not, cookie notwithstanding", () => {
  // Revocation has to bite on the next request, not at cookie expiry.
  assert.equal(canAccessConsole({ isActive: false }), false);
});

test("a missing admin may not", () => {
  assert.equal(canAccessConsole(null), false);
});

test("only the owner manages admins", () => {
  assert.equal(canManageAdmins({ isActive: true, isOwner: true }), true);
  assert.equal(canManageAdmins({ isActive: true, isOwner: false }), false);
  assert.equal(canManageAdmins(null), false);
});

test("a deactivated owner manages nothing", () => {
  assert.equal(canManageAdmins({ isActive: false, isOwner: true }), false);
});

test("the owner cannot be deactivated, including by themselves", () => {
  // The only recovery from this would be re-running the bootstrap script.
  const owner = { id: "a1", isActive: true, isOwner: true };
  assert.equal(canDeactivate({ id: "a1", isOwner: true }, owner), false);
});

test("the owner may deactivate a regular admin", () => {
  const owner = { id: "a1", isActive: true, isOwner: true };
  assert.equal(canDeactivate({ id: "a2", isOwner: false }, owner), true);
});

test("a regular admin may deactivate nobody", () => {
  const plain = { id: "a2", isActive: true, isOwner: false };
  assert.equal(canDeactivate({ id: "a3", isOwner: false }, plain), false);
});

test("an email identifier is trimmed and lowercased", () => {
  assert.deepEqual(normalizeIdentifier("  Michael@Example.COM "), {
    email: "michael@example.com",
  });
});

test("a username identifier is trimmed and lowercased", () => {
  assert.deepEqual(normalizeIdentifier("  Michael "), { username: "michael" });
});

test("a username may not contain @", () => {
  // Keeps the two namespaces disjoint: no username can shadow another
  // admin's email address.
  assert.equal(normalizeIdentifier("mich@el"), null);
});

test("a malformed email is rejected rather than stored as a username", () => {
  assert.equal(normalizeIdentifier("michael@"), null);
  assert.equal(normalizeIdentifier("@example.com"), null);
});

test("a too-short or oversized username is rejected", () => {
  assert.equal(normalizeIdentifier("ab"), null);
  assert.equal(normalizeIdentifier("a".repeat(33)), null);
});

test("an empty identifier is rejected", () => {
  assert.equal(normalizeIdentifier("   "), null);
});

test("usernames allow dot, dash and underscore only", () => {
  assert.deepEqual(normalizeIdentifier("mike_g.1-x"), { username: "mike_g.1-x" });
  assert.equal(normalizeIdentifier("mike g"), null);
  assert.equal(normalizeIdentifier("mike!"), null);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-access.mts`
Expected: FAIL — cannot find module `../src/lib/admin-access`.

- [ ] **Step 3: Write minimal implementation**

Create `src/lib/admin-access.ts`:

```ts
/**
 * Every admin authorization decision, as pure functions.
 *
 * These are deliberately database-free so they can be unit tested the way
 * `flashcard-ownership.ts` is. The database lookup that feeds them lives in
 * `admin-session.ts`; keeping the two apart is what makes the rules testable.
 */

export type AdminPrincipal = {
  id: string;
  isActive: boolean;
  isOwner: boolean;
};

/** Admins created through the console are always non-owners. */
export function canAccessConsole(
  admin: Pick<AdminPrincipal, "isActive"> | null,
): boolean {
  return admin?.isActive === true;
}

/** Creating and revoking admins is the owner's tier, not every admin's. */
export function canManageAdmins(
  admin: Pick<AdminPrincipal, "isActive" | "isOwner"> | null,
): boolean {
  return admin?.isActive === true && admin.isOwner === true;
}

/**
 * The owner row is never deactivatable — not by another admin, and not by the
 * owner themselves. Locking yourself out of your own console would leave the
 * bootstrap script as the only way back in.
 */
export function canDeactivate(
  target: { id: string; isOwner: boolean },
  actor: Pick<AdminPrincipal, "id" | "isActive" | "isOwner">,
): boolean {
  if (!canManageAdmins(actor)) return false;
  return !target.isOwner;
}

export type Identifier = { email: string } | { username: string };

const USERNAME_RE = /^[a-z0-9._-]{3,32}$/;
// Deliberately stricter than the RFC: one @, a dotted domain, no whitespace.
const EMAIL_RE = /^[^\s@]+@[^\s@.]+\.[^\s@]+$/;

/**
 * Resolves one typed value into the column it belongs in.
 *
 * Used by both the create path and the sign-in path so they cannot drift — if
 * they normalized differently, an admin would become uncreatable or
 * unreachable. Returns null for anything that is neither a valid email nor a
 * valid username, so a caller cannot store an unvalidated identifier.
 */
export function normalizeIdentifier(raw: string): Identifier | null {
  const value = raw.trim().toLowerCase();
  if (!value) return null;

  if (value.includes("@")) {
    return EMAIL_RE.test(value) ? { email: value } : null;
  }

  return USERNAME_RE.test(value) ? { username: value } : null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-access.mts`
Expected: PASS, 15 tests.

- [ ] **Step 5: Register the test in the suite**

In `package.json`, append ` scripts/test-admin-access.mts` to the end of the `test` script value (after `scripts/test-flashcard-ownership.mts`).

Run: `npm test`
Expected: the whole suite passes, including the new file.

- [ ] **Step 6: Commit**

```bash
git add src/lib/admin-access.ts scripts/test-admin-access.mts package.json
git commit -m "feat(admin): pure access rules and identifier normalization"
```

---

### Task 2: Schema and migration

**Files:**
- Modify: `prisma/schema.prisma` (add `Admin`, repoint `AdminAudit.actor`, comment `Role`)
- Create: `prisma/migrations/20260826000000_admin_identity/migration.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: the `Admin` Prisma model and `db.admin` client access used by Tasks 3, 4, 5 and 9.

**Ordering hazard, read before starting:** this task repoints `AdminAudit.actorId` at `Admin`, but the existing admin API routes still pass `User` ids to `recordAudit()` until Task 7. `recordAudit()` swallows its own failures by design (`src/lib/admin-audit.ts:31`), so those writes fail silently rather than breaking an edit. Do not treat a missing audit row between Task 2 and Task 7 as a bug.

- [ ] **Step 1: Add the model to the schema**

In `prisma/schema.prisma`, add above `model AdminAudit`:

```prisma
/// Admins are a separate identity from students — see
/// docs/superpowers/specs/2026-08-26-admin-identity-design.md.
/// No Account/Session relations: the admin auth instance uses the credentials
/// provider with a JWT strategy and never touches the Prisma adapter.
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

- [ ] **Step 2: Repoint AdminAudit and annotate Role**

In `prisma/schema.prisma`, change `AdminAudit` lines 840-841 from:

```prisma
  actorId   String
  actor     User     @relation(fields: [actorId], references: [id], onDelete: Cascade)
```

to:

```prisma
  actorId   String
  actor     Admin    @relation(fields: [actorId], references: [id], onDelete: Cascade)
```

Remove the now-dangling `adminAudits AdminAudit[]` relation field from `model User` (the `// Admin` block around line 837).

Above `enum Role`, add:

```prisma
/// STUDENT and TEACHER only. ADMIN is retained because Postgres has no
/// DROP VALUE, but it is never assigned — admins live in the Admin model.
```

- [ ] **Step 3: Write the migration SQL**

Create `prisma/migrations/20260826000000_admin_identity/migration.sql`. **Save with LF line endings.**

```sql
-- Admin identity: own table, own auth.

CREATE TABLE "Admin" (
    "id" TEXT NOT NULL,
    "email" TEXT,
    "username" TEXT,
    "passwordHash" TEXT NOT NULL,
    "isOwner" BOOLEAN NOT NULL DEFAULT false,
    "isActive" BOOLEAN NOT NULL DEFAULT true,
    "lastLoginAt" TIMESTAMP(3),
    "createdById" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "Admin_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "Admin_email_key" ON "Admin"("email");
CREATE UNIQUE INDEX "Admin_username_key" ON "Admin"("username");

-- Exactly one owner, ever. Prisma cannot express a partial unique index, and
-- without it a stray script run mints a second account that can grant access.
CREATE UNIQUE INDEX "Admin_single_owner" ON "Admin"("isOwner") WHERE "isOwner";

-- An admin must be reachable by at least one identifier.
ALTER TABLE "Admin" ADD CONSTRAINT "Admin_has_identifier"
    CHECK ("email" IS NOT NULL OR "username" IS NOT NULL);

ALTER TABLE "Admin" ADD CONSTRAINT "Admin_createdById_fkey"
    FOREIGN KEY ("createdById") REFERENCES "Admin"("id")
    ON DELETE SET NULL ON UPDATE CASCADE;

-- Repoint the audit trail. Existing rows hold User ids, which the new foreign
-- key would reject, so they are remapped to the owner first. This is the one
-- destructive step: where several admins already acted, that attribution is
-- lost. Acceptable only because there is currently one.
DELETE FROM "AdminAudit"
WHERE "actorId" NOT IN (SELECT "id" FROM "Admin");

ALTER TABLE "AdminAudit" DROP CONSTRAINT IF EXISTS "AdminAudit_actorId_fkey";

ALTER TABLE "AdminAudit" ADD CONSTRAINT "AdminAudit_actorId_fkey"
    FOREIGN KEY ("actorId") REFERENCES "Admin"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;
```

**A decision for the operator before applying:** the `DELETE` discards existing audit rows rather than remapping them, because the owner row does not exist yet at this point in the script — it is created by Task 3. If you would rather keep the history, run Task 3's script first against a manually-inserted owner and replace the `DELETE` with an `UPDATE "AdminAudit" SET "actorId" = '<owner-id>'`. Check the row count first:

```sql
SELECT COUNT(*) FROM "AdminAudit";
```

If it returns 0, the `DELETE` is a no-op and there is nothing to decide.

- [ ] **Step 4: Apply the migration by hand**

`prisma migrate` cannot reach the database from this machine. Paste the SQL above into the Supabase SQL Editor and run it. Report what it returns before continuing.

Then mark it applied locally so Prisma's history stays consistent:

```bash
npx prisma migrate resolve --applied 20260826000000_admin_identity
```

- [ ] **Step 5: Regenerate the client and typecheck**

```bash
npx prisma generate
npx tsc --noEmit
```

Expected: `tsc` reports errors in `src/lib/admin-guard.ts` and the six admin route files, because `AdminAudit.actor` no longer points at `User`. That is expected and is resolved in Tasks 5 and 7.

- [ ] **Step 6: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/20260826000000_admin_identity/
git commit -m "feat(admin): add Admin model and repoint the audit trail"
```

---

### Task 3: Bootstrap script

**Files:**
- Create: `scripts/create-admin.ts`
- Delete: `scripts/promote-admin.ts`

**Interfaces:**
- Consumes: `normalizeIdentifier` from `src/lib/admin-access.ts` (Task 1); the `Admin` model (Task 2).
- Produces: the owner row that Task 4's sign-in is verified against.

- [ ] **Step 1: Write the script**

Create `scripts/create-admin.ts`:

```ts
import { PrismaClient } from "@prisma/client";
import bcrypt from "bcryptjs";
import { normalizeIdentifier } from "../src/lib/admin-access";

// Mints the owner admin. This is the only way to open the console for the
// first time — there is no seeded account and no env-var backdoor.
//
//   npx tsx scripts/create-admin.ts michael@example.com "some password"
//   npx tsx scripts/create-admin.ts michael "some password"
//
// Every subsequent admin is created from /admin/team by the owner.

const prisma = new PrismaClient();
const BCRYPT_ROUNDS = 12;

async function main() {
  const [rawIdentifier, password] = process.argv.slice(2);

  if (!rawIdentifier || !password) {
    console.error(
      'Usage: npx tsx scripts/create-admin.ts <email-or-username> "<password>"',
    );
    process.exit(1);
  }

  if (password.length < 12) {
    // Stricter than the student minimum of 6: this account edits the question
    // bank, and nobody has to type it on a phone during registration.
    console.error("Password must be at least 12 characters.");
    process.exit(1);
  }

  const identifier = normalizeIdentifier(rawIdentifier);
  if (!identifier) {
    console.error(
      `"${rawIdentifier}" is neither a valid email nor a valid username ` +
        "(3-32 chars, a-z 0-9 . _ - only).",
    );
    process.exit(1);
  }

  const existingOwner = await prisma.admin.findFirst({
    where: { isOwner: true },
    select: { id: true, email: true, username: true },
  });

  if (existingOwner) {
    console.error(
      `An owner already exists (${existingOwner.email ?? existingOwner.username}). ` +
        "Create further admins from /admin/team.",
    );
    process.exit(1);
  }

  const admin = await prisma.admin.create({
    data: {
      ...identifier,
      passwordHash: await bcrypt.hash(password, BCRYPT_ROUNDS),
      isOwner: true,
    },
    select: { id: true, email: true, username: true },
  });

  console.log(`Owner created: ${admin.email ?? admin.username} (${admin.id})`);
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
```

- [ ] **Step 2: Verify the guard rails without touching the database**

```bash
npx tsx scripts/create-admin.ts
npx tsx scripts/create-admin.ts michael short
npx tsx scripts/create-admin.ts "mich@el" "a-long-enough-password"
```

Expected, in order: the usage line; "Password must be at least 12 characters."; the invalid-identifier message. All exit non-zero before opening a connection.

- [ ] **Step 3: Create the owner**

```bash
npx tsx scripts/create-admin.ts <your-email-or-username> "<a real password>"
```

Expected: `Owner created: … (…)`. Note the id.

- [ ] **Step 4: Verify the single-owner index bites**

Run the same command again with a different identifier.
Expected: `An owner already exists (…)`. Then confirm the database-level guard by attempting a direct insert with `isOwner = true` in the Supabase SQL Editor; expected: a unique violation on `Admin_single_owner`.

- [ ] **Step 5: Remove the superseded script**

```bash
git rm scripts/promote-admin.ts
```

It promotes a `User` to `ADMIN`, which no longer grants anything.

- [ ] **Step 6: Commit**

```bash
git add scripts/create-admin.ts
git commit -m "feat(admin): bootstrap script for the owner account"
```

---

### Task 4: The admin auth instance

**Files:**
- Create: `src/lib/admin-route.ts`
- Create: `src/lib/admin-auth.ts`
- Create: `src/app/admin/api/auth/[...nextauth]/route.ts`
- Modify: `.env` and `.env.example` (add `ADMIN_AUTH_SECRET`)
- Modify: `README.md` (document the variable)

**Interfaces:**
- Consumes: `normalizeIdentifier` (Task 1); the `Admin` model (Task 2); the owner row (Task 3).
- Produces: `adminAuth()` (used by Task 5) and `adminHandlers` (mounted in Step 5); and from `admin-route.ts` the constants `ADMIN_SESSION_COOKIE` and `ADMIN_AUTH_BASE_PATH`, used by Tasks 6 and 8. `adminSignIn` / `adminSignOut` are exported for completeness but have no consumer in this plan — the client uses `next-auth/react` inside `AdminSessionProvider` (Task 6). Do not go looking for their call sites.

**Why the constants get their own file:** `src/proxy.ts` needs the cookie name, and the proxy runs in a constrained runtime. Importing it from `admin-auth.ts` would drag the whole NextAuth instance — and through it `./db`, i.e. Prisma — into the proxy bundle, which cannot run there. `admin-route.ts` stays free of framework and database imports so both sides can share it.

- [ ] **Step 1: Read the docs**

Read `node_modules/next/dist/docs/01-app/02-guides/authentication.md`, sections "Setting cookies (recommended options)" and "Authorization". Do not skip this — the cookie options and the optimistic-vs-secure distinction drive the next three tasks.

- [ ] **Step 2: Generate and set the secret**

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
```

Add to `.env` as `ADMIN_AUTH_SECRET=<value>`, and add `ADMIN_AUTH_SECRET=` to `.env.example`. It **must differ** from `AUTH_SECRET`: sharing one would let a leaked student secret forge admin tokens and make the separation cosmetic.

- [ ] **Step 3: Create the shared constants**

Create `src/lib/admin-route.ts`:

```ts
/**
 * Admin routing constants, deliberately free of framework and database
 * imports so `src/proxy.ts` can use them. Importing these from
 * `admin-auth.ts` would pull NextAuth and Prisma into the proxy bundle,
 * which cannot run them.
 */

export const ADMIN_SESSION_COOKIE = "scholarscrib.admin-session";
export const ADMIN_AUTH_BASE_PATH = "/admin/api/auth";
```

- [ ] **Step 4: Write the instance**

Create `src/lib/admin-auth.ts`:

```ts
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import bcrypt from "bcryptjs";
import { db } from "./db";
import { normalizeIdentifier } from "./admin-access";
import { ADMIN_SESSION_COOKIE, ADMIN_AUTH_BASE_PATH } from "./admin-route";

/**
 * The admin authentication instance — entirely separate from `auth.ts`.
 *
 * Separate secret, separate cookie, separate base path. The two sessions
 * coexist in one browser because their cookies share no name and no scope, so
 * you can hold the console open in one tab and a student account in another.
 */

/** A working day. The console can rewrite the entire question bank. */
const SESSION_MAX_AGE = 60 * 60 * 8;

const useSecureCookies = process.env.NODE_ENV === "production";

/**
 * Scoped to /admin, which is why the admin API lives at /admin/api rather than
 * /api/admin: the browser must send this cookie to the API routes, and must
 * never send it to a student route.
 */
const cookieOptions = {
  httpOnly: true,
  sameSite: "lax",
  path: "/admin",
  secure: useSecureCookies,
} as const;

export const {
  handlers: adminHandlers,
  auth: adminAuth,
  signIn: adminSignIn,
  signOut: adminSignOut,
} = NextAuth({
  basePath: ADMIN_AUTH_BASE_PATH,
  secret: process.env.ADMIN_AUTH_SECRET,
  session: { strategy: "jwt", maxAge: SESSION_MAX_AGE },

  cookies: {
    sessionToken: { name: ADMIN_SESSION_COOKIE, options: cookieOptions },
    callbackUrl: {
      name: "scholarscrib.admin-callback-url",
      options: cookieOptions,
    },
    csrfToken: {
      name: "scholarscrib.admin-csrf-token",
      options: cookieOptions,
    },
  },

  pages: { signIn: "/admin/login" },

  providers: [
    Credentials({
      name: "admin-credentials",
      credentials: {
        identifier: { label: "Email or username", type: "text" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const rawIdentifier = credentials?.identifier;
        const password = credentials?.password;
        if (typeof rawIdentifier !== "string" || typeof password !== "string") {
          return null;
        }

        const identifier = normalizeIdentifier(rawIdentifier);
        if (!identifier) return null;

        const admin = await db.admin.findFirst({
          where: identifier,
          select: { id: true, passwordHash: true, isActive: true },
        });

        // Deactivated admins are refused at the door as well as at every
        // request, so revoking access does not depend on a cookie expiring.
        if (!admin || !admin.isActive) return null;
        if (!(await bcrypt.compare(password, admin.passwordHash))) return null;

        await db.admin.update({
          where: { id: admin.id },
          data: { lastLoginAt: new Date() },
        });

        return { id: admin.id };
      },
    }),
  ],

  callbacks: {
    async jwt({ token, user }) {
      if (user?.id) token.sub = user.id;
      return token;
    },

    // Carries the id only. isActive and isOwner are deliberately absent: they
    // are authorization facts, and authorization is re-read from the database
    // by admin-session.ts on every request.
    async session({ session, token }) {
      if (token.sub && session.user) session.user.id = token.sub;
      return session;
    },
  },
});
```

- [ ] **Step 5: Mount the handlers**

Create `src/app/admin/api/auth/[...nextauth]/route.ts`:

```ts
import { adminHandlers } from "@/lib/admin-auth";

export const { GET, POST } = adminHandlers;
```

Route handlers ignore layouts, so this is unaffected by the console guard added in Task 6.

- [ ] **Step 6: Verify the endpoint answers**

```bash
npm run dev
```

Then:

```bash
curl -s -i http://localhost:3000/admin/api/auth/csrf
```

Expected: `200`, a JSON body containing `csrfToken`, and a `Set-Cookie` header for `scholarscrib.admin-csrf-token` with `Path=/admin`. If `Path` is `/`, the cookie options did not apply — fix before continuing, because Task 7 depends on this scope.

- [ ] **Step 7: Document and commit**

Add `ADMIN_AUTH_SECRET` to the environment section of `README.md`, noting it must differ from `AUTH_SECRET` and that admin sign-in fails until it is set.

```bash
git add src/lib/admin-auth.ts src/lib/admin-route.ts src/app/admin/api/auth .env.example README.md
git commit -m "feat(admin): second auth instance scoped to /admin"
```

---

### Task 5: The authorization wall

**Files:**
- Create: `src/lib/admin-session.ts`
- Delete: `src/lib/admin-guard.ts`

**Interfaces:**
- Consumes: `adminAuth` (Task 4); `canAccessConsole`, `canManageAdmins`, `AdminPrincipal` (Task 1).
- Produces:
  - `getAdminPrincipal(): Promise<AdminPrincipal | null>`
  - `requireAdminPage(): Promise<AdminPrincipal>` — redirects
  - `requireOwnerPage(): Promise<AdminPrincipal>` — redirects
  - `requireAdminApi(): Promise<{ ok: true; actor: AdminPrincipal } | { ok: false; response: NextResponse }>`
  - `requireOwnerApi(): Promise<{ ok: true; actor: AdminPrincipal } | { ok: false; response: NextResponse }>`

- [ ] **Step 1: Write the module**

Create `src/lib/admin-session.ts`:

```ts
import { redirect } from "next/navigation";
import { NextResponse } from "next/server";
import { adminAuth } from "./admin-auth";
import { db } from "./db";
import {
  canAccessConsole,
  canManageAdmins,
  type AdminPrincipal,
} from "./admin-access";

/**
 * The single place admin identity is resolved.
 *
 * This — not the proxy and not a layout — is the wall. The proxy check is
 * optimistic and can be outrun by a stale cookie; Next's own docs state it
 * "should not be used as a full session management or authorization solution".
 * A layout check is skipped by Partial Rendering on client-side navigation
 * between admin routes, which is exactly how the previous implementation was
 * weak.
 */

export type AdminGuardResult =
  | { ok: true; actor: AdminPrincipal }
  | { ok: false; response: NextResponse };

/** Always reads the row, so deactivation takes effect on the next request. */
export async function getAdminPrincipal(): Promise<AdminPrincipal | null> {
  const session = await adminAuth();
  const id = session?.user?.id;
  if (!id) return null;

  return db.admin.findUnique({
    where: { id },
    select: { id: true, isActive: true, isOwner: true },
  });
}

export async function requireAdminPage(): Promise<AdminPrincipal> {
  const admin = await getAdminPrincipal();
  if (!canAccessConsole(admin)) redirect("/admin/login");
  return admin as AdminPrincipal;
}

export async function requireOwnerPage(): Promise<AdminPrincipal> {
  const admin = await getAdminPrincipal();
  if (!canAccessConsole(admin)) redirect("/admin/login");
  // Signed in but not the owner: back to the console, not to the login page.
  if (!canManageAdmins(admin)) redirect("/admin");
  return admin as AdminPrincipal;
}

export async function requireAdminApi(): Promise<AdminGuardResult> {
  const admin = await getAdminPrincipal();
  if (!canAccessConsole(admin)) {
    return {
      ok: false,
      response: NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    };
  }
  return { ok: true, actor: admin as AdminPrincipal };
}

export async function requireOwnerApi(): Promise<AdminGuardResult> {
  const admin = await getAdminPrincipal();
  if (!canAccessConsole(admin)) {
    return {
      ok: false,
      response: NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    };
  }
  if (!canManageAdmins(admin)) {
    return {
      ok: false,
      response: NextResponse.json(
        { error: "Owner access required" },
        { status: 403 },
      ),
    };
  }
  return { ok: true, actor: admin as AdminPrincipal };
}
```

- [ ] **Step 2: Delete the old guard**

```bash
git rm src/lib/admin-guard.ts
```

`tsc` will now fail in the six admin route files and `src/app/admin/layout.tsx`. Tasks 6 and 7 fix them; do not patch them here.

- [ ] **Step 3: Commit**

```bash
git add src/lib/admin-session.ts
git commit -m "feat(admin): data-access layer replacing the role-based guard"
```

---

### Task 6: Route restructure and the login page

**Files:**
- Delete: `src/app/admin/layout.tsx`
- Create: `src/app/admin/(console)/layout.tsx`
- Create: `src/app/admin/(entry)/login/page.tsx`
- Create: `src/components/admin/admin-session-provider.tsx`
- Create: `src/components/admin/admin-sign-out.tsx`
- Move: `src/app/admin/page.tsx`, `questions/`, `lessons/` into `src/app/admin/(console)/`
- Modify: each moved `page.tsx` to call `requireAdminPage()`
- Modify: `src/components/admin/admin-nav.tsx`, `src/lib/admin-nav.ts`

**Interfaces:**
- Consumes: `requireAdminPage` (Task 5); `ADMIN_AUTH_BASE_PATH` (Task 4).
- Produces: `/admin/login` as an unguarded route; every existing admin URL unchanged.

**The client basePath trap — read before Step 3.** `signIn()` and `signOut()` from `next-auth/react` do **not** accept a `basePath`: `SignInOptions` has no such field (`node_modules/next-auth/lib/client.d.ts:35-50`), and only `SessionProviderProps` does (line ~78). Called bare, they post to the default `/api/auth` — the **student** instance — looking for a provider that does not exist there, which surfaces as an opaque sign-in failure. Every admin client component that calls them must therefore sit inside a `SessionProvider` carrying the admin base path. The student app renders no `SessionProvider` at all (see the comment in `src/app/(dashboard)/layout.tsx`), so there is nothing to conflict with.

- [ ] **Step 1: Move the console pages**

```bash
mkdir -p "src/app/admin/(console)" "src/app/admin/(entry)"
git mv src/app/admin/page.tsx "src/app/admin/(console)/page.tsx"
git mv src/app/admin/questions "src/app/admin/(console)/questions"
git mv src/app/admin/lessons "src/app/admin/(console)/lessons"
```

Route groups do not appear in URLs, so `/admin`, `/admin/questions`, `/admin/lessons` are all unchanged.

- [ ] **Step 2: Delete the old layout and write the console layout**

```bash
git rm src/app/admin/layout.tsx
```

**It must be deleted, not kept.** A layout at `src/app/admin/` wraps *both* route groups, so leaving it would reapply its redirect to `/admin/login` and make the login page unreachable — the exact problem the split exists to solve.

Create `src/app/admin/(console)/layout.tsx` with the previous file's markup, with three changes: `requireAdminPage()` replaces the session-plus-`isAdminUser` pair; the "Back to Dashboard" link is replaced by a sign-out control; and the principal is passed to `AdminNav` so Task 9 can hide the owner-only entry.

```tsx
import { LuShield } from "react-icons/lu";
import { requireAdminPage } from "@/lib/admin-session";
import { AdminNav } from "@/components/admin/admin-nav";
import { AdminSignOut } from "@/components/admin/admin-sign-out";
import { AdminSessionProvider } from "@/components/admin/admin-session-provider";

export default async function ConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Chrome only. Each page calls requireAdminPage() itself — Partial Rendering
  // means this layout does not re-run on client-side navigation between admin
  // routes, so it cannot be the wall.
  const admin = await requireAdminPage();

  return (
    <div className="min-h-full">
      <a
        href="#admin-main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-card focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
      >
        Skip to content
      </a>
      <div className="flex min-h-full">
        <aside className="w-56 border-r border-border bg-card flex-shrink-0 hidden lg:block">
          <div className="flex items-center gap-2 px-4 py-5 border-b border-border">
            <LuShield className="w-5 h-5 text-primary" />
            <span className="font-bold text-foreground text-sm">Admin</span>
          </div>
          <AdminNav variant="sidebar" isOwner={admin.isOwner} />
          <div className="px-3 pt-3 border-t border-border mt-3">
            {/* AdminSignOut calls next-auth/react signOut, which needs the
                admin base path or it targets the student instance. */}
            <AdminSessionProvider>
              <AdminSignOut />
            </AdminSessionProvider>
          </div>
        </aside>

        <main id="admin-main" tabIndex={-1} className="flex-1 pb-24 lg:pb-0">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-8">
            {children}
          </div>
        </main>
      </div>

      <AdminNav variant="mobile" isOwner={admin.isOwner} />
    </div>
  );
}
```

- [ ] **Step 3: Add the session provider and the sign-out control**

Create `src/components/admin/admin-session-provider.tsx`:

```tsx
"use client";

import { SessionProvider } from "next-auth/react";
import { ADMIN_AUTH_BASE_PATH } from "@/lib/admin-route";

/**
 * Points the next-auth React client at the admin instance.
 *
 * Without this, signIn()/signOut() post to the default /api/auth — the student
 * instance — because SignInOptions has no basePath field. Wrap every admin
 * client component that calls them.
 */
export function AdminSessionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <SessionProvider basePath={ADMIN_AUTH_BASE_PATH}>{children}</SessionProvider>
  );
}
```

Then create `src/components/admin/admin-sign-out.tsx`:

```tsx
"use client";

import { LuLogOut } from "react-icons/lu";

// Signs out of the admin session only. The student cookie has a different name
// and a different scope, so a student session in another tab survives this.
// Must be rendered inside AdminSessionProvider.
export function AdminSignOut() {
  async function handleSignOut() {
    const { signOut } = await import("next-auth/react");
    await signOut({ callbackUrl: "/admin/login" });
  }

  return (
    <button
      type="button"
      onClick={handleSignOut}
      className="flex w-full items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium text-muted hover:text-foreground transition-colors"
    >
      <LuLogOut className="w-3.5 h-3.5" />
      Sign out
    </button>
  );
}
```

- [ ] **Step 4: Teach AdminNav about the owner tier**

In `src/components/admin/admin-nav.tsx`, change the signature to
`{ variant, isOwner }: { variant: "sidebar" | "mobile"; isOwner: boolean }`
and derive the list once at the top of the component:

```tsx
const items = ADMIN_NAV.filter((item) => !item.ownerOnly || isOwner);
```

Replace both `ADMIN_NAV.map` calls with `items.map`.

In `src/lib/admin-nav.ts`, widen the entries and add the Team link:

```ts
import {
  LuBookOpen,
  LuDatabase,
  LuLayoutDashboard,
  LuUpload,
  LuUsers,
} from "react-icons/lu";
import type { IconType } from "react-icons";

type AdminNavItem = {
  name: string;
  href: string;
  icon: IconType;
  /** Hidden from non-owners. The page and its routes also enforce this. */
  ownerOnly?: boolean;
};

// Every entry must have a page behind it. An earlier version listed Subjects,
// Users and Lessons with no routes — three links straight to a 404.
export const ADMIN_NAV: readonly AdminNavItem[] = [
  { name: "Overview", href: "/admin", icon: LuLayoutDashboard },
  { name: "Questions", href: "/admin/questions", icon: LuDatabase },
  { name: "Import", href: "/admin/questions/import", icon: LuUpload },
  { name: "Lessons", href: "/admin/lessons", icon: LuBookOpen },
  { name: "Team", href: "/admin/team", icon: LuUsers, ownerOnly: true },
];
```

- [ ] **Step 5: Add the per-page guard**

In each of `src/app/admin/(console)/page.tsx`, `questions/page.tsx`, `questions/import/page.tsx`, `questions/new/page.tsx`, `questions/[id]/edit/page.tsx`, `lessons/page.tsx`, `lessons/upload/page.tsx`, add the guard as the first statement:

```tsx
import { requireAdminPage } from "@/lib/admin-session";

export default async function AdminOverviewPage() {
  // The layout's check does not re-run on client-side navigation between admin
  // routes, so each page carries its own.
  await requireAdminPage();

  // …existing body unchanged
}
```

Several of these are client components (`"use client"` with hooks — `questions/page.tsx` and `questions/import/page.tsx` among them) and cannot `await`. For each, rename the existing file to a sibling component and add a server page that guards and renders it:

```bash
git mv "src/app/admin/(console)/questions/page.tsx" \
       "src/app/admin/(console)/questions/questions-client.tsx"
```

```tsx
// src/app/admin/(console)/questions/page.tsx
import { requireAdminPage } from "@/lib/admin-session";
import { QuestionsClient } from "./questions-client";

export default async function QuestionsPage() {
  await requireAdminPage();
  return <QuestionsClient />;
}
```

In the renamed file, change the default export to a named export (`export function QuestionsClient()`) and keep `"use client"` at the top.

- [ ] **Step 6: Write the login page**

Create `src/app/admin/(entry)/login/page.tsx`. Mirror the student login's structure (`src/app/(auth)/login/page.tsx`) — `Suspense` around a `useSearchParams` consumer, `redirect: false`, `router.push` on success — with one identifier field, no Google button, and no registration link.

```tsx
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { LuShield, LuLock, LuUser } from "react-icons/lu";

function AdminLoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Relative paths under /admin only — anything else is an open redirect or a
  // way to bounce an admin into the student app.
  const requested = searchParams.get("callbackUrl");
  const callbackUrl =
    requested && requested.startsWith("/admin") && !requested.startsWith("//")
      ? requested
      : "/admin";

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const { signIn } = await import("next-auth/react");
      const result = await signIn("admin-credentials", {
        identifier,
        password,
        redirect: false,
      });

      // One message for every failure — bad identifier, bad password, and
      // deactivated account are indistinguishable to someone guessing.
      if (result?.error) setError("Invalid credentials.");
      else {
        router.push(callbackUrl);
        router.refresh();
      }
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-sm flex-col justify-center px-6">
      <div className="mb-8 flex items-center gap-2.5">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary">
          <LuShield className="h-5 w-5 text-white" />
        </div>
        <h1 className="text-xl font-bold tracking-tight">Admin console</h1>
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-danger/25 bg-danger-soft p-3.5 text-sm font-medium text-danger">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-foreground">
            <LuUser className="h-4 w-4" /> Email or username
          </span>
          <input
            type="text"
            autoComplete="username"
            required
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            className="w-full rounded-xl border border-border bg-card px-4 py-3 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>

        <label className="block">
          <span className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-foreground">
            <LuLock className="h-4 w-4" /> Password
          </span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-xl border border-border bg-card px-4 py-3 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-xl bg-primary px-4 py-3 text-sm font-bold text-white transition-all disabled:opacity-60"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

export default function AdminLoginPage() {
  return (
    // The provider is what points signIn() at /admin/api/auth. Without it the
    // form posts to the student instance and "admin-credentials" is unknown.
    <AdminSessionProvider>
      <Suspense>
        <AdminLoginForm />
      </Suspense>
    </AdminSessionProvider>
  );
}
```

Add the import at the top of the file:

```tsx
import { AdminSessionProvider } from "@/components/admin/admin-session-provider";
```

- [ ] **Step 7: Verify by hand**

```bash
npm run dev
```

- Visit `/admin/login` — the form renders, with no admin sidebar around it.
- Visit `/admin` signed out — you are redirected to `/admin/login`.
- Sign in with the Task 3 credentials — you land on `/admin` with the sidebar. In the network tab, confirm the sign-in POST went to **`/admin/api/auth/callback/admin-credentials`**. If it went to `/api/auth/...`, the `SessionProvider` basePath is not applied — fix it before continuing, since nothing downstream will work.
- Click through `/admin/questions` → `/admin/lessons` — both render.
- In DevTools → Application → Cookies, confirm `scholarscrib.admin-session` has `Path=/admin`.
- Sign in as a student in the same browser, then reload `/admin` — both sessions are live at once.

- [ ] **Step 8: Commit**

```bash
npx tsc --noEmit && npm run lint
git add -A src/app/admin src/components/admin src/lib/admin-nav.ts
git commit -m "feat(admin): own login page and a guard on every console page"
```

---

### Task 7: Relocate the admin API

**Files:**
- Move: `src/app/api/admin/*` → `src/app/admin/api/*` (6 route files)
- Modify: 9 `fetch` call sites across 4 files
- Modify: `src/lib/admin-audit.ts` (new audit actions)

**Interfaces:**
- Consumes: `requireAdminApi` (Task 5).
- Produces: admin endpoints under `/admin/api/*`, reachable by the `/admin`-scoped cookie.

- [ ] **Step 1: Move the routes**

```bash
mkdir -p src/app/admin/api
git mv src/app/api/admin/questions src/app/admin/api/questions
git mv src/app/api/admin/lessons src/app/admin/api/lessons
rmdir src/app/api/admin
```

- [ ] **Step 2: Switch each route to the new guard**

In all six moved files, replace `import { requireAdmin } from "@/lib/admin-guard";` with `import { requireAdminApi } from "@/lib/admin-session";` and each `await requireAdmin()` with `await requireAdminApi()` — 11 call sites. The result shape is unchanged (`{ ok, actor, response }`), so the surrounding code stands. `guard.actor.id` is now an `Admin` id, which is what `AdminAudit.actorId` expects after Task 2.

- [ ] **Step 3: Update the callers**

Replace `/api/admin/` with `/admin/api/` at all 9 call sites. Note that Task 6 renamed two of these files, so search rather than trusting the paths:

```bash
grep -rn '/api/admin/' src --include=*.ts --include=*.tsx
```

The sites are 1 in the questions-import client, 4 in the questions-list client (originally `questions/page.tsx:157,240,270,321`), 2 in `src/components/admin/question-form.tsx:426,462`, and 2 in `src/components/admin/lesson-upload-form.tsx:123,196`.

Verify none remain:

```bash
grep -rn '"/api/admin\|`/api/admin' src --include=*.ts --include=*.tsx
```

Expected: only the prose reference in `src/lib/classroom.ts:81`, which should be updated to `/admin/api/lessons/import` for accuracy.

- [ ] **Step 4: Extend the audit actions**

In `src/lib/admin-audit.ts`, add to `AuditAction`:

```ts
  | "admin.create"
  | "admin.deactivate"
  | "admin.reactivate";
```

- [ ] **Step 5: Verify**

```bash
npx tsc --noEmit && npm run lint && npm test
npm run dev
```

In the browser, signed in as admin: load `/admin/questions`, edit a question, and confirm the network tab shows `/admin/api/questions` returning 200. Then confirm an `AdminAudit` row was written with your admin id:

```sql
SELECT "actorId", "action", "createdAt" FROM "AdminAudit" ORDER BY "createdAt" DESC LIMIT 5;
```

- [ ] **Step 6: Commit**

```bash
git add -A src/app src/lib/admin-audit.ts src/lib/classroom.ts src/components/admin
git commit -m "feat(admin): move the admin API under /admin so the cookie scope applies"
```

---

### Task 8: The proxy branch

**Files:**
- Modify: `src/lib/admin-route.ts` (created in Task 4)
- Create: `scripts/test-admin-route.mts`
- Modify: `src/proxy.ts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `ADMIN_SESSION_COOKIE` from `src/lib/admin-route.ts` (Task 4).
- Produces: `classifyAdminPath(pathname: string): "auth" | "login" | "console" | null`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-route.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyAdminPath } from "../src/lib/admin-route";

test("auth endpoints are always let through", () => {
  // Guarding these would make signing in impossible.
  assert.equal(classifyAdminPath("/admin/api/auth/csrf"), "auth");
  assert.equal(classifyAdminPath("/admin/api/auth/callback/admin-credentials"), "auth");
});

test("the login page is its own case", () => {
  assert.equal(classifyAdminPath("/admin/login"), "login");
});

test("everything else under /admin is console", () => {
  assert.equal(classifyAdminPath("/admin"), "console");
  assert.equal(classifyAdminPath("/admin/questions"), "console");
  assert.equal(classifyAdminPath("/admin/api/questions"), "console");
  assert.equal(classifyAdminPath("/admin/team"), "console");
});

test("non-admin paths are not classified", () => {
  assert.equal(classifyAdminPath("/dashboard"), null);
  assert.equal(classifyAdminPath("/login"), null);
  assert.equal(classifyAdminPath("/"), null);
});

test("a path merely starting with the letters admin is not admin", () => {
  // /administration must not inherit the console's rules.
  assert.equal(classifyAdminPath("/administration"), null);
  assert.equal(classifyAdminPath("/adminfoo"), null);
});

test("the login prefix does not swallow neighbouring routes", () => {
  assert.equal(classifyAdminPath("/admin/loginsomething"), "console");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-route.mts`
Expected: FAIL — cannot find module `../src/lib/admin-route`.

- [ ] **Step 3: Write the implementation**

Append to `src/lib/admin-route.ts`, below the constants added in Task 4:

```ts
/**
 * Which admin rule a path falls under. Extracted from the proxy so the
 * boundary cases — /administration, /admin/loginsomething — are testable
 * without booting Next.
 */
export type AdminPathKind = "auth" | "login" | "console";

export function classifyAdminPath(pathname: string): AdminPathKind | null {
  if (pathname !== "/admin" && !pathname.startsWith("/admin/")) return null;
  if (pathname.startsWith("/admin/api/auth")) return "auth";
  if (pathname === "/admin/login") return "login";
  return "console";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-route.mts`
Expected: PASS, 6 tests. Then append the file to the `test` script in `package.json`.

- [ ] **Step 5: Add the branch to the proxy**

In `src/proxy.ts`, add the imports and insert the admin branch immediately after `const { pathname, search } = req.nextUrl;` — **before** the student token is read, so `/admin` never reaches the student redirect:

```ts
import { classifyAdminPath, ADMIN_SESSION_COOKIE } from "@/lib/admin-route";
```

Both come from `admin-route.ts`, never from `admin-auth.ts` — importing the auth instance here would pull Prisma into the proxy bundle, which cannot run it.

```ts
  const adminPath = classifyAdminPath(pathname);
  if (adminPath) {
    if (adminPath === "auth") return NextResponse.next();

    // Optimistic only. Next's docs are explicit that Proxy "should not be used
    // as a full session management or authorization solution" — the wall is
    // admin-session.ts, which re-reads the row on every request.
    //
    // salt is not optional: @auth/core derives the decryption key from secret
    // AND salt, and salt defaults to the cookie name. Omitting it returns null
    // silently, which presents as an unexplained redirect loop.
    const adminToken = await getToken({
      req,
      secret: process.env.ADMIN_AUTH_SECRET,
      cookieName: ADMIN_SESSION_COOKIE,
      salt: ADMIN_SESSION_COOKIE,
    });

    if (adminPath === "login") {
      return adminToken
        ? NextResponse.redirect(new URL("/admin", req.url))
        : NextResponse.next();
    }

    if (adminToken) return NextResponse.next();

    if (pathname.startsWith("/admin/api/")) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const adminLogin = new URL("/admin/login", req.url);
    adminLogin.searchParams.set("callbackUrl", `${pathname}${search}`);
    return NextResponse.redirect(adminLogin);
  }
```

The existing `matcher` already covers `/admin`; leave it unchanged.

- [ ] **Step 6: Verify by hand**

```bash
npm run dev
```

- Signed out, visit `/admin/questions` → redirected to `/admin/login?callbackUrl=%2Fadmin%2Fquestions`.
- Sign in → you land on `/admin/questions`, not `/admin`.
- Signed in, visit `/admin/login` → redirected to `/admin`.
- Visit `/admin/login?callbackUrl=https://evil.example.com` and sign in → you land on `/admin`, not the external host.
- Signed out: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/admin/api/questions` → `401`.
- Confirm `/dashboard` and `/login` still behave as before.

- [ ] **Step 7: Commit**

```bash
npx tsc --noEmit && npm run lint && npm test
git add src/proxy.ts src/lib/admin-route.ts scripts/test-admin-route.mts package.json
git commit -m "feat(admin): route /admin traffic to the admin login, not the student one"
```

---

### Task 9: Admin management at /admin/team

**Files:**
- Create: `src/app/admin/(console)/team/page.tsx`
- Create: `src/components/admin/admin-team-manager.tsx`
- Create: `src/app/admin/api/admins/route.ts` (GET list, POST create)
- Create: `src/app/admin/api/admins/[id]/status/route.ts` (PATCH activate/deactivate)
- Modify: `src/lib/validators.ts`

**Interfaces:**
- Consumes: `requireOwnerPage`, `requireOwnerApi` (Task 5); `canDeactivate`, `normalizeIdentifier` (Task 1); `recordAudit` (Task 7).
- Produces: `createAdminSchema`, `adminStatusSchema` in `src/lib/validators.ts`.

- [ ] **Step 1: Add the schemas**

In `src/lib/validators.ts`, add near the other account schemas:

```ts
// isOwner is deliberately absent — it is written as a literal false by the
// create route and only ever true via scripts/create-admin.ts.
export const createAdminSchema = z.object({
  identifier: z.string().min(3, "Enter an email or a username"),
  password: z.string().min(12, "Password must be at least 12 characters"),
});

export const adminStatusSchema = z.object({
  isActive: z.boolean(),
});
```

And with the other inferred types at the bottom:

```ts
export type CreateAdminInput = z.infer<typeof createAdminSchema>;
export type AdminStatusInput = z.infer<typeof adminStatusSchema>;
```

- [ ] **Step 2: Write the list and create route**

Create `src/app/admin/api/admins/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { Prisma } from "@prisma/client";
import { db } from "@/lib/db";
import { requireOwnerApi } from "@/lib/admin-session";
import { normalizeIdentifier } from "@/lib/admin-access";
import { recordAudit } from "@/lib/admin-audit";
import { createAdminSchema } from "@/lib/validators";

export const dynamic = "force-dynamic";

const BCRYPT_ROUNDS = 12;

const LIST_SELECT = {
  id: true,
  email: true,
  username: true,
  isOwner: true,
  isActive: true,
  lastLoginAt: true,
  createdAt: true,
} as const;

export async function GET() {
  const guard = await requireOwnerApi();
  if (!guard.ok) return guard.response;

  const admins = await db.admin.findMany({
    select: LIST_SELECT,
    orderBy: [{ isOwner: "desc" }, { createdAt: "asc" }],
  });

  return NextResponse.json({ admins });
}

export async function POST(req: NextRequest) {
  const guard = await requireOwnerApi();
  if (!guard.ok) return guard.response;

  const parsed = createAdminSchema.safeParse(await req.json());
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  const identifier = normalizeIdentifier(parsed.data.identifier);
  if (!identifier) {
    return NextResponse.json(
      {
        error:
          "Enter a valid email, or a username of 3-32 characters using a-z, 0-9, dot, dash or underscore.",
      },
      { status: 400 },
    );
  }

  try {
    const admin = await db.admin.create({
      data: {
        ...identifier,
        passwordHash: await bcrypt.hash(parsed.data.password, BCRYPT_ROUNDS),
        // Never from the request body.
        isOwner: false,
        createdById: guard.actor.id,
      },
      select: LIST_SELECT,
    });

    await recordAudit({
      actorId: guard.actor.id,
      action: "admin.create",
      entity: "Admin",
      entityId: admin.id,
      summary: `Created admin ${admin.email ?? admin.username}`,
    });

    return NextResponse.json({ admin }, { status: 201 });
  } catch (error) {
    if (
      error instanceof Prisma.PrismaClientKnownRequestError &&
      error.code === "P2002"
    ) {
      return NextResponse.json(
        { error: "That email or username is already taken" },
        { status: 409 },
      );
    }
    throw error;
  }
}
```

- [ ] **Step 3: Write the status route**

Create `src/app/admin/api/admins/[id]/status/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { requireOwnerApi } from "@/lib/admin-session";
import { canDeactivate } from "@/lib/admin-access";
import { recordAudit } from "@/lib/admin-audit";
import { adminStatusSchema } from "@/lib/validators";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = await requireOwnerApi();
  if (!guard.ok) return guard.response;

  const { id } = await params;

  const parsed = adminStatusSchema.safeParse(await req.json());
  if (!parsed.success) {
    return NextResponse.json({ error: "Validation failed" }, { status: 400 });
  }

  const target = await db.admin.findUnique({
    where: { id },
    select: { id: true, email: true, username: true, isOwner: true },
  });
  if (!target) {
    return NextResponse.json({ error: "Admin not found" }, { status: 404 });
  }

  // The owner is never deactivatable, whatever the client posts.
  if (!parsed.data.isActive && !canDeactivate(target, guard.actor)) {
    return NextResponse.json(
      { error: "The owner account cannot be deactivated" },
      { status: 403 },
    );
  }

  await db.admin.update({
    where: { id },
    data: { isActive: parsed.data.isActive },
  });

  await recordAudit({
    actorId: guard.actor.id,
    action: parsed.data.isActive ? "admin.reactivate" : "admin.deactivate",
    entity: "Admin",
    entityId: target.id,
    summary: `${parsed.data.isActive ? "Reactivated" : "Deactivated"} admin ${
      target.email ?? target.username
    }`,
  });

  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 4: Write the page and the client manager**

Create `src/app/admin/(console)/team/page.tsx`:

```tsx
import { requireOwnerPage } from "@/lib/admin-session";
import { db } from "@/lib/db";
import { PageHeader } from "@/components/ui/page-header";
import { AdminTeamManager } from "@/components/admin/admin-team-manager";

export const dynamic = "force-dynamic";

export default async function AdminTeamPage() {
  const owner = await requireOwnerPage();

  const admins = await db.admin.findMany({
    select: {
      id: true,
      email: true,
      username: true,
      isOwner: true,
      isActive: true,
      lastLoginAt: true,
      createdAt: true,
    },
    orderBy: [{ isOwner: "desc" }, { createdAt: "asc" }],
  });

  return (
    <div>
      <PageHeader
        title="Team"
        description="Create admin accounts and hand the credentials over yourself. There is no invite email."
      />
      <AdminTeamManager
        initialAdmins={admins.map((a) => ({
          ...a,
          lastLoginAt: a.lastLoginAt?.toISOString() ?? null,
          createdAt: a.createdAt.toISOString(),
        }))}
        currentAdminId={owner.id}
      />
    </div>
  );
}
```

Create `src/components/admin/admin-team-manager.tsx`:

```tsx
"use client";

import { useState } from "react";
import { StatusBanner } from "@/components/admin/status-banner";

export type TeamAdmin = {
  id: string;
  email: string | null;
  username: string | null;
  isOwner: boolean;
  isActive: boolean;
  lastLoginAt: string | null;
  createdAt: string;
};

const label = (a: TeamAdmin) => a.email ?? a.username ?? a.id;
const CELL = "px-4 py-2.5 text-sm";

export function AdminTeamManager({
  initialAdmins,
  currentAdminId,
}: {
  initialAdmins: TeamAdmin[];
  currentAdminId: string;
}) {
  const [admins, setAdmins] = useState(initialAdmins);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  // Shown once after creation. Nothing can recover the password later — it is
  // stored only as a bcrypt hash.
  const [created, setCreated] = useState<{ id: string; password: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setCreated(null);

    try {
      const res = await fetch("/admin/api/admins", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier, password }),
      });
      const data = await res.json();

      if (!res.ok) {
        setError(data.error ?? "Could not create that admin.");
        return;
      }

      setAdmins((prev) => [...prev, data.admin]);
      setCreated({ id: label(data.admin), password });
      setIdentifier("");
      setPassword("");
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(admin: TeamAdmin) {
    setBusy(true);
    setError("");

    try {
      const res = await fetch(`/admin/api/admins/${admin.id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isActive: !admin.isActive }),
      });

      if (!res.ok) {
        const data = await res.json();
        setError(data.error ?? "Could not update that admin.");
        return;
      }

      setAdmins((prev) =>
        prev.map((a) =>
          a.id === admin.id ? { ...a, isActive: !admin.isActive } : a,
        ),
      );
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      {error && <StatusBanner tone="danger" title={error} />}

      {created && (
        <StatusBanner
          tone="success"
          title={`Admin ${created.id} created`}
          message={`Password: ${created.password} — copy it now. It is stored only as a hash and cannot be shown again.`}
        />
      )}

      <form
        onSubmit={handleCreate}
        className="flex flex-col gap-3 rounded-lg border border-border-strong bg-card p-4 sm:flex-row sm:items-end"
      >
        <label className="flex-1">
          <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-muted">
            Email or username
          </span>
          <input
            type="text"
            required
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            className="w-full rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>

        <label className="flex-1">
          <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-muted">
            Password
          </span>
          <input
            type="text"
            required
            minLength={12}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>

        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
        >
          Create admin
        </button>
      </form>

      <div className="overflow-hidden rounded-lg border border-border-strong bg-card">
        <table className="w-full">
          <caption className="sr-only">Admin accounts</caption>
          <thead>
            <tr className="border-b border-border-strong text-[11px] font-semibold uppercase tracking-wider text-muted">
              <th scope="col" className="px-4 py-2.5 text-left">Admin</th>
              <th scope="col" className="px-4 py-2.5 text-left">Status</th>
              <th scope="col" className="px-4 py-2.5 text-left">Last login</th>
              <th scope="col" className="px-4 py-2.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {admins.map((admin) => (
              <tr key={admin.id} className="border-b border-border-strong last:border-0">
                <td className={CELL}>
                  <span className="font-medium text-foreground">{label(admin)}</span>
                  {admin.isOwner && (
                    <span className="ml-2 rounded bg-secondary px-1.5 py-0.5 text-[10px] font-semibold uppercase text-muted">
                      Owner
                    </span>
                  )}
                  {admin.id === currentAdminId && (
                    <span className="ml-2 text-xs text-muted">(you)</span>
                  )}
                </td>
                <td className={CELL}>
                  <span className={admin.isActive ? "text-success" : "text-muted"}>
                    {admin.isActive ? "Active" : "Deactivated"}
                  </span>
                </td>
                <td className={`${CELL} text-muted`}>
                  {admin.lastLoginAt
                    ? new Date(admin.lastLoginAt).toLocaleDateString()
                    : "Never"}
                </td>
                <td className={`${CELL} text-right`}>
                  {/* The owner row carries no control. The route refuses it too. */}
                  {admin.isOwner ? (
                    <span className="text-xs text-muted">—</span>
                  ) : (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => toggleActive(admin)}
                      className="rounded-lg border border-border-strong px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-secondary disabled:opacity-60"
                    >
                      {admin.isActive ? "Deactivate" : "Reactivate"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

Check `src/components/admin/status-banner.tsx` for the exact `tone` values it accepts before using `"danger"` — match whatever it actually declares rather than adding a new one. Do not use `window.confirm` or `alert`; they block the page.

- [ ] **Step 5: Verify by hand**

```bash
npm run dev
```

As the owner:
- `/admin/team` renders and "Team" appears in the sidebar.
- Create an admin with a username; it appears in the list.
- Creating the same identifier again → "already taken".
- Creating with an 8-character password → the validation message, no row created.
- Your own row shows an Owner badge and **no** deactivate control.

Then, in a private window, sign in as the new admin:
- `/admin` works; **"Team" is absent** from the sidebar.
- Visiting `/admin/team` directly → redirected to `/admin`.
- `curl` a POST to `/admin/api/admins` with that session → `403`.

Back as owner, deactivate the new admin. In the private window, reload `/admin` → redirected to `/admin/login` on the next request. Then attempt the owner deactivation directly, which the UI does not offer:

```bash
curl -s -X PATCH http://localhost:3000/admin/api/admins/<owner-id>/status \
  -H 'Content-Type: application/json' -d '{"isActive":false}' \
  -H 'Cookie: scholarscrib.admin-session=<owner cookie>'
```

Expected: `403`, and the owner row unchanged.

- [ ] **Step 6: Commit**

```bash
npx tsc --noEmit && npm run lint && npm test
git add src/app/admin src/components/admin/admin-team-manager.tsx src/lib/validators.ts
git commit -m "feat(admin): owner-only admin management at /admin/team"
```

---

### Task 10: Student and teacher account types

**Files:**
- Modify: `src/lib/validators.ts` (`registerSchema`)
- Modify: `src/lib/user-account.ts` (`RegisterInput`, `registerUser`)
- Modify: `src/app/api/auth/register/route.ts`
- Modify: `src/app/(auth)/register/page.tsx`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `role` on the registration payload.

- [ ] **Step 1: Widen the schema**

In `src/lib/validators.ts`, add to `registerSchema` as the first field:

```ts
  // TEACHER is offered in the UI as "Coming soon" and is deliberately not
  // accepted here — a hand-crafted POST must not mint a teacher through a door
  // the interface has not opened.
  role: z.literal("STUDENT").default("STUDENT"),
```

- [ ] **Step 2: Thread it through registration**

In `src/lib/user-account.ts`, add `role: Prisma.UserCreateInput["role"]` to `RegisterInput`, and in `registerUser` replace the hardcoded `role: "STUDENT"` with `role: input.role`.

In `src/app/api/auth/register/route.ts`, destructure `role` from `parsed.data` and pass it to `registerUser`.

- [ ] **Step 3: Add the choice to the wizard**

In `src/app/(auth)/register/page.tsx`, add `role: "STUDENT" as string` to the `form` state object (alongside `classLevel` and `track` around line 38), and include `role: form.role` in the `fetch` body at line 90.

Then add this block inside `{step === 1 && (…)}`, above the name fields. It reuses the existing `optionClass` helper and `aria-pressed` pattern used by the class-level and track pickers:

```tsx
<div>
  <label className="mb-2 block text-sm font-semibold text-foreground">
    I am signing up as
  </label>
  <div className="grid grid-cols-2 gap-2">
    <button
      type="button"
      aria-pressed={form.role === "STUDENT"}
      onClick={() => update("role", "STUDENT")}
      className={optionClass(form.role === "STUDENT")}
    >
      Student
    </button>

    {/* Teacher capabilities are not built yet. Disabled rather than hidden so
        the path is visibly planned — and the API rejects TEACHER regardless. */}
    <button
      type="button"
      disabled
      aria-disabled="true"
      className={`${optionClass(false)} cursor-not-allowed opacity-50`}
    >
      Teacher
      <span className="ml-1.5 rounded bg-secondary px-1.5 py-0.5 text-[10px] font-semibold uppercase text-muted">
        Coming soon
      </span>
    </button>
  </div>
</div>
```

Do not add a third step, and do not branch the class/track step — teacher selection is unreachable, so there is no second path to build yet.

- [ ] **Step 4: Verify by hand**

```bash
npm run dev
```

- `/register` step 1 shows both options; Teacher cannot be clicked or focused into a selected state.
- Registering as a student still works end to end and lands on `/login?registered=true`.
- Confirm the row: `SELECT "email", "role" FROM "User" ORDER BY "createdAt" DESC LIMIT 1;` → `STUDENT`.
- A crafted `POST /api/auth/register` with `"role":"TEACHER"` → `400 Validation failed`.

- [ ] **Step 5: Commit**

```bash
npx tsc --noEmit && npm run lint && npm test
git add src/lib/validators.ts src/lib/user-account.ts src/app/api/auth/register/route.ts "src/app/(auth)/register/page.tsx"
git commit -m "feat(auth): ask for account type at registration, teacher coming soon"
```

---

## Final verification

- [ ] `npm test` — the full suite, including `test-admin-access.mts` and `test-admin-route.mts`.
- [ ] `npx tsc --noEmit` and `npm run lint` — clean.
- [ ] `npm run build` — succeeds.
- [ ] `grep -rn "admin-guard\|promote-admin\|isAdminUser" src scripts` — no results.
- [ ] `grep -rn '"/api/admin' src` — no results.
- [ ] In one browser: an admin session on `/admin` and a student session on `/dashboard`, simultaneously, neither disturbing the other.
- [ ] Signing out of the admin console leaves the student session intact.
