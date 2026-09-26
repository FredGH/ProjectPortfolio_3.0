"""Integration tests for core.skills.write_job_skills against live Postgres."""

from __future__ import annotations

import json
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine, purge_fixtures
from tests.skills_fakes import FakeAdapter

from core.skills.write_job_skills import count_pending_jobs, write_job_skills


def _reply(*skills: tuple[str, str]) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": lvl} for s, lvl in skills]}
    )


class TestWriteJobSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _add_survivor(
        self,
        job: str,
        description: str | None,
        *,
        source: str = "greenhouse",
        category: str | None = None,
        country: str | None = None,
    ) -> None:
        """Insert a survivor and, if given, its gold.dim_job row."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, apply_source_job_id, "
                    "apply_job_url, apply_title_for_display) VALUES (:g, :d, "
                    ":src, :s, 'https://example.test/x', 'Data Engineer')"
                ),
                {"g": job, "d": description, "s": f"src-{job}", "src": source},
            )
            if category is not None or country is not None:
                conn.execute(
                    text(
                        "INSERT INTO gold.dim_job "
                        "(job_group_id, category, country_iso) "
                        "VALUES (:g, :c, :co)"
                    ),
                    {"g": job, "c": category, "co": country},
                )

    def _write(self, adapter: FakeAdapter, jobs: list[str], **kwargs):
        return write_job_skills(
            self.engine, adapters={"ollama": adapter}, job_group_ids=jobs, **kwargs
        )

    def _raw(self, job: str) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT raw_norm, requirement_level FROM silver.job_skill_raw "
                    "WHERE job_group_id = :j ORDER BY raw_norm"
                ),
                {"j": job},
            ).all()
        return [(r.raw_norm, r.requirement_level) for r in rows]

    def _extractions(self, job: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*) FROM silver.job_skill_extraction "
                    "WHERE job_group_id = :j"
                ),
                {"j": job},
            ).scalar_one()

    def test_extracts_and_stores_skills_with_levels(self) -> None:
        self._add_survivor(self.job, "Python required. dbt is a plus.")
        adapter = FakeAdapter(_reply(("Python", "must_have"), ("dbt", "nice_to_have")))
        summary = self._write(adapter, [self.job])
        self.assertEqual(
            (summary.extracted_jobs, summary.skill_rows, summary.failed_jobs), (1, 2, 0)
        )
        self.assertEqual(
            self._raw(self.job), [("dbt", "nice_to_have"), ("python", "must_have")]
        )
        with self.engine.connect() as conn:
            version = conn.execute(
                text(
                    "SELECT prompt_version FROM silver.job_skill_extraction "
                    "WHERE job_group_id = :j"
                ),
                {"j": self.job},
            ).scalar_one()
        self.assertEqual(version, "local.v1")

    def test_on_job_done_reports_every_job_after_it_is_committed(self) -> None:
        prefix = uuid.uuid4().hex[:6]  # one shared prefix: jobs run in id order
        jobs = [f"fixture-job-{prefix}-{c}" for c in "abc"]
        for job in jobs:
            self._add_survivor(job, "Python required.")
        # Job b's reply is cut off twice (first try + the retry) so it fails;
        # a and c succeed.
        adapter = FakeAdapter(
            _reply(("Python", "must_have")), truncated=[False, True, True, False]
        )
        seen: list[tuple[str, bool, int]] = []

        def on_done(job_group_id: str, extracted: bool) -> None:
            seen.append((job_group_id, extracted, self._extractions(job_group_id)))

        self._write(adapter, jobs, on_job_done=on_done)
        # Reported in order, once each, and a success is already durable
        # when its callback fires (a failure has no extraction row).
        self.assertEqual(
            seen, [(jobs[0], True, 1), (jobs[1], False, 0), (jobs[2], True, 1)]
        )

    def test_should_stop_ends_the_batch_before_the_next_job(self) -> None:
        prefix = uuid.uuid4().hex[:6]  # one shared prefix: jobs run in id order
        jobs = [f"fixture-job-{prefix}-{c}" for c in "abc"]
        for job in jobs:
            self._add_survivor(job, "Python required.")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        checks = iter([False, True])  # let one job through, then stop

        summary = self._write(adapter, jobs, should_stop=lambda: next(checks))

        self.assertEqual(summary.extracted_jobs, 1)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self._extractions(jobs[0]), 1)
        self.assertEqual(self._extractions(jobs[1]), 0)  # left for a later run

    def test_a_second_run_does_no_llm_work(self) -> None:
        self._add_survivor(self.job, "Python required.")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self._write(adapter, [self.job])
        calls = len(adapter.calls)
        summary = self._write(adapter, [self.job])
        self.assertEqual(summary.extracted_jobs, 0)
        self.assertEqual(len(adapter.calls), calls)

    def test_a_reply_cut_off_by_the_token_cap_counts_as_failed_and_is_retried(
        self,
    ) -> None:
        self._add_survivor(self.job, "Python required.")
        looping = FakeAdapter(_reply(("Python", "must_have")), truncated=True)
        summary = self._write(looping, [self.job])
        self.assertEqual((summary.extracted_jobs, summary.failed_jobs), (0, 1))
        self.assertEqual(self._extractions(self.job), 0)
        # Nothing was recorded, so the next run picks the job up again.
        fixed = FakeAdapter(_reply(("Python", "must_have")))
        self.assertEqual(self._write(fixed, [self.job]).extracted_jobs, 1)

    def test_a_job_with_no_skills_is_recorded_and_not_retried(self) -> None:
        self._add_survivor(self.job, "We are a friendly company.")
        adapter = FakeAdapter(_reply())
        self._write(adapter, [self.job])
        self.assertEqual(self._extractions(self.job), 1)
        self.assertEqual(self._raw(self.job), [])
        calls = len(adapter.calls)
        self._write(adapter, [self.job])
        self.assertEqual(len(adapter.calls), calls)

    def test_an_unparseable_response_counts_as_failed_and_is_retried(self) -> None:
        self._add_survivor(self.job, "Python required.")
        summary = self._write(FakeAdapter("no idea"), [self.job])
        self.assertEqual((summary.extracted_jobs, summary.failed_jobs), (0, 1))
        self.assertEqual(self._extractions(self.job), 0)
        retry = self._write(FakeAdapter(_reply(("Python", "must_have"))), [self.job])
        self.assertEqual(retry.extracted_jobs, 1)

    def test_jobs_without_a_description_are_skipped(self) -> None:
        self._add_survivor(self.job, None)
        adapter = FakeAdapter(_reply())
        self.assertEqual(self._write(adapter, [self.job]).extracted_jobs, 0)
        self.assertEqual(adapter.calls, [])

    def test_limit_caps_the_jobs_processed(self) -> None:
        other = f"fixture-job-{uuid.uuid4().hex[:8]}"
        self._add_survivor(self.job, "Python required.")
        self._add_survivor(other, "SQL required.")
        summary = self._write(
            FakeAdapter(_reply(("Python", "must_have"))), [self.job, other], limit=1
        )
        self.assertEqual(summary.extracted_jobs, 1)

    def _two_jobs(self, **second) -> tuple[str, str]:
        """Add a data_engineer greenhouse job and a second job; return both ids."""
        first = f"fixture-job-{uuid.uuid4().hex[:8]}"
        other = f"fixture-job-{uuid.uuid4().hex[:8]}"
        self._add_survivor(first, "Python.", category="data_engineer")
        self._add_survivor(other, "Python.", **second)
        return first, other

    def test_the_source_filter_leaves_other_sources_pending(self) -> None:
        first, other = self._two_jobs(source="adzuna", category="data_engineer")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        summary = self._write(adapter, [first, other], sources=["greenhouse"])
        self.assertEqual(summary.extracted_jobs, 1)
        self.assertEqual(self._extractions(first), 1)
        self.assertEqual(self._extractions(other), 0)

    def test_the_source_filter_excludes_leaked_test_sources(self) -> None:
        first, other = self._two_jobs(source="test_source_ab12", category="other")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self._write(adapter, [first, other], sources=["greenhouse", "adzuna"])
        self.assertEqual(self._extractions(other), 0)

    def test_the_category_filter_leaves_other_categories_pending(self) -> None:
        first, other = self._two_jobs(category="other")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        summary = self._write(adapter, [first, other], categories=["data_engineer"])
        self.assertEqual(summary.extracted_jobs, 1)
        self.assertEqual(self._extractions(first), 1)
        self.assertEqual(self._extractions(other), 0)

    def test_several_categories_are_or_ed(self) -> None:
        first, other = self._two_jobs(category="ai_ml_engineer")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        summary = self._write(
            adapter, [first, other], categories=["data_engineer", "ai_ml_engineer"]
        )
        self.assertEqual(summary.extracted_jobs, 2)

    def test_a_job_with_no_category_row_is_excluded_by_a_category_filter(self) -> None:
        first, other = self._two_jobs()  # `other` has no gold.dim_job row
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self._write(adapter, [first, other], categories=["data_engineer"])
        self.assertEqual(self._extractions(other), 0)

    def test_the_country_filter_leaves_other_countries_pending(self) -> None:
        first, other = self._two_jobs(category="data_engineer", country="US")
        first_country = f"fixture-job-{uuid.uuid4().hex[:8]}"
        self._add_survivor(
            first_country, "Python.", category="data_engineer", country="GB"
        )
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        summary = self._write(adapter, [first, other, first_country], countries=["GB"])
        self.assertEqual(summary.extracted_jobs, 1)
        self.assertEqual(self._extractions(first_country), 1)
        self.assertEqual(self._extractions(first), 0)
        self.assertEqual(self._extractions(other), 0)

    def test_several_countries_are_or_ed(self) -> None:
        gb_job = f"fixture-job-{uuid.uuid4().hex[:8]}"
        us_job = f"fixture-job-{uuid.uuid4().hex[:8]}"
        de_job = f"fixture-job-{uuid.uuid4().hex[:8]}"
        self._add_survivor(gb_job, "Python.", country="GB")
        self._add_survivor(us_job, "Python.", country="US")
        self._add_survivor(de_job, "Python.", country="DE")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        summary = self._write(adapter, [gb_job, us_job, de_job], countries=["GB", "US"])
        self.assertEqual(summary.extracted_jobs, 2)
        self.assertEqual(self._extractions(de_job), 0)

    def test_a_job_with_no_country_row_is_excluded_by_a_country_filter(self) -> None:
        first, other = self._two_jobs()  # `other` has no gold.dim_job row at all
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self._write(adapter, [first, other], countries=["GB"])
        self.assertEqual(self._extractions(other), 0)
        self.assertEqual(self._extractions(first), 0)  # first has no country either

    def test_country_and_category_filters_both_apply(self) -> None:
        gb_data_eng = f"fixture-job-{uuid.uuid4().hex[:8]}"
        gb_other = f"fixture-job-{uuid.uuid4().hex[:8]}"
        self._add_survivor(
            gb_data_eng, "Python.", category="data_engineer", country="GB"
        )
        self._add_survivor(gb_other, "Python.", category="other", country="GB")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        summary = self._write(
            adapter,
            [gb_data_eng, gb_other],
            countries=["GB"],
            categories=["data_engineer"],
        )
        self.assertEqual(summary.extracted_jobs, 1)
        self.assertEqual(self._extractions(gb_other), 0)

    def test_source_and_category_filters_both_apply(self) -> None:
        first, other = self._two_jobs(source="adzuna", category="data_engineer")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        summary = self._write(
            adapter,
            [first, other],
            sources=["greenhouse"],
            categories=["data_engineer"],
        )
        self.assertEqual(summary.extracted_jobs, 1)
        self.assertEqual(self._extractions(other), 0)

    def test_no_filter_still_extracts_every_pending_job(self) -> None:
        first, other = self._two_jobs(source="adzuna")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self.assertEqual(self._write(adapter, [first, other]).extracted_jobs, 2)

    def test_count_pending_jobs_matches_what_a_run_would_process(self) -> None:
        first, other = self._two_jobs(source="adzuna", category="other", country="US")
        ids = [first, other]
        self.assertEqual(count_pending_jobs(self.engine, job_group_ids=ids), 2)
        self.assertEqual(
            count_pending_jobs(self.engine, job_group_ids=ids, sources=["greenhouse"]),
            1,
        )
        self.assertEqual(
            count_pending_jobs(
                self.engine, job_group_ids=ids, categories=["data_engineer"]
            ),
            1,
        )
        self.assertEqual(
            count_pending_jobs(
                self.engine, job_group_ids=ids, categories=["software_engineer"]
            ),
            0,
        )
        self.assertEqual(
            count_pending_jobs(self.engine, job_group_ids=ids, countries=["US"]), 1
        )
        self.assertEqual(
            count_pending_jobs(self.engine, job_group_ids=ids, countries=["GB"]), 0
        )
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self._write(adapter, [first])
        self.assertEqual(count_pending_jobs(self.engine, job_group_ids=ids), 1)


if __name__ == "__main__":
    unittest.main()
