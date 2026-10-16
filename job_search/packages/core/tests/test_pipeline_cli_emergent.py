"""Unit tests for the emergent-role CLI subcommands (services patched out)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402

from core.emergent.maturity import Maturity  # noqa: E402
from core.emergent.service import (  # noqa: E402
    ClusterOutcome,
    NamingResult,
    TitlesResult,
)
from core.emergent.titles import TitleStats  # noqa: E402

_YOUNG = Maturity(1.0, 1, 5, False, "history 1 of 90 days")
_OLD = Maturity(120.0, 12, 30, True, "")


def _run(argv, **patches):  # type: ignore[no-untyped-def]
    """Run the CLI with the emergent services patched.

    Args:
        argv: CLI arguments.
        **patches: Names of `emergent_service`/`emergent_store` callables to
            their return values, plus `stdin`.

    Returns:
        `(exit code, stdout, mocks)`.
    """
    out = io.StringIO()
    stdin = patches.pop("stdin", "")
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli._build_llm_adapters", return_value={}),
        mock.patch("app.cli.load_seed_examples", return_value={}),
        mock.patch(
            "app.cli.emergent_service.build_titles", return_value=patches.get("titles")
        ),
        mock.patch(
            "app.cli.emergent_service.run_clustering",
            return_value=patches.get("outcome"),
        ),
        mock.patch(
            "app.cli.emergent_store.latest_run", return_value=patches.get("run")
        ),
        mock.patch(
            "app.cli.emergent_service.unnamed_candidates",
            return_value=patches.get("todo", []),
        ),
        mock.patch(
            "app.cli.emergent_service.name_candidates",
            return_value=patches.get("named"),
        ) as name,
        mock.patch("builtins.input", return_value=stdin),
        contextlib.redirect_stdout(out),
    ):
        code = main(argv)
    return code, out.getvalue(), SimpleNamespace(name=name)


class TestEmergentCli(unittest.TestCase):
    """The three commands report honestly and ask before spending."""

    def test_build_titles_reports_the_drops_and_the_maturity(self) -> None:
        result = TitlesResult(1200, TitleStats(2200, 14, 3), 90, _YOUNG)
        code, text, _m = _run(["build-emergent-titles"], titles=result)
        self.assertEqual(code, 0)
        self.assertIn("1200 canonical titles", text)
        self.assertIn("90 agency jobs excluded", text)
        self.assertIn("NOT mature: history 1 of 90 days", text)

    def test_cluster_says_preview_only_when_the_corpus_is_young(self) -> None:
        outcome = ClusterOutcome(uuid.uuid4(), 800, 12, 300, 2, _YOUNG)
        code, text, _m = _run(["cluster-emergent-roles"], outcome=outcome)
        self.assertEqual(code, 0)
        self.assertIn("2 emergent candidate(s)", text)
        self.assertIn("PREVIEW ONLY", text)

    def test_cluster_on_a_mature_corpus_does_not_say_preview(self) -> None:
        outcome = ClusterOutcome(uuid.uuid4(), 800, 12, 300, 2, _OLD)
        _c, text, _m = _run(["cluster-emergent-roles"], outcome=outcome)
        self.assertNotIn("PREVIEW", text)

    def test_cluster_with_no_titles_exits_one(self) -> None:
        code, text, _m = _run(
            ["cluster-emergent-roles"], outcome=ClusterOutcome(None, 0, 0, 0, 0, _YOUNG)
        )
        self.assertEqual(code, 1)
        self.assertIn("no discovery titles", text)

    def test_naming_prints_the_estimate_and_asks_first(self) -> None:
        run = SimpleNamespace(id=uuid.uuid4())
        code, text, mocks = _run(
            ["name-emergent-roles"], run=run, todo=[object(), object()], stdin="n"
        )
        self.assertEqual(code, 1)
        self.assertIn("estimated cost about $0.06", text)
        self.assertIn("nothing spent", text)
        mocks.name.assert_not_called()

    def test_naming_with_yes_names_and_reports_failures(self) -> None:
        run = SimpleNamespace(id=uuid.uuid4())
        named = NamingResult(named=1, failed=[(3, "unusable naming reply")])
        code, text, mocks = _run(
            ["name-emergent-roles", "--yes"],
            run=run,
            todo=[object(), object()],
            named=named,
        )
        self.assertEqual(code, 1)
        self.assertIn("1 named", text)
        self.assertIn("cluster 3 failed", text)
        mocks.name.assert_called_once()

    def test_naming_needs_a_run_and_has_nothing_to_do_without_candidates(self) -> None:
        code, text, _m = _run(["name-emergent-roles"], run=None)
        self.assertEqual(code, 1)
        self.assertIn("run cluster-emergent-roles first", text)
        code, text, _m = _run(
            ["name-emergent-roles"], run=SimpleNamespace(id=uuid.uuid4()), todo=[]
        )
        self.assertEqual(code, 0)
        self.assertIn("no unnamed candidates", text)


if __name__ == "__main__":
    unittest.main()
