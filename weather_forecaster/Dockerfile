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

# Install runtime + dev dependencies together
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# Copy source package, tests, and pytest config
COPY weather_forecaster_sources/ ./weather_forecaster_sources/
COPY tests/ ./tests/
COPY pyproject.toml .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Unit tests only — no API key needed
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

# Copy installed runtime packages from deps stage (no build tools in final image)
COPY --from=deps /deps /usr/local

# Copy only application source — no tests, no docs, no tooling
COPY weather_forecaster_sources/ ./weather_forecaster_sources/
COPY .env.example .

# Pre-create data directories; overridden by the volume mount at runtime
RUN mkdir -p /app/data/data_zone /app/data/bronze

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default: incremental mode.
# Full reload: docker compose run pipeline python weather_forecaster_sources/pipeline_runner.py full
CMD ["python", "weather_forecaster_sources/pipeline_runner.py"]
