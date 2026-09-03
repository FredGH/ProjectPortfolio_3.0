from __future__ import annotations

import os
import unittest
from unittest import mock

from core.settings import Settings, get_settings


class TestSettings(unittest.TestCase):
    """Test the Settings class and get_settings() function."""

    def test_reads_required_fields_from_env(self) -> None:
        """Required fields are read and accessible as attributes."""
        database_url = "postgresql+psycopg://owner:pw@localhost:5432/job_search"
        app_database_url = "postgresql+psycopg://app:pw@localhost:5432/job_search"
        settings = Settings(
            _env_file=None,
            database_url=database_url,
            app_database_url=app_database_url,
        )
        self.assertEqual(settings.database_url, database_url)

    def test_defaults_env_to_local(self) -> None:
        """env field defaults to 'local' when not provided."""
        database_url = "postgresql+psycopg://owner:pw@localhost:5432/job_search"
        app_database_url = "postgresql+psycopg://app:pw@localhost:5432/job_search"
        settings = Settings(
            _env_file=None,
            database_url=database_url,
            app_database_url=app_database_url,
        )
        self.assertEqual(settings.env, "local")

    def test_rejects_unknown_env_value(self) -> None:
        """Unknown env values raise ValueError."""
        database_url = "postgresql+psycopg://owner:pw@localhost:5432/job_search"
        app_database_url = "postgresql+psycopg://app:pw@localhost:5432/job_search"
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                env="staging",
                database_url=database_url,
                app_database_url=app_database_url,
            )

    def test_get_settings_returns_cached_singleton(self) -> None:
        """get_settings() returns the same instance on repeated calls."""
        database_url = "postgresql+psycopg://owner:pw@localhost:5432/job_search"
        app_database_url = "postgresql+psycopg://app:pw@localhost:5432/job_search"

        # Use mock.patch.dict to isolate environment changes to this test
        with mock.patch.dict(
            os.environ,
            {
                "DATABASE_URL": database_url,
                "APP_DATABASE_URL": app_database_url,
            },
        ):
            get_settings.cache_clear()
            self.addCleanup(get_settings.cache_clear)
            first = get_settings()
            second = get_settings()
            self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
