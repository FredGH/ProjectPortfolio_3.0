## What Is Already Well-Implemented

The pipeline demonstrates a solid foundation across several engineering dimensions. These practices should be preserved as the system evolves.

### Data Ingestion

| Practice | Where | Why it matters |
|---|---|---|
| Two-stage ingestion: extract to Parquet, then load separately | `extraction.py` → `bronze_loader.py` | Decouples network I/O from DB writes; Parquet files act as a replayable audit trail |
| Composite key deduplication at bronze load | `bronze_loader.py:COMPOSITE_KEYS` | Prevents duplicate rows when the same extraction is replayed |
| Two load modes: `INCREMENTAL` and `FULL_RELOAD` | `bronze_loader.py:LoadMode` | Allows targeted re-processing without hardcoding a strategy |
| Load metadata table (`staging._load_metadata`) | `bronze_loader.py:433–450` | Tracks exactly which files have been ingested; prevents double-loading |
| Source separation: live (OpenWeather) vs. historical (Open-Meteo) | `assets.py`, `bronze_loader.py` | Keeps two distinct data quality guarantees; each source can fail independently |

### Error Management & Resilience

| Practice | Where | Why it matters |
|---|---|---|
| Tenacity retry with exponential backoff on all OpenWeather API calls | `extraction.py:@retry` decorator | Handles transient network failures without manual retry loops |
| Rate-limit back-off with per-city retry on historical backfill | `historical_extraction.py:fetch_all_capitals_history` | Survives HTTP 429 from Open-Meteo without aborting the full run |
| Per-location error isolation: one city failure does not abort extraction | `assets.py:99–102` | 194 cities succeed even if 1 fails |
| `_RateLimitError` as a distinct exception class | `historical_extraction.py:42–44` | Rate limits are not silently swallowed alongside other errors |

### Idempotency

| Practice | Where | Why it matters |
|---|---|---|
| Historical backfill upsert: DELETE matching keys then INSERT | `bronze_loader.py:629–645` | Re-running the backfill replaces stale rows rather than duplicating them |
| `capitals_load` replaces all rows on every run | `bronze_loader.py:load_capitals_to_staging` | Reference data stays in sync with the JSON without manual cleanup |
| `_load_metadata` ded  

### Data Architecture

| Practice | Where | Why it matters |
|---|---|---|
| Three-layer dbt medallion architecture (bronze → silver → gold) | `dbt/models/` | Clear separation of raw, enriched, and consumption-ready data |
| Historical data wins for past months; live data fills current month | `gold_temperature_monthly.sql:59–88` | Combines ERA5 quality with real-time freshness in a deterministic merge |
| World capitals as an explicit reference dataset | `staging.world_capitals` | Canonical city list is decoupled from transactional data; governs all extractions |
| Views for bronze/silver, materialised tables for gold | `dbt_project.yml` | Keeps transformations lightweight while serving the dashboard from pre-computed tables |

### Security

| Practice | Where | Why it matters |
|---|---|---|
| Parameterised SQL throughout the API (`?` placeholders) | `api/main.py:query()` | Prevents SQL injection even when user input reaches DuckDB |
| API opens DuckDB in `read_only=True` mode | `api/main.py:42` | Database writes are structurally impossible from the API process |
| API opens and closes a connection per request (no persistent lock) | `api/main.py:query()` | Dagster can acquire a write lock between API requests |
| API key loaded from environment, never hardcoded | `config.py:get_api_key()` | Secrets are not committed to source control |
| `.env` is gitignored; `.env.example` provided as template | `.gitignore`, `.env.example` | Prevents accidental credential exposure in the repository |

### Scalability & Operations

| Practice | Where | Why it matters |
|---|---|---|
| Multi-stage Docker build (deps → dbt-parse → runtime) | `Dockerfile.dagster` | Minimises final image size; dbt manifest is pre-compiled so the container starts without network access |
| `dbt parse` at build time, not at runtime | `Dockerfile.dagster:53` | Eliminates a DuckDB connection at startup that would block concurrent asset runs |
| `depends_on: condition: service_healthy` for all Dagster services | `docker-compose.dagster.yml` | Services only start when their dependencies are genuinely ready, not just started |
| Docker healthchecks on all services | `docker-compose.dagster.yml` | Container orchestrators (ECS, Kubernetes) can restart unhealthy services automatically |
| Dagster schedules offset by 15 minutes (extraction at `:00`, dbt at `:15`) | `schedules.py` | Ensures extraction always completes before dbt runs without an explicit dependency chain |
| Shared Dagster image for code-location, webserver, and daemon | `docker-compose.dagster.yml` | One build artefact to maintain; topology mirrors AWS ECS without code changes |

### Observability

| Practice | Where | Why it matters |
|---|---|---|
| `/health` liveness probe on the FastAPI service | `api/main.py:85–88` | Enables Docker and ECS to restart the service if it becomes unresponsive |
| Dagster asset `compute_kind` annotations (`duckdb`, `python`) | `assets.py:@asset` decorators | Assets are visually labelled in the Dagit UI, making the data flow easier to navigate |
| Extraction returns a result dict with file count and error count | `assets.py:114–119` | Dagster materialisation metadata captures run statistics for each execution |
| `DUCKDB_PATH` configurable via environment variable | `api/main.py:30–32` | Database location can be changed without rebuilding the image |

### Testing

| Practice | Where | Why it matters |
|---|---|---|
| Unit tests use real DuckDB with `tmp_path` (no mocks) | `tests/test_bronze_loader.py` | Tests exercise actual SQL logic; mocking DuckDB would hide real bugs |
| Integration tests marked with `@pytest.mark.integration` | `tests/test_weather_api.py` | Clear separation between fast unit tests and slow API-dependent tests |
| `PYTHONPATH=.` pattern documented for test invocation | `CLAUDE.md`, `README.md` | Avoids import errors in a project without a full package install |

------------------

# Proposed Improvements

Improvements are grouped by theme and ranked in the priority matrix below.


## Priority Matrix

**Complexity**: Low / Medium / High  
**Value**: Low / Medium / High

| # | Improvement | Category | Complexity | Value | Priority |
|---|---|---|---|---|---|
| 1 | Replace delete-then-insert with `INSERT OR REPLACE` / `ON CONFLICT DO UPDATE` | Idempotency | Low | High | **P1** |
| 2 | Add Dagster `@asset_check` on row counts for every asset | Observability | Low | High | **P1** |
| 3 | Add `not_null` + `unique` dbt tests on all gold primary keys | Data Quality | Low | High | **P1** |
| 4 | Surface rate-limit and extraction errors to Dagster log (replace bare `print`) | Error Management | Low | High | **P1** |
| 5 | Fail the `weather_extraction` asset when error rate exceeds a threshold | Error Management | Low | High | **P1** |
| 6 | Add `dbt source freshness` checks and `expect_table_row_count_to_be_between` | Data Quality | Low | High | **P1** |
| 7 | Move all magic numbers (delays, timeouts, forecast windows) to `config.py` | Maintainability | Low | Medium | **P2** |
| 8 | Fix CORS: restrict `allow_origins` to known frontend domain | Security | Low | Medium | **P2** |
| 9 | Add range validation dbt tests (temp, humidity, wind speed, cloud cover) | Data Quality | Low | Medium | **P2** |
| 10 | Add `stop_grace_period: 5m` to `dagster-code` in docker-compose | Maintainability | Low | Medium | **P2** |
| 11 | Add Docker log rotation (`max-size`, `max-file`) to all services | Maintainability | Low | Medium | **P2** |
| 12 | Validate `world_capitals.json` entry count at startup in `capitals_load` | Data Quality | Low | Medium | **P2** |
| 13 | Add `unique` constraint on `(city_name, country_code)` in `gold_weather_summary` | Data Integrity | Low | Medium | **P2** |
| 14 | Fix duplicate `columns:` block in `bronze/schema.yml` (copy-paste bug) | Maintainability | Low | Low | **P3** |
| 15 | Add `dbt_expectations` column type and count contract tests at each layer boundary | Data Quality | Medium | High | **P1** |
| 16 | Add DuckDB indexes on high-cardinality join/filter columns (`city_name`, `lat/lon`, `observed_at`) | Performance | Medium | High | **P1** |
| 17 | Parallelize `weather_extraction` with `concurrent.futures.ThreadPoolExecutor` (respecting rate limit) | Performance | Medium | High | **P1** |
| 18 | Add end-to-end integration test: extract → bronze load → dbt → assert gold row count > 0 | Testing | Medium | High | **P1** |
| 19 | Implement proper upsert in `load_capitals_to_staging` (`ON CONFLICT DO UPDATE` instead of `DROP TABLE`) | Idempotency | Medium | High | **P1** |
| 20 | Normalize coordinates to 2 decimal places at bronze load to prevent lat/lon drift duplicates | Data Integrity | Medium | High | **P1** |
| 21 | Add `relationships` dbt tests: `gold_temperature_monthly.city_name` → `stg_world_capitals.city` | Data Integrity | Medium | Medium | **P2** |
| 22 | Add HTTP `Cache-Control` headers on API endpoints (data is stale for up to 1 hour) | Performance | Medium | Medium | **P2** |
| 23 | Replace per-request DuckDB `connect()` with a connection pool (SQLAlchemy or `duckdb.connect` singleton + lock) | Performance / Scalability | Medium | Medium | **P2** |
| 24 | Add API rate limiting middleware (e.g., `slowapi`) to `/api/current` and `/api/forecast` | Security | Medium | Medium | **P2** |
| 25 | Add Dagster `op_failure` sensor to alert (Slack/email) on asset failures | Observability | Medium | Medium | **P2** |
| 26 | Enforce schema version in the extraction layer — fail fast on unexpected API fields | Data Quality | Medium | Medium | **P2** |
| 27 | Add negative and edge-case unit tests (null values, empty API responses, partial writes) | Testing | Medium | Medium | **P2** |
| 28 | Add `is_active` soft-delete flag to `staging.world_capitals` to track removed cities | Data Integrity | Medium | Low | **P3** |
| 29 | Parameterize the 24-hour forecast window in `gold_weather_summary.sql` via a dbt variable | Maintainability | Medium | Low | **P3** |
| 30 | Materialize `bronze` staging views as tables (`+materialized: table`) to avoid repeated view re-execution | Performance | Medium | Medium | **P2** |
| 31 | Partition `staging.current_weather` and `staging.weather_forecast` by date at load time | Performance / Scalability | High | High | **P1** |
| 32 | Add Dagster asset partitions (daily) to `weather_extraction` and `bronze_load` for replay-safe incremental loads | Idempotency / Scalability | High | High | **P1** |
| 33 | Replace DuckDB with MotherDuck (managed DuckDB) or migrate to Postgres for multi-writer concurrency | Scalability | High | High | **P1** |
| 34 | Add pagination (`limit` / `offset`) to all API list endpoints | Scalability | High | Medium | **P2** |
| 35 | Migrate secrets to Docker Secrets or AWS Secrets Manager (remove `.env` bind mount from containers) | Security | High | Medium | **P2** |
| 36 | Add query-level audit logging (who queried what, when) to the FastAPI layer | Security | High | Medium | **P2** |
| 37 | Introduce an async extraction layer (`httpx` + `asyncio`) with semaphore-based rate limiting | Performance | High | Medium | **P2** |
| 38 | Add Prometheus metrics endpoint to FastAPI and Dagster for external monitoring | Observability | High | Medium | **P2** |
| 39 | Add chaos/resilience tests: API timeout, partial write, DuckDB lock, extraction mid-run failure | Testing | High | Medium | **P2** |
| 40 | Introduce dbt snapshots on `gold_weather_summary` for SCD Type 2 historical trending | Data Quality | High | Low | **P3** |

---

## Detail by Category

### 1. Idempotency

**Current state**: The bronze loader uses a delete-then-insert pattern (`bronze_loader.py:237–254`). If a run fails between the delete and the insert, the next retry produces different results. The `staging.world_capitals` table is rebuilt from scratch on every `capitals_load` run using `DROP TABLE IF EXISTS` — destructive and unnecessary.

**Improvements**:
- Replace all delete-then-insert blocks with DuckDB's native `INSERT INTO ... ON CONFLICT (key) DO UPDATE SET ...`. This is atomic and replay-safe.
- For `world_capitals`, switch to `INSERT OR REPLACE` keyed on `country_code`.
- Introduce Dagster asset partitions (daily) so each run owns a defined time slice and replaying a day overwrites exactly that slice — no overlap, no gap.

---

### 2. Error Management & Logging

**Current state**: `fetch_all_capitals_history` uses `print(..., flush=True)` for rate-limit warnings — invisible in the Dagster event log. The `weather_extraction` asset collects errors in a list but returns a dict without failing the asset even when half the extractions fail. `bronze_loader.get_loaded_files()` has a bare `except:` that silently returns an empty set on any database error.

**Improvements**:
- Replace all `print()` calls in pipeline code with `context.log.warning()` / `context.log.error()`.
- Fail the asset (raise `Failure`) when the error rate exceeds a configurable threshold (e.g., >10% of locations fail).
- Remove the bare `except:` in `get_loaded_files()` and replace with `except duckdb.IOException as e: context.log.error(...)`.
- Add structured error metadata to Dagster events using `AssetObservation` or `Output(metadata={...})`.

---

### 3. Data Quality

**Current state**: No range checks on any numeric field. Temperature, wind speed, cloud cover, and humidity can hold any value including physically impossible ones. The `gold_weather_summary` and `gold_temperature_monthly` tables lack `unique` tests on their natural keys. No freshness assertions — a silent extraction failure leaves stale gold data serving the dashboard with no warning.

**Improvements**:
- Add dbt `dbt_expectations.expect_column_values_to_be_between` tests: temperature −90 to 60°C, humidity 0–100%, wind speed ≥ 0, cloud cover 0–100%.
- Add `unique` tests on `(city_name, country_code)` in `gold_weather_summary` and `(city_name, country_code, year, month)` in `gold_temperature_monthly`.
- Add `dbt source freshness` with a 2-hour warn threshold and 4-hour error threshold on `staging.current_weather`.
- Add `expect_table_row_count_to_be_between` (min: 150, max: 220) on every gold table after each run.
- Validate the `world_capitals.json` entry count in the `capitals_load` asset before loading.

---

### 4. Data Flow — Sequential vs. Parallel

**Current state**: `weather_extraction` loops over 195 capitals sequentially with a 0.5 s delay per location (`assets.py:32`). Minimum runtime is ~97 s just in sleep time. The entire run is single-threaded.

**Improvements**:
- Parallelize with `concurrent.futures.ThreadPoolExecutor(max_workers=10)`. A semaphore-based token bucket controls the OpenWeather rate limit (60 req/min free tier) while running multiple cities concurrently. Expected speedup: 5–8×.
- Long-term: migrate to `httpx` + `asyncio` for true async I/O, reducing extraction time to under 30 s.
- Separate `weather_extraction` and `historical_backfill` into independent parallel asset branches in the Dagster graph — they have no shared dependency and can run simultaneously.

---

### 5. Security

**Current state**: `api/main.py:75–80` sets `allow_origins=["*"]` with a comment noting it should be tightened for production — but it never is. The `city` query parameter in `/api/current` is passed into a `LIKE` clause with `%` wildcards; while DuckDB parameterises the value, crafted inputs like `%` or `_` can cause expensive full scans. Secrets are injected via a `.env` bind-mount into the container, making the entire key file accessible if the container is compromised.

**Improvements**:
- Set `allow_origins` to a specific list (e.g., `["http://localhost:3002"]`) configurable via environment variable.
- Add input validation on `city` — strip SQL wildcards or whitelist against `staging.world_capitals`.
- Add `slowapi` rate limiting to public API endpoints (e.g., 60 requests/minute per IP).
- Migrate secrets to Docker Secrets (`docker secret`) or AWS Secrets Manager; remove `.env` from the volume mount.
- Add request logging middleware to capture IP, endpoint, and response time for every API call.

---

### 6. Performance

**Current state**: No indexes on any table. All silver/gold queries do full table scans. `silver_weather_observations` joins on `(lat, lon)` — unindexed on a growing table. Bronze staging views are `materialized: view`, so every downstream reference re-executes the underlying SQL.

**Improvements**:
- Add DuckDB indexes at bronze load time: `CREATE INDEX IF NOT EXISTS idx_current_weather_city ON staging.current_weather(name)` and equivalent indexes on `lat, lon` and `_fetched_at`.
- Set `+materialized: table` for bronze staging models in `dbt_project.yml` to pre-compute and cache them.
- Add HTTP `Cache-Control: max-age=3600` headers on API responses — data is updated hourly so caching at the HTTP layer is safe and eliminates repeated DuckDB queries.
- Partition `staging.current_weather` by `DATE(_fetched_at)` so queries scoped to recent data skip historical partitions entirely.

---

### 7. Scalability

**Current state**: DuckDB allows only one active writer connection. When `bronze_load` writes and the API service reads, lock contention causes `IO Error: Could not set lock`. As the number of dashboard users grows, concurrent read connections will exhaust this model.

**Improvements**:
- Short-term: configure DuckDB WAL mode (`PRAGMA journal_mode='WAL'`) to allow one writer and multiple readers to coexist.
- Medium-term: introduce a connection pool (SQLAlchemy + `duckdb` dialect or a single `duckdb.DuckDBPyConnection` instance protected by a threading lock in FastAPI).
- Long-term: migrate the read layer to MotherDuck (managed serverless DuckDB) or Postgres to support multi-user, multi-writer workloads without file-level locking.
- Add `LIMIT`/`OFFSET` pagination to all list endpoints — `/api/current` currently returns all 195 rows in one response; at scale this becomes a memory and latency issue.

---

### 8. Data Integrity

**Current state**: No foreign key relationships are enforced between layers. `gold_temperature_monthly.city_name` can reference a city that no longer exists in `staging.world_capitals`. Coordinates drift slightly between runs (OpenWeather API returns slightly different lat/lon values for the same city), causing duplicate rows in silver and gold.

**Improvements**:
- Add dbt `relationships` tests: `gold_temperature_monthly.city_name` → `stg_world_capitals.city` and `gold_weather_summary.country_code` → `stg_world_capitals.country_code`.
- Normalize all coordinates to 2 decimal places at bronze load time in `bronze_loader.py` (`round(lat, 2)`, `round(lon, 2)`) to eliminate coordinate drift.
- Add an `is_active BOOLEAN DEFAULT TRUE` column to `staging.world_capitals` so removed cities are soft-deleted rather than orphaned.
- Define a surrogate key (`city_key = HASH(LOWER(city_name) || country_code)`) as the canonical join key across all layers, eliminating text-matching fragility.

---

### 9. Observability

**Current state**: Extraction errors are logged as Dagster warnings but there is no summary metric, no alerting, and no way to know how many cities failed in a given run. dbt test failures are not propagated back to the Dagster run status. If an asset fails at 2 AM, the on-call engineer won't know until someone notices the stale dashboard.

**Improvements**:
- Add Dagster `@asset_check` decorators on `capitals_load` (row count > 150), `bronze_load` (files loaded > 0), and `weather_dbt_assets` (gold tables row count in expected range).
- Add an `op_failure` sensor or `run_failure_sensor` that fires a Slack/email notification on any failed asset.
- Integrate dbt test results into the Dagster run: parse `dbt test` output and raise `Failure` if any test fails.
- Add a query timing middleware to FastAPI that logs `endpoint, duration_ms, row_count` for every request — this is the baseline for identifying slow queries.
- Add Prometheus counters to FastAPI (`/metrics` endpoint) for request count, latency histogram, and error rate.

---

### 10. Maintainability

**Current state**: Magic numbers are scattered across four files: `_INTER_LOCATION_DELAY_S = 0.5` in `assets.py`, `timeout=30` repeated in `extraction.py`, `rate_limit_wait_s=60.0` in `historical_extraction.py`, `BETWEEN 0 AND 24` in `gold_weather_summary.sql`. The `BASE_URL` for OpenWeather is duplicated in both `extraction.py` and `weather_source.py`. The `bronze/schema.yml` has a duplicate `columns:` block (copy-paste error) that silently ignores one definition.

**Improvements**:
- Centralise all tuneable constants in `config.py`: API URLs, delays, timeouts, forecast windows, rate-limit parameters.
- Fix the duplicate `columns:` block in `bronze/schema.yml`.
- Parameterise the forecast window in `gold_weather_summary.sql` with a dbt variable: `{{ var('forecast_summary_hours', 24) }}`.
- Add a `pyproject.toml` or `settings.py` dataclass for runtime configuration — makes it easy to override values in tests without touching source files.
- Consolidate all dbt model configs in `dbt_project.yml` rather than per-file `{{ config(...) }}` blocks, so materialization strategy can be changed in one place.

---

### 11. Testing

**Current state**: Unit tests cover the happy path for extraction and bronze loading. There are no integration tests that run the full pipeline. No tests verify idempotency (run the same data twice, assert no duplicates). No tests for rate-limit handling, partial write recovery, or DuckDB lock scenarios.

**Improvements**:
- Add an end-to-end integration test using a fixture DuckDB + fixture parquet files: run `load_all_to_bronze` twice on the same data, assert that the bronze table has the same row count after both runs (idempotency check).
- Add a contract test for each layer boundary: assert the expected column names and types in bronze match what silver models reference.
- Add chaos fixtures: a mock HTTP server that returns 429 after N requests — verify that `fetch_all_capitals_history` retries and completes all cities.
- Add parametrized edge-case tests: empty API response, single-city response, response with all-null temperatures.
- Add a performance regression test: bronze load of 10,000 synthetic rows must complete in under 5 seconds.

---

## Glossary

| Term | Meaning in this document |
|---|---|
| Idempotency | Running the same pipeline twice produces exactly the same result as running it once |
| Upsert | Insert new record OR update existing record if the primary key already exists — atomic, no race condition |
| Partition | Dividing a large table into smaller segments by a column (e.g., date) so queries can skip irrelevant segments |
| Asset check | A Dagster assertion attached to an asset that verifies data quality after materialisation |
| SCD Type 2 | Slowly Changing Dimension — tracks historical versions of a row by adding `valid_from` / `valid_to` dates |
| WAL mode | Write-Ahead Log — a database journaling mode that allows readers and a writer to coexist on the same file |
| dbt variable | A compile-time value in dbt (`{{ var('name', default) }}`) that can be overridden at run time without code change |
