# Data Engineering Pipeline - Prototype

This prototype implements the Extraction and Storage layers described in the Technical Proposal.

## Architecture Overview

```
Source Systems → dlt (Extraction) → DuckDB/Iceberg (Storage)
                                           ↓
                                    Bronze (Raw)
                                           ↓
                                    Silver (Cleaned)
                                           ↓
                                    Gold (Aggregated)
```

## Prerequisites

- Python 3.11+
- Docker (not Docker Desktop - use Colima or Rancher Desktop)
- Google Cloud service account (for Google Sheets)

## Installing Docker

If Docker is not installed, follow these steps:

### Option 1: Install Docker Desktop (Recommended)

1. Create the required directory:
   ```bash
   sudo mkdir -p /usr/local/cli-plugins && sudo chown $USER:admin /usr/local/cli-plugins
   ```

2. Install Docker Desktop:
   ```bash
   brew install --cask docker
   ```

3. If you see an error "Please download the latest Apple Silicon build", download from:
   https://desktop.docker.com/mac/main/arm64/Docker.dmg

4. Open the downloaded .dmg file and drag Docker.app to Applications

5. Open Docker Desktop from `/Applications/Docker.app`

6. Wait for Docker to start (whale icon in menu bar)

### Option 2: Install Colima (Lightweight Alternative)

```bash
brew install colima
colima start
```

### Option 3: Install Rancher Desktop

```bash
brew install --cask rancher-desktop
```

## Quick Start

### 1. Install Dependencies

```bash
# Navigate to project directory
cd projects/ProjectPortfolio_3.0/weather_forecaster

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

### 2. Run the Pipeline

```bash
# Default: Incremental mode (loads latest data_zone folder only)
python weather_forecaster_sources/pipeline_runner.py

# Full reload mode (truncates tables and reloads all data)
python weather_forecaster_sources/pipeline_runner.py full
```

### 3. Start Local Infrastructure (Optional)

```bash
# Start Docker Compose (newer syntax)
docker compose up -d

# Or if you have older docker-compose
docker-compose up -d

# Verify services are running
docker compose ps
```

### 3. Run Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_extraction.py -v

# Run with coverage
pytest --cov=prototype --cov-report=html
```

## Testing the Solution

### Unit Tests

Run unit tests for each data source:

```bash
# Test REST API source
pytest tests/test_rest_api.py -v

# Test CSV source
pytest tests/test_csv_source.py -v

# Test Parquet source
pytest tests/test_parquet_source.py -v

# Test Google Sheets source
pytest tests/test_google_sheets.py -v
```

### Integration Tests

Run end-to-end pipeline tests:

```bash
# Test full pipeline (extraction → storage)
pytest tests/integration/ -v

# Test with sample data
python -m pytest tests/integration/test_pipeline.py -v --sample-data
```

### Manual Testing

Test individual sources manually:

```bash
# Test CSV extraction
python -c "
from dlt.sources import csv_source
import dlt

pipeline = dlt.pipeline(pipeline_name='test_csv', destination='duckdb', dataset_name='test')
info = pipeline.run(csv_source('tests/fixtures/sample.csv'))
print(info)
"

# Test Parquet extraction
python -c "
from dlt.sources import parquet_source
import dlt

pipeline = dlt.pipeline(pipeline_name='test_parquet', destination='duckdb', dataset_name='test')
info = pipeline.run(parquet_source('tests/fixtures/sample.parquet'))
print(info)
"
```

## Testing Without Docker

You can test the extraction sources directly without Docker:

```bash
# Create test fixtures
mkdir -p tests/fixtures data/bronze data/silver data/gold

# Create sample CSV
echo "id,name,value" > tests/fixtures/sample.csv
echo "1,Alice,100" >> tests/fixtures/sample.csv
echo "2,Bob,200" >> tests/fixtures/sample.csv
echo "3,Charlie,300" >> tests/fixtures/sample.csv

# Create sample Parquet using Python
python -c "
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

df = pd.DataFrame({
    'id': [1, 2, 3],
    'name': ['Alice', 'Bob', 'Charlie'],
    'value': [100, 200, 300]
})
pq.write_table(pa.Table.from_pandas(df), 'tests/fixtures/sample.parquet')
"

# Run tests without Docker
pytest tests/test_extraction.py -v
```

## Verification

### Verify Bronze Layer (Raw Data)

```python
import duckdb

conn = duckdb.connect('data/duckdb/prototype.duckdb')

# List all tables
conn.sql("SHOW TABLES").show()

# Query bronze data
conn.sql("SELECT * FROM bronze.sample_csv LIMIT 10").show()
```

### Verify Data Quality

```bash
# Run Great Expectations
python -c "
import great_expectations as gx

context = gx.get_context()
expectation_suite = context.get_expectation_suite('bronze_suite')
print(expectation_suite)
"
```

## Troubleshooting

### Common Issues

1. **Docker not found:**
   - Install Docker or use Colima: `brew install colima`
   - Start Colima: `colima start`

2. **Permission errors:**
   ```bash
   # Fix permissions
   chmod -R 755 data/
   ```

3. **Missing credentials:**
   ```bash
   # Set Google Sheets credentials
   export GOOGLE_SHEETS_CREDENTIALS="path/to/credentials.json"
   ```

4. **Port conflicts:**
   - The docker-compose uses ports: 9000, 9001, 5432, 5433

## Testing Commands Summary

| Command | Description |
|---------|-------------|
| `pytest` | Run all tests |
| `pytest -v` | Run with verbose output |
| `pytest --cov` | Run with coverage report |
| `pytest -k "csv"` | Run tests matching "csv" |
| `docker compose up -d` | Start local infrastructure |
| `docker compose logs -f` | View logs |

## Next Steps

1. Add more test cases for edge cases
2. Implement dbt transformations for Silver/Gold layers
3. Add Great Expectations data quality checks
4. Set up CI/CD pipeline

## Recommended VS Code/Cursor Extensions

The following extensions are recommended for working with this project:

| Extension | Purpose |
|-----------|---------|
| anthropic.claude-code | Claude AI integration |
| chuckjonas.duckdb | DuckDB database client (alternative: dbcode.dbcode) |
| dbcode.dbcode | DBcode - Database management and query tool |
| kilocode.kilo-code | Kilo Code AI assistant |
| ms-python.python | Python language support |
| ms-python.debugpy | Python debugger |
| ms-toolsai.jupyter | Jupyter notebook support |
| ms-vscode.live-server | Local development server |
| anysphere.cursorpyright | Pyright type checker for Cursor |

### Installing Extensions

To install these extensions in Cursor or VS Code:

```bash
# Using cursor CLI
cursor --install-extension dbcode.dbcode
cursor --install-extension chuckjonas.duckdb
cursor --install-extension ms-python.python
cursor --install-extension anthropic.claude-code
cursor --install-extension kilocode.kilo-code
cursor --install-extension ms-toolsai.jupyter
cursor --install-extension anysphere.cursorpyright

# Or manually:
# 1. Open Cursor/VS Code
# 2. Press Cmd+Shift+X
# 3. Search for the extension name
# 4. Click Install
```

### DuckDB Connection

To connect to the DuckDB database using the extension:

1. **Database file:** `prototype/openweather_ingestion.duckdb`
2. **Connection name:** `weather_db`
3. **Schema:** Data is stored in the `bronze` schema

Example query:
```sql
SELECT * FROM bronze.current_weather;
```
