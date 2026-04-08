# ETL Pipeline Rules

Rules and patterns for the weather_forecaster ETL pipeline using dlt 1.x.

## Package Structure

All source modules live in `weather_forecaster_sources/`:

| Module | Purpose |
|---|---|
| `config.py` | API key loading and environment management |
| `weather_source.py` | dlt source definitions (OpenWeather Free API 2.5) |
| `extraction.py` | API → parquet extraction with retry logic |
| `bronze_loader.py` | Parquet → DuckDB incremental loading |
| `pipeline_runner.py` | Orchestrates the full two-stage pipeline |

## dlt 1.x Patterns

### Import sources
```python
from weather_forecaster_sources.weather_source import current_weather, weather_forecast
```

### Create and run a dlt source
```python
import dlt

source = current_weather(api_key=api_key, lat=51.5074, lon=-0.1278, units="metric")
pipeline = dlt.pipeline(pipeline_name="weather_pipeline", destination="duckdb")
load_info = pipeline.run(source)
```

### Common dlt 1.x issues

| Issue | Solution |
|---|---|
| `add_primary_key()` fails | Use `apply_hints(primary_key=...)` instead |
| `read_csv(columns=...)` fails | Use filesystem pipe pattern |
| Invalid file error | dlt 1.x defers error to execution time |

## Load Modes

Always use the `LoadMode` enum — never pass raw strings:

```python
from weather_forecaster_sources.pipeline_runner import run_pipeline, LoadMode

# Incremental (default) — loads latest data_zone folder only
results = run_pipeline(api_key=..., lat=..., lon=..., load_mode=LoadMode.INCREMENTAL)

# Full reload — truncates tables, loads all historical folders
results = run_pipeline(api_key=..., lat=..., lon=..., load_mode=LoadMode.FULL_RELOAD)
```

## Composite Keys

Each bronze table has a defined composite key for deduplication. Never bypass these — always let `bronze_loader.py` manage inserts:

| Table | Composite Key Columns |
|---|---|
| `current_weather` | `lat, lon, _fetched_at` |
| `weather_forecast` | `lat, lon, dt, _fetched_at` |
| `geocoding` | `lat, lon, name, country, _fetched_at` |
| `reverse_geocoding` | `lat, lon, name, country, _fetched_at` |

## API Resilience

The extraction layer uses tenacity for retry logic — do not add manual retry around calls to `extract_*` functions, it is already built in:

- 3 attempts max
- Exponential backoff: 2–10 seconds between attempts
- Timeout: 30 seconds per request

## Secrets

- Use `OPENWEATHER_API_KEY` in `.env` for local development
- Never log or print API keys (the config module masks them in output)
- Validate config at pipeline start with `validate_config()`
- In production use environment variables, not `.env` files
