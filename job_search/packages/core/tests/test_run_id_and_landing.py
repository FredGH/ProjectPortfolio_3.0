from __future__ import annotations

import datetime
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from core.ingestion.landing import write_landing_record
from core.ingestion.run_id import generate_run_id


class TestGenerateRunId(unittest.TestCase):
    """Tests for generate_run_id's ULID shape and uniqueness."""

    def test_returns_a_26_character_ulid(self) -> None:
        """A ULID string is always 26 Crockford-base32 characters."""
        run_id = generate_run_id()
        self.assertEqual(len(run_id), 26)

    def test_successive_calls_are_unique(self) -> None:
        """Two calls never collide."""
        self.assertNotEqual(generate_run_id(), generate_run_id())


class TestWriteLandingRecord(unittest.TestCase):
    """Tests for write_landing_record's path layout and gzip-JSONL content."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()

    def test_writes_gzip_jsonl_at_the_expected_path(self) -> None:
        """The record lands at the expected path layout.

        Path: landing/source=.../dt=.../run_id=.../part-0001.jsonl.gz
        """
        fetched_at = datetime.datetime(2026, 8, 23, 12, 0, tzinfo=datetime.UTC)
        path = write_landing_record(
            self.landing_uri,
            source_name="linkedin_manual",
            run_id="01J000000000000000000000",
            record={"hello": "world"},
            fetched_at=fetched_at,
        )
        expected_suffix = (
            "source=linkedin_manual/dt=2026-08-23/"
            "run_id=01J000000000000000000000/part-0001.jsonl.gz"
        )
        self.assertTrue(path.endswith(expected_suffix))

        local_path = Path(self._tmp_dir.name) / expected_suffix
        with gzip.open(local_path, "rt") as handle:
            lines = handle.readlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {"hello": "world"})


if __name__ == "__main__":
    unittest.main()
