"""Integration tests for the skill-review API (no mocking the database)."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.routers import skills  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.integration.skills_fixtures import (  # noqa: E402
    FIXTURE_ESCO_DIR,
    insert_job_skills,
    insert_mapping,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)

from core.skills.esco_load import load_esco  # noqa: E402

# Mount only the skills router: the full app (app.main) also imports the cv
# router, which needs docling / python-multipart that this test does not.
app = FastAPI()
app.include_router(skills.router)

_UNMAPPED = "zzfixture unmapped one"
_MATCHED = "zzfixture matched"


class TestSkillReviewApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        load_esco(self.owner, FIXTURE_ESCO_DIR)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"
        with self.owner.begin() as conn:
            insert_mapping(
                conn,
                _UNMAPPED,
                candidate_skill_id="fixture-cloud",
                candidate_score=0.6,
                seen_in_cv=True,
            )
            insert_mapping(
                conn,
                _MATCHED,
                skill_id="fixture-python",
                method="embedding",
                score=0.86,
                review_status=None,
            )
            insert_job_skills(
                conn, self.job, "local.v1", [(_UNMAPPED, _UNMAPPED, "must_have")]
            )
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_app_db_engine, None)
        purge_fixtures(self.owner)

    def _mapping(self, raw_norm: str):
        with self.owner.connect() as conn:
            return conn.execute(
                text("SELECT * FROM silver.skill_mapping WHERE raw_norm = :n"),
                {"n": raw_norm},
            ).one()

    def _review_norms(self) -> set[str]:
        body = self.client.get("/skills/review", params={"limit": 500}).json()
        return {item["raw_norm"] for item in body}

    def test_review_list_shows_the_unmapped_string_with_context_and_suggestion(
        self,
    ) -> None:
        body = self.client.get("/skills/review", params={"limit": 500}).json()
        item = next(i for i in body if i["raw_norm"] == _UNMAPPED)
        self.assertEqual(item["jd_job_count"], 1)
        self.assertEqual(item["sample_job_group_ids"], [self.job])
        self.assertTrue(item["seen_in_cv"])
        self.assertEqual(item["candidate_skill_id"], "fixture-cloud")
        self.assertEqual(item["candidate_label"], "zzfixture cloud technologies")
        self.assertAlmostEqual(item["candidate_score"], 0.6)
        self.assertNotIn(_MATCHED, {i["raw_norm"] for i in body})

    def test_embedding_matches_list_shows_the_auto_mapped_string(self) -> None:
        body = self.client.get(
            "/skills/review/embedding-matches", params={"limit": 500}
        ).json()
        item = next(i for i in body if i["raw_norm"] == _MATCHED)
        self.assertEqual(item["skill_id"], "fixture-python")
        self.assertEqual(item["skill_label"], "zzfixture python")
        self.assertAlmostEqual(item["score"], 0.86)

    def test_search_finds_skills_by_any_label(self) -> None:
        body = self.client.get("/skills/search", params={"q": "zzfixture cloud"}).json()
        found = {o["skill_id"]: o["source"] for o in body}
        self.assertEqual(found.get("fixture-cloud"), "esco")

    def test_search_treats_wildcards_literally(self) -> None:
        body = self.client.get("/skills/search", params={"q": "%"}).json()
        self.assertNotIn("fixture-cloud", {o["skill_id"] for o in body})

    def test_resolving_to_an_esco_skill_maps_it_and_creates_a_review_alias(
        self,
    ) -> None:
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _UNMAPPED, "skill_id": "fixture-cloud"},
        )
        self.assertEqual(response.status_code, 200)
        row = self._mapping(_UNMAPPED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status),
            ("fixture-cloud", "alias", "resolved"),
        )
        with self.owner.connect() as conn:
            alias = conn.execute(
                text(
                    "SELECT skill_id, source FROM silver.skill_alias "
                    "WHERE alias_norm = :n"
                ),
                {"n": _UNMAPPED},
            ).one()
        self.assertEqual((alias.skill_id, alias.source), ("fixture-cloud", "review"))
        self.assertNotIn(_UNMAPPED, self._review_norms())

    def test_resolving_as_a_custom_skill_creates_it(self) -> None:
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _UNMAPPED, "custom_label": "ZZFixture My Tool"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._mapping(_UNMAPPED).skill_id, "custom:zzfixture-my-tool")
        with self.owner.connect() as conn:
            label = conn.execute(
                text(
                    "SELECT canonical_label FROM silver.custom_skill "
                    "WHERE skill_id = 'custom:zzfixture-my-tool'"
                )
            ).scalar_one()
        self.assertEqual(label, "ZZFixture My Tool")

    def test_resolving_to_an_unknown_skill_is_rejected(self) -> None:
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _UNMAPPED, "skill_id": "nope"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "open")

    def test_resolve_needs_exactly_one_target(self) -> None:
        for payload in (
            {"raw_norm": _UNMAPPED},
            {"raw_norm": _UNMAPPED, "skill_id": "fixture-cloud", "custom_label": "X"},
        ):
            self.assertEqual(
                self.client.post("/skills/review/resolve", json=payload).status_code,
                422,
            )

    def test_an_unknown_string_is_a_404(self) -> None:
        response = self.client.post(
            "/skills/review/dismiss", json={"raw_norm": "zzfixture does not exist"}
        )
        self.assertEqual(response.status_code, 404)

    def test_dismissing_removes_it_from_the_list_and_only_applies_to_unmapped(
        self,
    ) -> None:
        ok = self.client.post("/skills/review/dismiss", json={"raw_norm": _UNMAPPED})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "dismissed")
        self.assertNotIn(_UNMAPPED, self._review_norms())
        bad = self.client.post("/skills/review/dismiss", json={"raw_norm": _MATCHED})
        self.assertEqual(bad.status_code, 422)

    def test_rejecting_an_embedding_match_returns_it_to_the_unmapped_list(
        self,
    ) -> None:
        response = self.client.post(
            "/skills/review/reject", json={"raw_norm": _MATCHED}
        )
        self.assertEqual(response.status_code, 200)
        row = self._mapping(_MATCHED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status, row.candidate_skill_id),
            (None, "none", "rejected", "fixture-python"),
        )
        self.assertIn(_MATCHED, self._review_norms())
        again = self.client.post("/skills/review/reject", json={"raw_norm": _MATCHED})
        self.assertEqual(again.status_code, 422)


if __name__ == "__main__":
    unittest.main()
