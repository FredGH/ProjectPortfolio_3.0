"""Integration test for core.skills.post_run_mapping against live Postgres.

Runs as the app-role engine — the role the API process uses in production —
so a missing privilege on the tables the mapper touches shows up here. Only
the Ollama embeddings endpoint is faked.
"""

from __future__ import annotations

import unittest
import uuid

import httpx
from sqlalchemy import text
from tests.integration.skills_fixtures import (
    insert_job_skills,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
    sparse_vector,
)

from core.settings import get_settings
from core.skills.post_run_mapping import build_post_run_mapping


def _fake_embeddings(request: httpx.Request) -> httpx.Response:
    """Answer Ollama's /api/embeddings with a vector far from every ESCO label."""
    return httpx.Response(200, json={"embedding": sparse_vector({767: 1.0})})


class TestPostRunMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"
        self.raw = f"zzfixture postrun {uuid.uuid4().hex[:6]}"

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def _hook(self):
        return build_post_run_mapping(
            self.app,
            ollama_base_url="http://fake-ollama:11434",
            embedding_model=get_settings().embedding_model,
            http_client=httpx.Client(transport=httpx.MockTransport(_fake_embeddings)),
            raw_norms=[self.raw],
        )

    def test_maps_new_strings_as_the_app_role_and_summarises_the_outcome(self) -> None:
        with self.owner.begin() as conn:
            insert_job_skills(
                conn, self.job, "local.v1", [(self.raw, self.raw, "must_have")]
            )
        summary = self._hook()()
        # Nothing in ESCO resembles the fixture, so it goes to the review list.
        self.assertEqual(
            summary, "Mapped 0 new skill string(s) to ESCO; 1 need review."
        )
        with self.owner.connect() as conn:
            status = conn.execute(
                text(
                    "SELECT review_status FROM silver.skill_mapping WHERE raw_norm = :r"
                ),
                {"r": self.raw},
            ).scalar_one()
        self.assertEqual(status, "open")

    def test_a_second_call_finds_nothing_left_to_map(self) -> None:
        with self.owner.begin() as conn:
            insert_job_skills(
                conn, self.job, "local.v1", [(self.raw, self.raw, "must_have")]
            )
        hook = self._hook()
        hook()
        self.assertEqual(hook(), "Mapped 0 new skill string(s) to ESCO; 0 need review.")


if __name__ == "__main__":
    unittest.main()
