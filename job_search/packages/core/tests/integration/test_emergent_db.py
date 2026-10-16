# ruff: noqa: E501
"""DB-backed tests for the emergent-role store, review queue and orchestration.

Run on the host with the database URLs pointing at localhost (see the README)
because clustering needs scikit-learn.
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

from sqlalchemy import text
from tests.company_intel_fixtures import FakeAdapter
from tests.emergent_fixtures import NOW, SEEDS, corpus, embed
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.emergent import review, service, store
from core.emergent.config import load_config

_CONFIG = load_config()
_JOBS = [j for j in corpus() if j.employer != "agency-x"]


def _patched_jobs():  # type: ignore[no-untyped-def]
    """Patch the corpus loader with the synthetic jobs.

    Returns:
        A patcher; the agency count is reported as 1.
    """
    return mock.patch(
        "core.emergent.service.load_discovery_jobs", return_value=(_JOBS, 1)
    )


class TestEmergentDb(unittest.TestCase):
    """Run, name and review against the real database."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect as the owner and as the app role."""
        cls.owner = live_owner_engine()
        cls.engine = live_app_engine()

    def setUp(self) -> None:
        """Create two users and remember pre-existing run ids."""
        self.user, self.other = uuid.uuid4(), uuid.uuid4()
        with self.owner.begin() as conn:
            for u in (self.user, self.other):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) VALUES (:i, :e, 'zzfixture')"
                    ),
                    {"i": u, "e": f"zzfixture-{u}@example.com"},
                )
        self.run_ids: list[uuid.UUID] = []

    def tearDown(self) -> None:
        """Remove every fixture row (reviews first: they reference candidates)."""
        with self.owner.begin() as conn:
            ids = [str(r) for r in self.run_ids]
            conn.execute(
                text(
                    "DELETE FROM emergent.emergent_role_review WHERE user_id IN (:a, :b)"
                ),
                {"a": self.user, "b": self.other},
            )
            for table in ("role_candidate", "title_cluster", "cluster_run"):
                col = "id" if table == "cluster_run" else "run_id"
                conn.execute(
                    text(f"DELETE FROM emergent.{table} WHERE {col}::text = ANY(:ids)"),
                    {"ids": ids},
                )
            conn.execute(
                text("DELETE FROM emergent.title_embedding WHERE model = 'zz-model'")
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": self.user, "b": self.other},
            )

    def _run(self):  # type: ignore[no-untyped-def]
        """Cluster the synthetic corpus and remember the run.

        Returns:
            The `ClusterOutcome`.
        """
        with _patched_jobs():
            outcome = service.run_clustering(
                self.engine, _CONFIG, embed, "zz-model", SEEDS, NOW
            )
        assert outcome.run_id is not None
        self.run_ids.append(outcome.run_id)
        return outcome

    def _reply(self, name: str = "Field deployment engineer") -> FakeAdapter:
        """Script a naming reply.

        Args:
            name: The proposed name.

        Returns:
            A fake adapter.
        """
        return FakeAdapter(
            [
                json.dumps(
                    {"name": name, "characterisation": "Deploys software at customers."}
                )
            ]
        )

    def test_a_run_is_stored_with_its_candidates_and_never_overwrites_an_earlier_run(
        self,
    ) -> None:
        first = self._run()
        second = self._run()
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(first.n_candidates, 1)
        run = store.latest_run(self.engine)
        assert run is not None
        self.assertEqual(run.id, second.run_id)
        self.assertTrue(run.mature)
        candidates = store.list_clusters(
            self.engine, second.run_id, only_candidates=True
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(any("forward deployed" in t for t in candidates[0].titles))

    def test_embeddings_are_cached_per_model(self) -> None:
        self._run()
        cached = store.load_embeddings(self.engine, "zz-model")
        self.assertIn("forward deployed engineer", cached)
        calls = []
        with _patched_jobs():
            outcome = service.run_clustering(
                self.engine,
                _CONFIG,
                lambda t: calls.append(t) or embed(t),
                "zz-model",
                {},
                NOW,
            )
        self.run_ids.append(outcome.run_id)  # type: ignore[arg-type]
        self.assertEqual(calls, [])  # every title came from the cache

    def test_naming_covers_only_candidates_stores_them_uncorroborated_and_is_idempotent(
        self,
    ) -> None:
        outcome = self._run()
        adapter = self._reply()
        result = service.name_candidates(self.engine, outcome.run_id, adapters={"anthropic": adapter})  # type: ignore[arg-type]
        self.assertEqual((result.named, result.failed), (1, []))
        self.assertEqual(len(adapter.prompts), 1)
        (candidate,) = store.list_candidates(self.engine, outcome.run_id)  # type: ignore[arg-type]
        self.assertEqual(candidate.corroboration, "uncorroborated")
        again = self._reply()
        service.name_candidates(self.engine, outcome.run_id, adapters={"anthropic": again})  # type: ignore[arg-type]
        self.assertEqual(again.prompts, [])

    def test_naming_a_preview_run_is_refused_unless_allowed(self) -> None:
        outcome = self._run()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "UPDATE emergent.cluster_run SET mature = false, maturity_note = 'history 0 of 90 days' WHERE id = :i"
                ),
                {"i": outcome.run_id},
            )
        adapter = self._reply()
        with self.assertRaises(service.NotMatureError):
            service.name_candidates(self.engine, outcome.run_id, adapters={"anthropic": adapter})  # type: ignore[arg-type]
        self.assertEqual(adapter.prompts, [])
        result = service.name_candidates(
            self.engine, outcome.run_id, adapters={"anthropic": adapter}, allow_preview=True  # type: ignore[arg-type]
        )
        self.assertEqual(result.named, 1)

    def test_a_failed_naming_is_reported_not_raised(self) -> None:
        outcome = self._run()
        result = service.name_candidates(
            self.engine, outcome.run_id, adapters={"anthropic": FakeAdapter(["nope"])}  # type: ignore[arg-type]
        )
        self.assertEqual(result.named, 0)
        self.assertEqual(len(result.failed), 1)

    def _named(self):  # type: ignore[no-untyped-def]
        """Run, name and return (run_id, candidate).

        Returns:
            The run id and the stored candidate.
        """
        outcome = self._run()
        service.name_candidates(self.engine, outcome.run_id, adapters={"anthropic": self._reply()})  # type: ignore[arg-type]
        return outcome.run_id, store.list_candidates(self.engine, outcome.run_id)[0]  # type: ignore[arg-type]

    def test_a_verdict_needs_a_rationale_and_a_known_value(self) -> None:
        _, candidate = self._named()
        for verdict, rationale in (("confirmed", "  "), ("maybe", "why")):
            with self.assertRaises(ValueError):
                review.record_review(
                    self.engine, self.user, candidate.id, verdict, rationale
                )

    def test_the_queue_shows_pending_then_the_users_own_verdict(self) -> None:
        run_id, candidate = self._named()
        (entry,) = review.review_queue(self.engine, self.user, run_id, _CONFIG, NOW)
        self.assertEqual(entry.verdict, "pending")
        review.record_review(
            self.engine,
            self.user,
            candidate.id,
            "confirmed",
            "Seen it at three clients",
        )
        (entry,) = review.review_queue(self.engine, self.user, run_id, _CONFIG, NOW)
        self.assertEqual(
            (entry.verdict, entry.rationale), ("confirmed", "Seen it at three clients")
        )

    def test_another_user_sees_it_pending_and_cannot_see_the_verdict(self) -> None:
        run_id, candidate = self._named()
        review.record_review(
            self.engine, self.user, candidate.id, "dismissed", "A collection artefact"
        )
        (entry,) = review.review_queue(self.engine, self.other, run_id, _CONFIG, NOW)
        self.assertEqual((entry.verdict, entry.rationale), ("pending", None))

    def test_a_dismissal_suppresses_the_cluster_in_later_runs_until_it_expires(
        self,
    ) -> None:
        _, candidate = self._named()
        review.record_review(
            self.engine, self.user, candidate.id, "dismissed", "Not a real role"
        )
        later = self._run()
        service.name_candidates(self.engine, later.run_id, adapters={"anthropic": self._reply()})  # type: ignore[arg-type]
        today = datetime.now(UTC)  # the verdict is stamped by the database clock
        self.assertEqual(review.review_queue(self.engine, self.user, later.run_id, _CONFIG, today), [])  # type: ignore[arg-type]
        expired = today + timedelta(days=_CONFIG.dismiss_suppress_days + 1)
        self.assertEqual(
            len(review.review_queue(self.engine, self.user, later.run_id, _CONFIG, expired)), 1  # type: ignore[arg-type]
        )

    def test_a_user_cannot_write_a_review_for_someone_else(self) -> None:
        _, candidate = self._named()
        with self.assertRaises(
            Exception
        ):  # noqa: B017 - RLS WITH CHECK rejects the row
            with self.engine.begin() as conn:
                conn.execute(
                    text("SET LOCAL app.current_user_id = :u"), {"u": str(self.other)}
                )
                conn.execute(
                    text(
                        "INSERT INTO emergent.emergent_role_review (user_id, candidate_id, verdict, rationale) VALUES (:u, :c, 'confirmed', 'x')"
                    ),
                    {"u": self.user, "c": candidate.id},
                )

    def test_an_empty_corpus_makes_no_run(self) -> None:
        with mock.patch(
            "core.emergent.service.load_discovery_jobs", return_value=([], 0)
        ):
            outcome = service.run_clustering(
                self.engine, _CONFIG, embed, "zz-model", SEEDS, NOW
            )
        self.assertEqual((outcome.run_id, outcome.n_titles), (None, 0))


if __name__ == "__main__":
    unittest.main()
