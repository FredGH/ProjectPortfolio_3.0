# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: base — slim Python 3.11 with system build dependencies
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: deps — install runtime Python packages into an isolated prefix
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/deps -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: test — run unit tests (no API key required)
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS test

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY weather_forecaster_sources/ ./weather_forecaster_sources/
COPY tests/ ./tests/
COPY pyproject.toml .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "pytest", \
     "tests/test_extraction.py", \
     "tests/test_bronze_loader.py", \
     "tests/test_weather_mock.py", \
     "-v", "--tb=short"]

# ─────────────────────────────────────────────────────────────────────────────
# Stage 4: production — minimal runtime image for running the pipeline
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS production

WORKDIR /app

COPY --from=deps /deps /usr/local

COPY weather_forecaster_sources/ ./weather_forecaster_sources/
COPY .env.example .

RUN mkdir -p /app/data/data_zone /app/data/bronze

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "weather_forecaster_sources/pipeline_runner.py"]

# ─────────────────────────────────────────────────────────────────────────────
# Stage 5: dbt-fusion — install dbt Fusion binary
# Used as a shared base for the dbt stage below.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS dbt-fusion

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install dbt Fusion to /usr/local/bin so it is on PATH for all users
# SHELL must be exported to sh (not curl) so the rc-file step inside the script doesn't fail
RUN curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh \
    | SHELL=/bin/bash sh -s -- --update \
    && mv /root/.local/bin/dbt /usr/local/bin/dbt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 6: dbt — run dbt Fusion models against the DuckDB bronze layer
#
# Usage:
#   docker compose run --rm dbt run          # build all models
#   docker compose run --rm dbt build        # build + test
#   docker compose run --rm dbt test         # tests only
#   docker compose run --rm dbt run --select gold_weather_summary
# ─────────────────────────────────────────────────────────────────────────────
FROM dbt-fusion AS dbt

WORKDIR /app/dbt

# Copy dbt project files (excludes target/, logs/ via .dockerignore)
COPY dbt/ .

# profiles.yml lives inside the dbt/ directory; tell Fusion where to find it
ENV DBT_PROFILES_DIR=/app/dbt
ENV PYTHONUNBUFFERED=1

# Data volume is mounted at runtime — pre-create the mount point only
RUN mkdir -p /app/data/bronze

# dbt is the entrypoint — arguments are passed directly:
#   docker compose run --rm dbt run
#   docker compose run --rm dbt test
#   docker compose run --rm dbt build --select gold_weather_summary
ENTRYPOINT ["dbt"]
CMD ["build"]
