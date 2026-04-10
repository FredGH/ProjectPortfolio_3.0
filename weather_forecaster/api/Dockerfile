# ─────────────────────────────────────────────────────────────────────────────
# api/Dockerfile
#
# Minimal FastAPI image — read-only DuckDB access over the shared data volume.
#
# Build context: weather_forecaster/ root (so docker-compose can reach it)
# Usage (via docker-compose.dagster.yml):
#   docker compose -f docker-compose.dagster.yml build api
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/main.py .

# Data volume is mounted at runtime — pre-create the mount point
RUN mkdir -p /app/data/etl

ENV PYTHONUNBUFFERED=1
ENV DUCKDB_PATH=/app/data/etl/weather_forecaster.duckdb

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
