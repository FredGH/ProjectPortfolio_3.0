# Weather Forecaster

A data engineering pipeline that extracts weather data from the [OpenWeather Free API 2.5](https://openweathermap.org/api) and loads it incrementally into a DuckDB bronze layer via an intermediate parquet staging area (`data_zone`).

## Architecture

```
OpenWeather API (Free 2.5)
        │
        ▼  extraction.py
data/data_zone/{timestamp}/        ← parquet files (one per source)
        │
        ▼  bronze_loader.py
data/bronze/bronze.duckdb          ← DuckDB bronze layer (incremental merge)
        │
        ▼  dbt Fusion
dbt/models/
  bronze/   ← views over raw DuckDB tables (stg_*)
  silver/   ← enriched views (labels, derived fields)
  gold/     ← materialised summary tables
```

**Two load modes:**
- **Incremental** (default) — loads the latest `data_zone` folder only
- **Full reload** — truncates all tables and replays every historical folder

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

### 3. Run the pipeline

```bash
# Incremental mode (default)
PYTHONPATH=. python weather_forecaster_sources/pipeline_runner.py

# Full reload (truncates and replays all data_zone folders)
PYTHONPATH=. python weather_forecaster_sources/pipeline_runner.py full
```

### 4. Run unit tests (no API key required)

```bash
PYTHONPATH=. python3.11 -m pytest \
    tests/test_extraction.py \
    tests/test_bronze_loader.py \
    tests/test_weather_mock.py \
    -v
```

### 5. Run integration tests (API key required)

```bash
PYTHONPATH=. python3.11 -m pytest tests/test_weather_api.py -v -m integration
```

### 6. Query the bronze layer

```python
import duckdb

conn = duckdb.connect("data/bronze/bronze.duckdb")
conn.sql("SHOW TABLES").show()
conn.sql("SELECT * FROM current_weather LIMIT 5").show()
conn.sql("SELECT * FROM weather_forecast LIMIT 5").show()
conn.close()
```

### 7. Run dbt Fusion locally

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
      path: /absolute/path/to/data/bronze/bronze.duckdb   # update this
      schema: main
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
dbt run --select staging.*                   # layer wildcard
```

Output is written back into `bronze.duckdb` under schemas `bronze`, `silver`, and `gold`.

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

The `dbt` service builds dbt Fusion into an image and runs models against the bronze DuckDB file. Run the pipeline first to populate `data/bronze/bronze.duckdb`.

```bash
# First time — create host volume directories
mkdir -p dbt/target dbt/logs

# Build + run + test all models (default)
docker compose run --rm dbt

# Run models only (no tests)
docker compose run --rm dbt run

# Tests only
docker compose run --rm dbt test

# Single model
docker compose run --rm dbt run --select gold_weather_summary

# Layer wildcard
docker compose run --rm dbt run --select staging.*

# Verify connection
docker compose run --rm dbt debug
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

## Project Structure

```
weather_forecaster/
├── weather_forecaster_sources/    # ETL source modules
│   ├── config.py                  # API key and env management
│   ├── weather_source.py          # dlt source definitions
│   ├── extraction.py              # API → parquet (with retry)
│   ├── bronze_loader.py           # Parquet → DuckDB (incremental)
│   └── pipeline_runner.py         # Orchestrates the full pipeline
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
│   └── bronze/bronze.duckdb       # DuckDB bronze database
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

**Port conflicts:** The project uses no external ports. All data is local (DuckDB file + parquet).

**`OPENWEATHER_API_KEY` missing:** The unit tests do not need it. Only the pipeline and integration tests (`test_weather_api.py`) require a key.

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

### DuckDB connection

After running the pipeline, connect to:

```
File:   data/bronze/bronze.duckdb
Schema: (default)
```

Example query:
```sql
SELECT * FROM current_weather ORDER BY _fetched_at DESC LIMIT 10;
SELECT * FROM weather_forecast WHERE dt_txt LIKE '2026%' LIMIT 10;
```
