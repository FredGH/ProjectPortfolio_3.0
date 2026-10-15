"""Unit tests for the company-intel config."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.company_intel.config import CompanyIntelConfig, load_config


class TestLoadConfig(unittest.TestCase):
    """load_config reads the YAML, with defaults for missing keys."""

    def test_the_shipped_file_has_the_spec_values(self) -> None:
        self.assertEqual(load_config(), CompanyIntelConfig(90, 7, 8))

    def test_missing_keys_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yml"
            path.write_text("refresh_after_days: 30\n")
            self.assertEqual(load_config(path), CompanyIntelConfig(30, 7, 8))

    def test_a_non_positive_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yml"
            path.write_text("max_pages: 0\n")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
