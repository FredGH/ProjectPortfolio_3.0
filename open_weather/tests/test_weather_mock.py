"""
Unit tests for OpenWeather API sources using mock data.

These tests verify the source creation and parameter handling without 
making actual API calls.

Run with: pytest tests/test_weather_mock.py -v
"""
import pytest
import dlt
from unittest.mock import patch, MagicMock
from pathlib import Path


# Test configuration
TEST_API_KEY = "test_api_key"
TEST_LAT = 51.5074
TEST_LON = -0.1278


# Sample mock API responses (used for validation tests)
MOCK_CURRENT_WEATHER_RESPONSE = {
    "lat": 51.5074,
    "lon": -0.1278,
    "timezone": "Europe/London",
    "current": {
        "temp": 5.2,
        "weather": [{"id": 801, "main": "Clouds", "description": "few clouds"}]
    }
}

MOCK_FORECAST_RESPONSE = {
    "lat": 51.5074,
    "lon": -0.1278,
    "hourly": [{"temp": 5.2}],
    "daily": [{"temp": {"day": 5.2}}]
}

MOCK_ALERTS_RESPONSE = {
    "lat": 51.5074,
    "lon": -0.1278,
    "alerts": [{"event": "Wind Warning"}]
}


class TestWeatherSourceCreation:
    """Test source creation without making actual API calls."""
    
    def test_current_weather_source_creation(self):
        """Test creating current weather source returns a DltResource."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            units="metric"
        )
        
        # Should return a DltResource (decorated with @dlt.resource)
        assert source is not None
        # DltResource doesn't have 'resources' attribute - that's on dlt.source
        assert hasattr(source, 'name')  # DltResource has a name
    
    def test_weather_forecast_source_creation(self):
        """Test creating weather forecast source returns a DltResource."""
        from open_weather_sources.weather_source import weather_forecast
        
        source = weather_forecast(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            units="metric"
        )
        
        assert source is not None
        assert hasattr(source, 'name')
    
    def test_weather_alerts_source_creation(self):
        """Test creating weather alerts source returns a DltResource."""
        from open_weather_sources.weather_source import weather_alerts
        
        source = weather_alerts(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            units="metric"
        )
        
        assert source is not None
        assert hasattr(source, 'name')
    
    def test_current_weather_source_name(self):
        """Test current weather source has correct name."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(TEST_API_KEY, TEST_LAT, TEST_LON)
        assert source.name == "current_weather"
    
    def test_weather_forecast_source_name(self):
        """Test forecast source has correct name."""
        from open_weather_sources.weather_source import weather_forecast
        
        source = weather_forecast(TEST_API_KEY, TEST_LAT, TEST_LON)
        assert source.name == "weather_forecast"
    
    def test_weather_alerts_source_name(self):
        """Test alerts source has correct name."""
        from open_weather_sources.weather_source import weather_alerts
        
        source = weather_alerts(TEST_API_KEY, TEST_LAT, TEST_LON)
        assert source.name == "weather_alerts"


class TestWeatherSourceParameters:
    """Test parameter handling in sources."""
    
    def test_default_units(self):
        """Test default units parameter."""
        from open_weather_sources.weather_source import current_weather
        
        # Should not raise - just test creation
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON
        )
        assert source is not None
    
    def test_metric_units(self):
        """Test metric units."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            units="metric"
        )
        assert source is not None
    
    def test_imperial_units(self):
        """Test imperial units."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            units="imperial"
        )
        assert source is not None
    
    def test_standard_units(self):
        """Test standard units."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            units="standard"
        )
        assert source is not None
    
    def test_english_language(self):
        """Test English language."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            lang="en"
        )
        assert source is not None
    
    def test_spanish_language(self):
        """Test Spanish language."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            lang="es"
        )
        assert source is not None
    
    def test_french_language(self):
        """Test French language."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=TEST_LAT,
            lon=TEST_LON,
            lang="fr"
        )
        assert source is not None
    
    def test_different_coordinates(self):
        """Test with different coordinates."""
        from open_weather_sources.weather_source import current_weather
        
        # New York
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=40.7128,
            lon=-74.0060,
            units="imperial"
        )
        assert source is not None
        
        # Tokyo
        source = current_weather(
            api_key=TEST_API_KEY,
            lat=35.6762,
            lon=139.6503,
            units="metric"
        )
        assert source is not None


class TestMockDataStructures:
    """Tests for mock data structure validation."""
    
    def test_current_weather_response_structure(self):
        """Verify mock current weather has expected structure."""
        assert "current" in MOCK_CURRENT_WEATHER_RESPONSE
        assert "temp" in MOCK_CURRENT_WEATHER_RESPONSE["current"]
        assert "weather" in MOCK_CURRENT_WEATHER_RESPONSE["current"]
    
    def test_forecast_response_structure(self):
        """Verify mock forecast has expected structure."""
        assert "hourly" in MOCK_FORECAST_RESPONSE
        assert "daily" in MOCK_FORECAST_RESPONSE
    
    def test_alerts_response_structure(self):
        """Verify mock alerts has expected structure."""
        assert "alerts" in MOCK_ALERTS_RESPONSE
    
    def test_coordinates_in_response(self):
        """Verify coordinates are present in responses."""
        assert MOCK_CURRENT_WEATHER_RESPONSE["lat"] == TEST_LAT
        assert MOCK_CURRENT_WEATHER_RESPONSE["lon"] == TEST_LON


class TestBaseURL:
    """Test base URL configuration."""
    
    def test_base_url_constant(self):
        """Test that BASE_URL is defined correctly."""
        from open_weather_sources.weather_source import BASE_URL
        
        assert BASE_URL == "https://api.openweathermap.org/data/2.5"


class TestDLTResourceAttributes:
    """Test DltResource specific attributes."""
    
    def test_resource_has_table_name(self):
        """Test that resources have table name set."""
        from open_weather_sources.weather_source import current_weather, weather_forecast
        
        src1 = current_weather(TEST_API_KEY, TEST_LAT, TEST_LON)
        src2 = weather_forecast(TEST_API_KEY, TEST_LAT, TEST_LON)
        
        assert src1.name == "current_weather"
        assert src2.name == "weather_forecast"
    
    def test_resource_has_write_disposition(self):
        """Test that resources have write disposition."""
        from open_weather_sources.weather_source import current_weather
        
        source = current_weather(TEST_API_KEY, TEST_LAT, TEST_LON)
        
        # Resources should have write_disposition attribute
        assert hasattr(source, 'write_disposition')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
