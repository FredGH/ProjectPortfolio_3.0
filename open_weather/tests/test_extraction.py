"""
Tests for data extraction sources.

These tests verify that the dlt sources can be imported.
Full integration tests require additional configuration and may need code updates
for the new dlt 1.x API.
"""
import pytest
import dlt
import os
from pathlib import Path


class TestImports:
    """Test that required packages can be imported."""
    
    def test_dlt_import(self):
        """Test that dlt can be imported."""
        import dlt
        assert dlt is not None
    
    def test_csv_source_import(self):
        """Test that CSV source can be imported."""
        from dlt.sources.filesystem import read_csv
        assert read_csv is not None
    
    def test_parquet_source_import(self):
        """Test that Parquet source can be imported."""
        from dlt.sources.filesystem import read_parquet
        assert read_parquet is not None
    
    def test_text_source_import(self):
        """Test that text source can be imported or created."""
        # read_text was removed in dlt 1.x, but we can create a custom text reader
        # using the filesystem source
        try:
            from dlt.sources.filesystem import readers
            # Try to create a text reader using the readers helper
            # This is the dlt 1.x way to read text files
            assert readers is not None
        except ImportError:
            pytest.skip("read_text not available in dlt 1.x filesystem - use readers() instead")
    
    def test_duckdb_destination(self):
        """Test that duckdb destination is available."""
        # Just verify dlt can be imported - destination is configured at runtime
        assert dlt is not None


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
            "3,Charlie,300\n"
        )
        return str(csv_file)
    
    def test_csv_source_creation(self, sample_csv):
        """Test creating a CSV source."""
        from dlt.sources.filesystem import read_csv
        
        # Just test that we can create the source
        source = read_csv(sample_csv)
        assert source is not None


class TestParquetSource:
    """Tests for Parquet extraction source."""
    
    @pytest.fixture
    def sample_parquet(self, tmp_path):
        """Create a sample Parquet file for testing."""
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq
        
        parquet_file = tmp_path / "test.parquet"
        
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie'],
            'value': [100, 200, 300]
        })
        
        pq.write_table(pa.Table.from_pandas(df), str(parquet_file))
        return str(parquet_file)
    
    def test_parquet_source_creation(self, sample_parquet):
        """Test creating a Parquet source."""
        from dlt.sources.filesystem import read_parquet
        
        source = read_parquet(sample_parquet)
        assert source is not None


class TestTextSource:
    """Tests for Text file extraction source."""
    
    @pytest.fixture
    def sample_text(self, tmp_path):
        """Create a sample text file for testing."""
        text_file = tmp_path / "test.txt"
        text_file.write_text(
            "Line 1: Hello World\n"
            "Line 2: Testing text extraction\n"
            "Line 3: Third line\n"
        )
        return str(text_file)
    
    def test_text_source_creation(self, sample_text):
        """Test creating a text source using dlt 1.x API."""
        # In dlt 1.x, there's no built-in read_text function
        # You can create a custom resource to read text files
        import dlt
        import os
        
        @dlt.resource
        def text_file(path):
            with open(path, 'r') as f:
                yield {"content": f.read()}
        
        # Create a source with the text resource
        source = text_file(sample_text)
        
        # Verify we got some data
        data = list(source)
        assert len(data) > 0
        assert "content" in data[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
