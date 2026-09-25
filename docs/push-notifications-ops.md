# Push notifications — operations

Design: docs/superpowers/specs/2026-09-14-push-notifications-design.md

## Environment (Vercel → Settings → Environment Variables, and .env.local)

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | from `npx web-push generate-vapid-keys` |
| `VAPID_PRIVATE_KEY` | from the same command |
| `VAPID_SUBJECT` | `mailto:hello@scholarscrib.com` |
| `CRON_SECRET` | 32+ random characters (`node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`) |

Generate VAPID keys **once**. Rotating them invalidates every student's
subscription; only rotate after a leak, and expect every student to opt in
again. `NEXT_PUBLIC_VAPID_PUBLIC_KEY` is inlined at build time: redeploy after
changing it.

Any missing variable turns the feature off: no opt-in UI, cron routes answer
204, the admin page says push is not configured.

## Database migration

`prisma migrate` cannot reach Supabase from the dev machine. Apply
`prisma/migrations/20260915000001_push_notifications/migration.sql` in the
Supabase SQL Editor inside `BEGIN; … COMMIT;`, followed by its
`_prisma_migrations` row:

```sql
INSERT INTO "_prisma_migrations"
  (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
VALUES
  (gen_random_uuid()::text, '<sha256 of migration.sql bytes>', now(),
   '20260915000001_push_notifications', NULL, NULL, now(), 1);
```

Checksum: `sha256sum prisma/migrations/20260915000001_push_notifications/migration.sql`
(the file must be LF-only: `tr -cd '\r' < … | wc -c` prints 0).

The SQL Editor can report success on a half-applied batch. Verify:

```sql
select table_name from information_schema.tables
where table_schema = 'public' and table_name in
  ('PushSubscription','NotificationPreference','Announcement',
   'AnnouncementDelivery','AnnouncementDismissal','ReminderLog')
order by 1;  -- 6 rows

select typname from pg_type where typname in ('AnnouncementStatus','DeliveryStatus');  -- 2 rows

select migration_name, finished_at from "_prisma_migrations"
where migration_name = '20260915000001_push_notifications';  -- 1 row
```

## Scheduler (pg_cron + pg_net)

Deploy the code with the env vars first, so the routes exist before anything
calls them.

```sql
create extension if not exists pg_cron;
create extension if not exists pg_net;

select vault.create_secret('<CRON_SECRET>', 'push_cron_secret');
select vault.create_secret('https://<production-host>', 'push_base_url');

create or replace function public.call_push_cron(path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  base text := (select decrypted_secret from vault.decrypted_secrets where name = 'push_base_url');
  secret text := (select decrypted_secret from vault.decrypted_secrets where name = 'push_cron_secret');
begin
  perform net.http_post(
    url := base || path,
    headers := jsonb_build_object('Authorization', 'Bearer ' || secret),
    timeout_milliseconds := 55000
  );
end;
$$;

revoke all on function public.call_push_cron(text) from public, anon, authenticated;

-- Lagos is UTC+1 with no DST: 06:xx UTC = 07:xx Lagos, 18:xx UTC = 19:xx Lagos.
select cron.schedule('push-morning', '*/5 6 * * *', $$select public.call_push_cron('/api/cron/push/morning')$$);
select cron.schedule('push-streak',  '*/5 18 * * *', $$select public.call_push_cron('/api/cron/push/streak')$$);
select cron.schedule('push-drain',   '* * * * *',   $$select public.call_push_cron('/api/cron/push/drain')$$);
```

Verify:

```sql
select jobname, schedule, active from cron.job where jobname like 'push-%';  -- 3 rows, active

-- after a few minutes:
select j.jobname, d.status, d.start_time
from cron.job_run_details d join cron.job j on j.jobid = d.jobid
where j.jobname like 'push-%' order by d.start_time desc limit 10;

select status_code, left(content, 200), created
from net._http_response order by created desc limit 10;  -- 200s, never 401
```

A 401 means the Vault secret and Vercel's `CRON_SECRET` differ. A 204 means
the deployment is missing one of `NEXT_PUBLIC_VAPID_PUBLIC_KEY`,
`VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` or `CRON_SECRET`.

To pause: `select cron.unschedule('push-drain');` (and the others).
To rotate the cron secret: update Vercel, redeploy, then
`select vault.update_secret((select id from vault.secrets where name = 'push_cron_secret'), '<new>');`.

## Housekeeping

`AnnouncementDelivery` and `ReminderLog` grow daily. Clearing old rows is
safe at any time:

```sql
delete from "ReminderLog" where "sentAt" < now() - interval '30 days';
delete from "AnnouncementDelivery" d using "Announcement" a
where a.id = d."announcementId" and a."completedAt" < now() - interval '30 days';
```
