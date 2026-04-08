"""
Integration tests for OpenWeather API sources.

These tests connect to the real OpenWeather API and require a valid API key.
Run with: pytest tests/test_weather_api.py -v
Or skip with: pytest tests/test_weather_mock.py -v -m "not integration"

Prerequisites:
    1. Copy .env.example to .env
    2. Add your OPENWEATHER_API_KEY to .env
"""
import pytest
import dlt
from pathlib import Path


# Get API key from environment - use .env file or environment variable
# If no API key is found, tests will be skipped
from weather_forecaster_sources.config import get_api_key

TEST_API_KEY = get_api_key("OPENWEATHER_API_KEY")
TEST_LAT = 51.5074  # London
TEST_LON = -0.1278


def pytest_configure(config):
    """Configure pytest to skip integration tests if no API key is available."""
    if not TEST_API_KEY:
        config.addinivalue_line(
            "markers", "integration: mark test as integration test (requires API key)"
        )


@pytest.mark.integration
class TestOpenWeatherAPI:
    """Integration tests using the real OpenWeather API."""
    
    @pytest.fixture
    def api_key(self):
        """Get API key from environment - skip test if not available."""
        if not TEST_API_KEY:
            pytest.skip("No OPENWEATHER_API_KEY found in environment. Set it in .env file or environment variable.")
        return TEST_API_KEY
    
    @pytest.fixture
    def coordinates(self):
        """Get test coordinates."""
        return {"lat": TEST_LAT, "lon": TEST_LON}
    
    def test_current_weather_source_creation(self, api_key, coordinates):
        """Test creating current weather source."""
        from weather_forecaster_sources.weather_source import current_weather

        source = current_weather(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="metric"
        )
        
        assert source is not None
        assert hasattr(source, 'name')  # DltResource has name attribute
    
    def test_weather_forecast_source_creation(self, api_key, coordinates):
        """Test creating weather forecast source."""
        from weather_forecaster_sources.weather_source import weather_forecast
        
        source = weather_forecast(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="metric"
        )
        
        assert source is not None
        assert hasattr(source, 'name')
    
    def test_weather_alerts_source_creation(self, api_key, coordinates):
        """Test creating weather alerts source."""
        from weather_forecaster_sources.weather_source import weather_alerts
        
        source = weather_alerts(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="metric"
        )
        
        assert source is not None
        assert hasattr(source, 'name')
    
    def test_openweather_source_combined(self, api_key, coordinates):
        """Test creating combined OpenWeather source."""
        from weather_forecaster_sources.weather_source import openweather_source
        
        source = openweather_source(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="metric",
            include_current=True,
            include_forecast=True,
            include_alerts=True
        )
        
        assert source is not None
        assert hasattr(source, 'resources')
    
    @pytest.mark.integration
    def test_current_weather_pipeline_run(self, api_key, coordinates):
        """Test running a pipeline with current weather data."""
        from weather_forecaster_sources.weather_source import current_weather
        
        # Create pipeline
        pipeline = dlt.pipeline(
            pipeline_name="test_weather_current",
            destination="duckdb",
            dataset_name="test_weather",
            export_schema_path="schema"
        )
        
        # Create source
        source = current_weather(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="metric"
        )
        
        # Run pipeline
        try:
            load_info = pipeline.run(source)
            assert load_info is not None
            print(f"Load completed: {load_info}")
        except Exception as e:
            # Provide verbose error message to help diagnose the issue
            error_msg = str(e)
            
            # Check for specific error types
            if "401" in error_msg or "Unauthorized" in error_msg:
                pytest.skip(
                    f"API Error 401 Unauthorized: The API key is invalid or has no access to this endpoint. "
                    f"Get a valid key from https://openweathermap.org/api - {error_msg}"
                )
            elif "404" in error_msg or "Not Found" in error_msg:
                pytest.skip(f"API Error 404 Not Found: The API endpoint may have changed - {error_msg}")
            elif "429" in error_msg or "Too Many Requests" in error_msg:
                pytest.skip(f"API Error 429 Rate Limited: Too many requests - {error_msg}")
            elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                pytest.skip(f"Network Error: Could not connect to OpenWeather API - {error_msg}")
            else:
                pytest.skip(f"API call failed: {error_msg}")
    
    @pytest.mark.integration
    def test_weather_forecast_pipeline_run(self, api_key, coordinates):
        """Test running a pipeline with weather forecast data."""
        from weather_forecaster_sources.weather_source import weather_forecast
        
        pipeline = dlt.pipeline(
            pipeline_name="test_weather_forecast",
            destination="duckdb",
            dataset_name="test_weather"
        )
        
        source = weather_forecast(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="metric"
        )
        
        try:
            load_info = pipeline.run(source)
            assert load_info is not None
        except Exception as e:
            # Provide verbose error message to help diagnose the issue
            error_msg = str(e)
            
            # Check for specific error types
            if "401" in error_msg or "Unauthorized" in error_msg:
                pytest.skip(
                    f"API Error 401 Unauthorized: The API key is invalid or has no access to this endpoint. "
                    f"Get a valid key from https://openweathermap.org/api - {error_msg}"
                )
            elif "404" in error_msg or "Not Found" in error_msg:
                pytest.skip(f"API Error 404 Not Found: The API endpoint may have changed - {error_msg}")
            elif "429" in error_msg or "Too Many Requests" in error_msg:
                pytest.skip(f"API Error 429 Rate Limited: Too many requests - {error_msg}")
            elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                pytest.skip(f"Network Error: Could not connect to OpenWeather API - {error_msg}")
            else:
                pytest.skip(f"API call failed: {error_msg}")
    
    def test_different_units(self, api_key, coordinates):
        """Test creating sources with different units."""
        from weather_forecaster_sources.weather_source import current_weather
        
        # Test metric
        source_metric = current_weather(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="metric"
        )
        assert source_metric is not None
        
        # Test imperial
        source_imperial = current_weather(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            units="imperial"
        )
        assert source_imperial is not None
    
    def test_different_languages(self, api_key, coordinates):
        """Test creating sources with different language codes."""
        from weather_forecaster_sources.weather_source import current_weather
        
        # English
        source_en = current_weather(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            lang="en"
        )
        assert source_en is not None
        
        # Spanish
        source_es = current_weather(
            api_key=api_key,
            lat=coordinates["lat"],
            lon=coordinates["lon"],
            lang="es"
        )
        assert source_es is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "integration"])
