from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db.session import build_engine  # noqa: E402

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
_APP_DSN = "postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search"


class TestPairsToLabelAndLabels(unittest.TestCase):
    """Integration tests against real Postgres — no mocking the database."""

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{suffix}"
        self.job_key_b = f"test-b-{suffix}"
        with self.owner_engine.begin() as conn:
            for job_key in (self.job_key_a, self.job_key_b):
                conn.execute(
                    text(
                        "INSERT INTO silver.silver__job_posting "
                        "(job_key, source_name, source_job_id, job_url, "
                        "job_url_canonical, entry_method, title, company, "
                        "location, description, salary_raw, posted_at, "
                        "engagement_type, ir35_status, engagement_vehicle, "
                        "rate_basis, extension_likelihood) VALUES "
                        "(:job_key, 'test_source', :job_key, 'https://x', "
                        "'https://x', 'api', 'Data Engineer', 'Acme Ltd', "
                        "'London', 'A test description.', NULL, now(), "
                        "'unknown', 'unknown', 'unknown', 'unknown', "
                        "'unstated')"
                    ),
                    {"job_key": job_key},
                )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__candidate_pairs "
                    "(job_key_a, job_key_b, match_type) "
                    "VALUES (:a, :b, 'block')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) VALUES "
                    "(:a, :b, 'block', 1.0, 1.0, 1.0, 0.5, 0, 1.0, 0.5, "
                    "false, 0.85)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__similarity_scores "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__candidate_pairs "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__job_posting "
                    "WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_pairs_to_label_includes_the_seeded_pair_in_bootstrap_mode(self) -> None:
        # This dev database already carries ~72k real (non-test) unlabeled,
        # non-veto dedup__similarity_scores rows from earlier PLAN.md steps'
        # pipeline runs, spread over deciles of ~7.3k rows each. Bootstrap
        # mode's stratified sample returns only `limit // 10` rows per
        # decile, so a `limit` on the order of the brief's suggested 500
        # would have well under a 1% chance of drawing this test's single
        # freshly-seeded pair. Use a `limit` large enough that
        # `limit // 10` exceeds the largest real decile bucket, so every
        # unlabeled row (including ours) is guaranteed to be returned —
        # this keeps the assertion deterministic without touching the
        # router's genuinely-random production sampling behavior.
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 100_000})
        self.assertEqual(response.status_code, 200)
        pairs = response.json()
        keys = {(p["scores"]["job_key_a"], p["scores"]["job_key_b"]) for p in pairs}
        self.assertIn((self.job_key_a, self.job_key_b), keys)

    def test_posting_labels_round_trip(self) -> None:
        response = self.client.post(
            "/dedup/labels",
            json={
                "job_key_a": self.job_key_a,
                "job_key_b": self.job_key_b,
                "label": "match",
                "labeled_by": "test-user",
            },
        )
        self.assertEqual(response.status_code, 200)

        with self.owner_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT label, is_manual_override, labeled_by "
                    "FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).one()
        self.assertEqual(row.label, "match")
        self.assertTrue(row.is_manual_override)
        self.assertEqual(row.labeled_by, "test-user")

    def test_labeled_pairs_are_excluded_from_pairs_to_label(self) -> None:
        self.client.post(
            "/dedup/labels",
            json={
                "job_key_a": self.job_key_a,
                "job_key_b": self.job_key_b,
                "label": "not_match",
            },
        )
        # See the large `limit` comment in the bootstrap-mode inclusion
        # test above: with ~72k real unlabeled rows already in this dev
        # database, a small `limit` would make this assertion pass
        # trivially (by omission) rather than by proving exclusion, so
        # use the same guaranteed-full-decile `limit` here too.
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 100_000})
        keys = {
            (p["scores"]["job_key_a"], p["scores"]["job_key_b"])
            for p in response.json()
        }
        self.assertNotIn((self.job_key_a, self.job_key_b), keys)

    def test_relabeling_upserts_rather_than_duplicating(self) -> None:
        for label in ("match", "not_match"):
            self.client.post(
                "/dedup/labels",
                json={
                    "job_key_a": self.job_key_a,
                    "job_key_b": self.job_key_b,
                    "label": label,
                },
            )
        with self.owner_engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).scalar_one()
            row = conn.execute(
                text(
                    "SELECT label FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).one()
        self.assertEqual(count, 1)
        self.assertEqual(row.label, "not_match")
