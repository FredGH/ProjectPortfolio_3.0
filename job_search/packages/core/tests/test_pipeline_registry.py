"""Unit tests for core.pipeline.registry — no DB needed, this catalog
is pure Python data plus imports."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from core.pipeline.registry import REVIEW_STAGES, STAGES

_CLI_PATH = (
    Path(__file__).resolve().parents[3]
    / "apps" / "pipeline" / "app" / "cli.py"
)

# Stages deliberately excluded from STAGES' Run-button surface but not
# from the dependency graph or this completeness check — see this
# plan's Global Constraints for why each is excluded.
_EXCLUDED_FROM_RUN_BUTTON = {"ingest", "run-evals"}


def _cli_subcommand_names() -> set[str]:
    """Every subparsers.add_parser("...") name in cli.py, found by
    parsing the source rather than importing the CLI module (which
    pulls in argparse setup this test has no need to run)."""
    tree = ast.parse(_CLI_PATH.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(node.args[0].value)
    return names


class TestStageCatalogCompleteness(unittest.TestCase):
    def test_every_cli_subcommand_has_a_stages_entry_or_is_excluded(self) -> None:
        cli_names = _cli_subcommand_names()
        self.assertTrue(cli_names, "no subcommands found — parser broke")
        missing = cli_names - set(STAGES) - _EXCLUDED_FROM_RUN_BUTTON
        self.assertEqual(
            missing, set(),
            f"CLI subcommand(s) {missing} have no STAGES entry and are not "
            "on the documented exclusion list — add one or the other",
        )

    def test_excluded_stages_are_still_real_cli_subcommands(self) -> None:
        # Catches a stale exclusion entry (e.g. a renamed/removed command)
        # rather than letting it silently stop meaning anything.
        cli_names = _cli_subcommand_names()
        for name in _EXCLUDED_FROM_RUN_BUTTON:
            self.assertIn(name, cli_names)

    def test_excluded_stages_are_not_also_in_stages(self) -> None:
        self.assertEqual(set(STAGES) & _EXCLUDED_FROM_RUN_BUTTON, set())


class TestDependencyGraphConsistency(unittest.TestCase):
    def test_every_automated_dependency_resolves_to_a_real_stage(self) -> None:
        for name, spec in STAGES.items():
            for dep in spec.depends_on:
                self.assertIn(
                    dep, STAGES, f"{name} depends on undefined stage {dep!r}"
                )

    def test_every_review_dependency_resolves_to_a_real_automated_stage(self) -> None:
        for name, spec in REVIEW_STAGES.items():
            for dep in spec.depends_on:
                self.assertIn(
                    dep, STAGES,
                    f"review stage {name} depends on undefined automated stage {dep!r}",
                )

    def test_no_cycles_among_automated_stages(self) -> None:
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(name: str) -> None:
            if name in done:
                return
            self.assertNotIn(name, visiting, f"cycle detected at {name!r}")
            visiting.add(name)
            for dep in STAGES[name].depends_on:
                visit(dep)
            visiting.discard(name)
            done.add(name)

        for name in STAGES:
            visit(name)


class TestPerUserFlagging(unittest.TestCase):
    def test_scoring_funnel_stages_are_flagged_per_user(self) -> None:
        for name in (
            "score-filter-jobs", "chunk-embed-cv", "score-similarity",
            "score-skill-coverage", "score-llm-rerank", "score-blend",
            "map-cv-skills",
        ):
            self.assertTrue(STAGES[name].per_user, f"{name} should be per_user=True")

    def test_chunk_embed_jobs_is_not_per_user(self) -> None:
        # Shared across all users — README's own "(shared, run once
        # across all users)" note for this stage.
        self.assertFalse(STAGES["chunk-embed-jobs"].per_user)


if __name__ == "__main__":
    unittest.main()
