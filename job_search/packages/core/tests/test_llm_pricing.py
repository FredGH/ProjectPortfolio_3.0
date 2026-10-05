"""Tests for core.llm.pricing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.llm.pricing import estimate_cost_usd


class TestEstimateCostUsd(unittest.TestCase):
    def test_a_known_model_is_priced_per_million_tokens(self) -> None:
        cost = estimate_cost_usd("anthropic", "claude-sonnet-5", 3300, 4000)
        self.assertAlmostEqual(cost, 3300 * 2.0 / 1e6 + 4000 * 10.0 / 1e6)

    def test_ollama_is_free(self) -> None:
        self.assertEqual(estimate_cost_usd("ollama", "llama3.1:8b", 9000, 9000), 0.0)

    def test_an_unknown_anthropic_model_has_no_estimate(self) -> None:
        self.assertIsNone(estimate_cost_usd("anthropic", "claude-nope-9", 10, 10))

    def test_a_custom_price_table_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.yml"
            path.write_text("models:\n  m1: {input: 1.0, output: 3.0}\n")
            self.assertAlmostEqual(
                estimate_cost_usd(
                    "anthropic", "m1", 1_000_000, 1_000_000, config_path=path
                ),
                4.0,
            )
            self.assertIsNone(
                estimate_cost_usd(
                    "anthropic", "claude-sonnet-5", 1, 1, config_path=path
                )
            )


if __name__ == "__main__":
    unittest.main()
