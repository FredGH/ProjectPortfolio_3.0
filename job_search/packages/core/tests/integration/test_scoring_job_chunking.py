"""Integration tests for core.scoring.job_chunking against live Postgres."""

from __future__ import annotations

import unittest

from sqlalchemy import Engine, text
from tests.integration.skills_fixtures import live_owner_engine

from core.scoring.job_chunking import chunk_and_embed_jobs, detect_sections
from core.skills.vector import to_pgvector

_GUARD_MODEL = "zzfixture-guard"
"""Sentinel `embedding_model` used to quarantine real jobs during this
suite (see `_quarantine_real_jobs`). Never a value a real embedding run
would use, so it is always safe to delete every row carrying it."""


def _quarantine_real_jobs(engine: Engine) -> None:
    """Insert a placeholder chunk row for every real job not yet chunked.

    `chunk_and_embed_jobs` has no per-job scoping — it processes every
    unchunked job in `gold.dim_job`, which in this shared dev database
    includes thousands of real postings. Without this guard, calling it
    in a test would embed real jobs with the test's fake all-zero vector
    and a fixture model tag. Inserting one placeholder row per real
    unchunked job first makes them look already-chunked, so the function
    under test never touches them or calls `embed()` on their text.

    Args:
        engine: The owner-role engine.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scoring.job_chunk_embedding "
                "(job_group_id, section, chunk_index, chunk_text, embedding, "
                "embedding_model) "
                "SELECT j.job_group_id, 'other', 0, '', "
                "CAST(:embedding AS vector), :model "
                "FROM gold.dim_job AS j "
                "LEFT JOIN (SELECT DISTINCT job_group_id FROM "
                "scoring.job_chunk_embedding) AS c "
                "ON c.job_group_id = j.job_group_id "
                "WHERE c.job_group_id IS NULL AND j.description IS NOT NULL "
                "AND j.job_group_id NOT LIKE 'fixture-job-%' "
                "ON CONFLICT (job_group_id, section, chunk_index) DO NOTHING"
            ),
            {"embedding": to_pgvector([0.0] * 768), "model": _GUARD_MODEL},
        )


def _release_quarantine(engine: Engine) -> None:
    """Remove every placeholder row `_quarantine_real_jobs` inserted.

    Args:
        engine: The owner-role engine.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM scoring.job_chunk_embedding "
                "WHERE embedding_model = :model"
            ),
            {"model": _GUARD_MODEL},
        )


_STRUCTURED = """We are a fast-growing fintech company.

Responsibilities
Build and maintain our core ledger service.
Own the on-call rotation for payments.

Requirements
5+ years of backend experience.
Strong SQL skills.

Nice to have
Experience with Kafka.

Benefits
25 days holiday.
"""

_UNSTRUCTURED = "Great company looking for a great engineer to do great things."


class TestDetectSections(unittest.TestCase):
    def test_a_structured_description_splits_into_its_named_sections(self) -> None:
        sections = detect_sections(_STRUCTURED)
        names = [s for s, _ in sections]
        self.assertEqual(
            names,
            [
                "company_blurb",
                "responsibilities",
                "requirements",
                "nice_to_have",
                "benefits",
            ],
        )
        self.assertIn("ledger service", dict(sections)["responsibilities"])

    def test_an_unstructured_description_becomes_one_other_section(self) -> None:
        sections = detect_sections(_UNSTRUCTURED)
        self.assertEqual([s for s, _ in sections], ["other"])

    def test_a_long_line_is_never_mistaken_for_a_heading(self) -> None:
        text_ = (
            "Requirements and expectations for this particular role include the "
            "following extensive list of desirable attributes\n"
            "5+ years experience.\n"
        )
        sections = detect_sections(text_)
        # The long line does not match a canonical heading exactly enough, or
        # is over the length cutoff, so it stays inside company_blurb/other —
        # not treated as a "requirements" heading of its own.
        self.assertNotIn("requirements", [s for s, _ in sections])


class TestChunkAndEmbedJobs(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()
        # chunk_and_embed_jobs sweeps every unchunked job in gold.dim_job,
        # which in this shared dev database includes many real postings —
        # quarantine them so this suite's fake embedder never touches real
        # data (see _quarantine_real_jobs's docstring).
        _quarantine_real_jobs(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        _release_quarantine(cls.engine)

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id LIKE 'fixture-job-%'"
                )
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'fixture-job-%'")
            )

    def _insert_job(self, job_group_id: str, description: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, description) VALUES "
                    "(:j, 'zzfixture role', 'zzfixture co', :d)"
                ),
                {"j": job_group_id, "d": description},
            )

    def _fake_embed(self, text_: str) -> list[float]:
        return [0.0] * 768

    def test_chunking_a_job_writes_one_row_per_chunk(self) -> None:
        self._insert_job("fixture-job-chunk-1", _STRUCTURED)
        written = chunk_and_embed_jobs(
            self.engine, embed=self._fake_embed, embedding_model="zzfixture-model"
        )
        self.assertGreaterEqual(written, 1)
        with self.engine.connect() as conn:
            sections = (
                conn.execute(
                    text(
                        "SELECT DISTINCT section FROM scoring.job_chunk_embedding "
                        "WHERE job_group_id = 'fixture-job-chunk-1'"
                    )
                )
                .scalars()
                .all()
            )
        self.assertIn("responsibilities", sections)

    def test_rerunning_does_not_duplicate_or_recompute_an_already_chunked_job(
        self,
    ) -> None:
        self._insert_job("fixture-job-chunk-2", _STRUCTURED)
        chunk_and_embed_jobs(
            self.engine, embed=self._fake_embed, embedding_model="zzfixture-model"
        )
        with self.engine.connect() as conn:
            before = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-2'"
                )
            ).scalar_one()
        written_again = chunk_and_embed_jobs(
            self.engine, embed=self._fake_embed, embedding_model="zzfixture-model"
        )
        with self.engine.connect() as conn:
            after = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-2'"
                )
            ).scalar_one()
        self.assertEqual(before, after)
        self.assertEqual(written_again, 0)


if __name__ == "__main__":
    unittest.main()
