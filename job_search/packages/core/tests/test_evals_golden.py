"""Tests for `core.evals.golden`'s file-based golden-set loading path.

The DB-backed path (`job_categorisation`) is exercised by Task 11's
tests once `core.evals.golden_db` exists; this module never touches
that branch.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.evals.golden import EvalConfigError, GoldenCase, load_golden_set

_SAMPLE_YAML = """
cases:
  - case_id: "case-001"
    input: {"title": "Senior Data Engineer"}
    expected: {"category": "data_engineer"}
  - case_id: "case-002"
    input: {"title": "Product Manager"}
    expected: {"category": "other"}
"""


class TestLoadGoldenSetFileBased(unittest.TestCase):
    """Tests for the file-based branch of `load_golden_set`."""

    def setUp(self) -> None:
        """Create a temp directory with one `<task>.yml` golden file.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.tmp_dir = tempfile.TemporaryDirectory()
        golden_dir = Path(self.tmp_dir.name)
        (golden_dir / "some_future_task.yml").write_text(_SAMPLE_YAML)
        self.golden_dir = golden_dir

    def tearDown(self) -> None:
        """Remove the temp directory created in `setUp`.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.tmp_dir.cleanup()

    def test_loads_cases_from_a_yaml_file(self) -> None:
        """A `<task>.yml` file under `golden_dir` loads as `GoldenCase`s.

        Args:
            None.

        Returns:
            None.

        Raises:
            AssertionError: If the loaded cases don't match the file.
        """
        cases = load_golden_set("some_future_task", golden_dir=self.golden_dir)
        self.assertEqual(len(cases), 2)
        self.assertEqual(
            cases[0],
            GoldenCase(
                case_id="case-001",
                input={"title": "Senior Data Engineer"},
                expected={"category": "data_engineer"},
            ),
        )

    def test_raises_when_no_source_is_registered_for_the_task(self) -> None:
        """A task with neither a DB-backed loader nor a YAML file errors.

        Args:
            None.

        Returns:
            None.

        Raises:
            AssertionError: If `EvalConfigError` is not raised.
        """
        with self.assertRaises(EvalConfigError):
            load_golden_set("truly_unregistered_task", golden_dir=self.golden_dir)


if __name__ == "__main__":
    unittest.main()
