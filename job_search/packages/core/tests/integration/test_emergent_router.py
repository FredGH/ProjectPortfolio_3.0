# ruff: noqa: E501
"""API tests for emergent-role detection against the real database.

Run on the host with the database URLs pointing at localhost (clustering needs
scikit-learn). A synthetic run is stored and removed again; the maturity
status comes from the REAL discovery corpus, which is far too young: that is
what proves the gate refuses a novelty index even when one was stored.
"""

from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

from sqlalchemy import text
from tests.company_intel_fixtures import FakeAdapter
from tests.emergent_fixtures import NOW, SEEDS, corpus, embed
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.routers import emergent, market  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db.session import get_current_user_id  # noqa: E402
from core.emergent import service  # noqa: E402
from core.emergent.config import load_config  # noqa: E402

# A slim app: the full one imports the CV-extraction stack (docling), which the
# host venv may not have.
app = FastAPI()
app.include_router(emergent.router)
app.include_router(market.router)

_CONFIG = load_config()
_JOBS = [j for j in corpus() if j.employer != "agency-x"]


class TestEmergentRouter(unittest.TestCase):
    """Status, clusters, candidates and verdicts."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def setUp(self) -> None:
        """Create two users and store a synthetic named run."""
        self.user, self.other = uuid.uuid4(), uuid.uuid4()
        with self.owner.begin() as conn:
            for u in (self.user, self.other):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) VALUES (:i, :e, 'zzfixture')"
                    ),
                    {"i": u, "e": f"zzfixture-{u}@example.com"},
                )
        with mock.patch(
            "core.emergent.service.load_discovery_jobs", return_value=(_JOBS, 0)
        ):
            outcome = service.run_clustering(
                self.engine, _CONFIG, embed, "zz-model", SEEDS, NOW
            )
        self.run_id = outcome.run_id
        adapter = FakeAdapter(
            [
                json.dumps(
                    {
                        "name": "Field engineer",
                        "characterisation": "Deploys at customers.",
                    }
                )
            ]
        )
        service.name_candidates(self.engine, self.run_id, adapters={"anthropic": adapter})  # type: ignore[arg-type]
        self.current = self.user
        app.dependency_overrides[get_app_db_engine] = lambda: self.engine
        app.dependency_overrides[get_current_user_id] = lambda: self.current
        self.client = TestClient(app)

    def tearDown(self) -> None:
        """Remove the fixture rows and the overrides."""
        app.dependency_overrides.pop(get_app_db_engine, None)
        app.dependency_overrides.pop(get_current_user_id, None)
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM emergent.emergent_role_review WHERE user_id IN (:a, :b)"
                ),
                {"a": self.user, "b": self.other},
            )
            for table, col in (
                ("role_candidate", "run_id"),
                ("title_cluster", "run_id"),
                ("cluster_run", "id"),
            ):
                conn.execute(
                    text(f"DELETE FROM emergent.{table} WHERE {col} = :r"),
                    {"r": self.run_id},
                )
            conn.execute(
                text("DELETE FROM emergent.title_embedding WHERE model = 'zz-model'")
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user, "b": self.other},
            )

    def _young(self):  # type: ignore[no-untyped-def]
        """Patch the live corpus to a young one (the real one will mature).

        Returns:
            A patcher over the API module's corpus loader.
        """
        young = [
            type(j)(j.job_group_id, j.title, j.company, j.employer, NOW, j.source)
            for j in _JOBS[:20]
        ]
        return mock.patch(
            "app.routers.emergent.load_discovery_jobs", return_value=(young, 0)
        )

    def test_a_young_corpus_is_immature_and_the_status_says_exactly_what_is_missing(
        self,
    ) -> None:
        with self._young():
            body = self.client.get("/emergent/status").json()
        self.assertFalse(body["mature"])
        self.assertFalse(body["novelty_shown"])
        self.assertIn("days", body["missing"])
        self.assertIn("preview", body["preview_label"])

    def test_no_novelty_index_is_returned_while_immature_even_if_one_was_stored(
        self,
    ) -> None:
        with self._young():
            body = self.client.get("/emergent/clusters").json()
        self.assertTrue(body["clusters"])
        self.assertTrue(all(c["novelty"] is None for c in body["clusters"]))
        self.assertTrue(all("withheld" in c["novelty_note"] for c in body["clusters"]))

    def test_a_preview_run_keeps_its_label_even_after_the_corpus_matures(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("UPDATE emergent.cluster_run SET mature = false WHERE id = :i"),
                {"i": self.run_id},
            )
        mature = type(
            "M",
            (),
            {
                "mature": True,
                "note": "",
                "history_days": 120.0,
                "periods": 12,
                "employers": 30,
            },
        )()
        with mock.patch("app.routers.emergent.assess", return_value=mature):
            body = self.client.get("/emergent/clusters").json()
        self.assertTrue(body["mature"])
        self.assertFalse(body["run_mature"])
        self.assertFalse(body["novelty_shown"])
        self.assertIn("preview", body["preview_label"])
        self.assertTrue(all(c["novelty"] is None for c in body["clusters"]))

    def test_the_candidate_is_an_uncorroborated_pending_hypothesis(self) -> None:
        (candidate,) = self.client.get("/emergent/candidates").json()["candidates"]
        self.assertEqual(candidate["name"], "Field engineer")
        self.assertEqual(
            (candidate["corroboration"], candidate["verdict"]),
            ("uncorroborated", "pending"),
        )
        self.assertGreaterEqual(
            candidate["cluster"]["n_employers"], _CONFIG.min_employers
        )
        self.assertIsNone(candidate["cluster"]["novelty"])

    def test_a_verdict_needs_a_rationale_and_a_real_candidate(self) -> None:
        (candidate,) = self.client.get("/emergent/candidates").json()["candidates"]
        blank = self.client.post(
            f"/emergent/candidates/{candidate['id']}/review",
            json={"verdict": "confirmed", "rationale": "  "},
        )
        self.assertEqual(blank.status_code, 422)
        unknown = self.client.post(
            f"/emergent/candidates/{uuid.uuid4()}/review",
            json={"verdict": "confirmed", "rationale": "x"},
        )
        self.assertEqual(unknown.status_code, 404)
        bad = self.client.post(
            f"/emergent/candidates/{candidate['id']}/review",
            json={"verdict": "maybe", "rationale": "x"},
        )
        self.assertEqual(bad.status_code, 422)

    def test_a_confirmed_verdict_is_recorded_for_this_user_only_and_promotes_nothing(
        self,
    ) -> None:
        (candidate,) = self.client.get("/emergent/candidates").json()["candidates"]
        ok = self.client.post(
            f"/emergent/candidates/{candidate['id']}/review",
            json={"verdict": "confirmed", "rationale": "Seen it at three clients"},
        )
        self.assertEqual(ok.status_code, 200)
        (mine,) = self.client.get("/emergent/candidates").json()["candidates"]
        self.assertEqual(
            (mine["verdict"], mine["rationale"]),
            ("confirmed", "Seen it at three clients"),
        )
        self.current = self.other
        (theirs,) = self.client.get("/emergent/candidates").json()["candidates"]
        self.assertEqual((theirs["verdict"], theirs["rationale"]), ("pending", None))

    def test_a_dismissed_candidate_stays_listed_as_dismissed_for_the_user_who_dismissed_it(
        self,
    ) -> None:
        (candidate,) = self.client.get("/emergent/candidates").json()["candidates"]
        self.client.post(
            f"/emergent/candidates/{candidate['id']}/review",
            json={"verdict": "dismissed", "rationale": "Artefact"},
        )
        later = self.client.get("/emergent/candidates").json()["candidates"]
        self.assertEqual([c["verdict"] for c in later], ["dismissed"])

    def test_the_macro_endpoints_answer_from_the_built_marts(self) -> None:
        with self.engine.begin() as conn:
            built = conn.execute(
                text("SELECT to_regclass('gold.fct_ai_penetration') IS NOT NULL")
            ).scalar_one()
        if not built:
            self.skipTest("macro marts not built")
        for path in ("ai-penetration", "role-drift", "displacement"):
            response = self.client.get(
                f"/market/macro/{path}", params={"category": "data_engineer"}
            )
            self.assertEqual(response.status_code, 200, path)
            self.assertIsInstance(response.json(), list)
        self.assertEqual(
            self.client.get("/market/macro/skill-migration").status_code, 200
        )


if __name__ == "__main__":
    unittest.main()
