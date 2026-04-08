"""
Tests for data extraction module.

These tests verify that the extraction module can:
1. Save data to parquet files in data_zone
2. Handle different data structures
3. Flatten nested dictionaries correctly
"""

import pytest
import os
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import tempfile
import shutil


# Import the extraction module
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from weather_forecaster_sources.extraction import (
    flatten_dict,
    save_to_parquet,
    save_list_to_parquet,
    get_data_zone_path,
    ensure_data_zone_exists,
)


class TestFlattenDict:
    """Tests for the flatten_dict function."""

    def test_flatten_simple_dict(self):
        """Test flattening a simple dictionary."""
        data = {"name": "John", "age": 30}
        result = flatten_dict(data)
        assert result == {"name": "John", "age": 30}

    def test_flatten_nested_dict(self):
        """Test flattening a nested dictionary."""
        data = {
            "user": {"name": "John", "address": {"city": "London", "country": "UK"}}
        }
        result = flatten_dict(data)
        assert "user_name" in result
        assert "user_address_city" in result
        assert "user_address_country" in result
        assert result["user_name"] == "John"
        assert result["user_address_city"] == "London"
        assert result["user_address_country"] == "UK"

    def test_flatten_with_list(self):
        """Test flattening a dictionary with list values."""
        data = {"tags": ["a", "b", "c"]}
        result = flatten_dict(data)
        assert "tags" in result
        # Lists should be converted to JSON strings
        import json

        assert json.loads(result["tags"]) == ["a", "b", "c"]


class TestSaveToParquet:
    """Tests for saving single-row data to parquet."""

    @pytest.fixture
    def temp_data_zone(self, tmp_path):
        """Create a temporary data_zone directory."""
        data_zone = tmp_path / "data_zone"
        data_zone.mkdir()
        return data_zone

    def test_save_simple_dict(self, tmp_path, monkeypatch):
        """Test saving a simple dictionary to parquet."""
        # Mock the data zone path
        data_zone = tmp_path / "data_zone"
        data_zone.mkdir()
        monkeypatch.setattr("weather_forecaster_sources.extraction.DATA_ZONE_PATH", data_zone)

        data = {"id": 1, "name": "Test"}
        filepath = save_to_parquet(data, "test_source", "20240315_120000")

        assert filepath.exists()
        # File is saved as {source}.parquet inside a timestamped folder
        assert filepath.name == "test_source.parquet"
        assert "20240315_120000" in str(filepath.parent)

        # Verify we can read it back
        df = pd.read_parquet(filepath)
        assert len(df) == 1
        assert df.iloc[0]["id"] == 1
        assert df.iloc[0]["name"] == "Test"

    def test_save_nested_dict(self, tmp_path, monkeypatch):
        """Test saving a nested dictionary to parquet."""
        data_zone = tmp_path / "data_zone"
        data_zone.mkdir()
        monkeypatch.setattr("weather_forecaster_sources.extraction.DATA_ZONE_PATH", data_zone)

        data = {"location": {"lat": 51.5, "lon": -0.1}, "value": 100}
        filepath = save_to_parquet(data, "nested_test", "20240315_120000")

        # Verify file exists and has flattened columns
        df = pd.read_parquet(filepath)
        assert "location_lat" in df.columns
        assert "location_lon" in df.columns
        assert "value" in df.columns


class TestSaveListToParquet:
    """Tests for saving list of records to parquet."""

    def test_save_list_of_dicts(self, tmp_path, monkeypatch):
        """Test saving a list of dictionaries to parquet."""
        data_zone = tmp_path / "data_zone"
        data_zone.mkdir()
        monkeypatch.setattr("weather_forecaster_sources.extraction.DATA_ZONE_PATH", data_zone)

        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Charlie"},
        ]
        filepath = save_list_to_parquet(data, "list_test", "20240315_120000")

        # Verify file exists and has correct data
        df = pd.read_parquet(filepath)
        assert len(df) == 3
        assert list(df["name"]) == ["Alice", "Bob", "Charlie"]


class TestDataZonePath:
    """Tests for data zone path functions."""

    def test_get_data_zone_path(self, tmp_path, monkeypatch):
        """Test getting the data zone path."""
        data_zone = tmp_path / "data_zone"
        data_zone.mkdir()
        monkeypatch.setattr("weather_forecaster_sources.extraction.DATA_ZONE_PATH", data_zone)

        result = get_data_zone_path()
        assert result == data_zone

    def test_ensure_data_zone_exists(self, tmp_path, monkeypatch):
        """Test ensuring data zone directory exists."""
        data_zone = tmp_path / "new_data_zone"
        monkeypatch.setattr("weather_forecaster_sources.extraction.DATA_ZONE_PATH", data_zone)

        result = ensure_data_zone_exists()
        assert result.exists()
        assert result.is_dir()


class TestDataZoneFiles:
    """Tests for data zone file operations."""

    def test_list_data_zone_files(self, tmp_path, monkeypatch):
        """Test listing parquet files in data zone."""
        data_zone = tmp_path / "data_zone"
        data_zone.mkdir()
        monkeypatch.setattr("weather_forecaster_sources.extraction.DATA_ZONE_PATH", data_zone)

        # Create some test parquet files
        df = pd.DataFrame({"a": [1, 2, 3]})
        pq.write_table(
            pa.Table.from_pandas(df), str(data_zone / "source1_20240315_120000.parquet")
        )
        pq.write_table(
            pa.Table.from_pandas(df), str(data_zone / "source2_20240315_120000.parquet")
        )

        from weather_forecaster_sources.extraction import list_data_zone_files

        files = list_data_zone_files()

        assert len(files) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
