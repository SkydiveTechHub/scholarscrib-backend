# Admin Console Structure — Design

**Date:** 2026-08-27
**Status:** Approved, awaiting implementation plan
**Scope:** Admin shell/IA, student management, subscription tier seam, audit log viewer

---

## Problem

The admin console works but has no structure. Five flat nav entries sit at the
same level regardless of importance — `Import` is a peer of `Questions`, which it
belongs inside. There is no user management at all: the people the product exists
for cannot be found, supported, or suspended from the console. `AdminAudit` rows
are written on every mutation and never displayed, so no admin action is
answerable. And subscription tiers, which the business depends on, exist nowhere
in the schema or the code.

Underneath that, two incompatible rendering patterns have grown side by side:

- **Lessons** (`src/app/admin/(console)/lessons/page.tsx`) is server-rendered.
  `searchParams` → `normaliseFilter()` (pure, testable) → one server query →
  server-rendered table. Filters are shareable and back-button-correct.
- **Questions** (`src/app/admin/(console)/questions/questions-client.tsx`) is a
  722-line Client Component that fetches its own API and holds filter and
  pagination state in React. Filters are not shareable and the pure logic is not
  reachable from a unit test.

The divergence — not the visual design — is why the console does not feel like one
product. Table styling is duplicated three times (each file declares its own
`TH_CLS`), empty states are written three different ways, and pagination exists
in one place as client state and nowhere else.

## Non-goals

- **Rewriting `questions-client.tsx`.** It adopts the shared primitives where that
  is a drop-in and moves under the new shell, but a 722-line rewrite is its own
  round. Called out here so it does not creep in.
- **Billing integration.** No provider, no webhooks, no `Subscription` table. See
  "Deferred" below.
- **Defining tier entitlements.** What FREEMIUM / STANDARD / PREMIUM each unlock
  is a later decision. This round builds the seam that decision plugs into.
- **Curriculum (Subject/Topic) management.** A later round.
- **An admin role enum.** The existing owner/admin split is retained; see
  [Permissions](#permissions).
- **Admin-triggered password reset.** Blocked on infrastructure that does not
  exist; replaced by force sign-out. See
  [Force sign-out](#force-sign-out-not-password-reset).

---

## Architecture

### Rendering standard: server-first

Every new section follows the Lessons shape, not the Questions shape:

```
searchParams → normalise*Filter() → one server query → server-rendered table
               (pure, in a lib,
                unit-tested)
```

Client Components are used only where interaction genuinely requires them: a
confirm dialog, a debounced search input that writes to the URL. State that
describes *what is being viewed* lives in the URL, never in React state. This is
what makes filters shareable, the back button correct, and the filter logic
testable without booting Next.

### Separation of pure logic from data access

New libs mirror the split the codebase already uses (`admin-question.ts` /
`admin-question-data.ts`, `admin-access.ts` / `admin-session.ts`):

- `*.ts` — pure: validation, normalisation, display shaping, permission
  predicates. No Prisma import. Unit-tested by an `.mts` script.
- `*-data.ts` — Prisma queries only.

The rule that makes `admin-access.ts` testable is the rule applied here.

### Navigation as grouped data

`src/lib/admin-nav.ts` changes from a flat `readonly AdminNavItem[]` to:

```ts
type AdminNavItem = {
  name: string;
  href: string;
  icon: IconType;
  /** Hidden from non-owners. The page and its routes also enforce this. */
  ownerOnly?: boolean;
};

type AdminNavGroup = {
  label: string;
  items: readonly AdminNavItem[];
};

export const ADMIN_NAV_GROUPS: readonly AdminNavGroup[];
```

Resulting structure:

| Group | Item | Href | Gate |
|---|---|---|---|
| OVERVIEW | Dashboard | `/admin` | admin |
| CONTENT | Questions | `/admin/questions` | admin |
| CONTENT | Lessons | `/admin/lessons` | admin |
| PEOPLE | Students | `/admin/students` | admin |
| PEOPLE | Team | `/admin/team` | `ownerOnly` |
| SYSTEM | Audit log | `/admin/audit` | admin |

`Import` is removed from the nav and becomes an action in the Questions page
header; the route `/admin/questions/import` is unchanged. Lesson upload is
likewise an action in the Lessons page header.

**Curriculum and Billing are not added.** The existing comment in
`admin-nav.ts` records that an earlier version listed routeless links — three
links straight to a 404. Every entry must have a page behind it.

`AdminNav` renders group labels in the sidebar variant. The mobile variant
renders a curated four-item subset rather than every link, because grouped
navigation does not fit a bottom bar: **Overview**, **Questions**, **Students**,
and **More**. "More" is not a route — it opens a bottom sheet listing every
remaining item the actor may see, grouped by the same labels as the sidebar, so
nothing is unreachable on mobile. It is marked active whenever the current path
matches one of the items it contains. The
existing exact-match active rule is retained — prefix matching would light
"Questions" while the admin is on "Questions › Import".

### Shared primitives

Extracted into `src/components/admin/`:

| Component | Replaces |
|---|---|
| `AdminTable` | The header / zebra / `tabular-nums` treatment duplicated across Overview, Lessons and Questions, each with its own `TH_CLS` |
| `FilterBar` | Generalises `lesson-filter-bar.tsx` into URL-writing select and search controls |
| `Pagination` | Server-side, URL-driven (`?page=`), replacing the Questions client's local pagination state |
| `EmptyState` | The "choose a subject" / "no questions yet" / "no students match" cases, currently written three ways |
| `DetailShell` | Breadcrumb + title + action row, for record detail pages |

`PageHeader` (`src/components/ui/page-header.tsx`), `StatusBanner` and
`ConfirmDialog` are already shared and are reused as-is.

---

## Subscription tiers

### Schema

```prisma
enum SubscriptionTier {
  FREEMIUM
  STANDARD
  PREMIUM
}

model User {
  // ...existing fields
  tier          SubscriptionTier @default(FREEMIUM)
  tierUpdatedAt DateTime?

  @@index([tier])
}
```

### The seam — `src/lib/subscription.ts`

Database-free, unit-tested, the single place tier logic lives:

```ts
export const TIER_ORDER: Record<SubscriptionTier, number>;   // FREEMIUM 0 → PREMIUM 2
export const TIER_LABELS: Record<SubscriptionTier, string>;

/** The single predicate every future gate calls. */
export function hasAtLeast(
  user: { tier: SubscriptionTier },
  required: SubscriptionTier,
): boolean;

/** Label + badge tone for display. */
export function describeTier(user: { tier: SubscriptionTier }): {
  label: string;
  tone: "neutral" | "info" | "success";
};
```

**Entitlements are deliberately undefined.** When the decision is made about what
each tier unlocks, it becomes a table in this one file. Call sites — which only
ever ask `hasAtLeast(user, "STANDARD")` — do not change.

### Manual override as the pre-billing stopgap

Until a provider is wired, `POST /admin/api/students/[id]/tier` lets an admin set
a tier directly. This is how early customers, comped accounts and support
corrections are handled. Every change is audited (`student.tier`) with the
before and after tier in the summary, and stamps `tierUpdatedAt`.

### Deferred: the `Subscription` table

A separate `Subscription` model is **not** created this round. Its shape is
determined entirely by the provider — Paystack and Stripe differ in identifiers,
webhook semantics and renewal representation — so a speculative table would be
migrated twice. `User.tier` + `tierUpdatedAt` carries everything the admin
console needs now.

When billing lands, `Subscription` becomes the source of truth that *writes*
`User.tier`. `User.tier` stays as the denormalised read column, so every gate
written against `hasAtLeast` keeps working unchanged.

---

## Student management

### Schema

```prisma
model User {
  // ...existing fields
  isActive        Boolean   @default(true)
  suspendedAt     DateTime?
  suspendedReason String?
  /** Tokens issued before this instant are rejected. Powers force sign-out. */
  sessionsValidFrom DateTime?

  @@index([isActive])
}
```

### Suspension enforcement — two touch-points

Student sessions are JWT-based with a 60-second profile-refresh TTL
(`PROFILE_TTL_MS`, `src/lib/auth.ts:27`). Gating `authorize()` alone would block
new sign-ins while leaving an already-signed-in suspended student browsing on a
live token until it expired. Both points are therefore required:

1. **`authorize()`** returns `null` when `!user.isActive` — new sign-ins blocked
   immediately.
2. **`isActive` joins `PROFILE_SELECT`**, and the `jwt` callback invalidates the
   token when it comes back `false` — an active session is cut off within the
   60-second TTL.

`sessionsValidFrom` joins `PROFILE_SELECT` on the same refresh and is compared
against the token's issued-at claim, so force sign-out reuses this one
invalidation path rather than adding a second mechanism.

**A third case shares the same path: the user no longer exists.** When the
profile lookup returns no row, the callback returns `null` too. Without this, a
deleted student's token would stay valid until it expired naturally while a
merely suspended student's is revoked within the TTL — the more severe action
getting the weaker enforcement. The lookup returning nothing is authoritative:
a database failure throws and is caught, keeping the cached profile, so an
absent row means the account is genuinely gone rather than temporarily
unreachable. This is what makes deletion (below) take effect on live sessions.

The exact NextAuth v5 token-invalidation contract must be confirmed against
`node_modules/next/dist/docs/` and the next-auth beta types before
implementation, per `AGENTS.md`.

The existing `catch` in the `jwt` callback — which keeps the cached profile
rather than throwing a `JWTSessionError` that would take the whole request down —
must be preserved. A database failure must not suspend every signed-in student.

### Permissions

No role enum. `admin-access.ts` gains four pure predicates in the shape of the
existing `canDeactivate`:

| Predicate | Requires |
|---|---|
| `canEditStudent(actor)` | any active admin |
| `canSuspendStudent(actor)` | any active admin |
| `canDeleteStudent(actor)` | active **and** owner |
| `canForceSignOutStudent(actor)` | active **and** owner |

Owner-only routes use the existing `requireOwnerApi()` / `requireOwnerPage()`.
Nav and UI hide what an actor cannot do, and the routes enforce it independently
— hiding a control is presentation, never authorization.

### Routes

| Path | Method | Guard |
|---|---|---|
| `/admin/students` | page | admin |
| `/admin/students/[id]` | page | admin |
| `/admin/api/students/[id]` | `PATCH` | admin |
| `/admin/api/students/[id]/status` | `POST` | admin |
| `/admin/api/students/[id]/tier` | `POST` | admin |
| `/admin/api/students/[id]/force-signout` | `POST` | **owner** |
| `/admin/api/students/[id]` | `DELETE` | **owner** |

### Libs

- `src/lib/admin-student.ts` — pure. Filter normalisation, profile-edit
  validation, display shaping, suspension-reason validation.
- `src/lib/admin-student-data.ts` — Prisma. List query with filters and
  pagination, detail query with activity aggregates, deletion-impact counts.

### List page

Columns: Name · Email/phone · Class · Track · **Plan** · Status · Joined · Last
active.

Filters, all URL-driven: search (name, email, phone), class level, track, tier,
status (active / suspended). Server-side pagination via `?page=`.

`Plan` renders through `describeTier()`, so the column is correct the moment
tiers carry real data.

### Detail page

- **Profile** — name, email, phone, class level, track, school, state.
  Inline-editable by any active admin.

  **Known limitation: fields can be corrected but not cleared.** The optional
  fields are `.optional()` rather than nullable, and the form omits empty
  inputs rather than sending them, so a blank input means "leave unchanged",
  not "erase". An admin can change a wrong email to a right one, but cannot
  remove an email from an account that should be phone-only. This is a
  deliberate trade: the same behaviour that prevents a half-filled form from
  silently wiping stored contact details also prevents an intentional erase.
  Clearing needs nullable fields plus a form that distinguishes absent from
  emptied, and belongs in a later round with the delete-vs-blank UI thought
  through.
- **Plan** — current tier, `tierUpdatedAt`, manual override control.
- **Activity** — attempts, topic mastery summary, flashcard activity, last seen.
  Read-only aggregates.
- **Danger zone** — suspend / reactivate (any active admin); force sign-out and
  delete account (owner only, hidden otherwise).

### Deletion semantics

A student delete cascades across progress, attempts, responses, mastery,
flashcards and learning events. Following the precedent already set by
`/admin/api/questions/[id]/usage`, the confirm dialog first shows what would be
destroyed — actual row counts per relation, not a generic warning — and requires
type-to-confirm. Suspension is offered in the same dialog as the reversible
alternative.

### Force sign-out, not password reset

Admin-triggered password reset was in the approved scope, but the codebase cannot
support it. `/api/user/password` is a *change* endpoint requiring the current
password; there is no forgot-password flow, no reset-token model, and no email
sending infrastructure anywhere in the project. "Issue a reset link" would mean
building a token model and an email subsystem — a separate project, not a line
item in this one.

**Force sign-out** ships instead. It stamps `sessionsValidFrom = now()`,
invalidating every existing token within the 60-second TTL. It answers the case
that actually matters for support — a student's session is on a shared or lost
device — without inventing infrastructure. It never sets a password an admin
knows.

It is gated to the owner, matching the gating originally approved for password
reset. Loosening it to any active admin later is a one-line change to a pure
predicate.

**When an email subsystem exists**, real password reset becomes a follow-up
round: a `PasswordResetToken` model, a student-facing forgot-password flow, and
an admin action that triggers it. Force sign-out remains useful alongside it.

---

## Audit log

`AdminAudit` rows exist and are never displayed. Giving admins delete and
force-sign-out powers without a viewer would mean "who deleted this student" has
no answer, so the viewer ships in the same round as the powers.

`/admin/audit` — server page. Filter by actor, action, entity and date range;
paginated. Backed by the existing `@@index([actorId, createdAt])` and
`@@index([entity, entityId])`.

`AuditAction` in `src/lib/admin-audit.ts` extends with:

```
student.update          student.suspend  student.reactivate
student.force_signout   student.tier     student.delete
```

Every new mutation route calls `recordAudit`. Summaries carry before and after
values for edits, the reason for suspensions, and both tiers for a tier change.

`recordAudit`'s existing swallow-failures behaviour is retained: losing an audit
row must never turn a successful edit into an error the admin sees.

---

## Error handling

Follows the routes already in place:

| Condition | Response |
|---|---|
| Zod parse failure | `400` with field errors |
| Not signed in, or inactive admin | `401` via `requireAdminApi` |
| Non-owner on an owner route | `403` via `requireOwnerApi` |
| Unknown id | `404` |
| Duplicate email or phone (Prisma `P2002`) | `409` naming the offending field |
| Unexpected | logged server-side, generic `500` |

A write failure is only blamed on the admin when the database says so. Assigning
`P2002`'s message to every error would tell someone their email is taken after a
dropped connection, sending them to hunt a duplicate that does not exist.

Pages surface these through the existing `StatusBanner`.

---

## Testing

New database-free `.mts` scripts in `scripts/`, registered in the `test` script
in `package.json`:

- **`test-subscription.mts`** — tier ordering; `hasAtLeast` at every boundary
  including equal-tier; label and tone mapping for all three tiers.
- **`test-admin-student.mts`** — filter normalisation against bad params, empty
  params, and out-of-range or non-numeric page values; profile-edit validation
  (email and phone format, required fields, enum values); display shaping.
- **`test-admin-access.mts`** *(extend)* — the four new predicates across
  owner / non-owner × active / deactivated actor.
- **`test-admin-nav.mts`** — grouped nav filters `ownerOnly` correctly for both
  owner and non-owner; every href is non-empty; the mobile subset stays four
  items; every item visible to an actor is reachable from either the mobile bar
  or the "More" sheet, so no route is orphaned on mobile.

---

## Migration

`prisma migrate` cannot reach Supabase from the development machine
(`DIRECT_URL` is unreachable). Migrations are therefore applied through the
Supabase SQL Editor, and three constraints follow:

1. The migration file must use **LF** line endings. With `core.autocrlf=true`,
   CRLF silently drifts the Prisma migration checksum.
2. The SQL Editor can report success on a **partially applied** batch. Success is
   verified by querying `information_schema` for the new columns, the new enum
   type and the new indexes — never by trusting the editor's message.
3. `_prisma_migrations` is reconciled afterwards so Prisma's state matches the
   database.

Both migrations are additive with defaults, so they are safe against live data:
existing rows get `tier = FREEMIUM` and `isActive = true`.

---

## Implementation order

Each step is independently shippable, so a stall in one does not block the rest.

1. **Schema + seam** — the two migrations, `subscription.ts`, its tests. Inert
   without UI, so it can land first and alone.
2. **Shell** — grouped nav, shared primitives, existing pages moved onto them.
   No behaviour change; purely structural.
3. **Students** — access predicates, libs, list page, detail page, mutation
   routes, auth enforcement.
4. **Audit log** — new actions wired into the student routes, then the viewer.
