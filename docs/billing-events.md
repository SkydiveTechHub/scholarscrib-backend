# Billing PostHog events

Catalog / plan ops (Admin → Billing): see [`billing-catalog-ops.md`](./billing-catalog-ops.md).
Architecture / write path: [`backend-port/10-billing.md`](./backend-port/10-billing.md).

Server-side events for product analytics and PostHog Workflows. Captured via
`app.services.product_events.capture` (never raises; no PII).

## Person properties (`$set`)

- `tier` — `FREEMIUM` | `STANDARD` | `PREMIUM` (mirrored only via `apply_billing_state`)
- `plan_code` — `freemium` | `standard` | `premium`
- `billing_status` — `trialing` | `active` | `canceling` | `past_due` | `freemium`
- `intro_ends_at` — ISO date when set

`User.next_billing_at` is the sole renewal / grace / cancel cursor (not stored on `Subscription`).
Subscription history rows use `kind`: `intro` | `checkout` | `renewal` | `comp` | `freemium`.

Recurring charges: default `BILLING_PROVIDER=paystack` (Paystack Subscriptions auto-charge; we react to webhooks). Alternate `BILLING_PROVIDER=lago` uses Lago `invoice.created` → Paystack `charge_authorization` and appends a renewal term.

Billing emails are enqueued to `EmailOutbox` and drained by `POST /api/cron/email/drain` (not sent inline from reminder/payment handlers).

## Cancel behaviour

| Action | Result |
| --- | --- |
| Student `POST /api/billing/cancel` while **trialing** | Immediate Freemium |
| Student cancel while **active** / **past_due** | `billing_status=canceling`; keep tier until `next_billing_at`; remote renewals stopped (Paystack disable / Lago terminate) |
| Student `POST /api/billing/resume` while canceling | Back to `active` (before period end); remote enable/recreate |
| Admin revoke | Immediate Freemium |
| Cron after cancel period / Paystack `subscription.disable` / Lago invoice while canceling | Freemium |

## Shared event properties

- `plan_code`, `previous_plan_code`
- `amount_kobo`, `currency` (`NGN`)
- `source` — `signup_intro` | `checkout` | `renewal` | `admin_comp` | `grace_downgrade` | `cancel` | `gate`
- `lago_subscription_external_id` / `paystack_subscription_code` / `billing_provider`
- `lead_days` — `7` | `1` on reminder events

## Catalog

| Event | When |
| --- | --- |
| `billing_intro_started` | Signup provisions free Standard month |
| `billing_intro_ending_soon` | Reminder job 7d / 1d before intro ends |
| `billing_intro_ended` | Intro → Freemium |
| `checkout_started` | Paystack initialize |
| `checkout_failed` | Verify/webhook non-success |
| `subscription_activated` | Paid plan live (also resume-from-cancel) |
| `subscription_canceled` | Student cancel (immediate or period-end scheduled) |
| `subscription_renewal_upcoming` | Reminder 7d / 1d before renewal |
| `subscription_renewal_charged` | Lago path: `charge_authorization` success |
| `subscription_renewed` | Paystack path: renewal `charge.success` applied |
| `subscription_renewal_failed` | Charge / invoice payment failed |
| `subscription_past_due` | Entered grace |
| `billing_downgraded_to_freemium` | Grace / revoke / cancel period end → Freemium |
| `billing_entitlement_blocked` | 403 from a paid gate |
| `billing_page_viewed` | Frontend pricing / billing surfaces |
| `plan_selected` | Frontend plan CTA |
