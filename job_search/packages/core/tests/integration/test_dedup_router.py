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

    def _skip_unless_bootstrap_mode(self) -> None:
        """Skip this test if a calibration_thresholds row already exists.

        This test's assertions only hold in bootstrap mode (see
        `GET /dedup/pairs-to-label`'s docstring) — once a real
        calibration_thresholds row exists (which this whole branch
        exists to let a real user create), the endpoint switches to
        production mode and this test's seeded pair (blended_score
        0.85) may or may not fall inside whatever band that real user
        chose, for reasons unrelated to what this test claims to
        verify. `dedup.calibration_thresholds` is shared, global dev-DB
        state with no per-test isolation, so rather than delete/restore
        a real user's calibration row, skip cleanly when one is
        present.
        """
        with self.owner_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM dedup.calibration_thresholds")
            ).scalar_one()
        if count > 0:
            self.skipTest(
                "requires bootstrap mode - a calibration_thresholds row "
                "already exists"
            )

    def test_pairs_to_label_includes_the_seeded_pair_in_bootstrap_mode(self) -> None:
        self._skip_unless_bootstrap_mode()
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
        self._skip_unless_bootstrap_mode()
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


class TestPairsToLabelSkipsPairsWithAMissingPosting(unittest.TestCase):
    """Regression test for the fix in 32dc684: `get_pairs_to_label` used
    to look up each pair's postings with `postings_by_key[...]`, which
    raises an unhandled KeyError (-> 500) if a scored pair references a
    job_key with no matching `silver__job_posting` row. The dedup dbt
    layer runs out-of-band relative to silver (see dbt/README.md's
    dedup bullet), so this is a documented, reachable operational
    state. The fix uses `.get(...)` and silently skips any pair missing
    either posting instead of 500ing the whole batch.
    """

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-missingposting-a-{suffix}"
        self.job_key_b = f"test-missingposting-b-{suffix}"
        with self.owner_engine.begin() as conn:
            # Only job_key_a gets a silver__job_posting row — job_key_b
            # is deliberately never inserted, simulating the dedup
            # layer having scored a pair before silver caught up.
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
                {"job_key": self.job_key_a},
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
                    "DELETE FROM dedup.dedup__similarity_scores "
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

    def _skip_unless_bootstrap_mode(self) -> None:
        """Skip unless in bootstrap mode.

        See `TestPairsToLabelAndLabels._skip_unless_bootstrap_mode`'s
        docstring for why this test's assertions only hold when no
        `calibration_thresholds` row exists yet.
        """
        with self.owner_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM dedup.calibration_thresholds")
            ).scalar_one()
        if count > 0:
            self.skipTest(
                "requires bootstrap mode - a calibration_thresholds row "
                "already exists"
            )

    def test_pair_with_a_missing_posting_is_skipped_not_500ed(self) -> None:
        self._skip_unless_bootstrap_mode()
        # Same guaranteed-full-decile `limit` reasoning as the other
        # bootstrap-mode tests in this file: with ~72k real unlabeled
        # rows already in this dev database, a small `limit` would make
        # the "not in results" assertion pass trivially by omission.
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 100_000})
        self.assertEqual(response.status_code, 200)
        keys = {
            (p["scores"]["job_key_a"], p["scores"]["job_key_b"])
            for p in response.json()
        }
        self.assertNotIn((self.job_key_a, self.job_key_b), keys)


class TestProductionModeExcludesHardVetoedPairs(unittest.TestCase):
    """Production mode (a calibration_thresholds row exists) must still
    honor Step 8's hard veto — a vetoed pair whose blended_score happens
    to land inside the auto-reject/auto-match band must never reach the
    human review queue, since hard_veto and blended_score are
    independently computed columns.
    """

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-veto-a-{suffix}"
        self.job_key_b = f"test-veto-b-{suffix}"
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
            # hard_veto = true with a blended_score that falls strictly
            # inside the auto_reject/auto_match band set up below — the
            # two columns are independently computed, so this is a
            # realistic case (e.g. a date-proximity veto on two postings
            # that otherwise look very similar).
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) VALUES "
                    "(:a, :b, 'block', 1.0, 1.0, 1.0, 0.5, 90, 0.0, 0.5, "
                    "true, 0.85)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            # Switches the router into production mode for every request
            # made while this row exists — 0.85 sits strictly between
            # these thresholds, so this pair would land in the queue
            # if the hard-veto filter were missing.
            conn.execute(
                text(
                    "INSERT INTO dedup.calibration_thresholds "
                    "(auto_match_threshold, auto_reject_threshold, "
                    "measured_precision, measured_recall, "
                    "labeled_pair_count, calibrated_by) VALUES "
                    "(0.95, 0.3, 0.9, 0.9, 10, 'test-runner')"
                )
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.calibration_thresholds "
                    "WHERE calibrated_by = 'test-runner'"
                )
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
                    "DELETE FROM silver.silver__job_posting "
                    "WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_hard_vetoed_pair_is_excluded_even_inside_the_threshold_band(
        self,
    ) -> None:
        # Production mode has no decile bucketing — it's a flat
        # `ORDER BY random() LIMIT :limit` over every unlabeled row
        # whose blended_score falls in the 0.3-0.95 band set up above.
        # This dev database already has ~72k real rows in that band, so
        # (as in the bootstrap-mode tests above) a small `limit` would
        # make this assertion pass by omission — a plain random sample
        # would almost never draw our one seeded row regardless of
        # whether the hard-veto filter is present. Use a `limit` large
        # enough to cover every matching row so the SQL `WHERE` clause,
        # not sampling luck, is what's actually being tested.
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 100_000})
        self.assertEqual(response.status_code, 200)
        keys = {
            (p["scores"]["job_key_a"], p["scores"]["job_key_b"])
            for p in response.json()
        }
        self.assertNotIn((self.job_key_a, self.job_key_b), keys)


class TestProductionModeReturnsPairsInsideTheBand(unittest.TestCase):
    """Production mode's positive case — the only prior production-mode
    coverage (TestProductionModeExcludesHardVetoedPairs) was purely
    negative, so swapping the query's `>`/`<` band comparisons for their
    opposites (or dropping them) would still pass every existing test.
    This asserts a pair strictly inside the band IS returned, and a pair
    at/beyond either edge is NOT.
    """

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.in_band_a = f"test-inband-a-{suffix}"
        self.in_band_b = f"test-inband-b-{suffix}"
        self.out_of_band_a = f"test-outband-a-{suffix}"
        self.out_of_band_b = f"test-outband-b-{suffix}"
        self.all_job_keys = (
            self.in_band_a,
            self.in_band_b,
            self.out_of_band_a,
            self.out_of_band_b,
        )
        with self.owner_engine.begin() as conn:
            for job_key in self.all_job_keys:
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
            # Strictly inside the 0.3-0.95 band set up below.
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) VALUES "
                    "(:a, :b, 'block', 1.0, 1.0, 1.0, 0.5, 0, 1.0, 0.5, "
                    "false, 0.6)"
                ),
                {"a": self.in_band_a, "b": self.in_band_b},
            )
            # At the auto_match edge (>= auto_match_threshold) — should
            # never appear in the review queue, it would auto-match.
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) VALUES "
                    "(:a, :b, 'block', 1.0, 1.0, 1.0, 0.5, 0, 1.0, 0.5, "
                    "false, 0.95)"
                ),
                {"a": self.out_of_band_a, "b": self.out_of_band_b},
            )
            conn.execute(
                text(
                    "INSERT INTO dedup.calibration_thresholds "
                    "(auto_match_threshold, auto_reject_threshold, "
                    "measured_precision, measured_recall, "
                    "labeled_pair_count, calibrated_by) VALUES "
                    "(0.95, 0.3, 0.9, 0.9, 10, 'test-runner')"
                )
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.calibration_thresholds "
                    "WHERE calibrated_by = 'test-runner'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__similarity_scores "
                    "WHERE job_key_a = :in_a AND job_key_b = :in_b "
                    "OR job_key_a = :out_a AND job_key_b = :out_b"
                ),
                {
                    "in_a": self.in_band_a,
                    "in_b": self.in_band_b,
                    "out_a": self.out_of_band_a,
                    "out_b": self.out_of_band_b,
                },
            )
            conn.execute(
                text(
                    "DELETE FROM silver.silver__job_posting "
                    "WHERE job_key = ANY(:job_keys)"
                ),
                {"job_keys": list(self.all_job_keys)},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_pair_strictly_inside_the_band_is_returned(self) -> None:
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 100_000})
        self.assertEqual(response.status_code, 200)
        keys = {
            (p["scores"]["job_key_a"], p["scores"]["job_key_b"])
            for p in response.json()
        }
        self.assertIn((self.in_band_a, self.in_band_b), keys)

    def test_pair_at_or_beyond_the_band_edge_is_not_returned(self) -> None:
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 100_000})
        self.assertEqual(response.status_code, 200)
        keys = {
            (p["scores"]["job_key_a"], p["scores"]["job_key_b"])
            for p in response.json()
        }
        self.assertNotIn((self.out_of_band_a, self.out_of_band_b), keys)


class TestCalibrationAndThresholds(unittest.TestCase):
    """Integration tests for /dedup/calibration and /dedup/thresholds."""

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{suffix}"
        self.job_key_b = f"test-b-{suffix}"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) VALUES "
                    "(:a, :b, 'block', 1.0, 1.0, 1.0, 0.5, 0, 1.0, 0.5, "
                    "false, 0.9)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "INSERT INTO dedup.pair_labels (job_key_a, job_key_b, label) "
                    "VALUES (:a, :b, 'match')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.calibration_thresholds "
                    "WHERE calibrated_by = 'test-runner'"
                )
            )
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
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_calibration_curve_includes_the_seeded_labeled_pair(self) -> None:
        response = self.client.get("/dedup/calibration")
        self.assertEqual(response.status_code, 200)
        curve = response.json()
        self.assertTrue(any(point["threshold"] == 0.9 for point in curve))

    def test_thresholds_returns_the_current_row_once_one_exists(self) -> None:
        # `GET /dedup/thresholds` returning `null` is only true in a
        # database with zero calibration_thresholds rows anywhere — not
        # a stable invariant once a real user has calibrated through the
        # UI (this branch's entire purpose). Test the thing this test
        # actually controls instead: post a known row, then assert the
        # endpoint returns exactly that row as "current" (most recent by
        # calibrated_at), which holds regardless of what else is in the
        # table.
        post_response = self.client.post(
            "/dedup/thresholds",
            json={
                "auto_match_threshold": 0.92,
                "auto_reject_threshold": 0.55,
                "measured_precision": 0.97,
                "measured_recall": 0.85,
                "labeled_pair_count": 50,
                "calibrated_by": "test-runner",
            },
        )
        self.assertEqual(post_response.status_code, 200)

        get_response = self.client.get("/dedup/thresholds")
        self.assertEqual(get_response.status_code, 200)
        body = get_response.json()
        self.assertIsNotNone(body)
        self.assertEqual(body["auto_match_threshold"], 0.92)
        self.assertEqual(body["auto_reject_threshold"], 0.55)
        self.assertEqual(body["measured_precision"], 0.97)
        self.assertEqual(body["measured_recall"], 0.85)
        self.assertEqual(body["labeled_pair_count"], 50)

    def test_posting_thresholds_makes_them_retrievable(self) -> None:
        post_response = self.client.post(
            "/dedup/thresholds",
            json={
                "auto_match_threshold": 0.9,
                "auto_reject_threshold": 0.6,
                "measured_precision": 0.97,
                "measured_recall": 0.85,
                "labeled_pair_count": 50,
                "calibrated_by": "test-runner",
            },
        )
        self.assertEqual(post_response.status_code, 200)

        get_response = self.client.get("/dedup/thresholds")
        body = get_response.json()
        self.assertEqual(body["auto_match_threshold"], 0.9)
        self.assertEqual(body["auto_reject_threshold"], 0.6)

    def test_transposed_thresholds_are_rejected_with_422(self) -> None:
        response = self.client.post(
            "/dedup/thresholds",
            json={
                # Transposed: auto_reject above auto_match — would leave
                # no valid middle band and brick the review queue.
                "auto_match_threshold": 0.5,
                "auto_reject_threshold": 0.9,
                "measured_precision": 0.9,
                "measured_recall": 0.9,
                "labeled_pair_count": 10,
                "calibrated_by": "test-runner",
            },
        )
        self.assertEqual(response.status_code, 422)

        with self.owner_engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM dedup.calibration_thresholds "
                    "WHERE calibrated_by = 'test-runner'"
                )
            ).scalar_one()
        self.assertEqual(count, 0)

    def test_out_of_range_threshold_is_rejected_with_422(self) -> None:
        response = self.client.post(
            "/dedup/thresholds",
            json={
                "auto_match_threshold": 1.5,
                "auto_reject_threshold": 0.6,
                "measured_precision": 0.9,
                "measured_recall": 0.9,
                "labeled_pair_count": 10,
                "calibrated_by": "test-runner",
            },
        )
        self.assertEqual(response.status_code, 422)
