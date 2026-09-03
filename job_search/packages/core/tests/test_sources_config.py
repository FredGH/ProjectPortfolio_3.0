from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.ingestion.sources_config import SourceConfig, load_sources_config

_SAMPLE_YAML = """
sources:
  adzuna:
    enabled: true
    auth:
      app_id: ${ADZUNA_APP_ID}
      app_key: ${ADZUNA_APP_KEY}
    calls_per_hour: 40
    concurrency: 2
    backoff:
      base: 2
      max_retries: 5
    regions: [gb, ie, fr, de, us]
  reed:
    enabled: false
"""


class TestLoadSourcesConfig(unittest.TestCase):
    """Tests for load_sources_config's parsing of the sources.yml schema."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        tmp.write(_SAMPLE_YAML)
        tmp.close()
        self.config_path = Path(tmp.name)

    def tearDown(self) -> None:
        self.config_path.unlink(missing_ok=True)

    def test_parses_a_fully_specified_source(self) -> None:
        """Every field of a fully-specified source is parsed correctly."""
        config = load_sources_config(self.config_path)
        adzuna = config["adzuna"]
        self.assertEqual(
            adzuna,
            SourceConfig(
                enabled=True,
                calls_per_hour=40,
                concurrency=2,
                backoff_base=2.0,
                backoff_max_retries=5,
                regions=["gb", "ie", "fr", "de", "us"],
            ),
        )

    def test_a_minimal_disabled_source_defaults_the_rest_to_none(self) -> None:
        """A source with only `enabled` set gets None for everything else."""
        config = load_sources_config(self.config_path)
        reed = config["reed"]
        self.assertEqual(reed.enabled, False)
        self.assertIsNone(reed.calls_per_hour)
        self.assertIsNone(reed.regions)

    def test_missing_file_returns_an_empty_mapping(self) -> None:
        """An empty (or absent) sources.yml is valid — no connectors configured yet."""
        empty_path = self.config_path.parent / "does_not_exist.yml"
        config = load_sources_config(empty_path)
        self.assertEqual(config, {})


if __name__ == "__main__":
    unittest.main()
