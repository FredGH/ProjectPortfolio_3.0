from __future__ import annotations

import datetime
import json
import tempfile
import unittest
from pathlib import Path

from core.ingestion.run_metadata import RunMetadata, write_run_metadata


class TestWriteRunMetadata(unittest.TestCase):
    """Tests for write_run_metadata's path layout and JSON content."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()

    def test_writes_json_at_the_expected_path(self) -> None:
        """The metadata lands at _runs/{source_name}/{run_id}.json."""
        metadata = RunMetadata(
            run_id="01J000000000000000000000",
            source_name="adzuna",
            query="data engineer",
            records=12,
            started_at=datetime.datetime(2026, 9, 3, 10, 0, tzinfo=datetime.UTC),
            finished_at=datetime.datetime(2026, 9, 3, 10, 1, tzinfo=datetime.UTC),
            status="success",
        )
        path = write_run_metadata(self.landing_uri, metadata)
        expected_suffix = "_runs/adzuna/01J000000000000000000000.json"
        self.assertTrue(path.endswith(expected_suffix))

        local_path = Path(self._tmp_dir.name) / expected_suffix
        content = json.loads(local_path.read_text())
        self.assertEqual(content["records"], 12)
        self.assertEqual(content["status"], "success")
        self.assertEqual(content["query"], "data engineer")


if __name__ == "__main__":
    unittest.main()
