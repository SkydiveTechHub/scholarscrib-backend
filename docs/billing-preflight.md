# Billing pre-flight (Paystack test mode)

Run these before enabling live keys. Local entitlements and `User.next_billing_at`
are the access cursor; Paystack owns recurring charges when `BILLING_PROVIDER=paystack`.

## Architectural guards (already in code)

| Risk | Guard |
| --- | --- |
| Mid-cycle upgrade double-bills | Checkout freezes `priorPaystackSubscriptionCode` on the PENDING row; activation disables **that** code only (never a `SUB_new` already stamped by `subscription.create`) |
| JWT tier lags upgrade | `require_feature` always sets `student.tier` from the refreshed DB tier before returning |
| Reconcile without history row | `PaystackReconcileService` inserts a `kind=renewal` Subscription term so `RefreshUserTierService` keeps access |
| `/verify` + webhook race | `ApplyChargeSuccessService` locks the `Subscription` row with `SELECT … FOR UPDATE` before activate / email / analytics |
| Dropped renewal webhooks | Daily reminders cron runs `PaystackReconcileService` first — advances `next_billing_at` from Paystack when still active |

Manual reconcile: `POST /api/cron/billing/reconcile` (same cron auth).

## Enabling billing after a freemium-only period

While `PAYSTACK_SECRET_KEY` is unset, signup skips the Standard intro. After keys
are live:

```bash
python -m scripts.backfill_signup_intros --dry-run
python -m scripts.backfill_signup_intros
```

That grants the free Standard month to active `FREEMIUM` users who never got
`intro_ends_at`. New signups pick it up automatically once billing is enabled.

## Test matrix

| Test case | Expected behavior |
| --- | --- |
| **Mid-cycle upgrade** | Standard → Premium checkout disables the Standard Paystack subscription; only Premium renews next month |
| **Double-hit race** | Fire `/verify` and the webhook together → exactly one `EmailOutbox` receipt (`checkout_ok:{reference}`) and one `subscription_activated` event |
| **Card decline on renewal** | `invoice.payment_failed` → `past_due`, grace countdown, `billing_payment_failed.html` enqueued |
| **Cancel → lapse** | Cancel keeps tier until `next_billing_at`; after that timestamp the daily cron assigns freemium |
| **Failed webhook re-delivery** | Force 500 on first webhook delivery; Paystack retry with the same event key returns `{duplicate: true}` and does not double-apply |

## Suggested Paystack dashboard checks after upgrade

1. Customer has a single **active** subscription (old plan `complete` / non-renewing).
2. `User.paystack_subscription_code` matches the Premium subscription code.
3. `User.next_billing_at` matches Paystack `next_payment_date`.
