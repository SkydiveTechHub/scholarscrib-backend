# Billing catalog ops

How to change what each plan costs and unlocks. **Local DB tables are the
source of truth for gates**; Lago is a mirror for subscriptions/invoices.

Admin console: **System → Billing** (`/admin/billing`). Same operations are
available as HTTP under `/admin/api/billing/*` (admin session required).

## Mental model

| Layer | Role |
|---|---|
| `BillingPlan` / `BillingFeature` / `BillingPlanEntitlement` | Editable catalog |
| Request gates (`require_feature`, tutor caps, explanation depth) | Read catalog by the user’s `plan_code` / `tier` (short TTL cache) |
| `User.next_billing_at` | Sole renewal / grace / cancel cursor (not on `Subscription`) |
| Lago | Subscriptions + invoices; entitlements pushed on admin save / seed |
| Pricing UI (`GET /api/billing/plans`) | Reads the same catalog |

Cancel: student `POST /api/billing/cancel` keeps paid access until `next_billing_at`
(`billing_status=canceling`), then Freemium. Intro cancel is immediate. See
[`billing-events.md`](./billing-events.md).

Changing an entitlement **does not** require a redeploy for students already
on that plan — the next request picks up the new privilege (cache ≤ ~60s).

## What you can do from Admin → Billing

### A. Change a plan’s price or copy

1. Open **Admin → Billing**.
2. Edit **display name**, **blurb**, **amount (₦)**, **purchasable**, **active**.
3. Save. Cache clears; Lago plan amount is updated when `LAGO_API_KEY` is set.

API equivalent:

```http
PATCH /admin/api/billing/plans/{code}
{ "amountKobo": 350000, "displayName": "Standard", "blurb": "…", "isPurchasable": true, "active": true }
```

Amounts are **kobo** (₦3,000 → `300000`).

### B. Change what a plan can do (existing feature)

Examples: raise Standard tutor cap 20 → 30; turn flashcards on for Freemium;
give Standard `by_subject` performance.

1. Open **Admin → Billing**.
2. In the plan’s feature matrix, toggle **enabled** and/or edit the
   **privilege value**.
3. Save the row. Existing subscribers pick up the change on the next request.

| Feature `code` | `value_type` | Privilege examples |
|---|---|---|
| `past_questions` | select | `answers` \| `explanation` \| `ai_explanation` |
| `performance` | select | `overview` \| `by_subject` |
| `ai_tutor` | integer | daily request cap (`2`, `20`, `60`, …) |
| `study_plan` | boolean | `true` / `false` |
| `class_notes` | boolean | `true` / `false` |
| `classroom_pdf` | boolean | `true` / `false` |
| `flashcards` | boolean | `true` / `false` |

API:

```http
PATCH /admin/api/billing/plans/standard/entitlements/ai_tutor
{ "enabled": true, "privilegeValue": 30 }
```

If the entitlement row is missing, the admin UI/API **creates** it (upsert).

### C. Seed / reset from code defaults

First deploy or after wiping catalog tables:

```http
POST /admin/api/billing/plans/seed
```

Or CLI: `python -m scripts.seed_billing_catalog [--push-remote]` (also accepts
`--push-lago` as an alias). Remote push uses `BILLING_PROVIDER` (Paystack plans
by default, or Lago when configured).

Seed is **upsert** — it refreshes known codes from
`app/services/billing/catalog.py` (`SEED_*`) without deleting custom rows for
unknown codes.

## Adding a brand-new *feature* (new capability dimension)

Example: a new gate `mock_exams` that Premium unlocks.

### From Admin UI / API (catalog only)

1. **Create feature** — code, name, value type (`boolean` \| `integer` \| `select`),
   optional `gateKey` (legacy `require_feature` key), optional select options.
2. **Attach to each plan** — set enabled + privilege value for freemium /
   standard / premium (and any other plans).
3. Lago feature + plan entitlements sync when Lago is configured.

### Still required in code (deploy)

Catalog rows alone do **not** enforce a new gate. Also:

1. Map `gateKey` → feature in `GATE_TO_FEATURE` in
   `app/services/billing/catalog.py` (if you use `require_feature("…")`).
2. Put `Depends(require_feature("yourGateKey"))` on the routes (or check
   privilege helpers such as `past_questions_detail` / `ai_tutor_daily_limit`).
3. Update frontend paywalls / `ENTITLEMENTS` in `subscription.ts` if the UI
   hides the surface before calling the API.
4. Prefer documenting the feature in `SEED_FEATURES` / `SEED_ENTITLEMENTS` so
   fresh environments stay consistent.

Without steps 1–3, admins can store the privilege but students are never
blocked or unlocked by it.

## Adding a brand-new *plan* (new paid tier)

Today the product tiers are fixed as **`freemium` / `standard` / `premium`**,
mapped 1:1 to `User.tier` (`FREEMIUM` \| `STANDARD` \| `PREMIUM`).

**Preferred path:** keep those three codes and change prices / privileges via
Admin → Billing.

**To introduce a fourth named plan** (e.g. `pro`):

1. Add `PRO` to the `SubscriptionTier` Postgres enum + Alembic migration.
2. Extend `PLAN_CODE_TO_TIER` / `TIER_TO_PLAN_CODE`, `TIER_RANK`, prices,
   frontend `SUBSCRIPTION_TIERS`, checkout validation, and admin comp UI.
3. Create the plan in Admin → Billing (or seed) with `code=pro`, rank, amount.
4. Set entitlements for every feature; push Lago.
5. Update landing `/pricing` copy if it does not already load from
   `GET /api/billing/plans`.

Until the enum and mappings ship, creating a random `BillingPlan.code` will
not attach cleanly to `User.tier` or checkout.

## Checklist (common changes)

| Goal | Admin UI enough? | Also need code? |
|---|---|---|
| Change ₦ price / blurb | Yes | No |
| Change tutor daily cap | Yes | No |
| Turn a seeded feature on/off per plan | Yes | No |
| New feature with API gating | Create feature + entitlements | Gate map + routes (+ UI) |
| New named tier | Partial (catalog row) | Enum, mappings, checkout, UI |

## Related

- Event taxonomy: [`billing-events.md`](./billing-events.md)
- Seed script: `python -m scripts.seed_billing_catalog`
- Migrate live Paystack holders: `python -m scripts.migrate_paid_users_to_lago`
- Cron reminders: `POST /api/cron/billing/reminders` (GitHub Actions / scheduler)
- Email outbox drain: `POST /api/cron/email/drain` (same GHA matrix as push drain)
- Catalog cache: Upstash key `billing:catalog:version` (invalidate bumps it)
