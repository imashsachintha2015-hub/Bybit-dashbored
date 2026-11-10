# SupplierONE → new Supabase account

Source: project `droywqsxnojflxjroaiw` ("SupplierONE", org "P&S", ap-southeast-1, Postgres 17).
Inventoried 2026-11-10 after restoring it from paused. It is now ACTIVE — pause it again once migration is verified.

## Easiest path first
If the new account is one you also own, use **Dashboard → Project Settings → General → Transfer project** to move it to the other organization. It keeps the URL, keys, users, data and files, and needs no scripts. Everything below is only for a move into a fully separate account, where transfer isn't possible.

## What exists (what must move)

| Area | Contents |
|---|---|
| Schema | 31 migrations (`0001_extensions` … `supplier_directory_view`), 20 public tables, 1 view (`supplier_directory`), 10 functions (7 SECURITY DEFINER), 29 triggers, 61 RLS policies |
| Auth trigger | `on_auth_user_created` on `auth.users` → `public.handle_new_user()` |
| Extensions in use | `pgcrypto`, `citext`, `moddatetime`, `pg_trgm`, `uuid-ossp`, `pg_stat_statements`, `supabase_vault` (vault is empty) — all enabled by migration `0001`/`move_extensions_out_of_public` |
| DB role settings | `anon` statement_timeout 3s, `authenticated` 8s, `authenticator` 8s/lock 8s, JWT exp `app.settings.jwt_exp=3600` (set by Supabase defaults; verify on the new project) |
| Data | `erp_suppliers` 4,838 · `audit_log` 185 · `items` 272 · `categories` 67 · `permissions` 32 · `role_permissions` 82 · `modules` 12 · `roles` 6 · `profiles` 3 · `user_permission_overrides` 5 · `auth.users` 3. Suppliers, documents, contacts: 0 rows |
| Storage | 1 private bucket `supplier-documents` (4 MB limit; pdf/png/jpeg/webp/octet-stream), 3 policies, **16 files / 5.1 MB** under `pending-registrations/…` |
| Edge Functions | none |
| Cron / Vault secrets / custom roles / extra publications | none |

## Steps
1. Create the new project in the new account: **same region (ap-southeast-1), Postgres 17**.
2. Run `OLD_DB_URL=… NEW_DB_URL=… ./migrate.sh` (schema → data → verification counts). Passwords come from each project's Dashboard → Connect.
3. Run `copy_storage.py` (needs both service-role keys; see header).
4. Do the settings checklist below.
5. Re-point the app, test, then pause/delete the old project.

## Settings checklist (not copied by any dump — set by hand in the new project)
**Auth → Providers / Sign In**
- [ ] Email provider on; confirm "Confirm email" and "Allow new users to sign up" match the old project (the app uses invites + a public registration form, so check signup is allowed as before).
- [ ] Minimum password length / requirements — copy from old project.
- [ ] **Enable leaked-password protection** (flagged as off on the old project by the advisor; free improvement).
**Auth → URL Configuration**
- [ ] Site URL and every Redirect URL → your app's production + preview domains (invite and password-reset links break otherwise).
**Auth → Email**
- [ ] Invite / Confirm / Reset templates (copy text from old Dashboard → Auth → Emails).
- [ ] SMTP: if the old project used custom SMTP, re-enter host/user/password (the default mailer is heavily rate-limited).
**Auth → Sessions / Rate limits** — copy values (JWT expiry was 3600s).
**API**
- [ ] Exposed schemas / max rows — confirm same as old.
**Database**
- [ ] Compute size, backups/PITR (plan-dependent), network restrictions / SSL enforcement.
**Storage** — bucket `supplier-documents` is created by the migrations; confirm it is **private** with the 4 MB limit and MIME list above.

## App configuration to change (in your hosting provider, not in Supabase)
- `NEXT_PUBLIC_SUPABASE_URL` / `SUPABASE_URL` → new project URL
- anon / publishable key → new project's key
- `SUPABASE_SERVICE_ROLE_KEY` (server only) → new service-role key
- **Document encryption key** (app-level; files are stored as `application/octet-stream`). It is not in the database. It must be copied unchanged or the 16 migrated files become unreadable.
- Any other keys that were derived from the old JWT secret (custom signed tokens, webhooks).

## Things that cannot be copied
- **Passwords:** hashes in `auth.users` are copied by `migrate.sh`, so the 3 users keep their passwords. Existing sessions are invalidated (new JWT secret) — everyone logs in once.
- **API keys / JWT secret** are new. Old keys stop working when the old project is deleted.
- Dashboard-only settings (above) have no export.

## Safety notes
- Dumps contain user emails and password hashes. `dump/` is git-ignored; delete it when done.
- Nothing here has been run against a new project. Test on the new project before cutting over.
- Advisor findings on the old project (carry over or fix): 5 SECURITY DEFINER functions callable by `authenticated` via RPC (`activate_own_account`, `current_user_active`, `get_my_permission_keys`, `has_permission`, `is_superuser`) — these look intentional (permission checks) but worth reviewing; leaked-password protection off.
