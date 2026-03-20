# ETL Project Rules

**Priority File** - This is the auto-loaded quick reference for this project.

## Documentation (Read These)

| Topic | File | When |
|-------|------|------|
| Architecture | `architecture.md` | System design questions |
| API Reference | `api_reference.md` | How to use sources |
| Data Schemas | `data_dictionary.md` | Type mappings |
| Deployment | `deployment.md` | Docker/CI-CD |
| Testing | `unit_testing_guide.md` | Writing tests |

## API Key Management

**IMPORTANT**: Never hardcode API keys in source code!

1. Use `.env` files for local development (already in `.gitignore`)
2. Use environment variables for production
3. Copy `.env.example` to `.env` and add your keys, if it does not exist already create the `.env` file
4. Use the config module to load keys:

```python
from open_weather_sources.config import get_api_key

# Get API key from environment
api_key = get_api_key("OPENWEATHER_API_KEY", required=True)
```

## Key dlt 1.x Patterns

### Import Sources
```python
from etl_sources.sources import csv_source, rest_api_source
```

### Create Source
```python
source = csv_source("data.csv", primary_key="id")
```

### Run Pipeline
```python
import dlt
pipeline = dlt.pipeline(pipeline_name="my_pipeline", destination="duckdb")
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
pytest tests/test_etl_sources.py -v
```

## Project Location
`projects/ProjectPortfolio_3.0/open_weather/`

## Agent Role (If Needed)

For specialized ETL work, define an agent with:
- Role: ETL Data Engineer using dlt 1.x
- Capabilities: Source implementation, testing, documentation
- Guidelines: Read docs first, use dlt 1.x patterns, test thoroughly
