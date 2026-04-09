# ETL Project Rules

**Priority File** - This is the auto-loaded quick reference for this project.

## Documentation (Read These)

| Topic | File | When |
|-------|------|------|
| Architecture | `.claude/docs/architecture.md` | System design questions |
| API Reference | `.claude/docs/api-reference.md` | How to use sources |
| Data Schemas | `.claude/docs/data-dictionary.md` | Type mappings |
| Deployment | `.claude/docs/deployment.md` | Docker/CI-CD |
| Testing | `unit_testing_guide.md` | Writing tests |

## API Key Management

**IMPORTANT**: Never hardcode API keys in source code!

1. Use `.env` files for local development (already in `.gitignore`)
2. Use environment variables for production
3. Copy `.env.example` to `.env` and add your keys, if it does not exist already create the `.env` file
4. Use the config module to load keys:

```python
from weather_forecaster_sources.config import get_api_key

# Get API key from environment
api_key = get_api_key("OPENWEATHER_API_KEY", required=True)
```

## Key dlt 1.x Patterns

### Import Sources
```python
from weather_forecaster_sources.weather_source import current_weather, weather_forecast
```

### Create Source
```python
source = current_weather(api_key=api_key, lat=51.5074, lon=-0.1278, units="metric")
```

### Run Pipeline
```python
import dlt
pipeline = dlt.pipeline(pipeline_name="weather_pipeline", destination="duckdb")
load_info = pipeline.run(source)
```

## Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| `add_primary_key()` fails | Use `apply_hints(primary_key=...)` |
| `read_csv(columns=...)` fails | Use filesystem pipe pattern |
| Invalid file error | dlt 1.x defers error to execution |

## Testing
```bash
PYTHONPATH=. python3.11 -m pytest tests/test_extraction.py tests/test_bronze_loader.py tests/test_weather_mock.py -v
```

## Project Location
`projects/ProjectPortfolio_3.0/weather_forecaster/`

## Agent Role (If Needed)

For specialized ETL work, define an agent with:
- Role: ETL Data Engineer using dlt 1.x
- Capabilities: Source implementation, testing, documentation
- Guidelines: Read docs first, use dlt 1.x patterns, test thoroughly
