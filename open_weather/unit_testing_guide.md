# Unit Testing Guide for Prototype Data Pipeline

This document contains gold code instructions for writing unit tests for the files in the `etl_sources/` directory.

## Project Structure

```
prototype/
├── etl_sources/
│   ├── __init__.py
│   └── sources/
│       ├── csv_source.py       # CSV file extraction
│       ├── parquet_source.py   # Parquet file extraction
│       ├── text_source.py      # Text/log file extraction
│       ├── rest_api.py         # REST API extraction
│       └── google_sheets.py    # Google Sheets extraction
└── tests/
    └── test_extraction.py      # Existing tests
```

## Testing Framework

- **Framework**: pytest
- **Location**: `tests/` directory
- **Configuration**: `pyproject.toml`

## How to Run Tests

### Run all tests
```bash
cd projects/ProjectPortfolio_3.0/prototype
pytest
```

### Run specific test file
```bash
pytest tests/test_extraction.py -v
```

### Run tests matching a pattern
```bash
pytest -k "test_csv" -v
```

## Test Patterns

### 1. Testing dlt Sources

All dlt sources should be tested for:
- Import capability
- Source creation with valid parameters
- Source creation with invalid parameters (error handling)
- Schema inference

#### Example: Testing CSV Source

```python
import pytest
import dlt
from etl_sources.sources import csv_source

class TestCSVSource:
    """Tests for CSV extraction source."""
    
    @pytest.fixture
    def sample_csv(self, tmp_path):
        """Create a sample CSV file for testing."""
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(
            "id,name,value\n"
            "1,Alice,100\n"
            "2,Bob,200\n"
        )
        return str(csv_file)
    
    def test_csv_source_creation(self, sample_csv):
        """Test creating a CSV source."""
        source = csv_source(file_path=sample_csv)
        assert source is not None
        assert hasattr(source, 'resources')
    
    def test_csv_source_with_delimiter(self, sample_csv):
        """Test CSV source with custom delimiter."""
        source = csv_source(file_path=sample_csv, delimiter=";")
        assert source is not None
    
    def test_csv_source_invalid_file(self):
        """Test CSV source with invalid file raises error."""
        with pytest.raises(Exception):
            csv_source(file_path="/nonexistent/file.csv")
```

### 2. Testing Source Import Paths

Always import from the correct path:

```python
# CORRECT - Import from etl_sources
from etl_sources.sources import csv_source, parquet_source, text_source

# CORRECT - Import from dlt.sources.filesystem for filesystem sources
from dlt.sources.filesystem import read_csv, read_parquet
```

### 3. Testing with Fixtures

Use pytest fixtures for common test data:

```python
@pytest.fixture
def sample_csv_data():
    """Return sample CSV data as string."""
    return "id,name,value\n1,Alice,100\n2,Bob,200\n"

@pytest.fixture
def sample_csv_file(tmp_path, sample_csv_data):
    """Create a temporary CSV file."""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(sample_csv_data)
    return csv_file

@pytest.fixture
def sample_parquet_file(tmp_path):
    """Create a temporary Parquet file."""
    import pandas as pd
    import pyarrow.parquet as pq
    
    parquet_file = tmp_path / "test.parquet"
    df = pd.DataFrame({'id': [1, 2], 'name': ['Alice', 'Bob']})
    pq.write_table(pa.Table.from_pandas(df), str(parquet_file))
    return str(parquet_file)
```

### 4. Testing Error Handling

```python
def test_parquet_source_invalid_file():
    """Test Parquet source with invalid file."""
    with pytest.raises(Exception):
        parquet_source(file_path="/nonexistent/file.parquet")

def test_csv_source_empty_file(tmp_path):
    """Test CSV source with empty file."""
    empty_file = tmp_path / "empty.csv"
    empty_file.write_text("")
    # Should handle gracefully or raise appropriate error
    source = csv_source(file_path=str(empty_file))
    assert source is not None
```

### 5. Testing Incremental Loading

For sources with incremental loading:

```python
def test_rest_api_source_with_incremental():
    """Test REST API source with incremental loading."""
    source = rest_api_source(
        name="test_api",
        base_url="https://api.example.com",
        endpoints=["users"],
        incremental_column="updated_at",
        primary_key="id"
    )
    assert source is not None
```

### 6. Testing Google Sheets

```python
@pytest.fixture
def mock_google_credentials(tmp_path):
    """Create mock Google credentials."""
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{}')
    return str(creds_file)

def test_google_sheets_source_creation(mock_google_credentials):
    """Test Google Sheets source creation."""
    # Note: Requires valid credentials or mocking
    source = google_sheets_source(
        spreadsheet_id="test_id",
        credentials_path=mock_google_credentials
    )
    assert source is not None
```

## dlt 1.x API Notes

- Use `dlt.sources.filesystem.read_csv` instead of deprecated methods
- Use `dlt.sources.filesystem.read_parquet` for Parquet files
- Note: `read_text` may not be available in all dlt versions
- Use `@dlt.source` decorator for creating sources
- Use `dlt.pipeline()` for running pipelines

## Mocking dlt

```python
from unittest.mock import patch, MagicMock

def test_csv_pipeline_run():
    """Test running a CSV pipeline with mocked destination."""
    with patch('dlt.pipeline') as mock_pipeline:
        mock_pipeline.return_value.run = MagicMock()
        
        pipeline = dlt.pipeline(
            pipeline_name="test_pipeline",
            destination="duckdb",
            dataset_name="test"
        )
        
        # Run your source
        # (test code here)
        
        mock_pipeline.assert_called_once()
```

## Code Coverage

Run with coverage:

```bash
pytest --cov=etl_sources --cov-report=html
```

## Best Practices

1. **Test one thing per test function**
2. **Use descriptive test names**: `test_<function>_<expected_behavior>`
3. **Use fixtures for reusable test data**
4. **Test both success and failure cases**
5. **Keep tests independent** - no shared state between tests
6. **Use temporary directories** (`tmp_path`) for file operations
7. **Mock external dependencies** (API calls, file systems when needed)

## Example Test File Template

```python
"""
Tests for etl_sources module.

Run with: pytest tests/ -v
"""
import pytest
import dlt
from pathlib import Path

# Import sources from etl_sources
from etl_sources.sources import (
    csv_source,
    parquet_source,
    text_source,
    rest_api_source,
    google_sheets_source,
)


class TestCSVSources:
    """Tests for CSV source functions."""
    
    @pytest.fixture
    def csv_file(self, tmp_path):
        """Create a test CSV file."""
        file = tmp_path / "test.csv"
        file.write_text("id,name\n1,Alice\n2,Bob\n")
        return str(file)
    
    def test_csv_source_creation(self, csv_file):
        """Test CSV source can be created."""
        source = csv_source(file_path=csv_file)
        assert source is not None
    
    # Add more tests...


class TestParquetSources:
    """Tests for Parquet source functions."""
    pass


class TestTextSources:
    """Tests for Text source functions."""
    pass


class TestRESTApiSources:
    """Tests for REST API source functions."""
    pass


class TestGoogleSheetsSources:
    """Tests for Google Sheets source functions."""
    pass
```

## Integration Tests

For integration tests that require actual data or external services:

```python
@pytest.mark.integration
def test_csv_to_duckdb():
    """Integration test: CSV to DuckDB."""
    # Requires DuckDB and actual data
    pytest.skip("Requires external services")
```

Run only unit tests:
```bash
pytest -m "not integration"
```

Run only integration tests:
```bash
pytest -m integration
```

## Tests TRun
On run completion, the unit test should be checked and rerun automcatically up to 5 times to ensure errors and warnings are removed.