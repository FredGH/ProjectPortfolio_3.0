"""Integration tests for core.skills.write_job_skills against live Postgres."""

from __future__ import annotations

import json
import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine, purge_fixtures
from tests.skills_fakes import FakeAdapter

from core.skills.write_job_skills import write_job_skills


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

    def _add_survivor(self, job: str, description: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, apply_source_job_id, "
                    "apply_job_url, apply_title_for_display) VALUES (:g, :d, "
                    "'greenhouse', :s, 'https://example.test/x', 'Data Engineer')"
                ),
                {"g": job, "d": description, "s": f"src-{job}"},
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

    def test_a_second_run_does_no_llm_work(self) -> None:
        self._add_survivor(self.job, "Python required.")
        adapter = FakeAdapter(_reply(("Python", "must_have")))
        self._write(adapter, [self.job])
        calls = len(adapter.calls)
        summary = self._write(adapter, [self.job])
        self.assertEqual(summary.extracted_jobs, 0)
        self.assertEqual(len(adapter.calls), calls)

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


if __name__ == "__main__":
    unittest.main()
