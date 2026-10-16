"""Tests for the read-only market API against the built marts (real Postgres).

They need the Step 21a marts built (see the README); otherwise they skip.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.market import _rows  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class TestMarketRouter(unittest.TestCase):
    """Every endpoint reads a built mart and shows its denominators."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the app role; skip when the marts are not built."""
        cls.engine = live_app_engine()
        with cls.engine.begin() as conn:
            built = conn.execute(
                text("SELECT to_regclass('gold.market_build_info') IS NOT NULL")
            ).scalar_one()
        if not built:
            raise unittest.SkipTest("market marts not built")
        app.dependency_overrides[get_app_db_engine] = lambda: cls.engine
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the dependency override."""
        app.dependency_overrides.pop(get_app_db_engine, None)

    def _category(self) -> str:
        """Pick a category that exists in the marts.

        Returns:
            The category with the most postings.
        """
        return self.client.get("/market/overview").json()["categories"][0]

    def test_overview_describes_the_build_and_lists_categories(self) -> None:
        body = self.client.get("/market/overview").json()
        for key in (
            "built_at",
            "n_jobs",
            "panel_sources",
            "matrix_version",
            "categories",
        ):
            self.assertIn(key, body)
        self.assertGreater(body["n_jobs"], 0)
        self.assertIn("dbt build", body["rebuild_command"])

    def test_salary_rows_always_carry_n_and_hide_thin_statistics(self) -> None:
        rows = self.client.get("/market/salary").json()
        self.assertTrue(rows)
        for row in rows:
            self.assertIsNotNone(row["n_postings"])
            self.assertIsNotNone(row["disclosure_rate"])
            if row["n_disclosed"] < 5:
                self.assertIsNone(row["median"])

    def test_skills_are_ranked_by_frequency_and_limited(self) -> None:
        category = self._category()
        rows = self.client.get(
            "/market/skills", params={"category": category, "limit": 5}
        ).json()
        self.assertLessEqual(len(rows), 5)
        freqs = [r["frequency"] for r in rows]
        self.assertEqual(freqs, sorted(freqs, reverse=True))

    def test_the_other_readers_answer_with_lists_or_a_summary(self) -> None:
        category = self._category()
        for path in ("premium", "cooccurrence", "composition", "volume", "employers"):
            response = self.client.get(f"/market/{path}", params={"category": category})
            self.assertEqual(response.status_code, 200, path)
            self.assertIsInstance(response.json(), list, path)
        life = self.client.get("/market/lifecycle").json()
        self.assertEqual(life["n_live"] + life["n_gone"], life["n_jobs"])

    def test_composition_shares_still_sum_to_one_after_the_region_rollup(self) -> None:
        category = self._category()
        for row in self.client.get(
            "/market/composition", params={"category": category}
        ).json():
            work = sum(
                row[k]
                for k in (
                    "share_remote",
                    "share_hybrid",
                    "share_onsite",
                    "share_work_model_unknown",
                )
            )
            self.assertAlmostEqual(work, 1.0, places=3)

    def test_an_unknown_or_hostile_category_is_just_empty(self) -> None:
        for category in ("nope", "x'; DROP TABLE gold.fct_market_salary; --"):
            response = self.client.get("/market/skills", params={"category": category})
            self.assertEqual((response.status_code, response.json()), (200, []))
        self.assertEqual(
            self.client.get("/market/salary", params={"category": "nope"}).json(), []
        )

    def test_bad_limits_are_rejected(self) -> None:
        for limit in (0, 1000):
            response = self.client.get(
                "/market/skills", params={"category": "x", "limit": limit}
            )
            self.assertEqual(response.status_code, 422)

    def test_an_unbuilt_mart_is_a_503_that_names_the_command(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            _rows(self.engine, "SELECT * FROM gold.zz_not_a_table")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("dbt build", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
