"""Integration tests for the eval runner (PLAN.md Step 12a, Task 13).

These tests exercise `core.evals.runner.run_eval` against a real
Postgres database — no mocking the database, per this project's
testing rules. `classification.category_review_labels` and
`evals.eval_runs` are SHARED tables, so every test scopes its writes
carefully (see the per-test docstrings and setUp/tearDown comments
below) to avoid contaminating, or being contaminated by, other data in
this dev database.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

from core.db.session import build_engine
from core.evals.runner import run_eval
from core.llm.types import LLMResponse

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"

# A task-config YAML pinning eval_regression_threshold to exactly 0 —
# used by TestRunEvalRegressionThresholdZeroPinning below to prove a
# zero-tolerance threshold still flags any negative delta, rather than
# being (incorrectly) treated the same as an unconfigured (`None`)
# threshold. Mirrors config/llm_tasks.yml's real job_categorisation
# entry except for this one field.
_ZERO_THRESHOLD_TASK_CONFIG_YAML = """
tasks:
  job_categorisation:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
    eval_metric: exact_match
    eval_regression_threshold: 0
"""


def _insert_dim_job(conn, **overrides: object) -> None:
    """Insert one row into `gold.dim_job` for a golden-set fixture.

    Args:
        conn: An open SQLAlchemy connection with INSERT on `gold.dim_job`.
        **overrides: Column values overriding this helper's defaults.

    Returns:
        None.
    """
    values = {
        "job_group_id": None,
        "title_for_display": "Data Engineer",
        "title_raw": "Data Engineer",
        "company": "Acme Ltd",
        "location": "London",
        "description": "A test description.",
        "category": "data_engineer",
        "category_confidence": 0.9,
        "category_method": "rules",
        "qa_category": "data_engineer",
        "seniority_band": "mid",
    }
    values.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO gold.dim_job (
                job_group_id, title_for_display, title_raw, company,
                location, description, category, category_confidence,
                category_method, qa_category, seniority_band
            ) VALUES (
                :job_group_id, :title_for_display, :title_raw, :company,
                :location, :description, :category, :category_confidence,
                :category_method, :qa_category, :seniority_band
            )
            """
        ),
        values,
    )


class _FakeAdapter:
    """Always classifies correctly against whatever title it's given,
    by echoing back a category the test controls per-title.
    """

    def __init__(self, category_by_title: dict[str, str]) -> None:
        """Initialise the fake adapter.

        Args:
            category_by_title: Maps an exact job title to the category
                this adapter should predict for it. A title not in
                this mapping predicts "other".
        """
        self._category_by_title = category_by_title

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Return a canned classification response for `prompt`.

        Args:
            model: The provider-specific model identifier (echoed back
                unused, to satisfy `LLMAdapter`'s interface).
            prompt: The rendered classification prompt. The job title
                is recovered from its "Job title: {title}" line.
            temperature: Unused — accepted only to satisfy `LLMAdapter`.
            seed: Unused — accepted only to satisfy `LLMAdapter`.

        Returns:
            An `LLMResponse` whose `text` is a JSON object naming the
            category configured for this prompt's title, or "other" if
            the title isn't in `category_by_title`.
        """
        import json
        import re

        match = re.search(r"Job title: (.+)", prompt)
        title = match.group(1).strip() if match else ""
        category = self._category_by_title.get(title, "other")
        return LLMResponse(
            text=json.dumps({"category": category, "confidence": 0.9}),
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestRunEvalInsufficientData(unittest.TestCase):
    """No seeded golden-set rows at all — under MINIMUM_GOLDEN_SET_SIZE."""

    def setUp(self) -> None:
        """Build an owner-role engine for the test.

        Returns:
            None.
        """
        self.engine = build_engine(_OWNER_DSN)

    def tearDown(self) -> None:
        """Dispose of the test engine.

        Returns:
            None.
        """
        self.engine.dispose()

    def test_reports_insufficient_data_rather_than_a_false_pass(self) -> None:
        """A too-small golden set must report insufficient_data, not a
        misleading pass/fail score.

        Returns:
            None.
        """
        # Relies on this dev database's classification.category_review_labels
        # genuinely having fewer than MINIMUM_GOLDEN_SET_SIZE rows at the
        # point this test runs (true as of this branch — JOB-170's
        # hand-check hasn't been done yet). If that ever changes, this
        # test's premise no longer holds and should be revisited rather
        # than the runner's behaviour.
        with self.engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM classification.category_review_labels")
            ).scalar_one()
        if count >= 20:
            self.skipTest(
                "category_review_labels already has >= MINIMUM_GOLDEN_SET_SIZE "
                "rows in this environment"
            )
        result = run_eval(
            "job_categorisation", "target", engine=self.engine, adapters={}
        )
        self.assertEqual(result.status, "insufficient_data")
        self.assertIsNone(result.score)


class TestRunEvalWithEnoughCases(unittest.TestCase):
    """Seeds MINIMUM_GOLDEN_SET_SIZE reviewed rows directly so the run
    proceeds regardless of this dev database's real JOB-170 progress.
    """

    def setUp(self) -> None:
        """Seed 20 fixture jobs and reviewed labels, and capture a
        high-water mark on `evals.eval_runs` so this test's teardown
        can scope its cleanup to rows it created.

        `evals.eval_runs` has no per-test scoping column (unlike
        `category_review_labels`, which is scoped via `job_group_ids`
        below) and, since Task 14 added the real `run-evals` CLI, may
        legitimately hold real `job_categorisation` history written by
        a developer outside these tests. Deleting unscoped rows (as
        this test used to) would silently destroy that history, so
        instead we record the current max `id` here and only ever
        touch rows above it.

        Returns:
            None.
        """
        self.engine = build_engine(_OWNER_DSN)
        self.job_group_ids: list[str] = []
        with self.engine.begin() as conn:
            self.eval_runs_high_water_mark = conn.execute(
                text(
                    "SELECT COALESCE(MAX(id), 0) FROM evals.eval_runs "
                    "WHERE task = 'job_categorisation'"
                )
            ).scalar_one()
            for i in range(20):
                job_group_id = f"test-runner-{uuid.uuid4().hex}"
                self.job_group_ids.append(job_group_id)
                title = f"Data Engineer {i}"
                _insert_dim_job(conn, job_group_id=job_group_id, title_raw=title)
                conn.execute(
                    text(
                        "INSERT INTO classification.category_review_labels "
                        "(job_group_id, reviewed_category, reviewed_seniority_band) "
                        "VALUES (:id, 'data_engineer', 'mid')"
                    ),
                    {"id": job_group_id},
                )
        self.adapters = {
            "anthropic": _FakeAdapter(
                {f"Data Engineer {i}": "data_engineer" for i in range(20)}
            )
        }

    def tearDown(self) -> None:
        """Delete this test's fixture rows and only the eval_runs rows
        this test itself produced (those with `id` above the
        high-water mark captured in `setUp`), never any pre-existing
        real `job_categorisation` history.

        Returns:
            None.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id = ANY(:ids)"
                ),
                {"ids": self.job_group_ids},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = ANY(:ids)"),
                {"ids": self.job_group_ids},
            )
            conn.execute(
                text(
                    "DELETE FROM evals.eval_runs "
                    "WHERE task = 'job_categorisation' AND id > :high_water_mark"
                ),
                {"high_water_mark": self.eval_runs_high_water_mark},
            )
        self.engine.dispose()

    def test_perfect_predictions_score_one_and_persist_a_run(self) -> None:
        """A perfectly-predicting adapter scores 1.0 and persists a new
        eval_runs row carrying the real prompt_version used.

        Whether a *previous* run exists is no longer asserted here: now
        that Task 14's real `run-evals` CLI writes to this same shared
        table, a prior `job_categorisation` row may legitimately exist
        in this dev database, so `result.previous_score is None` is not
        a safe premise. What this test can safely assert is that THIS
        run inserted a genuinely new row (id above the high-water mark
        captured in setUp) with this run's own correct score,
        case_count, provider, metric, and prompt_version.

        Returns:
            None.
        """
        result = run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=self.adapters,
            job_group_ids=self.job_group_ids,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.score, 1.0)
        self.assertGreaterEqual(result.case_count, 20)
        self.assertFalse(result.regressed)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, task, provider, prompt_version, metric, "
                    "score, case_count FROM evals.eval_runs "
                    "WHERE task = 'job_categorisation' "
                    "ORDER BY run_at DESC LIMIT 1"
                )
            ).one()
        self.assertGreater(row.id, self.eval_runs_high_water_mark)
        self.assertEqual(row.provider, "anthropic")
        self.assertEqual(row.metric, "exact_match")
        self.assertEqual(float(row.score), 1.0)
        # The real prompt_version classify_by_llm used, not a hardcoded
        # string — resolved_prompt_family ("claude") + the classifier
        # module's own _PROMPT_VERSION_NUMBER (1).
        self.assertEqual(row.prompt_version, "claude.v1")

    def test_a_worse_second_run_is_flagged_as_a_regression(self) -> None:
        """A second run that gets everything wrong is flagged as a
        regression against the first run's perfect score.

        Returns:
            None.
        """
        run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=self.adapters,
            job_group_ids=self.job_group_ids,
        )
        # Second run: adapter now gets everything wrong.
        worse_adapters = {"anthropic": _FakeAdapter({})}
        result = run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=worse_adapters,
            job_group_ids=self.job_group_ids,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.previous_score, 1.0)
        self.assertEqual(result.delta, -1.0)
        self.assertTrue(result.regressed)

    def test_provider_not_configured_for_local(self) -> None:
        """Requesting "local" for a task with no local_* config must
        not crash, and must not persist an eval_runs row.

        Returns:
            None.
        """
        # job_categorisation has no local_provider configured (Task 6) —
        # requesting "local" must not crash, and must not persist a row.
        result = run_eval(
            "job_categorisation", "local", engine=self.engine, adapters={}
        )
        self.assertEqual(result.status, "provider_not_configured")
        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM evals.eval_runs "
                    "WHERE task = 'job_categorisation' AND provider = 'ollama'"
                )
            ).scalar_one()
        self.assertEqual(count, 0)


class TestRunEvalRegressionThresholdZeroPinning(unittest.TestCase):
    """Pins the round-1 fix from Task 13's review (JOB-182 final review
    finding 5): `eval_regression_threshold: 0` is a deliberately strict
    zero-tolerance setting, not "unconfigured" (`None`) — ANY negative
    delta, however small, must still be flagged as a regression.
    """

    def setUp(self) -> None:
        """Seed 20 fixture jobs/labels and write a temp task-config
        YAML pinning job_categorisation's eval_regression_threshold to
        exactly 0.

        Returns:
            None.
        """
        self.engine = build_engine(_OWNER_DSN)
        self.job_group_ids: list[str] = []
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        )
        tmp.write(_ZERO_THRESHOLD_TASK_CONFIG_YAML)
        tmp.close()
        self.config_path = Path(tmp.name)
        with self.engine.begin() as conn:
            self.eval_runs_high_water_mark = conn.execute(
                text(
                    "SELECT COALESCE(MAX(id), 0) FROM evals.eval_runs "
                    "WHERE task = 'job_categorisation'"
                )
            ).scalar_one()
            for i in range(20):
                job_group_id = f"test-runner-zero-thr-{uuid.uuid4().hex}"
                self.job_group_ids.append(job_group_id)
                title = f"Zero Threshold Engineer {i}"
                _insert_dim_job(conn, job_group_id=job_group_id, title_raw=title)
                conn.execute(
                    text(
                        "INSERT INTO classification.category_review_labels "
                        "(job_group_id, reviewed_category, reviewed_seniority_band) "
                        "VALUES (:id, 'data_engineer', 'mid')"
                    ),
                    {"id": job_group_id},
                )
        self.perfect_adapters = {
            "anthropic": _FakeAdapter(
                {f"Zero Threshold Engineer {i}": "data_engineer" for i in range(20)}
            )
        }
        # Gets exactly one of the 20 cases wrong — the smallest
        # possible negative delta (-0.05) short of a perfect rerun.
        self.slightly_worse_adapters = {
            "anthropic": _FakeAdapter(
                {f"Zero Threshold Engineer {i}": "data_engineer" for i in range(1, 20)}
            )
        }

    def tearDown(self) -> None:
        """Delete this test's fixture rows, its own eval_runs rows, and
        the temp config file.

        Returns:
            None.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id = ANY(:ids)"
                ),
                {"ids": self.job_group_ids},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = ANY(:ids)"),
                {"ids": self.job_group_ids},
            )
            conn.execute(
                text(
                    "DELETE FROM evals.eval_runs "
                    "WHERE task = 'job_categorisation' AND id > :high_water_mark"
                ),
                {"high_water_mark": self.eval_runs_high_water_mark},
            )
        self.engine.dispose()
        self.config_path.unlink(missing_ok=True)

    def test_threshold_zero_flags_a_small_negative_delta_as_regression(
        self,
    ) -> None:
        """A -0.05 delta against a threshold of exactly 0 must still be
        `regressed=True` — `0` must never be treated as falsy-equivalent
        to "unconfigured".

        Returns:
            None.
        """
        run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=self.perfect_adapters,
            config_path=self.config_path,
            job_group_ids=self.job_group_ids,
        )
        result = run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=self.slightly_worse_adapters,
            config_path=self.config_path,
            job_group_ids=self.job_group_ids,
        )
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.delta, -0.05, places=6)
        self.assertTrue(result.regressed)


if __name__ == "__main__":
    unittest.main()
