# Security deployment guide

This document covers the authentication and database hardening introduced by
`supabase/migrations/202607240001_security_hardening.sql`. The repository does
not apply that migration remotely.

## Authentication behavior

- Five failed attempts for the same normalized identifier within five minutes
  cause a five-minute lock. The limits are configured in `config/settings.py`.
- The in-process limiter is bounded and stores only keyed HMAC digests, never
  raw phone numbers. It intentionally does not log submitted identifiers.
- All invalid phone, unknown-account, and wrong-password login paths return the
  same public error. Unknown accounts still perform PBKDF2 work to reduce timing
  differences.
- New passwords use PBKDF2-HMAC-SHA256 with 600,000 iterations. A successful
  login transparently upgrades older PBKDF2 hashes. New registrations require
  12 to 128 characters; existing shorter legacy passwords can still log in and
  be rehashed.
- New application user IDs are random and no longer contain a phone number.
- Persistent-login cookies contain only a signed, random v2 session ID. The
  server stores the identity mapping in `data/auth/sessions.json`, using a hash
  of the session ID as the lookup key and only a masked phone for display.
  Valid legacy v1 cookies are rotated on first use.
- Login and logout clear all Streamlit session state, preventing another
  account in the same browser session from inheriting portfolio/widget data.

Generate `AUTH_COOKIE_SECRET` from at least 32 random bytes, keep it only in the
deployment secret store, and rotate it if exposed. Rotation invalidates signed
cookies. The session file is set to mode `0600` where the operating system
supports POSIX permissions.

The application-level limiter and session store are process/local-filesystem
controls. A multi-replica deployment must add an infrastructure/shared-store
rate limit and a shared session store (or use sticky sessions). Streamlit sets
the cookie from a component, so it cannot mark it `HttpOnly`; keep custom
components and all rendered HTML trusted, and use a strict Content Security
Policy at the reverse proxy where available.

## Database preflight

Take a database backup first. Run these read-only checks and resolve every
returned row deliberately. Do not blindly delete duplicates.

```sql
select phone, count(*) from public.app_users
group by phone having count(*) > 1;

select user_id, code, count(*) from public.app_watchlist
group by user_id, code having count(*) > 1;

select user_id, id, count(*) from public.app_adjustments
group by user_id, id having count(*) > 1;

select user_id, date, code, count(*) from public.app_daily_ledger
group by user_id, date, code having count(*) > 1;

select 'app_watchlist' as table_name, count(*) as missing_owner
from public.app_watchlist where user_id is null or btrim(user_id) = ''
union all
select 'app_adjustments', count(*) from public.app_adjustments
where user_id is null or btrim(user_id) = ''
union all
select 'app_daily_ledger', count(*) from public.app_daily_ledger
where user_id is null or btrim(user_id) = '';
```

The migration aborts and rolls back on duplicate natural keys. It never chooses
a duplicate row on the operator's behalf.

## RLS rollout

The migration:

1. Adds owner-leading indexes and unique indexes used by current upserts.
2. Adds nullable `app_users.auth_user_id`, which maps a Supabase Auth UUID to
   the existing text application user ID.
3. Enables RLS, revokes `anon`, and installs owner-only policies for
   `app_watchlist`, `app_adjustments`, and `app_daily_ledger`.
4. Prevents the `authenticated` role from selecting `password_hash`.
5. Installs `app_apply_position_edit`, an atomic transaction RPC.

Before applying it, confirm that the server-side `SUPABASE_KEY` is a protected
server/service credential. An anon key will lose access after RLS is enabled.
Never expose a service-role key to browser JavaScript or a downloadable client.
The service role bypasses RLS, so current server code must continue passing and
filtering `user_id` on every operation.

For direct authenticated access, link each migrated user to exactly one
Supabase Auth identity:

```sql
update public.app_users
set auth_user_id = '<supabase-auth-uuid>'::uuid
where user_id = '<opaque-application-user-id>'
  and auth_user_id is null;
```

Do not enable direct authenticated access for an identity until this mapping is
verified. Existing legacy IDs such as `u_<phone>` remain unchanged to preserve
foreign data ownership; migrate them to opaque IDs only with a separately
reviewed, cross-table data migration.

Apply the SQL through the normal reviewed Supabase migration workflow, then
refresh the PostgREST schema cache and test with:

- an anonymous request (must receive no business rows);
- user A reading/writing user A rows;
- user A attempting user B's `user_id` (must fail);
- the service credential executing current server flows;
- two repeated calls to the position-edit RPC (same final snapshot, no
  duplicate UI-edit rows).

## Position edit transaction

`services/edit_bridge_service.py` first calls `app_apply_position_edit`. The RPC
uses a transaction-scoped advisory lock, deletes only same-day `ui_edit` rows,
replays the remaining adjustment history, and writes the rows needed for the
requested target. Any SQL exception rolls back the entire edit.

For compatibility, a PostgREST `PGRST202`/HTTP 404 response (RPC not installed)
uses the prior best-effort multi-request implementation. Any other RPC error is
surfaced without starting that fallback because a timeout can leave the
transaction outcome unknown. Install the migration before relying on atomic
edits in production.

References:

- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase API key guidance](https://supabase.com/docs/guides/getting-started/api-keys)
