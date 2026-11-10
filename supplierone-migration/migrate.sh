#!/usr/bin/env bash
# SupplierONE -> new Supabase account. Run from a machine with: supabase CLI, psql, pg_dump (v17).
# Usage:  OLD_DB_URL=... NEW_DB_URL=... ./migrate.sh
#   OLD_DB_URL / NEW_DB_URL = "Session pooler" or direct connection strings from
#   Dashboard -> Connect, e.g. postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres
# Create the NEW project first (same region ap-southeast-1, Postgres 17). Never commit URLs/passwords.
set -euo pipefail
: "${OLD_DB_URL:?}" "${NEW_DB_URL:?}"
OUT=./dump; mkdir -p "$OUT"

echo "1/5  Schema: replay the 31 migration files (tables, RLS, functions, triggers, view, bucket, storage + auth trigger)"
# Pull remote migration history into ./supabase/migrations, then push to the new project.
[ -d supabase ] || supabase init --force >/dev/null
supabase db pull --db-url "$OLD_DB_URL"
supabase db push --db-url "$NEW_DB_URL"

echo "2/5  Clear seed rows that migrations inserted, so live data loads exactly as-is"
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -c "truncate public.role_permissions, public.user_permission_overrides, public.permissions, public.modules, public.roles, public.items, public.categories restart identity cascade;"

echo "3/5  Dump data (public tables + auth users/identities). Excludes storage.objects (files are copied by copy_storage.py)"
pg_dump "$OLD_DB_URL" --data-only --schema=public --inserts --column-inserts -f "$OUT/public_data.sql"
pg_dump "$OLD_DB_URL" --data-only --inserts --column-inserts \
  -t auth.users -t auth.identities -f "$OUT/auth_data.sql"

echo "4/5  Load. Triggers are disabled (replica role) so on_auth_user_created / audit / profile-protection don't duplicate rows"
export PGOPTIONS='-c session_replication_role=replica'
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -f "$OUT/auth_data.sql"
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -f "$OUT/public_data.sql"
unset PGOPTIONS

echo "5/5  Verify counts (expect: erp_suppliers 4838, items 272, categories 67, audit_log 185, profiles 3, auth.users 3)"
psql "$NEW_DB_URL" -c "select 'erp_suppliers',count(*) from public.erp_suppliers union all select 'items',count(*) from public.items union all select 'categories',count(*) from public.categories union all select 'audit_log',count(*) from public.audit_log union all select 'profiles',count(*) from public.profiles union all select 'roles',count(*) from public.roles union all select 'permissions',count(*) from public.permissions union all select 'auth.users',count(*) from auth.users;"
echo "Done. Next: python3 copy_storage.py, then follow README.md settings checklist."
