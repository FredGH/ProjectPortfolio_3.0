"""Test pipeline CLI."""

from __future__ import annotations

import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

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

from app.cli import main  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
