# ruff: noqa: E501
"""DB-backed tests for the offers store and API (real Postgres, built dbt pool)."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.routers import offers  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db.session import get_current_user_id  # noqa: E402
from core.offers import store  # noqa: E402

app = FastAPI()
app.include_router(offers.router)

_CONTRACT = {
    "amount": 500,
    "basis": "daily",
    "engagement_type": "contract",
    "ir35_status": "outside",
    "category": "data_engineer",
    "region": "unknown",
    "seniority_band": "senior",
}


class TestOffersApi(unittest.TestCase):
    """Benchmark, save, list, compare."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect; skip when the benchmark pool is not built."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()
        with cls.engine.begin() as conn:
            if not conn.execute(
                text("SELECT to_regclass('gold.offer_benchmark_pool') IS NOT NULL")
            ).scalar_one():
                raise unittest.SkipTest("offer_benchmark_pool not built")

    def setUp(self) -> None:
        """Create two users and wire the app."""
        self.user, self.other = uuid.uuid4(), uuid.uuid4()
        with self.owner.begin() as conn:
            for u in (self.user, self.other):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) VALUES (:i, :e, 'zzfixture')"
                    ),
                    {"i": u, "e": f"zzfixture-{u}@example.com"},
                )
        self.current = self.user
        app.dependency_overrides[get_app_db_engine] = lambda: self.engine
        app.dependency_overrides[get_current_user_id] = lambda: self.current
        self.client = TestClient(app)

    def tearDown(self) -> None:
        """Remove the fixture rows and overrides."""
        app.dependency_overrides.clear()
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM offers.offer_received WHERE user_id IN (:a, :b)"),
                {"a": self.user, "b": self.other},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user, "b": self.other},
            )

    def test_the_pool_loads_with_both_engagement_types(self) -> None:
        pool = store.load_pool(self.engine)
        self.assertTrue(pool)
        self.assertEqual({r.contract_type for r in pool}, {"contract", "permanent"})

    def test_a_benchmark_always_states_n_and_the_disclosure_rate(self) -> None:
        body = self.client.post("/offers/benchmark", json=_CONTRACT).json()["benchmark"]
        for key in (
            "n_disclosed",
            "n_postings",
            "disclosure_rate",
            "status",
            "ladder",
            "warnings",
        ):
            self.assertIn(key, body)
        if body["status"] == "too_thin":
            self.assertIsNone(body["percentile"])
            self.assertTrue(any("Too thin" in w for w in body["warnings"]))
        else:
            self.assertGreaterEqual(body["n_disclosed"], 10)

    def test_a_contract_offer_with_a_known_status_gets_a_take_home_estimate(
        self,
    ) -> None:
        net = self.client.post("/offers/benchmark", json=_CONTRACT).json()["net"]
        self.assertEqual(net["status"], "outside")
        self.assertFalse(net["verified"])
        self.assertTrue(net["assumptions"])

    def test_an_undetermined_status_gets_no_take_home_and_says_why(self) -> None:
        body = self.client.post(
            "/offers/benchmark", json={**_CONTRACT, "ir35_status": "undetermined"}
        ).json()
        self.assertIsNone(body["net"])
        self.assertIn("IR35", body["net_note"])

    def test_a_basis_that_does_not_fit_is_a_422(self) -> None:
        for bad in (
            {"basis": "annual"},
            {"amount": 0},
            {"engagement_type": "freelance"},
        ):
            self.assertEqual(
                self.client.post(
                    "/offers/benchmark", json={**_CONTRACT, **bad}
                ).status_code,
                422,
            )

    def test_saving_records_the_offer_with_its_benchmark_for_this_user_only(
        self,
    ) -> None:
        saved = self.client.post(
            "/offers",
            json={
                **_CONTRACT,
                "company": "Acme",
                "title": "Data Engineer",
                "notes": "verbal",
            },
        )
        self.assertEqual(saved.status_code, 200)
        (mine,) = self.client.get("/offers").json()
        self.assertEqual(
            (mine["company"], mine["amount"], mine["basis"]), ("Acme", 500.0, "daily")
        )
        self.assertIn("n_disclosed", mine["benchmark"])
        self.assertEqual(mine["tax_year"], "2025-26")
        self.current = self.other
        self.assertEqual(self.client.get("/offers").json(), [])

    def test_checking_does_not_save(self) -> None:
        self.client.post("/offers/benchmark", json=_CONTRACT)
        self.assertEqual(self.client.get("/offers").json(), [])

    def test_two_offers_can_be_compared_on_take_home(self) -> None:
        body = self.client.post(
            "/offers/net-compare",
            json={
                "first": {"day_rate": 600, "ir35_status": "inside"},
                "second": {"day_rate": 550, "ir35_status": "outside"},
            },
        ).json()
        self.assertEqual(
            (body["first"]["status"], body["second"]["status"]), ("inside", "outside")
        )
        self.assertGreater(body["difference_year"], 0)  # 550 outside beats 600 inside
        undetermined = self.client.post(
            "/offers/net-compare",
            json={
                "first": {"day_rate": 600, "ir35_status": "undetermined"},
                "second": {"day_rate": 550, "ir35_status": "outside"},
            },
        )
        self.assertEqual(undetermined.status_code, 422)


if __name__ == "__main__":
    unittest.main()
