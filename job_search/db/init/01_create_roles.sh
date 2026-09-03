#!/bin/bash
# Runs once, automatically, the first time the postgres data volume is
# initialised (docker-entrypoint-initdb.d semantics — never on restart of
# an existing volume). Creates the RLS-enforced application role.
#
# The migration/owner role is POSTGRES_USER itself: as the tables' owner it
# bypasses row-level security by default, which is exactly the "migration
# role bypasses RLS" split PLAN.md Step 1a calls for — no second superuser
# role is needed for that half.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'job_search_app') THEN
        CREATE ROLE job_search_app LOGIN PASSWORD '${APP_DB_PASSWORD}';
      END IF;
    END
    \$\$;
EOSQL
