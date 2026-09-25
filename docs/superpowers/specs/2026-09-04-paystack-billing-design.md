# Paystack billing — design

**Date:** 2026-09-04
**Status:** Approved, not yet implemented
**Scope:** Payment plumbing only. Tier entitlements are a separate piece of work.

## Why now

`docs/PRD.md` lists two open questions for M5. This design answers the first —
the payment provider is Paystack — and deliberately leaves the second, what
each tier unlocks, untouched.

The seam this lands in already exists. `prisma/schema.prisma` says of
`User.tier`:

> Tier is the denormalised read column: when billing lands, a Subscription
> model becomes the source of truth that WRITES this, and every gate written
> against `hasAtLeast()` keeps working unchanged.

and `src/app/admin/api/students/[id]/tier/route.ts` says of the admin override:

> When a provider is wired, a Subscription row becomes the source of truth that
> writes `User.tier` and this route becomes the comp/correction path rather
> than the only path.

This design is the cashing-in of both comments. No call site of `hasAtLeast()`
changes.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Provider | Paystack | PRD open question #1, resolved |
| Renewal | One-off charge buying a fixed term | Nigerian students commonly pay by transfer and USSD, where recurring is not possible. Makes the system a pure function of "is there a paid period covering now?" |
| Plan catalogue | A table in `lib/subscription.ts` | Database-free, so it unit tests like the rest of that file. Three rows do not need admin CRUD |
| Expiry | Computed on read; `User.tier` is a cache | No scheduler, and no row that is stale in a way nothing notices |
| Admin override | A comped `Subscription` row | One resolver, one rule. Comps expire, so free access does not leak forever |

## Data model

```prisma
enum BillingPeriod       { MONTHLY YEARLY }
enum SubscriptionSource  { PAYSTACK COMP }
enum SubscriptionStatus  { PENDING ACTIVE FAILED ABANDONED REVOKED }

model Subscription {
  id          String   @id @default(cuid())
  userId      String
  user        User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  tier        SubscriptionTier
  period      BillingPeriod
  source      SubscriptionSource
  status      SubscriptionStatus @default(PENDING)
  reference   String   @unique
  amountKobo  Int
  currency    String   @default("NGN")
  channel     String?
  paidAt      DateTime?
  startsAt    DateTime?
  endsAt      DateTime?
  grantedById String?
  note        String?
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt

  @@index([userId, endsAt])
}

model PaystackEvent {
  eventKey   String   @id
  type       String
  payload    Json
  receivedAt DateTime @default(now())
}
```

`reference` is ours, generated at checkout and passed to Paystack, so a
transaction can always be traced back to the row that authorised it.

`amountKobo` snapshots what was actually charged. That is what lets prices live
in code: repricing a plan does not rewrite history, and a dispute can be
answered from the row alone.

`PaystackEvent` is both the idempotency ledger and the raw audit trail. The key
is the Paystack reference joined with the event type, so the same event
delivered twice collides on the primary key.

`grantedById` is set on `COMP` rows only, naming the `Admin` who issued the
grant.

## The plan catalogue

`components/landing/pricing.tsx` currently hardcodes three plans under display
names that do not match the enum. The mapping is fixed here and becomes the
`PLANS` table:

| Display name | Tier | Monthly | Yearly |
|---|---|---|---|
| Free | `FREEMIUM` | ₦0 | ₦0 |
| Basic | `STANDARD` | ₦2,500 | ₦24,000 |
| Premium | `PREMIUM` | ₦5,000 | ₦50,000 |

Amounts are stored in the table as kobo, because that is what the Paystack API
takes; the naira figures above are the display values. `FREEMIUM` is listed for
display only and is not purchasable — `/checkout` rejects it.

## Statuses

`PENDING` on creation at checkout. `ACTIVE` once a verified `charge.success`
is applied. `FAILED` when Paystack reports a failed charge for the reference.
`ABANDONED` when a `PENDING` row is superseded by a newer checkout for the same
user and plan, so stale rows do not accumulate indefinitely. `REVOKED` only by
an admin.

## Module layout

Pure logic lives in database-free files that unit test without a database,
matching how `subscription.ts` and `curriculum-scope.ts` are already built. IO
is kept separate.

| File | Kind | Responsibility |
|---|---|---|
| `lib/subscription.ts` (extend) | pure | `BILLING_PERIODS` and the `PLANS` table: `tier x period -> { amountKobo, label }` |
| `lib/billing/term.ts` | pure | `termEnd(start, period)`, and stacking |
| `lib/billing/entitlement.ts` | pure | `resolveTier(rows, now) -> { tier, expiresAt }` |
| `lib/billing/reference.ts` | pure | Reference generation |
| `lib/billing/settlement.ts` | pure | Decides the outcome of a verified transaction |
| `lib/billing/signature.ts` | pure | HMAC-SHA512 check of `x-paystack-signature` |
| `lib/billing/paystack.ts` | IO | `initializeTransaction`, `verifyTransaction` |
| `lib/billing/subscription-data.ts` | IO | Prisma reads and writes; `applyChargeSuccess()` |

Every rule worth getting wrong — term stacking, expiry precedence, amount
tampering, replayed webhooks — lands in a pure function.

### Stacking

`termEnd` starts a new term at the later of `now` and the user's current
`endsAt`. Paying twice extends the subscription; it never overwrites the
remaining time. This is the behaviour a user who double-pays expects, and the
behaviour that makes a duplicate charge harmless rather than costly.

### Resolution

`resolveTier(rows, now)` returns the highest-ranked tier among `ACTIVE` rows
whose `startsAt <= now < endsAt`, or `FREEMIUM` if none. It ranks by the
existing `TIER_RANK` rather than by date, so a comped PREMIUM overlapping a
paid STANDARD resolves in the student's favour. `endsAt` is exclusive: a
subscription ending at exactly `now` has ended.

## Payment flow

Three routes under `src/app/api/billing/`.

### `POST /checkout`

Authenticated. Body `{ tier, period }`, validated with a zod schema added to
`lib/validators.ts`, rate-limited with the existing `lib/rate-limit.ts`.

The price is read from `PLANS` server-side and never taken from the client. The
route writes a `PENDING` Subscription with a fresh reference, calls Paystack
`initialize`, and returns the `authorization_url` for the client to redirect
to.

### `GET /callback`

Where Paystack returns the user. Calls `verifyTransaction`, funnels the result
through `applyChargeSuccess()`, and redirects to the billing page with a
status.

This route exists for instant feedback only. It is not the source of truth, and
correctness must not depend on the user's browser coming back.

### `POST /webhook`

Authoritative. Reads the raw body, verifies the `x-paystack-signature` HMAC,
then records the event and applies it.

Idempotency: insert the `PaystackEvent` row first. A duplicate-key collision
means the event was already handled — return 200 and stop. Handles
`charge.success`; every other event type is recorded and answered with 200 so
Paystack does not retry indefinitely.

Both the callback and the webhook call one `applyChargeSuccess()`, so a race
between them produces exactly one activation. Inside `settlement.ts` the
verified amount and currency are re-checked against the pending row: a user who
edits the redirect cannot buy PREMIUM for a hundred naira.

Two implementation risks, called out because both fail silently:

1. **Middleware.** The webhook path must be excluded from any auth or CSRF
   matcher. A signed webhook redirected to `/login` looks like success to
   Paystack and does nothing.
2. **Raw body.** The signature check dies on any re-serialization of the JSON.
   Per `AGENTS.md`, confirm the correct raw-body access for this Next version
   in `node_modules/next/dist/docs/` rather than assuming `await req.text()`
   behaves as in earlier releases.

## Tier resolution at runtime

`User.tier` remains the denormalised cache. It is refreshed in exactly two
places:

1. **On activation.** `applyChargeSuccess()` writes the row and the cache
   column in one transaction.
2. **In the `jwt` callback.** `auth.ts` already re-reads the profile at most
   once per `PROFILE_TTL_MS` (60 seconds). That read gains the user's covering
   subscription rows, and writes back when `resolveTier()` disagrees with the
   stored column.

The consequence, stated plainly: an **expiry can lag the cached column by up to
60 seconds**. Upgrades are instant, because activation writes directly. That
asymmetry is acceptable for a cache and is the price of having no scheduler.

The rule, documented in `entitlement.ts`: any future hard entitlement gate
calls `resolveTier` against live rows. `User.tier` is for chrome, admin lists,
and analytics.

## Admin comps

`setStudentTier` stops writing `User.tier` directly. It creates a `COMP`
Subscription row — tier, a duration the admin chooses, `grantedById`, optional
note — and then refreshes the cache column. The duration is one month or one
year, so a comp uses the same `BillingPeriod` members as a payment and needs no
special case in `termEnd`. `amountKobo` is 0. Comps stack on an existing term by
the same rule as payments.

Setting a student **to FREEMIUM** means revoke: active rows move to `REVOKED`
with `endsAt = now`. This includes paid rows, so an admin can void a paid term.
That is a real consequence and is made visible rather than silent — the confirm
copy states it and the audit summary records it. Refunds remain a manual action
in the Paystack dashboard; refund flows are out of scope.

The existing `student.tier` audit action is kept, with the duration added to
its summary. `components/admin/student-tier-control.tsx` gains a duration
select and a note field.

## Configuration

`PAYSTACK_SECRET_KEY` and `NEXT_PUBLIC_APP_URL` (for the callback URL).

Following the pattern `auth.ts` uses for the Google provider: if the secret is
absent or still a placeholder, billing is off. `/checkout` returns a clean 503
and the UI hides purchase buttons rather than throwing. Local development
without keys keeps working. The webhook route still verifies signatures in that
state rather than accepting anything.

## Migration

`prisma migrate` cannot reach Supabase from the development machine. Generate
the SQL, apply it through the Supabase SQL Editor, then verify against the
catalog rather than trusting the editor's success message. Confirm the
migration file is LF-terminated so the Prisma checksum does not drift.

## UI

Kept to the minimum the scope requires.

- `components/landing/pricing.tsx` imports `PLANS` instead of hardcoding
  prices, which resolves the Basic/Premium versus STANDARD/PREMIUM naming split
  in one place.
- One new page, `/settings/billing`: current tier, expiry date, and buy
  buttons. It slots into the existing settings section rather than inventing a
  new area of the app.

## Testing

New pure-function suites, added to the `test` script in `package.json`:
`test-billing-term.mts`, `test-billing-entitlement.mts`,
`test-billing-settlement.mts`, `test-billing-signature.mts`.

The cases that matter:

- Stacking extends from `endsAt` rather than overwriting.
- A richer active row beats a poorer overlapping one.
- An expired row resolves to FREEMIUM.
- The boundary at exactly `endsAt` is treated as ended.
- An amount mismatch rejects.
- A currency mismatch rejects.
- A replayed event is a no-op.
- A tampered signature fails.

None of these need a database.

## Out of scope

Entitlement gating, recurring and auto-renew billing, refund flows, expiry
reminder emails, school and classroom billing, invoices.
