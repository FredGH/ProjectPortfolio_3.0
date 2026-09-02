"""Test pipeline CLI."""

from __future__ import annotations

import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

# Clean up cached app modules from other test contexts (e.g., apps/api).
# sys.modules caches by key, so if tests import different app packages
# within the same process, the first one wins unless we explicitly clear.
for key in list(sys.modules.keys()):
    if key == "app" or key.startswith("app."):
        del sys.modules[key]

# Insert pipeline app path so 'from app.cli import main' resolves to it.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402


class TestPipelineCli(unittest.TestCase):
    """Test the pipeline CLI entrypoint."""

    def test_runs_with_no_arguments_and_exits_zero(self) -> None:
        """Verify CLI runs with empty args and exits 0."""
        with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("pipeline scaffold ready", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
