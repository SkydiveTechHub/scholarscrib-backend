# Billing

Python modules under `app/services/billing/`. Provider factory selects Paystack
(default) or Lago. Local `BillingPlan` catalog is the entitlement / pricing SoT.

## Concepts

| Concept | Storage | Meaning |
| --- | --- | --- |
| Catalog SKU | `User.plan_code` → `BillingPlan.code` | What they bought / are on |
| Entitlement tier | `User.tier` (`FREEMIUM` / `STANDARD` / `PREMIUM`) | Gate rank — denormalized |
| Lifecycle | `User.billing_status` (Postgres enum) | `freemium` \| `trialing` \| `active` \| `canceling` \| `past_due` |
| Access cursor | `User.next_billing_at` | Sole renewal / grace / cancel cursor |
| History | `Subscription` rows | Every intro / checkout / renewal / comp / freemium term |

`Subscription.kind` is `intro` \| `checkout` \| `renewal` \| `comp` \| `freemium`.
`Subscription` never stores `nextBillingAt`.

## Single write path

All User billing mirror mutations go through
`app.services.billing.state.apply_billing_state` (optional `Subscription` insert
in the same flush). Thin helper `set_user_billing_cursor` only touches
`next_billing_at` + optional `billing_status`.

Plan/tier string mapping lives only in `app.services.billing.catalog`
(`plan_code_for_tier` / `tier_for_plan_code`). Domain re-exports for cold-start
fallbacks; billing code must import from catalog.

## Billing provider factory

`BILLING_PROVIDER` selects the recurring engine (`paystack` default, or `lago`):

- **paystack** — Paystack Plans + Subscriptions. Checkout passes `plan` into
  `transaction/initialize`. Renewals are charged by Paystack; we handle
  `subscription.*` / renewal `charge.success` / `invoice.payment_failed`.
  Catalog stores `BillingPlan.paystackPlanCode`; users store
  `paystackSubscriptionCode` + `paystackEmailToken`.
- **lago** — Lago mirrors subscriptions and emits `invoice.created`; we charge
  via Paystack `charge_authorization`. Success **appends** a `Subscription`
  term (`kind=renewal`) via `apply_billing_state`, same as Paystack renewals.

## Catalog cache

`load_catalog` caches snapshots in-process. When Upstash is configured, key
`billing:catalog:version` is compared on each load; mismatch reloads from DB.
`invalidate_catalog_cache` increments the Redis version then clears local
state. Without Upstash, behaviour stays TTL-only (60s).

## Schema

Squashed Alembic revision `b7c2e9a1d004` (after `c9a1d4e7b265`) creates catalog
tables, provider columns, `BillingStatus` / `SubscriptionKind` enums, and
`EmailOutbox`. Pre-launch: if older billing revisions were applied locally,
`alembic stamp` / drop-and-recreate is fine.

## Email outbox

Billing reminders and payment handlers call `EmailService.enqueue` (same TX as
the reminder claim). Sends happen on `POST /api/cron/email/drain` (GHA every
5 minutes, including morning/streak ticks). Rows retry with exponential
backoff; after 5 attempts → `failed`.

## Cron auth

`cron_decision` is fail-closed: missing/short `CRON_SECRET` → **503** unless
`CRON_FAIL_OPEN=true` (dev skip → 204). GHA treats 503 as misconfig failure;
204 remains intentional feature-off under fail-open.

## When billing is off

`PAYSTACK_SECRET_KEY` missing, blank, or equal to `your-paystack-secret-key` →
billing disabled. Checkout returns **503**.

## Reference / terms / settlement

References are `pw_` + UUID without hyphens. Term stacking uses
`term_start` / `term_end` (UTC month clamp). Live entitlement resolution is in
`app.services.billing.rules`. Settlement outcomes are unchanged
(`activate` / `already-applied` / amount-currency mismatches, etc.).

## Checkout / verify / webhook

- `POST /api/billing/checkout` — pending row + Paystack initialize (plan code
  when provider is paystack).
- Frontend callback → `GET /api/billing/verify` → `ApplyChargeSuccessService`.
- `POST /api/billing/webhook` — signature, idempotent `PaystackEvent`, then
  provider handlers. First-checkout `charge.success` activates; renewals append
  a term via `apply_billing_state`.

## Admin comps

`GrantCompSubscriptionService` inserts `kind=comp` through `apply_billing_state`.
Revoke assigns Freemium via `assign_freemium`.

## Upgrade / reconciliation

- Mid-cycle plan change disables the prior Paystack subscription (captured before
  activation) so Paystack does not keep charging both plans.
- Settlement takes `SELECT … FOR UPDATE` on the pending row so `/verify` and the
  webhook cannot both emit receipts.
- Daily `POST /api/cron/billing/reminders` runs Paystack reconcile first, then
  reminders / grace / cancel lapses.

Pre-launch checklist: [`docs/billing-preflight.md`](../billing-preflight.md).

## Related

- Events: [`docs/billing-events.md`](../billing-events.md)
- Catalog ops: [`docs/billing-catalog-ops.md`](../billing-catalog-ops.md)
