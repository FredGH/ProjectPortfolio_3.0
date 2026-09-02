from __future__ import annotations

import unittest

from core.settings import Settings, get_settings


class TestSettings(unittest.TestCase):
    def test_reads_required_fields_from_env(self) -> None:
        settings = Settings(
            _env_file=None,
            database_url="postgresql+psycopg://owner:pw@localhost:5432/job_search",
            app_database_url="postgresql+psycopg://app:pw@localhost:5432/job_search",
        )
        self.assertEqual(
            settings.database_url,
            "postgresql+psycopg://owner:pw@localhost:5432/job_search",
        )

    def test_defaults_env_to_local(self) -> None:
        settings = Settings(
            _env_file=None,
            database_url="postgresql+psycopg://owner:pw@localhost:5432/job_search",
            app_database_url="postgresql+psycopg://app:pw@localhost:5432/job_search",
        )
        self.assertEqual(settings.env, "local")

    def test_rejects_unknown_env_value(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                env="staging",
                database_url="postgresql+psycopg://owner:pw@localhost:5432/job_search",
                app_database_url="postgresql+psycopg://app:pw@localhost:5432/job_search",
            )

    def test_get_settings_returns_cached_singleton(self) -> None:
        import os

        os.environ["DATABASE_URL"] = "postgresql+psycopg://owner:pw@localhost:5432/job_search"
        os.environ["APP_DATABASE_URL"] = "postgresql+psycopg://app:pw@localhost:5432/job_search"
        get_settings.cache_clear()
        first = get_settings()
        second = get_settings()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
