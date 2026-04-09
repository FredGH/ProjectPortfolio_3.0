# CLAUDE.md

Team instructions for this project. This file is committed to git and shared with all team members.

## Project Overview

Weather Forecaster — a data engineering pipeline that extracts weather data from the OpenWeather Free API 2.5 and loads it incrementally into a DuckDB bronze layer via an intermediate parquet `data_zone`.

**Two-stage architecture:**
1. **Extraction** — OpenWeather API → parquet files in `data_zone/{timestamp}/`
2. **Bronze Load** — parquet → DuckDB using composite keys for deduplication

See [architecture.md](.claude/docs/architecture.md) for the full system design.

## Tech Stack

- Language: Python 3.11
- ETL Framework: dlt 1.x
- Database: DuckDB (bronze layer)
- Testing: pytest
- Data Source: OpenWeather Free API 2.5

## Development Workflow

1. Create a feature branch from `main`
2. Implement changes following the rules in `.claude/rules/`
3. Run unit tests before committing — all 55 must pass
4. Open a pull request for review

## Key Commands

```bash
# Run unit tests (no API key required)
PYTHONPATH=. python3.11 -m pytest tests/test_extraction.py tests/test_bronze_loader.py tests/test_weather_mock.py -v

# Run integration tests (requires valid OPENWEATHER_API_KEY in .env)
PYTHONPATH=. python3.11 -m pytest tests/test_weather_api.py -v -m integration

# Run pipeline — incremental mode (default)
PYTHONPATH=. python weather_forecaster_sources/pipeline_runner.py

# Run pipeline — full reload
PYTHONPATH=. python weather_forecaster_sources/pipeline_runner.py full

# Code quality
ruff check . && isort . && black .
```

## API Key Management

**Never hardcode API keys in source code.**

1. Copy `.env.example` to `.env` and add your `OPENWEATHER_API_KEY`
2. The `.env` file is gitignored — never commit it
3. Load keys via the config module:

```python
from weather_forecaster_sources.config import get_api_key

api_key = get_api_key("OPENWEATHER_API_KEY", required=True)
```

## Reference Documentation

| Topic | File | When to read |
|---|---|---|
| Architecture & data flow | [architecture.md](.claude/docs/architecture.md) | System design questions |
| API endpoints & function signatures | [api-reference.md](.claude/docs/api-reference.md) | How to call sources |
| Data schemas & type mappings | [data-dictionary.md](.claude/docs/data-dictionary.md) | Column types, composite keys |
| Docker & CI/CD | [deployment.md](.claude/docs/deployment.md) | Running in production |
| Writing tests | [unit_testing_guide.md](unit_testing_guide.md) | Adding new tests |

## Rules

Follow the conventions in `.claude/rules/`:

| Rule file | Covers |
|---|---|
| `python-style.md` | Formatting, naming, type hints |
| `python-testing.md` | pytest conventions, test structure |
| `etl-pipeline.md` | dlt 1.x patterns, load modes, composite keys |
| `sql-style.md` | DuckDB query conventions |
