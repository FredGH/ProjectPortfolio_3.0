# Weather Forecaster

A data engineering pipeline that extracts weather data from the [OpenWeather Free API 2.5](https://openweathermap.org/api) and loads it incrementally into a DuckDB bronze layer via an intermediate parquet staging area (`data_zone`). Orchestrated with **Dagster**, which schedules extraction, bronze loading, and dbt transformations.

## Architecture

```
OpenWeather API (Free 2.5)
        │
        ▼  extraction.py           [Dagster: weather_extraction asset]
data/data_zone/{timestamp}/        ← parquet files (one per source)
        │
        ▼  bronze_loader.py        [Dagster: bronze_load asset]
data/etl/weather_forecaster.duckdb ← DuckDB bronze layer (incremental merge)
        │
        ▼  dbt Fusion              [Dagster: weather_dbt_assets]
dbt/models/
  bronze/   ← views over raw DuckDB tables (stg_*)
  silver/   ← enriched views (labels, derived fields)
  gold/     ← materialised summary tables
```

**Two load modes:**
- **Incremental** (default) — loads the latest `data_zone` folder only
- **Full reload** — truncates all tables and replays every historical folder

**Dagster schedules:**
- `extraction_schedule` — runs every hour at `:00` (API extract + bronze load)
- `dbt_schedule` — runs every hour at `:15` (all dbt models, after extraction)

---

## Prerequisites

- Python 3.11+  **or** Docker (to run without a local Python install)
- An OpenWeather API key — free tier at [openweathermap.org](https://openweathermap.org/api)

---

## Running Locally

### 1. Set up environment

```bash
# From the project root
python3.11 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements-dev.txt      # includes pytest
```

### 2. Configure API key

```bash
cp .env.example .env
# Edit .env and set:  OPENWEATHER_API_KEY=your_key_here
```

### 3. Run the pipeline manually

```bash
# Incremental mode (default)
PYTHONPATH=. python weather_forecaster_sources/pipeline_runner.py

# Full reload (truncates and replays all data_zone folders)
PYTHONPATH=. python weather_forecaster_sources/pipeline_runner.py full
```

### 4. Start Dagster (Dagit UI)

```bash
PYTHONPATH=. ./venv/bin/python3.11 -m dagster dev -m orchestration
```

Open [http://localhost:3000](http://localhost:3000) to view assets, trigger jobs manually, and enable/disable schedules.

The two schedules are **off by default**. Enable them in the Dagit UI under **Automation → Schedules**, or trigger jobs manually from the **Assets** or **Jobs** pages.

> **Note:** Always use the full venv path (`./venv/bin/python3.11`) — `source venv/bin/activate` may be shadowed by a parent virtual environment.

### 5. Run unit tests (no API key required)

```bash
PYTHONPATH=. python3.11 -m pytest \
    tests/test_extraction.py \
    tests/test_bronze_loader.py \
    tests/test_weather_mock.py \
    -v
```

### 6. Run integration tests (API key required)

```bash
PYTHONPATH=. python3.11 -m pytest tests/test_weather_api.py -v -m integration
```

### 7. Query the bronze layer

```python
import duckdb

conn = duckdb.connect("data/etl/weather_forecaster.duckdb")
conn.sql("SELECT table_schema, table_name FROM information_schema.tables ORDER BY 1, 2").show()
conn.sql("SELECT * FROM staging.current_weather LIMIT 5").show()
conn.sql("SELECT * FROM staging.weather_forecast LIMIT 5").show()
conn.close()
```

### 8. Run dbt Fusion locally

dbt Fusion (Rust-based) must be installed separately — it is not a Python package.

```bash
# Install dbt Fusion (one-time)
curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh | sh -s -- --update
source ~/.bashrc   # or ~/.zshrc — adds ~/.local/bin to PATH
```

Add the `weather_forecaster` profile to `~/.dbt/profiles.yml`:

```yaml
weather_forecaster:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: /absolute/path/to/data/etl/weather_forecaster.duckdb   # update this
      schema: staging
      threads: 4
```

Then run dbt from the `dbt/` subdirectory:

```bash
cd dbt/

dbt debug                                    # verify connection
dbt build                                    # compile + run + test all models
dbt run                                      # run models only
dbt test                                     # tests only
dbt run --select gold_weather_summary        # single model
dbt run --select bronze.*                    # layer wildcard (bronze / silver / gold)
```

Output is written back into `weather_forecaster.duckdb` under schemas `bronze`, `silver`, and `gold`.

---

## Running with Docker

Docker is the quickest way to run without configuring a local Python environment.

### Prerequisites

Docker Desktop (or Colima / Rancher Desktop):

```bash
# Docker Desktop
brew install --cask docker

# Lightweight alternative
brew install colima && colima start
```

### Run the unit tests

```bash
docker compose run --rm test
```

This builds the `test` image (Python 3.11-slim + all dev dependencies) and runs the 55 unit tests. No API key or `.env` file needed.

Expected output:
```
55 passed in ~3s
```

### Run the pipeline

Ensure `.env` exists with a valid `OPENWEATHER_API_KEY` before running:

```bash
cp .env.example .env
# Edit .env — add your key

# Incremental mode (default)
docker compose run --rm pipeline

# Full reload
docker compose run --rm pipeline \
    python weather_forecaster_sources/pipeline_runner.py full
```

Pipeline output (parquet files and DuckDB) is written to `./data/` on the host via a volume mount.

### Run dbt in Docker

The `dbt` service builds dbt Fusion into an image and runs models against the bronze DuckDB file. Run the pipeline first to populate `data/etl/weather_forecaster.duckdb`.

```bash
# First time — create host volume directories
mkdir -p dbt/target dbt/logs

docker compose run --rm dbt          # build + run + test all models (default)
docker compose run --rm dbt run      # run models only
docker compose run --rm dbt test     # tests only
docker compose run --rm dbt debug    # verify connection

# Selectors
docker compose run --rm dbt run --select gold_weather_summary
docker compose run --rm dbt run --select bronze.*    # bronze / silver / gold
```

Compiled artefacts are persisted to `dbt/target/` and logs to `dbt/logs/` on the host via volume mounts.

### Build images manually

```bash
# Test image
docker build --target test -t weather-forecaster:test .

# Production image
docker build --target production -t weather-forecaster:latest .

# dbt image
docker build --target dbt -t weather-forecaster:dbt .
```

### Docker image stages

| Stage | Base | Contents | Purpose |
|---|---|---|---|
| `base` | python:3.11-slim | gcc only | Shared build layer |
| `deps` | base | runtime packages from `requirements.txt` | Package cache for production |
| `test` | base | dev packages + source + tests | Run unit tests in CI |
| `production` | python:3.11-slim | runtime packages + source only | Run the pipeline |
| `dbt-fusion` | python:3.11-slim | curl + dbt Fusion binary | Shared base for dbt stage |
| `dbt` | dbt-fusion | dbt project files + profiles | Run dbt models against DuckDB |

---

## Dagster Deployment (Docker + AWS)

The Dagster stack runs four services that mirror each other exactly between local Docker and AWS ECS — no code changes are needed to move between environments, only infrastructure.

### Stack overview

| Service | Role | Local | AWS |
|---|---|---|---|
| `dagster-postgres` | Event log, run history, schedule state | Docker container | RDS Postgres |
| `dagster-code` | gRPC code-location — serves asset definitions, executes Python assets + dbt | Docker container | ECS Fargate task |
| `dagster-webserver` | Dagit UI on `:3000` | Docker container | ECS Fargate task behind ALB |
| `dagster-daemon` | Evaluates schedules and sensors every 30 s | Docker container | ECS Fargate task |

### Local → AWS mapping

| Local Docker | AWS equivalent |
|---|---|
| `docker compose -f docker-compose.dagster.yml up` | ECS Fargate cluster |
| `dagster-postgres` container | RDS Postgres (same env vars) |
| `./data/` volume mount (DuckDB + parquet) | EFS mount or S3 |
| `./dagster_home/` volume mount (Dagster state) | EFS mount |
| `dagster-code` hostname in `workspace.yaml` | ECS Service Discovery DNS |
| `localhost:3000` | Application Load Balancer → Dagit task |
| `LocalComputeLogManager` in `dagster.yaml` | `S3ComputeLogManager` |
| `DefaultRunLauncher` in `dagster.yaml` | `EcsRunLauncher` |

### Workflow: local → AWS

1. Build and test the full stack locally with Docker Compose
2. Push the image to ECR (`docker tag` + `docker push`)
3. Point ECS task definitions at the ECR image URI
4. Swap the two lines in `dagster_home/dagster.yaml` (compute logs → S3, launcher → ECS)
5. Update `workspace.yaml` host to the ECS Service Discovery DNS name

### Run locally

```bash
# Build the shared image (code-location + webserver + daemon all use the same image)
docker compose -f docker-compose.dagster.yml build

# Start all four services
docker compose -f docker-compose.dagster.yml up

# Open Dagit
open http://localhost:3000
```

Enable the schedules in the Dagit UI under **Automation → Schedules**, or trigger jobs manually from **Assets** or **Jobs**.

```bash
# Tear down (Postgres data volume is preserved)
docker compose -f docker-compose.dagster.yml down

# Full reset including all stored run history
docker compose -f docker-compose.dagster.yml down -v
```

### Key files

| File | Purpose |
|---|---|
| `Dockerfile.dagster` | Multi-stage build: installs deps, pre-compiles dbt manifest, produces final image |
| `docker-compose.dagster.yml` | Defines the four-service local stack |
| `workspace.yaml` | Tells webserver and daemon where the code-location gRPC server is |
| `dagster_home/dagster.yaml` | Dagster instance config: Postgres storage, compute log path, run launcher |
| `requirements-dagster.txt` | Dagster packages + `dbt-duckdb` (pip-based dbt for `DbtCliResource`) |

---

## Project Structure

```
weather_forecaster/
├── weather_forecaster_sources/    # ETL source modules
│   ├── config.py                  # API key and env management
│   ├── weather_source.py          # dlt source definitions
│   ├── extraction.py              # API → parquet (with retry)
│   ├── bronze_loader.py           # Parquet → DuckDB (incremental)
│   └── pipeline_runner.py         # Orchestrates the full pipeline
├── orchestration/                 # Dagster orchestration layer
│   ├── assets.py                  # weather_extraction + bronze_load assets
│   ├── dbt_assets.py              # dbt model assets (via dagster-dbt)
│   ├── schedules.py               # Hourly extraction and dbt schedules
│   ├── definitions.py             # Dagster Definitions entry point
│   └── __init__.py
├── tests/
│   ├── test_extraction.py         # Unit tests — extraction
│   ├── test_bronze_loader.py      # Unit tests — bronze loader
│   ├── test_weather_mock.py       # Unit tests — dlt sources (mocked)
│   └── test_weather_api.py        # Integration tests (real API)
├── dbt/
│   ├── dbt_project.yml            # dbt project config (name, materializations)
│   ├── profiles.yml               # Docker-only connection profile
│   ├── models/
│   │   ├── bronze/                # Views over raw DuckDB tables (stg_*)
│   │   ├── silver/                # Enriched views (labels, derived fields)
│   │   └── gold/                  # Materialised summary tables
│   ├── target/                    # Compiled artefacts (gitignored)
│   └── logs/                      # dbt run logs (gitignored)
├── data/                          # Generated at runtime (gitignored)
│   ├── data_zone/                 # Parquet staging — one folder per run
│   └── etl/weather_forecaster.duckdb          # DuckDB database
├── .claude/
│   ├── docs/                      # Architecture, API reference, data dictionary
│   └── rules/                     # Claude coding rules for this project
├── Dockerfile                     # Multi-stage build (test / pipeline / dbt)
├── docker-compose.yml             # test + pipeline + dbt services
├── requirements.txt               # Runtime dependencies
├── requirements-dev.txt           # Adds pytest for local dev / test stage
├── pyproject.toml                 # Project metadata and pytest config
└── .env.example                   # Environment variable template
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENWEATHER_API_KEY` | Yes (pipeline) | Free API key from openweathermap.org |

Copy `.env.example` to `.env` and fill in the key. The `.env` file is gitignored and never baked into Docker images.

---

## Troubleshooting

**Docker daemon not running:**
```bash
open -a Docker          # start Docker Desktop
# or
colima start            # start Colima
```

**Permission errors on `data/`:**
```bash
chmod -R 755 data/
```

**Port conflicts:** Dagit runs on port 3000 by default. If it is in use, specify another:
```bash
PYTHONPATH=. ./venv/bin/python3.11 -m dagster dev -m orchestration --port 3001
```

**`OPENWEATHER_API_KEY` missing:** The unit tests do not need it. Only the pipeline and integration tests (`test_weather_api.py`) require a key.

**`dagster dev` uses the wrong Python:** Always invoke via the project venv directly — `source venv/bin/activate` may be overridden by a parent venv. Use the full path:
```bash
PYTHONPATH=. ./venv/bin/python3.11 -m dagster dev -m orchestration
```

---

## Recommended VS Code Extensions

| Extension | Purpose |
|---|---|
| `anthropic.claude-code` | Claude AI integration |
| `ms-python.python` | Python language support |
| `ms-python.debugpy` | Python debugger |
| `dbcode.dbcode` | Database management and query tool |
| `chuckjonas.duckdb` | DuckDB browser (alternative) |
| `ms-toolsai.jupyter` | Jupyter notebook support |

### DuckDB file location

The database file is always written to the host at:

```
<project_root>/data/etl/weather_forecaster.duckdb
```

This is true whether you run the pipeline locally or via Docker. The Docker volume mount (`./data:/app/data`) ensures the container writes to the same host path — the file is never stored inside the container.

### Querying with DBeaver

1. **New Database Connection** → search for **DuckDB** → install driver if prompted
2. Set **Path** to the absolute path of `weather_forecaster.duckdb` on your machine
3. **Test Connection** → **Finish**

**Connecting while Dagster is running (read-only mode)**

DuckDB allows multiple simultaneous read-only connections — the restriction is only on write connections. You can keep the Dagster stack running and connect DBeaver at the same time by enabling read-only mode:

- Open your DuckDB connection settings → **Driver Properties**
- Add property: `read_only` = `true`

In read-only mode you can query and browse all tables normally — Dagster continues writing without interference. If you need to run ad-hoc writes from DBeaver, stop the stack first:

```bash
docker compose -f docker-compose.dagster.yml down
```

Useful queries after running the pipeline and dbt:

```sql
-- Raw tables (written by the pipeline)
SELECT * FROM staging.current_weather;
SELECT * FROM staging.weather_forecast ORDER BY forecast_at LIMIT 10;

-- dbt bronze views
SELECT * FROM bronze.stg_current_weather;
SELECT * FROM bronze.stg_weather_forecast ORDER BY forecast_at LIMIT 10;

-- dbt silver views
SELECT * FROM silver.silver_weather_observations;
SELECT * FROM silver.silver_forecast_intervals ORDER BY forecast_at;

-- dbt gold summary (one row per location)
SELECT * FROM gold.gold_weather_summary;
```
