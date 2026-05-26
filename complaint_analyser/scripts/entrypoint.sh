#!/usr/bin/env sh
# Container entrypoint that constructs DATABASE_URL from a Docker secret file.
# In production (docker-compose.prod.yml), /run/secrets/postgres_password is
# mounted by Docker Compose from ./secrets/postgres_password on the host.
# In local dev the file is absent and DATABASE_URL is read from the environment.
set -e

SECRET_FILE="/run/secrets/postgres_password"
if [ -f "$SECRET_FILE" ]; then
    PG_PASS=$(cat "$SECRET_FILE")
    export DATABASE_URL="postgresql://triage:${PG_PASS}@postgres:5432/triage"
fi

exec "$@"
