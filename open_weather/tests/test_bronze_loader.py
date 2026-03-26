"""
Tests for bronze layer loader module.

These tests verify that the bronze loader can:
1. Load parquet files to DuckDB
2. Handle incremental loads with composite keys
3. Deduplicate records correctly
"""
import pytest
import os
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import tempfile
import shutil
import duckdb


# Import the bronze loader module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from open_weather_sources.bronze_loader import (
    read_parquet_file,
    get_composite_key_columns,
    create_composite_key,
    load_parquet_to_bronze,
    COMPOSITE_KEYS,
)


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary DuckDB database."""
    db_path = tmp_path / "test_bronze.duckdb"
    yield db_path
    if db_path.exists():
        db_path.unlink()


class TestCompositeKeys:
    """Tests for composite key functionality."""
    
    def test_get_composite_key_columns_current_weather(self):
        """Test getting composite key columns for current_weather."""
        keys = get_composite_key_columns("current_weather")
        assert "lat" in keys
        assert "lon" in keys
        assert "_fetched_at" in keys
    
    def test_get_composite_key_columns_weather_forecast(self):
        """Test getting composite key columns for weather_forecast."""
        keys = get_composite_key_columns("weather_forecast")
        assert "lat" in keys
        assert "lon" in keys
        assert "dt" in keys
        assert "_fetched_at" in keys
    
    def test_get_composite_key_columns_geocoding(self):
        """Test getting composite key columns for geocoding."""
        keys = get_composite_key_columns("geocoding")
        assert "lat" in keys
        assert "lon" in keys
        assert "name" in keys
        assert "country" in keys
    
    def test_get_composite_key_columns_unknown_table(self):
        """Test getting composite key for unknown table falls back to _fetched_at."""
        keys = get_composite_key_columns("unknown_table")
        assert "_fetched_at" in keys


class TestCreateCompositeKey:
    """Tests for creating composite keys."""
    
    def test_create_composite_key_with_columns(self):
        """Test creating composite key with available columns."""
        df = pd.DataFrame({
            "lat": [51.5, 51.6],
            "lon": [-0.1, -0.2],
            "_fetched_at": ["2024-01-01", "2024-01-02"],
            "value": [100, 200]
        })
        
        result = create_composite_key(df, "current_weather")
        
        assert "_composite_key" in result.columns
        assert result.iloc[0]["_composite_key"] == "51.5|-0.1|2024-01-01"
    
    def test_create_composite_key_with_partial_columns(self):
        """Test creating composite key with only some columns available."""
        df = pd.DataFrame({
            "lat": [51.5],
            "value": [100]
        })
        
        result = create_composite_key(df, "current_weather")
        
        assert "_composite_key" in result.columns
        # Should only use available columns
        assert "51.5" in result.iloc[0]["_composite_key"]
    
    def test_create_composite_key_fallback(self):
        """Test composite key fallback when no key columns available."""
        df = pd.DataFrame({
            "value": [100]
        })
        
        result = create_composite_key(df, "current_weather")
        
        assert "_composite_key" in result.columns


class TestLoadParquetToBronze:
    """Tests for loading parquet to bronze layer."""
    
    @pytest.fixture
    def temp_parquet(self, tmp_path):
        """Create a temporary parquet file."""
        parquet_file = tmp_path / "test.parquet"
        
        df = pd.DataFrame({
            "lat": [51.5, 51.6],
            "lon": [-0.1, -0.2],
            "_fetched_at": ["2024-01-01T00:00:00+00:00", "2024-01-02T00:00:00+00:00"],
            "value": [100, 200]
        })
        
        pq.write_table(pa.Table.from_pandas(df), str(parquet_file))
        return parquet_file
    
    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create a temporary DuckDB database."""
        db_path = tmp_path / "test_bronze.duckdb"
        yield db_path
        if db_path.exists():
            db_path.unlink()
    
    def test_load_creates_new_table(self, temp_parquet, temp_db):
        """Test loading parquet creates a new table."""
        result = load_parquet_to_bronze(temp_parquet, "test_table", temp_db)
        
        assert result["status"] == "created"
        assert result["rows"] == 2
        
        # Verify table exists and has data
        conn = duckdb.connect(str(temp_db))
        count = conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
        conn.close()
        
        assert count == 2
    
    def test_load_incremental_skips_duplicates(self, temp_parquet, temp_db):
        """Test that incremental load skips duplicate records."""
        # First load
        result1 = load_parquet_to_bronze(temp_parquet, "test_table", temp_db)
        assert result1["status"] == "created"
        
        # Second load with same data
        result2 = load_parquet_to_bronze(temp_parquet, "test_table", temp_db)
        
        # Should skip duplicates
        assert result2["status"] == "skipped"
        assert result2["duplicates"] == 2
        assert result2["rows"] == 0
    
    def test_load_incremental_adds_new_records(self, temp_parquet, temp_db):
        """Test that incremental load adds new records."""
        # First load
        result1 = load_parquet_to_bronze(temp_parquet, "test_table", temp_db)
        assert result1["rows"] == 2
        
        # Create new parquet with different data
        new_parquet = temp_parquet.parent / "test2.parquet"
        df2 = pd.DataFrame({
            "lat": [51.7, 51.8],
            "lon": [-0.3, -0.4],
            "_fetched_at": ["2024-01-03T00:00:00+00:00", "2024-01-04T00:00:00+00:00"],
            "value": [300, 400]
        })
        pq.write_table(pa.Table.from_pandas(df2), str(new_parquet))
        
        # Second load with different data
        result2 = load_parquet_to_bronze(new_parquet, "test_table", temp_db)
        
        # Should add new records
        assert result2["status"] == "loaded"
        assert result2["rows"] == 2
        assert result2["duplicates"] == 0
        
        # Verify total count
        conn = duckdb.connect(str(temp_db))
        count = conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
        conn.close()
        
        assert count == 4
    
    def test_load_empty_dataframe(self, temp_db, tmp_path):
        """Test loading an empty DataFrame is skipped."""
        empty_parquet = tmp_path / "empty.parquet"
        df = pd.DataFrame()
        pq.write_table(pa.Table.from_pandas(df), str(empty_parquet))
        
        result = load_parquet_to_bronze(empty_parquet, "empty_table", temp_db)
        
        assert result["status"] == "skipped"
        assert result["reason"] == "empty dataframe"


class TestBronzeTableStats:
    """Tests for bronze table statistics."""
    
    def test_get_bronze_table_stats_empty(self, temp_db):
        """Test getting stats for empty database."""
        from open_weather_sources.bronze_loader import get_bronze_table_stats
        
        # Database doesn't exist
        stats = get_bronze_table_stats(temp_db)
        assert stats == {}
    
    def test_get_bronze_table_stats_with_data(self, temp_db):
        """Test getting stats for database with tables."""
        from open_weather_sources.bronze_loader import get_bronze_table_stats
        
        # Create a table
        conn = duckdb.connect(str(temp_db))
        conn.execute("CREATE TABLE test_table (id INTEGER, name VARCHAR)")
        conn.execute("INSERT INTO test_table VALUES (1, 'Alice'), (2, 'Bob')")
        conn.close()
        
        stats = get_bronze_table_stats(temp_db)
        
        assert "test_table" in stats
        assert stats["test_table"]["row_count"] == 2


class TestCompositeKeyConstants:
    """Tests for composite key constants."""
    
    def test_composite_keys_defined(self):
        """Test that all expected composite keys are defined."""
        assert "current_weather" in COMPOSITE_KEYS
        assert "weather_forecast" in COMPOSITE_KEYS
        assert "geocoding" in COMPOSITE_KEYS
        assert "reverse_geocoding" in COMPOSITE_KEYS
    
    def test_composite_keys_all_lists(self):
        """Test that all composite keys are lists."""
        for key, value in COMPOSITE_KEYS.items():
            assert isinstance(value, list), f"{key} composite key should be a list"
            assert len(value) > 0, f"{key} composite key should not be empty"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
