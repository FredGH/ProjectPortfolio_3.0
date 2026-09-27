# dbt toolchain in its own image: dbt-core needs protobuf>=6 while streamlit
# (requirements.txt) needs <6, so dbt cannot share the api/ui images.
FROM python:3.11-slim

WORKDIR /app/dbt

COPY requirements-dbt.txt /tmp/requirements-dbt.txt
# dbt-postgres pins the *source* psycopg2, which needs a compiler and libpq
# headers the slim image lacks. Install psycopg2-binary (same module) instead
# and add dbt-postgres without its dependency list; the rest of the pins in
# requirements-dbt.txt install normally.
RUN grep -v '^dbt-postgres' /tmp/requirements-dbt.txt > /tmp/req-core.txt && \
    grep '^dbt-postgres' /tmp/requirements-dbt.txt > /tmp/req-adapter.txt && \
    pip install --no-cache-dir psycopg2-binary -r /tmp/req-core.txt && \
    pip install --no-cache-dir --no-deps -r /tmp/req-adapter.txt

ENV DBT_PROFILES_DIR=/app/dbt \
    DBT_PG_HOST=postgres

ENTRYPOINT ["dbt"]
