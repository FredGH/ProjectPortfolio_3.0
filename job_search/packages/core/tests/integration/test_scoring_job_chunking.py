"""Integration tests for core.scoring.job_chunking against live Postgres."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.scoring.job_chunking import chunk_and_embed_jobs, detect_sections

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

# Two separately-headed blocks ("Requirements" and "Essential") that both
# map to section "requirements" per _SECTION_PATTERNS, with an unrelated
# "Nice to have" block in between so they are genuinely two distinct
# detected blocks, not merged into one. Finding 2: chunk_index must run per
# section NAME across the whole job, not restart per detected block —
# otherwise the second block's chunks collide on (job_group_id, section,
# chunk_index) and get silently discarded by ON CONFLICT DO NOTHING.
_TWO_REQUIREMENTS_BLOCKS = """We are a fast-growing widget company.

Requirements
5+ years of backend experience.
Strong SQL skills.

Nice to have
Experience with Kafka.

Essential
Comfortable with ambiguity.
Excellent communication skills.
"""


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
            self.engine,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
            job_group_ids=["fixture-job-chunk-1"],
        )
        self.assertEqual(written, 1)
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
            self.engine,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
            job_group_ids=["fixture-job-chunk-2"],
        )
        with self.engine.connect() as conn:
            before = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-2'"
                )
            ).scalar_one()
        written_again = chunk_and_embed_jobs(
            self.engine,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
            job_group_ids=["fixture-job-chunk-2"],
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

    def test_chunk_and_embed_jobs_only_touches_the_named_job_group_ids(self) -> None:
        # A new "real-looking" job that is NOT passed in job_group_ids.
        self._insert_job("fixture-job-chunk-unscoped", _STRUCTURED)
        # The job actually scoped in this call.
        self._insert_job("fixture-job-chunk-scoped", _STRUCTURED)
        written = chunk_and_embed_jobs(
            self.engine,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
            job_group_ids=["fixture-job-chunk-scoped"],
        )
        self.assertEqual(written, 1)
        with self.engine.connect() as conn:
            scoped_rows = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-scoped'"
                )
            ).scalar_one()
            unscoped_rows = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.job_chunk_embedding "
                    "WHERE job_group_id = 'fixture-job-chunk-unscoped'"
                )
            ).scalar_one()
        self.assertGreater(scoped_rows, 0)
        self.assertEqual(unscoped_rows, 0)

    def test_a_job_with_no_non_empty_sections_is_not_counted_as_chunked(self) -> None:
        # Every line is a heading with no body text under it, so
        # detect_sections filters every section out (including
        # company_blurb) and returns an empty list — no rows get written
        # for this job, so it must not be counted as chunked.
        self._insert_job("fixture-job-chunk-empty", "Responsibilities\nRequirements\n")
        written = chunk_and_embed_jobs(
            self.engine,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
            job_group_ids=["fixture-job-chunk-empty"],
        )
        self.assertEqual(written, 0)

    def test_two_blocks_mapping_to_the_same_section_both_get_all_their_chunks(
        self,
    ) -> None:
        self._insert_job("fixture-job-chunk-two-req", _TWO_REQUIREMENTS_BLOCKS)
        written = chunk_and_embed_jobs(
            self.engine,
            embed=self._fake_embed,
            embedding_model="zzfixture-model",
            job_group_ids=["fixture-job-chunk-two-req"],
        )
        self.assertEqual(written, 1)
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT chunk_index, chunk_text "
                        "FROM scoring.job_chunk_embedding "
                        "WHERE job_group_id = 'fixture-job-chunk-two-req' "
                        "AND section = 'requirements' "
                        "ORDER BY chunk_index"
                    )
                )
                .mappings()
                .all()
            )
        self.assertEqual([r["chunk_index"] for r in rows], list(range(len(rows))))
        self.assertGreaterEqual(len(rows), 2)
        all_text = " ".join(r["chunk_text"] for r in rows)
        self.assertIn("backend experience", all_text)
        self.assertIn("ambiguity", all_text)


if __name__ == "__main__":
    unittest.main()
