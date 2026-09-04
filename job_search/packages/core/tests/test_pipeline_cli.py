"""Test pipeline CLI."""

from __future__ import annotations

import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from core.settings import get_settings

# Snapshot and restore app/* entries in sys.modules to avoid ordering
# collisions. Two different app packages (apps/api, apps/pipeline) exist.
# When unittest discover imports this module, whichever app package loads
# first gets cached in sys.modules. We temporarily clear the cache so the
# pipeline app imports cleanly, then restore the original cache for tests
# that sort after us.
_saved_app_modules = {
    name: module
    for name, module in sys.modules.items()
    if name == "app" or name.startswith("app.")
}
for name in list(_saved_app_modules):
    del sys.modules[name]

# Insert pipeline app path and import.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import _KNOWN_SOURCES, main  # noqa: E402

# Restore api's app package (or any other pre-existing app module) in
# sys.modules so tests imported after this one resolve app.* correctly.
for name, module in _saved_app_modules.items():
    sys.modules[name] = module


class TestPipelineCli(unittest.TestCase):
    """Test the pipeline CLI entrypoint."""

    def test_runs_with_no_arguments_and_exits_zero(self) -> None:
        """Verify CLI runs with empty args and exits 0."""
        with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("pipeline scaffold ready", stdout.getvalue())

    def test_ingest_subcommand_unknown_source_reports_an_error_and_exits_nonzero(
        self,
    ) -> None:
        """An unregistered --source name fails clearly, not with a traceback."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            exit_code = main(["ingest", "--source", "nonexistent", "--query", "{}"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("nonexistent", stderr.getvalue())

    def test_ingest_subcommand_manual_source_requires_valid_json_query(self) -> None:
        """A malformed --query for the manual source fails clearly."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            exit_code = main(["ingest", "--source", "manual", "--query", "not json"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("query", stderr.getvalue().lower())

    def test_ingest_subcommand_manual_source_missing_field_fails_cleanly(
        self,
    ) -> None:
        """Valid JSON missing a required field reports ValueError, not a traceback."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            exit_code = main(["ingest", "--source", "manual", "--query", "{}"])
        self.assertEqual(exit_code, 1)
        stderr_value = stderr.getvalue()
        self.assertIn("source_name", stderr_value)
        self.assertNotIn("Traceback", stderr_value)

    def test_ingest_subcommand_adzuna_requires_region(self) -> None:
        """--source adzuna with no --region reports a clean error."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            exit_code = main(
                ["ingest", "--source", "adzuna", "--query", "data engineer"]
            )
        self.assertEqual(exit_code, 1)
        self.assertIn("region", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_ingest_subcommand_adzuna_requires_settings_keys(self) -> None:
        """--source adzuna with no Adzuna keys configured reports a clean error."""
        with (
            mock.patch.dict(
                os.environ,
                {"ADZUNA_APP_ID": "", "ADZUNA_APP_KEY": ""},
                clear=False,
            ),
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            get_settings.cache_clear()
            exit_code = main(
                [
                    "ingest",
                    "--source",
                    "adzuna",
                    "--query",
                    "data engineer",
                    "--region",
                    "gb",
                ]
            )
            get_settings.cache_clear()
        self.assertEqual(exit_code, 1)
        self.assertIn("adzuna", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_greenhouse_is_a_known_source(self) -> None:
        """greenhouse is registered in the CLI's known-source registry.

        Asserts directly against `_KNOWN_SOURCES` rather than calling
        `main()` end-to-end: an end-to-end call would perform a real
        Greenhouse ingest (network + DB writes to bronze.raw_jobs) from
        what is meant to be a fast, isolated unit test.
        """
        self.assertIn("greenhouse", _KNOWN_SOURCES)

    def test_ingest_subcommand_reed_requires_settings_key(self) -> None:
        """--source reed with no Reed key configured reports a clean error."""
        with (
            mock.patch.dict(os.environ, {"REED_API_KEY": ""}, clear=False),
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            get_settings.cache_clear()
            exit_code = main(["ingest", "--source", "reed", "--query", "data engineer"])
            get_settings.cache_clear()
        self.assertEqual(exit_code, 1)
        self.assertIn("reed", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_ingest_subcommand_jooble_requires_settings_key(self) -> None:
        """--source jooble with no Jooble key configured reports a clean error."""
        with (
            mock.patch.dict(os.environ, {"JOOBLE_KEY": ""}, clear=False),
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            get_settings.cache_clear()
            exit_code = main(
                ["ingest", "--source", "jooble", "--query", "data engineer"]
            )
            get_settings.cache_clear()
        self.assertEqual(exit_code, 1)
        self.assertIn("jooble", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
