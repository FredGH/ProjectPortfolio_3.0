"""
Credentials Configuration Module

Loads API keys and secrets from environment variables.
Supports both OS environment variables and .env files.

Usage:
    from open_weather_sources.config import get_api_key, get_config
    
    # Get a specific API key
    api_key = get_api_key("OPENWEATHER_API_KEY")
    
    # Get all configuration
    config = get_config()
"""
import os
from typing import Optional
from pathlib import Path


def load_env_file(env_path: Optional[Path] = None) -> None:
    """
    Load environment variables from .env file if it exists.
    
    Args:
        env_path: Path to .env file. Defaults to project root.
    """
    if env_path is None:
        # Try to find .env in project root (parent of open_weather_sources)
        env_path = Path(__file__).parent.parent / ".env"
    
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)


def get_api_key(key_name: str, required: bool = False) -> Optional[str]:
    """
    Get an API key from environment variables.
    
    Args:
        key_name: Name of the environment variable (e.g., "OPENWEATHER_API_KEY")
        required: If True, raises ValueError when key is not found
    
    Returns:
        The API key value, or None if not found and not required
    
    Example:
        api_key = get_api_key("OPENWEATHER_API_KEY", required=True)
    """
    # Try to load .env file first (non-blocking)
    load_env_file()
    
    value = os.environ.get(key_name)
    
    if required and not value:
        raise ValueError(
            f"Required environment variable '{key_name}' is not set. "
            f"Create a .env file in the project root or set it in your environment."
        )
    
    return value


def get_config() -> dict:
    """
    Get all relevant configuration from environment variables.
    
    Returns:
        Dictionary with configuration values
    """
    load_env_file()
    
    return {
        "openweather_api_key": os.environ.get("OPENWEATHER_API_KEY"),
        # Add more services here as needed
        # "github_token": os.environ.get("GITHUB_TOKEN"),
        # "slack_webhook": os.environ.get("SLACK_WEBHOOK_URL"),
    }


def validate_config() -> list:
    """
    Validate that required configuration is present.
    
    Returns:
        List of missing required configuration keys
    """
    missing = []
    
    # Check for OpenWeather API key
    if not get_api_key("OPENWEATHER_API_KEY"):
        missing.append("OPENWEATHER_API_KEY")
    
    return missing


# Default configuration - used when no API key is provided
DEFAULT_API_KEY_PLACEHOLDER = "YOUR_API_KEY_HERE"


if __name__ == "__main__":
    # Test the config
    config = get_config()
    print("Current configuration:")
    for key, value in config.items():
        if value:
            masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
            print(f"  {key}: {masked}")
        else:
            print(f"  {key}: (not set)")
    
    missing = validate_config()
    if missing:
        print(f"\nMissing required config: {missing}")
        print("Copy .env.example to .env and add your API keys")
