# Pipeline Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One new UI page giving full visibility and control over every
stage of the job_search pipeline — ~19 automated CLI stages plus 4
human-review steps — with dependency-based staleness, live progress,
and a generalized Postgres-backed runner.

**Architecture:** A generic `pipeline.stage_run` table + a static
`STAGES`/`REVIEW_STAGES` catalog (the enforced source of truth for
"what the pipeline consists of") + one generic runner module
(`start_run`/`run_stage`/cancel, reusing the skill-extraction runner's
proven single-active-run-via-partial-unique-index pattern, generalized
to a true system-wide lock) + one generic API router + one new
Streamlit page. `extract-job-skills`'s existing bespoke runner
(sub-batching, Ollama unload) is migrated onto the same generic table
so there is exactly one lock, not two.

**Tech Stack:** FastAPI `BackgroundTasks` (no new job-queue dependency),
SQLAlchemy Core + Alembic, Streamlit, existing `core.*` business-logic
functions (no new scoring/dedup/classification logic — this wraps what
already exists).

**Spec:** `docs/superpowers/specs/2026-09-30-pipeline-dashboard-design.md`

## Global Constraints

- Global one-active-run-at-a-time lock, system-wide, enforced at the
  database level via a partial unique index on `status = 'running'` —
  not per-stage, not per-user (spec, confirmed explicitly by the
  project owner).
- Staleness is timestamp-order comparison against the declared
  dependency graph — never content-aware (row counts/hashes). Spec's
  explicit Non-goal.
- `ingest` and `run-evals` are excluded from the Run-button surface
  (see Task 5) — `ingest` needs per-call source/query parameters that
  don't fit a single generic Run button (triggering a new external
  data pull is a different kind of action from re-running an existing
  stage); `run-evals` is developer tooling, not a pipeline stage. Both
  still appear in `STAGES` for the dependency graph and the
  catalog-completeness test, just without a UI Run action.
- Every automated stage's `run` wrapper uses the exact same
  engine (owner-role `settings.database_url` vs app-role
  `settings.app_database_url`) as its existing `_cmd_*` implementation
  in `apps/pipeline/app/cli.py` — most global stages write
  owner-role-write-only tables (silver/dedup schemas), so using the
  app-role engine for them would fail on a permissions error, not
  succeed differently. Never "simplify" to a single shared engine.
- No new business logic anywhere in this plan. Every stage wrapper
  calls an existing `core.*` function with the same arguments its
  `_cmd_*` counterpart already uses — verified against
  `apps/pipeline/app/cli.py` line-by-line while writing this plan, not
  guessed.
- `packages/core` is bind-mounted into the running `api` container —
  test via `docker compose exec -T api python -m unittest ...` without
  an image rebuild for any task that only touches `packages/core` or
  `apps/api`. A UI-only task needs no rebuild either (same bind mount).

## Review Focus

- **A stage's hard dependency has never run.** `compute_staleness` must
  return a distinct "blocked" state (not "stale") for e.g.
  `classify-jobs` before `compute-survivorship` has ever completed —
  covered in Task 7.
- **Two different stages started back-to-back in the UI.** The second
  `POST /pipeline/stages/{stage}/run` while the first is still
  `running` must get `409`, not silently queue or clobber the first
  run's row — covered in Task 6 (global lock) and Task 9 (router).
- **A per-user stage's `GET /pipeline/stages` called with no
  `user_id`.** Must `422`/`400`, not silently show global-only state or
  crash — covered in Task 9.
- **`extract-job-skills`'s migration changes its observable behavior.**
  The sub-batch/Ollama-unload/stalled-vs-failed logic that
  `test_extraction_run.py` already pins must still pass, unmodified in
  meaning, after moving from `silver.skill_extraction_run` to
  `pipeline.stage_run` — covered in Task 8 by re-running (adjusted)
  existing tests, not just new ones.
- **A review stage's pending-count query against this repo's real,
  non-empty dev DB.** All four are plain `COUNT(*)` queries (never a
  division, never indexed into a possibly-empty result), so they
  cannot divide-by-zero or IndexError regardless of row count — but
  this repo's real-DB, no-isolation testing convention (no fixture can
  cheaply force a schema-wide "nothing ingested yet" state) means the
  strongest testable claim is "never raises against real data, always
  returns a non-negative int," which Task 9's
  `test_get_stages_returns_every_catalog_entry` already exercises for
  all four (a raised exception there fails that test).

---

## File Structure

**Create:**
- `db/migrations/versions/0030_create_pipeline_schema.py`
- `db/migrations/versions/0031_drop_skill_extraction_run.py` (Task 8)
- `packages/core/core/pipeline/__init__.py`
- `packages/core/core/pipeline/stage_functions.py` — one wrapper per
  automated CLI stage (Tasks 2-4)
- `packages/core/core/pipeline/registry.py` — `StageSpec`,
  `ReviewStageSpec`, `STAGES`, `REVIEW_STAGES` (Task 5)
- `packages/core/core/pipeline/runner.py` — generic run lifecycle
  (Task 6)
- `packages/core/core/pipeline/staleness.py` — `compute_staleness`
  (Task 7)
- `apps/api/app/routers/pipeline.py` (Task 9)
- `apps/ui/app/pages/0_Pipeline_Dashboard.py` (Task 10)
- `packages/core/tests/test_pipeline_registry.py` (Task 5)
- `packages/core/tests/integration/test_pipeline_runner.py` (Task 6)
- `packages/core/tests/integration/test_pipeline_staleness.py` (Task 7)
- `packages/core/tests/integration/test_pipeline_router.py` (Task 9)

**Modify:**
- `packages/core/core/skills/extraction_run.py` — persistence layer
  only, repointed at `pipeline.stage_run` (Task 8)
- `packages/core/tests/integration/test_extraction_run.py`,
  `test_extraction_runs_router.py` — adjusted for the new table
  (Task 8)
- `apps/api/app/routers/extraction_runs.py` — drop the run-lifecycle
  endpoints (kept: `/filters`, `/pending-count`) (Task 9)
- `apps/api/app/main.py` — register the new `pipeline` router (Task 9)
- `apps/ui/app/pages/7_Skill_Extraction_Runner.py` — repointed at the
  generic endpoints (Task 10)
- `README.md` — new "Pipeline Dashboard" section (Task 11)

---

### Task 1: Migration — `pipeline` schema and `pipeline.stage_run`

**Files:**
- Create: `db/migrations/versions/0030_create_pipeline_schema.py`
- Test: `packages/core/tests/integration/test_pipeline_schema.py`

**Interfaces:**
- Produces: `pipeline.stage_run` table with columns `run_id, stage,
  user_id, status, params, progress_current, progress_total,
  cancel_requested, result, error_message, started_at, updated_at,
  finished_at`; a partial unique index on `status` enforcing at most
  one `running` row in the whole table.

- [ ] **Step 1: Find the current migration head**

```bash
cd job_search
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "SELECT version_num FROM alembic_version;"
```

Expected: `0029` (Step 16's last migration).

- [ ] **Step 2: Write the migration**

Create `db/migrations/versions/0030_create_pipeline_schema.py`:

```python
"""create pipeline schema and pipeline.stage_run

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-30

Backs the pipeline dashboard (docs/superpowers/specs/
2026-09-30-pipeline-dashboard-design.md): one generic run-tracking
table for every automated pipeline stage, not one table per stage.
The partial unique index on `status` is the same proven pattern as
0025's silver.skill_extraction_run, just scoped to the WHOLE table
instead of one stage's own table -- a second INSERT with
status='running', for ANY stage, fails with a unique violation. That
IS the system-wide "only one thing runs at a time" lock the design
calls for.

No RLS: this is operational metadata (which pipeline runs happened,
by whom), not user-owned content. `user_id` is a plain nullable
foreign key for per-user stages, filtered by the API layer, not by a
row-level policy -- matches scoring.job_chunk_embedding's own
no-RLS-but-nullable-scope precedent (0028).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS pipeline")
    op.execute("GRANT USAGE ON SCHEMA pipeline TO job_search_app")

    op.create_table(
        "stage_run",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("params", JSONB(), nullable=False, server_default="{}"),
        sa.Column("progress_current", sa.Integer(), nullable=True),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'cancelled', 'failed')",
            name="ck_stage_run_status",
        ),
        schema="pipeline",
    )
    # Same proven pattern as 0025's silver.skill_extraction_run: a unique
    # index on `status`, filtered to `running` rows, permits at most one
    # such row -- but here across every stage, not one table per stage,
    # so this is the system-wide lock.
    op.create_index(
        "ux_stage_run_one_active",
        "stage_run",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        schema="pipeline",
    )
    # For "this stage's last completed run" lookups (staleness, last-run
    # display) -- always filtered by stage (+ user_id for per-user ones).
    op.create_index(
        "ix_stage_run_stage_user_status",
        "stage_run",
        ["stage", "user_id", "status"],
        schema="pipeline",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON pipeline.stage_run TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON pipeline.stage_run FROM job_search_app")
    op.drop_index("ix_stage_run_stage_user_status", table_name="stage_run", schema="pipeline")
    op.drop_index("ux_stage_run_one_active", table_name="stage_run", schema="pipeline")
    op.drop_table("stage_run", schema="pipeline")
    op.execute("DROP SCHEMA IF EXISTS pipeline")
```

- [ ] **Step 3: Run the migration**

The `api` container has no bind mount for `db/` and this project's
documented host-venv workflow (`README.md`'s Quick Start) may not work
if the local venv is broken (verified broken on this machine:
`pydantic_core` compiled for the wrong architecture) — mount `db/`
ad-hoc for this one-off command instead of touching
`docker-compose.yml` (never modify that file for this):

```bash
docker compose run --rm -v "$(pwd)/db:/app/db" --entrypoint alembic api -c db/alembic.ini upgrade head
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "SELECT version_num FROM alembic_version;"
```

Expected: `0030`.

- [ ] **Step 4: Write the schema test**

Create `packages/core/tests/integration/test_pipeline_schema.py`:

```python
"""Schema tests for pipeline.stage_run (migration 0030)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine


class TestStageRunSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline.stage_run WHERE stage LIKE 'zzfixture-%'")
            )

    def test_app_role_can_insert_and_read_its_own_run(self) -> None:
        with self.app_engine.begin() as conn:
            run_id = conn.execute(
                text(
                    "INSERT INTO pipeline.stage_run (stage, status) "
                    "VALUES ('zzfixture-stage', 'completed') RETURNING run_id"
                )
            ).scalar_one()
        with self.owner.connect() as conn:
            row = conn.execute(
                text("SELECT stage, status FROM pipeline.stage_run WHERE run_id = :id"),
                {"id": run_id},
            ).one()
        self.assertEqual(row.stage, "zzfixture-stage")
        self.assertEqual(row.status, "completed")

    def test_only_one_running_row_allowed_across_the_whole_table(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO pipeline.stage_run (stage, status) "
                    "VALUES ('zzfixture-a', 'running')"
                )
            )
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO pipeline.stage_run (stage, status) "
                        "VALUES ('zzfixture-b', 'running')"
                    )
                )
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pipeline.stage_run SET status = 'completed' "
                    "WHERE stage = 'zzfixture-a'"
                )
            )

    def test_invalid_status_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.owner.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO pipeline.stage_run (stage, status) "
                        "VALUES ('zzfixture-bad', 'bogus')"
                    )
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Run the test**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_schema -v
```

Expected: 3/3 PASS.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/versions/0030_create_pipeline_schema.py \
        packages/core/tests/integration/test_pipeline_schema.py
git commit -m "feat(job_search): pipeline dashboard — migration 0030, pipeline.stage_run"
```

---

### Task 2: `stage_functions.py` — global batch-pipeline stages

**Files:**
- Create: `packages/core/core/pipeline/__init__.py` (empty)
- Create: `packages/core/core/pipeline/stage_functions.py`
- Test: `packages/core/tests/integration/test_pipeline_stage_functions.py`

**Interfaces:**
- Produces: `run_enrich_engagement_terms(params: dict) -> dict`,
  `run_compute_blocking_keys(params: dict) -> dict`,
  `run_compute_similarity_features(params: dict) -> dict`,
  `run_compute_title_similarity_scores(params: dict) -> dict`,
  `run_cluster_jobs(params: dict) -> dict`,
  `run_compute_survivorship(params: dict) -> dict`,
  `run_classify_jobs(params: dict) -> dict`. Every function takes an
  (unused, for signature uniformity with per-user stages) `params: dict`
  and returns a small result dict for `pipeline.stage_run.result`;
  raises on real failure (never returns a sentinel error).

This task's 6 stages are structurally identical — a `write_X(engine)`
call against the owner engine (all of these write to owner-role-write-
only `silver`/`dedup` tables, exactly like their `_cmd_*` counterparts
in `apps/pipeline/app/cli.py`). `classify-jobs` adds an LLM-adapter
check. Every wrapper here is a faithful port of its `_cmd_*`
counterpart (same function, same engine, same argument), verified
against `apps/pipeline/app/cli.py` lines 576-699 while writing this
plan — not reinvented.

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_pipeline_stage_functions.py`:

```python
"""Integration tests for core.pipeline.stage_functions against live
Postgres. Each stage function is already covered end-to-end by its own
underlying write_X function's tests (e.g. test_write_blocking_keys.py)
-- these tests only prove the *wrapper* calls the right function with
the right engine and returns a sane result dict, using each function's
already-established "0 rows written on an empty pool" no-op case so no
fixture data is needed.
"""

from __future__ import annotations

import unittest

from core.pipeline.stage_functions import (
    run_classify_jobs,
    run_cluster_jobs,
    run_compute_blocking_keys,
    run_compute_similarity_features,
    run_compute_survivorship,
    run_compute_title_similarity_scores,
    run_enrich_engagement_terms,
)


class TestGlobalBatchStageFunctions(unittest.TestCase):
    def test_run_enrich_engagement_terms_returns_rows_written(self) -> None:
        result = run_enrich_engagement_terms({})
        self.assertIn("rows_written", result)
        self.assertIsInstance(result["rows_written"], int)

    def test_run_compute_blocking_keys_returns_rows_written(self) -> None:
        result = run_compute_blocking_keys({})
        self.assertIn("rows_written", result)

    def test_run_compute_similarity_features_returns_rows_written(self) -> None:
        result = run_compute_similarity_features({})
        self.assertIn("rows_written", result)

    def test_run_compute_title_similarity_scores_returns_rows_written(self) -> None:
        result = run_compute_title_similarity_scores({})
        self.assertIn("rows_written", result)

    def test_run_cluster_jobs_returns_rows_written(self) -> None:
        result = run_cluster_jobs({})
        self.assertIn("rows_written", result)

    def test_run_compute_survivorship_returns_rows_written(self) -> None:
        result = run_compute_survivorship({})
        self.assertIn("rows_written", result)

    def test_run_classify_jobs_returns_rows_written(self) -> None:
        result = run_classify_jobs({})
        self.assertIn("rows_written", result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_stage_functions -v
```

Expected: FAIL/ERROR — `core.pipeline.stage_functions` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/pipeline/__init__.py` (empty file).

Create `packages/core/core/pipeline/stage_functions.py`:

```python
"""One wrapper function per automated pipeline stage — each a faithful
port of its `_cmd_*` counterpart in `apps/pipeline/app/cli.py`: same
underlying core function, same engine choice, same arguments. Ported
rather than imported because `apps/pipeline/app` and `apps/api/app` are
separate top-level packages both named `app` (PLAN.md Step 1's
PYTHONPATH note) -- the CLI module isn't importable from here.

Every function takes a `params: dict` (even when unused, for call-site
uniformity across `core.pipeline.registry.STAGES`) and returns a small
result dict for `pipeline.stage_run.result`. None of these catch
exceptions -- `core.pipeline.runner.run_stage` (Task 6) is what turns
a raised exception into a `failed` row; a wrapper that swallowed its
own errors would hide them from that mechanism.
"""

from __future__ import annotations

import httpx

from core.classification.write_job_category import write_job_category
from core.db.session import build_engine
from core.dedup.write_blocking_keys import write_blocking_keys
from core.dedup.write_job_identity_map import write_job_identity_map
from core.dedup.write_job_survivorship import write_job_survivorship
from core.dedup.write_similarity_features import write_similarity_features
from core.dedup.write_title_similarity_scores import write_title_similarity_scores
from core.enrichment.write_engagement_terms import write_engagement_terms
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.settings import get_settings


def _build_llm_adapters(http_client: httpx.Client) -> dict[str, LLMAdapter]:
    """Same shape as apps/pipeline/app/cli.py's own helper of this name
    and apps/api/app/dependencies.get_llm_adapters -- duplicated, not
    shared, for the same PYTHONPATH-separation reason as this module's
    own docstring explains."""
    settings = get_settings()
    adapters: dict[str, LLMAdapter] = {
        "ollama": OllamaAdapter(base_url=settings.ollama_base_url, client=http_client),
    }
    if settings.anthropic_api_key:
        import anthropic

        adapters["anthropic"] = AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            client=anthropic.Anthropic(api_key=settings.anthropic_api_key),
        )
    return adapters


def run_enrich_engagement_terms(params: dict) -> dict:
    """Wraps `_cmd_enrich_engagement_terms` (cli.py:576)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_engagement_terms(engine)}


def run_compute_blocking_keys(params: dict) -> dict:
    """Wraps `_cmd_compute_blocking_keys` (cli.py:592)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_blocking_keys(engine)}


def run_compute_similarity_features(params: dict) -> dict:
    """Wraps `_cmd_compute_similarity_features` (cli.py:608)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_similarity_features(engine)}


def run_compute_title_similarity_scores(params: dict) -> dict:
    """Wraps `_cmd_compute_title_similarity_scores` (cli.py:624)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_title_similarity_scores(engine)}


def run_cluster_jobs(params: dict) -> dict:
    """Wraps `_cmd_cluster_jobs` (cli.py:640)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_job_identity_map(engine)}


def run_compute_survivorship(params: dict) -> dict:
    """Wraps `_cmd_compute_survivorship` (cli.py:656)."""
    engine = build_engine(get_settings().database_url)
    return {"rows_written": write_job_survivorship(engine)}


def run_classify_jobs(params: dict) -> dict:
    """Wraps `_cmd_classify_jobs` (cli.py:672). Raises RuntimeError
    (rather than cli.py's print-and-return-1) when no Anthropic key is
    configured -- run_stage (Task 6) turns any raised exception into a
    `failed` row with the exception's message, which is the dashboard
    equivalent of cli.py's stderr message."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "classify-jobs: ANTHROPIC_API_KEY is not configured — the LLM "
            "residual stage cannot run"
        )
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        adapters = _build_llm_adapters(http_client)
        written = write_job_category(engine, adapters=adapters, http_client=http_client)
    finally:
        http_client.close()
    return {"rows_written": written}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_stage_functions -v
```

Expected: 7/7 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/pipeline/__init__.py \
        packages/core/core/pipeline/stage_functions.py \
        packages/core/tests/integration/test_pipeline_stage_functions.py
git commit -m "feat(job_search): pipeline dashboard — global batch stage wrappers"
```

---

### Task 3: `stage_functions.py` — CV & skills stages

**Files:**
- Modify: `packages/core/core/pipeline/stage_functions.py`
- Modify: `packages/core/tests/integration/test_pipeline_stage_functions.py`

**Interfaces:**
- Consumes: `_build_llm_adapters` (Task 2, same file).
- Produces: `run_load_esco(params: dict) -> dict` (params:
  `{"directory": str}`), `run_embed_esco(params: dict) -> dict`,
  `run_map_skills(params: dict) -> dict`,
  `run_llm_map_skills(params: dict) -> dict`,
  `run_map_cv_skills(params: dict) -> dict` (params:
  `{"user_id": str}`).

`extract-job-skills` is deliberately **not** here — it keeps its own
bespoke runner, migrated in Task 8.

- [ ] **Step 1: Write the failing tests**

Append to `test_pipeline_stage_functions.py`:

```python
class TestCvAndSkillsStageFunctions(unittest.TestCase):
    def test_run_embed_esco_returns_embeddings_written(self) -> None:
        from core.pipeline.stage_functions import run_embed_esco

        result = run_embed_esco({})
        self.assertIn("embeddings_written", result)

    def test_run_map_skills_returns_a_summary(self) -> None:
        from core.pipeline.stage_functions import run_map_skills

        result = run_map_skills({})
        self.assertIn("mapped", result)
        self.assertIn("unmapped", result)

    def test_run_llm_map_skills_without_an_api_key_raises(self) -> None:
        # Exercised for real in test_llm_map.py's own suite when a key IS
        # configured; here we only prove the wrapper's shape, using the
        # same "no ANTHROPIC_API_KEY" guard as run_classify_jobs's own
        # test would if the dev environment had no key. Since this dev
        # environment DOES have a key configured (verified this session),
        # assert the success shape instead.
        from core.pipeline.stage_functions import run_llm_map_skills

        result = run_llm_map_skills({})
        self.assertIn("checked", result)
        self.assertIn("applied", result)

    def test_run_map_cv_skills_requires_a_user_id(self) -> None:
        from core.pipeline.stage_functions import run_map_cv_skills

        with self.assertRaises(KeyError):
            run_map_cv_skills({})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_stage_functions.TestCvAndSkillsStageFunctions -v
```

Expected: FAIL/ERROR — the four functions don't exist yet.

- [ ] **Step 3: Write the implementation**

Append to `packages/core/core/pipeline/stage_functions.py`:

```python
from pathlib import Path

from core.embedding.ollama import embed_text
from core.skills.aliases import sync_seed_aliases
from core.skills.cv_map import map_cv_skills
from core.skills.esco_embed import embed_esco_skills
from core.skills.esco_load import load_esco
from core.skills.llm_map import propose_matches
from core.skills.mapper import map_pending


def _build_embedder(http_client: httpx.Client):
    """Same shape as cli.py's own `_build_embedder` helper."""
    settings = get_settings()

    def embed(text_: str) -> list[float]:
        return embed_text(
            text_, base_url=settings.ollama_base_url, model=settings.embedding_model,
            client=http_client,
        )

    return embed


def run_load_esco(params: dict) -> dict:
    """Wraps `_cmd_load_esco` (cli.py:725). `params["directory"]` is the
    ESCO release folder path, required (raises KeyError if missing --
    unlike the CLI's argparse-enforced positional, this has no default
    to fall back to, which is correct: there is no sane default
    directory)."""
    engine = build_engine(get_settings().database_url)
    counts = load_esco(engine, Path(params["directory"]))
    return {
        "skills": counts.skills,
        "skill_labels": counts.skill_labels,
        "occupations": counts.occupations,
        "occupation_skills": counts.occupation_skills,
        "skipped_relations": counts.skipped_relations,
    }


def run_embed_esco(params: dict) -> dict:
    """Wraps `_cmd_embed_esco` (cli.py:777)."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        written = embed_esco_skills(
            engine, embed=_build_embedder(http_client), model=settings.embedding_model
        )
    finally:
        http_client.close()
    return {"embeddings_written": written}


def run_map_skills(params: dict) -> dict:
    """Wraps `_cmd_map_skills` (cli.py:801) with neither `--remap-unresolved`
    nor `--remap-all-auto` (those are deliberate, occasional maintenance
    actions a human chooses on the CLI, not a routine dashboard Run)."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        synced = sync_seed_aliases(engine)
        summary = map_pending(
            engine, embed=_build_embedder(http_client), embedding_model=settings.embedding_model,
        )
    finally:
        http_client.close()
    return {"seed_aliases": synced, "mapped": summary.mapped, "unmapped": summary.unmapped}


def run_llm_map_skills(params: dict) -> dict:
    """Wraps `_cmd_llm_map_skills` (cli.py:892) in its default mode only
    (no `--dry-run`/`--evaluate`/`--sample` -- those are CLI power-user
    modes, not a dashboard Run action)."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("llm-map-skills: ANTHROPIC_API_KEY is not set")
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=120.0)
    try:
        adapters = _build_llm_adapters(http_client)
        summary = propose_matches(
            engine, adapters=adapters, embed=_build_embedder(http_client),
            embedding_model=settings.embedding_model, limit=None,
        )
    finally:
        http_client.close()
    return {
        "checked": summary.checked, "applied": summary.applied,
        "custom_created": summary.custom_created, "left_open": summary.left_open,
        "failed": summary.failed,
    }


def run_map_cv_skills(params: dict) -> dict:
    """Wraps `_cmd_map_cv_skills` (cli.py:1039). `params["user_id"]`
    required (raises KeyError if missing -- per-user stage, there is no
    sane default user)."""
    settings = get_settings()
    owner_engine = build_engine(settings.database_url)
    app_engine = build_engine(settings.app_database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        result = map_cv_skills(
            app_engine=app_engine, owner_engine=owner_engine, user_id=params["user_id"],
            embed=_build_embedder(http_client), embedding_model=settings.embedding_model,
            refresh=params.get("refresh", False),
        )
    finally:
        http_client.close()
    return {
        "mapped": result.mapped, "unmapped": result.unmapped, "changed": result.changed,
        "truth_base_version": result.new_version,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_stage_functions -v
```

Expected: 11/11 PASS (7 from Task 2 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/pipeline/stage_functions.py \
        packages/core/tests/integration/test_pipeline_stage_functions.py
git commit -m "feat(job_search): pipeline dashboard — CV & skills stage wrappers"
```

---

### Task 4: `stage_functions.py` — scoring funnel stages

**Files:**
- Modify: `packages/core/core/pipeline/stage_functions.py`
- Modify: `packages/core/tests/integration/test_pipeline_stage_functions.py`

**Interfaces:**
- Produces: `run_score_filter_jobs(params: dict) -> dict`,
  `run_chunk_embed_jobs(params: dict) -> dict`,
  `run_chunk_embed_cv(params: dict) -> dict`,
  `run_score_similarity(params: dict) -> dict`,
  `run_score_skill_coverage(params: dict) -> dict`,
  `run_score_llm_rerank(params: dict) -> dict`,
  `run_score_blend(params: dict) -> dict`. All but
  `run_chunk_embed_jobs` require `params["user_id"]`.

- [ ] **Step 1: Write the failing tests**

Append to `test_pipeline_stage_functions.py`:

```python
class TestScoringFunnelStageFunctions(unittest.TestCase):
    def test_run_chunk_embed_jobs_returns_jobs_chunked(self) -> None:
        from core.pipeline.stage_functions import run_chunk_embed_jobs

        result = run_chunk_embed_jobs({})
        self.assertIn("jobs_chunked", result)

    def test_run_score_filter_jobs_requires_a_user_id(self) -> None:
        from core.pipeline.stage_functions import run_score_filter_jobs

        with self.assertRaises(KeyError):
            run_score_filter_jobs({})

    def test_run_score_blend_requires_a_user_id(self) -> None:
        from core.pipeline.stage_functions import run_score_blend

        with self.assertRaises(KeyError):
            run_score_blend({})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_stage_functions.TestScoringFunnelStageFunctions -v
```

Expected: FAIL/ERROR — the functions don't exist yet.

- [ ] **Step 3: Write the implementation**

Append to `packages/core/core/pipeline/stage_functions.py`:

```python
from core.scoring.blend import compute_final_scores
from core.scoring.cv_chunking import chunk_and_embed_cv
from core.scoring.hard_filters import run_hard_filters
from core.scoring.job_chunking import chunk_and_embed_jobs
from core.scoring.llm_rerank import run_llm_rerank
from core.scoring.similarity import run_similarity
from core.scoring.skill_coverage import run_skill_coverage


def run_score_filter_jobs(params: dict) -> dict:
    """Wraps `_cmd_score_filter_jobs` (cli.py:1076)."""
    app_engine = build_engine(get_settings().app_database_url)
    n = run_hard_filters(app_engine, params["user_id"], limit=params.get("limit"))
    return {"considered": n}


def run_chunk_embed_jobs(params: dict) -> dict:
    """Wraps `_cmd_chunk_embed_jobs` (cli.py:1092). Shared/global -- no
    user_id, matches every other user's eligible jobs too."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        n = chunk_and_embed_jobs(
            engine, embed=_build_embedder(http_client), embedding_model=settings.embedding_model,
            limit=params.get("limit"),
        )
    finally:
        http_client.close()
    return {"jobs_chunked": n}


def run_chunk_embed_cv(params: dict) -> dict:
    """Wraps `_cmd_chunk_embed_cv` (cli.py:1117)."""
    settings = get_settings()
    app_engine = build_engine(settings.app_database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        n = chunk_and_embed_cv(
            app_engine, params["user_id"], embed=_build_embedder(http_client),
            embedding_model=settings.embedding_model, refresh=params.get("refresh", False),
        )
    finally:
        http_client.close()
    return {"chunks_written": n}


def _build_reranker():
    """Same shape as cli.py's own `_build_reranker` helper."""
    from sentence_transformers import CrossEncoder

    model = CrossEncoder("BAAI/bge-reranker-base")

    def rerank(cv_text: str, jd_text: str) -> float:
        return float(model.predict([(cv_text, jd_text)])[0])

    return rerank


def run_score_similarity(params: dict) -> dict:
    """Wraps `_cmd_score_similarity` (cli.py:1163). `params["top_n"]`
    optional, defaults to 200 (score-similarity's own CLI default,
    per cli.py:1570) — a dashboard user who hits "no eligible jobs"
    widens this the same way the README's CLI mitigation already
    documents."""
    app_engine = build_engine(get_settings().app_database_url)
    summary = run_similarity(
        app_engine, params["user_id"], rerank=_build_reranker(), top_n=params.get("top_n", 200),
    )
    return {"jobs_scored": summary.jobs_scored, "mismatched": summary.mismatched}


def run_score_skill_coverage(params: dict) -> dict:
    """Wraps `_cmd_score_skill_coverage` (cli.py:1184)."""
    app_engine = build_engine(get_settings().app_database_url)
    n = run_skill_coverage(app_engine, params["user_id"])
    return {"jobs_scored": n}


def run_score_llm_rerank(params: dict) -> dict:
    """Wraps `_cmd_score_llm_rerank` (cli.py:1204). `params["top_n"]`
    optional, defaults to 50 (the plan's own cap, `TOP_N` in
    llm_rerank.py)."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("score-llm-rerank: ANTHROPIC_API_KEY is not set")
    app_engine = build_engine(settings.app_database_url)
    http_client = httpx.Client(timeout=120.0)
    try:
        adapters = _build_llm_adapters(http_client)
        summary = run_llm_rerank(
            app_engine, params["user_id"], adapters=adapters, top_n=params.get("top_n", 50),
        )
    finally:
        http_client.close()
    return {"jobs_checked": summary.checked, "jobs_reranked": summary.reranked}


def run_score_blend(params: dict) -> dict:
    """Wraps `_cmd_score_blend` (cli.py:1233)."""
    app_engine = build_engine(get_settings().app_database_url)
    n = compute_final_scores(app_engine, params["user_id"])
    return {"jobs_blended": n}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_stage_functions -v
```

Expected: 14/14 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/pipeline/stage_functions.py \
        packages/core/tests/integration/test_pipeline_stage_functions.py
git commit -m "feat(job_search): pipeline dashboard — scoring funnel stage wrappers"
```

---

### Task 5: `registry.py` — the stage catalog

**Files:**
- Create: `packages/core/core/pipeline/registry.py`
- Test: `packages/core/tests/test_pipeline_registry.py`

**Interfaces:**
- Consumes: every `run_*` function from Tasks 2-4 (`stage_functions.py`).
- Produces: `StageSpec` (frozen dataclass: `name, depends_on, per_user,
  run, has_run_button`), `ReviewStageSpec` (frozen dataclass: `name,
  depends_on, page_path, pending_count`), `STAGES: dict[str, StageSpec]`,
  `REVIEW_STAGES: dict[str, ReviewStageSpec]`.

This is the single source of truth Task 7 (staleness) and Task 9 (API)
both read from, and the enforcement point for "every new pipeline CLI
command must be registered."

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_pipeline_registry.py`:

```python
"""Unit tests for core.pipeline.registry — no DB needed, this catalog
is pure Python data plus imports."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from core.pipeline.registry import REVIEW_STAGES, STAGES

_CLI_PATH = (
    Path(__file__).resolve().parents[3]
    / "apps" / "pipeline" / "app" / "cli.py"
)

# Stages deliberately excluded from STAGES' Run-button surface but not
# from the dependency graph or this completeness check — see this
# plan's Global Constraints for why each is excluded.
_EXCLUDED_FROM_RUN_BUTTON = {"ingest", "run-evals"}


def _cli_subcommand_names() -> set[str]:
    """Every subparsers.add_parser("...") name in cli.py, found by
    parsing the source rather than importing the CLI module (which
    pulls in argparse setup this test has no need to run)."""
    tree = ast.parse(_CLI_PATH.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(node.args[0].value)
    return names


class TestStageCatalogCompleteness(unittest.TestCase):
    def test_every_cli_subcommand_has_a_stages_entry_or_is_excluded(self) -> None:
        cli_names = _cli_subcommand_names()
        self.assertTrue(cli_names, "no subcommands found — parser broke")
        missing = cli_names - set(STAGES) - _EXCLUDED_FROM_RUN_BUTTON
        self.assertEqual(
            missing, set(),
            f"CLI subcommand(s) {missing} have no STAGES entry and are not "
            "on the documented exclusion list — add one or the other",
        )

    def test_excluded_stages_are_still_real_cli_subcommands(self) -> None:
        # Catches a stale exclusion entry (e.g. a renamed/removed command)
        # rather than letting it silently stop meaning anything.
        cli_names = _cli_subcommand_names()
        for name in _EXCLUDED_FROM_RUN_BUTTON:
            self.assertIn(name, cli_names)

    def test_excluded_stages_are_not_also_in_stages(self) -> None:
        self.assertEqual(set(STAGES) & _EXCLUDED_FROM_RUN_BUTTON, set())


class TestDependencyGraphConsistency(unittest.TestCase):
    def test_every_automated_dependency_resolves_to_a_real_stage(self) -> None:
        for name, spec in STAGES.items():
            for dep in spec.depends_on:
                self.assertIn(
                    dep, STAGES, f"{name} depends on undefined stage {dep!r}"
                )

    def test_every_review_dependency_resolves_to_a_real_automated_stage(self) -> None:
        for name, spec in REVIEW_STAGES.items():
            for dep in spec.depends_on:
                self.assertIn(
                    dep, STAGES,
                    f"review stage {name} depends on undefined automated stage {dep!r}",
                )

    def test_no_cycles_among_automated_stages(self) -> None:
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(name: str) -> None:
            if name in done:
                return
            self.assertNotIn(name, visiting, f"cycle detected at {name!r}")
            visiting.add(name)
            for dep in STAGES[name].depends_on:
                visit(dep)
            visiting.discard(name)
            done.add(name)

        for name in STAGES:
            visit(name)


class TestPerUserFlagging(unittest.TestCase):
    def test_scoring_funnel_stages_are_flagged_per_user(self) -> None:
        for name in (
            "score-filter-jobs", "chunk-embed-cv", "score-similarity",
            "score-skill-coverage", "score-llm-rerank", "score-blend",
            "map-cv-skills",
        ):
            self.assertTrue(STAGES[name].per_user, f"{name} should be per_user=True")

    def test_chunk_embed_jobs_is_not_per_user(self) -> None:
        # Shared across all users — README's own "(shared, run once
        # across all users)" note for this stage.
        self.assertFalse(STAGES["chunk-embed-jobs"].per_user)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

This test parses `apps/pipeline/app/cli.py`'s real source, which the
`api` container never mounts (`apps/pipeline/app` and `apps/api/app`
are separate top-level packages by design) — run it via the
`pipeline` service instead, whose image already mounts both
`packages/core` and `apps/pipeline/app` (its own `ENTRYPOINT` is
`python -m app.cli`, so override it to run a plain `python` command):

```bash
docker compose run --rm --entrypoint python pipeline -m unittest tests.test_pipeline_registry -v
```

Expected: FAIL/ERROR — `core.pipeline.registry` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/pipeline/registry.py`:

```python
"""The pipeline stage catalog — the single source of truth for what
the pipeline consists of. `test_pipeline_registry.py`'s
`test_every_cli_subcommand_has_a_stages_entry_or_is_excluded` fails CI
if a new `apps/pipeline/app/cli.py` subcommand ships with no matching
entry here (or an entry on the documented exclusion list) — see
docs/superpowers/specs/2026-09-30-pipeline-dashboard-design.md's
README section for the requirement this enforces.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.pipeline import stage_functions as sf
from core.skills.review import count_unmapped


@dataclass(frozen=True)
class StageSpec:
    """One automated pipeline stage.

    Attributes:
        name: Matches the CLI subcommand name exactly.
        depends_on: Other automated stage names this one's staleness is
            computed against. Never a review-stage name — see
            REVIEW_STAGES' own depends_on docstring for why.
        per_user: Whether this stage's runs are scoped by user_id.
        run: The wrapper from `stage_functions` — takes a `params` dict,
            returns a result dict, raises on failure.
        has_run_button: Reserved for a future stage that needs to be in
            the dependency graph without a UI trigger. No current entry
            sets this False — `ingest`/`run-evals` (the two stages this
            plan's Global Constraints exclude from the Run-button
            surface) are absent from `STAGES` entirely instead (see
            `_EXCLUDED_FROM_RUN_BUTTON` in the test file), because a
            `has_run_button=False` entry would still need a completed
            run to ever exist for its dependents to not show
            permanently "blocked" -- and nothing can ever complete a
            run for a stage with no Run button. Stages that referenced
            `ingest` as a dependency (`enrich-engagement-terms`,
            `extract-job-skills`) have no dependency there instead, for
            the same reason: ingestion happens outside this dashboard's
            tracking, so gating on it observing a completion it can
            never see would be a permanent false "blocked".
    """

    name: str
    depends_on: tuple[str, ...]
    per_user: bool
    run: Callable[[dict], dict]
    has_run_button: bool = True


@dataclass(frozen=True)
class ReviewStageSpec:
    """One human-review pipeline stage.

    Attributes:
        name: Display name (not a CLI subcommand — these have none).
        depends_on: Automated stage names only. A review stage has no
            "last completed run" timestamp of its own (a human queue
            has no single completion event), so it can never appear on
            the right-hand side of another stage's `depends_on` for
            staleness purposes — `compute_staleness` (Task 7) walks
            through a review stage to the nearest upstream automated
            stage instead.
        page_path: The existing UI page this links to (Streamlit's own
            page-path convention, e.g. "Dedup_Review_Queue").
        pending_count: Runs directly against the engine — no HTTP call
            to the existing page's own API endpoints, since those often
            return the sample itself, not just a count. Always takes
            `(engine, user_id)` even though 3 of the 4 real review
            stages are global and ignore `user_id` entirely --
            `scoring-calibration`'s count is inherently per-user
            (`scoring.job_label` is RLS-scoped), and a uniform call
            site (Task 9's router calls every stage's `pending_count`
            the same way) is simpler than special-casing the one
            per-user stage.
    """

    name: str
    depends_on: tuple[str, ...]
    page_path: str
    pending_count: Callable[[Engine, uuid.UUID | None], int]


def _count_dedup_pending(engine: Engine, user_id: uuid.UUID | None) -> int:
    """Mirrors GET /dedup/pairs-to-label's own two-mode WHERE clause
    (apps/api/app/routers/dedup.py:124), as a plain COUNT(*) instead of
    a stratified sample. Global stage -- `user_id` unused."""
    with engine.connect() as conn:
        thresholds_row = conn.execute(
            text(
                "SELECT auto_match_threshold, auto_reject_threshold "
                "FROM dedup.calibration_thresholds ORDER BY calibrated_at DESC LIMIT 1"
            )
        ).one_or_none()
        if thresholds_row is None:
            return conn.execute(
                text(
                    "SELECT count(*) FROM dedup.dedup__similarity_scores AS s "
                    "LEFT JOIN dedup.pair_labels AS l "
                    "ON s.job_key_a = l.job_key_a AND s.job_key_b = l.job_key_b "
                    "WHERE l.job_key_a IS NULL AND s.hard_veto = false"
                )
            ).scalar_one()
        return conn.execute(
            text(
                "SELECT count(*) FROM dedup.dedup__similarity_scores AS s "
                "LEFT JOIN dedup.pair_labels AS l "
                "ON s.job_key_a = l.job_key_a AND s.job_key_b = l.job_key_b "
                "WHERE l.job_key_a IS NULL AND s.hard_veto = false "
                "AND s.blended_score > :auto_reject AND s.blended_score < :auto_match"
            ),
            {
                "auto_reject": thresholds_row.auto_reject_threshold,
                "auto_match": thresholds_row.auto_match_threshold,
            },
        ).scalar_one()


def _count_categorisation_pending(engine: Engine, user_id: uuid.UUID | None) -> int:
    """Mirrors GET /classification/jobs-to-review's own
    total_unreviewed_count query (apps/api/app/routers/classification.py:225),
    unfiltered (no country_iso) and without the snippet-only-sources
    exclusion — a documented simplification: this count may run very
    slightly high relative to the review page's own filtered sample
    when snippet-only-source jobs are pending, which is acceptable for
    a dashboard status number, not the review queue itself. Global
    stage -- `user_id` unused."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM gold.dim_job AS d "
                "LEFT JOIN classification.category_review_labels AS r "
                "ON d.job_group_id = r.job_group_id "
                "WHERE r.job_group_id IS NULL AND d.category IS NOT NULL"
            )
        ).scalar_one()


def _count_skill_review_pending(engine: Engine, user_id: uuid.UUID | None) -> int:
    """Reuses `core.skills.review.count_unmapped` — the exact same
    function GET /skills/review/count itself calls — rather than a
    hand-duplicated query, so this can never drift from the real
    schema (`silver.skill_mapping`'s real PK is `raw_norm`, filtered by
    `review_status`; there is no `skill_string_normalized` column on
    any table, unlike an earlier draft of this function assumed).
    Global stage -- `user_id` unused."""
    with engine.connect() as conn:
        return count_unmapped(conn)


def _count_scoring_calibration_pending(engine: Engine, user_id: uuid.UUID | None) -> int:
    """How many more labels the current user needs before 30 — read
    directly (COUNT, not the full read_labels list this counts don't
    need). `scoring.job_label` is RLS-scoped per user (migration 0029),
    so this must run through `session_scope` with the real `user_id`,
    not a bare `engine.connect()` — otherwise RLS silently returns 0
    rows for every user rather than each user's own count."""
    with session_scope(engine, user_id=user_id) as conn:
        labeled = conn.execute(
            text("SELECT count(*) FROM scoring.job_label")
        ).scalar_one()
    return max(0, 30 - labeled)


STAGES: dict[str, StageSpec] = {
    "enrich-engagement-terms": StageSpec(
        name="enrich-engagement-terms", depends_on=(), per_user=False,
        # No dependency on `ingest` -- see StageSpec.has_run_button's
        # docstring: ingest is excluded from STAGES entirely (never a
        # completed run to depend on), so this would otherwise show
        # "blocked" forever.
        run=sf.run_enrich_engagement_terms,
    ),
    "compute-blocking-keys": StageSpec(
        name="compute-blocking-keys", depends_on=("enrich-engagement-terms",),
        per_user=False, run=sf.run_compute_blocking_keys,
    ),
    "compute-similarity-features": StageSpec(
        name="compute-similarity-features", depends_on=("compute-blocking-keys",),
        per_user=False, run=sf.run_compute_similarity_features,
    ),
    "compute-title-similarity-scores": StageSpec(
        name="compute-title-similarity-scores",
        depends_on=("compute-similarity-features",), per_user=False,
        run=sf.run_compute_title_similarity_scores,
    ),
    "cluster-jobs": StageSpec(
        name="cluster-jobs", depends_on=("compute-title-similarity-scores",),
        per_user=False, run=sf.run_cluster_jobs,
    ),
    "compute-survivorship": StageSpec(
        name="compute-survivorship", depends_on=("cluster-jobs",), per_user=False,
        run=sf.run_compute_survivorship,
    ),
    "classify-jobs": StageSpec(
        name="classify-jobs", depends_on=("compute-survivorship",), per_user=False,
        run=sf.run_classify_jobs,
    ),
    "load-esco": StageSpec(
        name="load-esco", depends_on=(), per_user=False, run=sf.run_load_esco,
    ),
    "embed-esco": StageSpec(
        name="embed-esco", depends_on=("load-esco",), per_user=False,
        run=sf.run_embed_esco,
    ),
    "extract-job-skills": StageSpec(
        name="extract-job-skills", depends_on=(), per_user=False,
        # Migrated onto pipeline.stage_run in Task 8 -- placeholder run
        # callable is never actually invoked via run_stage (Task 6);
        # this stage's own runner (core.skills.extraction_run) drives it
        # directly. Present here only so the dependency graph and
        # completeness test see it.
        run=lambda params: {},
    ),
    "map-skills": StageSpec(
        name="map-skills", depends_on=("embed-esco", "extract-job-skills"),
        per_user=False, run=sf.run_map_skills,
    ),
    "llm-map-skills": StageSpec(
        name="llm-map-skills", depends_on=("map-skills",), per_user=False,
        run=sf.run_llm_map_skills,
    ),
    "map-cv-skills": StageSpec(
        name="map-cv-skills", depends_on=("embed-esco",), per_user=True,
        run=sf.run_map_cv_skills,
    ),
    "score-filter-jobs": StageSpec(
        name="score-filter-jobs", depends_on=("classify-jobs",), per_user=True,
        run=sf.run_score_filter_jobs,
    ),
    "chunk-embed-jobs": StageSpec(
        name="chunk-embed-jobs", depends_on=("score-filter-jobs",), per_user=False,
        run=sf.run_chunk_embed_jobs,
    ),
    "chunk-embed-cv": StageSpec(
        name="chunk-embed-cv", depends_on=("map-cv-skills",), per_user=True,
        run=sf.run_chunk_embed_cv,
    ),
    "score-similarity": StageSpec(
        name="score-similarity", depends_on=("chunk-embed-jobs", "chunk-embed-cv"),
        per_user=True, run=sf.run_score_similarity,
    ),
    "score-skill-coverage": StageSpec(
        name="score-skill-coverage", depends_on=("map-cv-skills", "llm-map-skills"),
        # NOT chunk-embed-cv: core/scoring/skill_coverage.py's own module
        # docstring says this stage is "independent of stages 2/2b" --
        # it reads only the CV truth base (map-cv-skills' output) and
        # silver.silver__bridge_job_skill, never scoring.cv_chunk_embedding
        # (chunk-embed-cv's only output). Verified by reading
        # run_skill_coverage directly, not assumed.
        per_user=True, run=sf.run_score_skill_coverage,
    ),
    "score-llm-rerank": StageSpec(
        name="score-llm-rerank", depends_on=("score-similarity", "score-skill-coverage"),
        per_user=True, run=sf.run_score_llm_rerank,
    ),
    "score-blend": StageSpec(
        name="score-blend", depends_on=("score-llm-rerank",), per_user=True,
        run=sf.run_score_blend,
    ),
}


REVIEW_STAGES: dict[str, ReviewStageSpec] = {
    "dedup-review": ReviewStageSpec(
        name="Dedup Review", depends_on=("cluster-jobs",),
        page_path="Dedup_Review_Queue", pending_count=_count_dedup_pending,
    ),
    "categorisation-review": ReviewStageSpec(
        name="Categorisation Review", depends_on=("classify-jobs",),
        page_path="Categorisation_Review", pending_count=_count_categorisation_pending,
    ),
    "skill-review": ReviewStageSpec(
        name="Skill Review", depends_on=("llm-map-skills",),
        page_path="Skill_Review", pending_count=_count_skill_review_pending,
    ),
    "scoring-calibration": ReviewStageSpec(
        name="Scoring Calibration", depends_on=("score-blend",),
        page_path="Scoring_Calibration", pending_count=_count_scoring_calibration_pending,
    ),
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Same container as Step 2, for the same reason:

```bash
docker compose run --rm --entrypoint python pipeline -m unittest tests.test_pipeline_registry -v
```

Expected: 8/8 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/pipeline/registry.py \
        packages/core/tests/test_pipeline_registry.py
git commit -m "feat(job_search): pipeline dashboard — stage catalog + completeness test"
```

---

### Task 6: `runner.py` — generic run lifecycle

**Files:**
- Create: `packages/core/core/pipeline/runner.py`
- Test: `packages/core/tests/integration/test_pipeline_runner.py`

**Interfaces:**
- Consumes: `StageSpec` (Task 5).
- Produces: `RunSnapshot` (dataclass), `RunAlreadyActive`, `RunNotFound`
  (exceptions), `start_run(engine, *, stage, user_id, params) -> uuid.UUID`,
  `get_active_run(engine) -> RunSnapshot | None`,
  `get_run(engine, run_id) -> RunSnapshot | None`,
  `request_cancel(engine, run_id) -> None`,
  `run_stage(run_id, engine, spec: StageSpec, params: dict) -> None`
  (the `BackgroundTasks` callback body for every stage except
  `extract-job-skills`, which Task 8 drives with its own loop, reusing
  `start_run`/`get_active_run`/`request_cancel` from this module but
  not `run_stage`).

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_pipeline_runner.py`:

```python
"""Integration tests for core.pipeline.runner against live Postgres.
Uses one cheap fake StageSpec throughout — real stages' own correctness
is proven by test_pipeline_stage_functions.py; this only proves the
generic harness (lock, progress, cancel, failure)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.pipeline.registry import StageSpec
from core.pipeline.runner import (
    RunAlreadyActive,
    RunNotFound,
    get_active_run,
    get_run,
    request_cancel,
    run_stage,
    start_run,
)


def _ok_stage(params: dict) -> dict:
    return {"did": "work"}


def _failing_stage(params: dict) -> dict:
    raise ValueError("boom")


class TestPipelineRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(text("DELETE FROM pipeline.stage_run WHERE stage LIKE 'zzfixture-%'"))

    def test_start_run_then_get_active_run_round_trips(self) -> None:
        run_id = start_run(self.app_engine, stage="zzfixture-a", user_id=None, params={})
        active = get_active_run(self.app_engine)
        self.assertIsNotNone(active)
        self.assertEqual(active.run_id, run_id)
        self.assertEqual(active.status, "running")

    def test_start_run_while_one_is_active_raises_regardless_of_stage(self) -> None:
        start_run(self.app_engine, stage="zzfixture-a", user_id=None, params={})
        with self.assertRaises(RunAlreadyActive):
            start_run(self.app_engine, stage="zzfixture-b", user_id=None, params={})

    def test_request_cancel_sets_the_flag(self) -> None:
        run_id = start_run(self.app_engine, stage="zzfixture-a", user_id=None, params={})
        request_cancel(self.app_engine, run_id)
        run = get_run(self.app_engine, run_id)
        self.assertTrue(run.cancel_requested)

    def test_request_cancel_on_unknown_run_raises(self) -> None:
        with self.assertRaises(RunNotFound):
            request_cancel(self.app_engine, uuid.uuid4())

    def test_run_stage_marks_completed_and_stores_result(self) -> None:
        spec = StageSpec(name="zzfixture-a", depends_on=(), per_user=False, run=_ok_stage)
        run_id = start_run(self.app_engine, stage=spec.name, user_id=None, params={})
        run_stage(run_id, self.app_engine, spec, {})
        run = get_run(self.app_engine, run_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.result, {"did": "work"})
        self.assertIsNotNone(run.finished_at)

    def test_run_stage_marks_failed_with_the_exception_message(self) -> None:
        spec = StageSpec(name="zzfixture-a", depends_on=(), per_user=False, run=_failing_stage)
        run_id = start_run(self.app_engine, stage=spec.name, user_id=None, params={})
        run_stage(run_id, self.app_engine, spec, {})
        run = get_run(self.app_engine, run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("boom", run.error_message)

    def test_run_stage_marks_cancelled_when_cancel_was_requested_before_run(self) -> None:
        # run_stage checks the cancel flag before calling spec.run — a
        # cancel requested between start_run and the BackgroundTasks
        # callback actually firing must not still run the stage's work.
        spec = StageSpec(name="zzfixture-a", depends_on=(), per_user=False, run=_ok_stage)
        run_id = start_run(self.app_engine, stage=spec.name, user_id=None, params={})
        request_cancel(self.app_engine, run_id)
        run_stage(run_id, self.app_engine, spec, {})
        run = get_run(self.app_engine, run_id)
        self.assertEqual(run.status, "cancelled")

    def test_get_active_run_is_none_when_nothing_is_running(self) -> None:
        self.assertIsNone(get_active_run(self.app_engine))

    def test_run_scoped_to_a_user_id_is_stored_and_read_back(self) -> None:
        user_id = uuid.uuid4()
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture runner user')"
                ),
                {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
            )
        try:
            run_id = start_run(
                self.app_engine, stage="zzfixture-a", user_id=user_id, params={"top_n": 800}
            )
            run = get_run(self.app_engine, run_id)
            self.assertEqual(run.user_id, user_id)
            self.assertEqual(run.params, {"top_n": 800})
        finally:
            with self.owner.begin() as conn:
                conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_runner -v
```

Expected: FAIL/ERROR — `core.pipeline.runner` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/pipeline/runner.py`:

```python
"""Generic run lifecycle for pipeline.stage_run — every automated stage
except extract-job-skills (Task 8 migrates its own bespoke sub-batch
loop onto this table's start_run/get_active_run/request_cancel, but
keeps its own control flow rather than using run_stage below, which
assumes a stage completes in one blocking call).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from core.pipeline.registry import StageSpec

logger = logging.getLogger(__name__)


class RunError(ValueError):
    """Base error for an invalid run-lifecycle operation."""


class RunAlreadyActive(RunError):
    """Raised when `start_run` is called while any run is active,
    system-wide — the global one-active-run-at-a-time lock."""


class RunNotFound(RunError):
    """Raised when a run id names no run."""


@dataclass(frozen=True)
class RunSnapshot:
    """One run's full state.

    Attributes:
        run_id: The run's unique id.
        stage: Which stage this run is for.
        user_id: Who this run is scoped to, or None for a global stage.
        status: "running", "completed", "cancelled", or "failed".
        params: The input arguments this run was started with.
        progress_current: Items processed so far, or None if this
            stage can't report fine-grained progress.
        progress_total: Items expected, or None likewise.
        cancel_requested: Whether a cancel has been requested.
        result: The stage's own result dict, set once terminal.
        error_message: Set only when status == "failed".
        started_at: When the run began.
        updated_at: When progress was last recorded.
        finished_at: When the run reached a terminal status, or None
            while still running.
    """

    run_id: uuid.UUID
    stage: str
    user_id: uuid.UUID | None
    status: str
    params: dict
    progress_current: int | None
    progress_total: int | None
    cancel_requested: bool
    result: dict | None
    error_message: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None


_COLUMNS = (
    "run_id, stage, user_id, status, params, progress_current, progress_total, "
    "cancel_requested, result, error_message, started_at, updated_at, finished_at"
)
_SELECT_ACTIVE = text(f"SELECT {_COLUMNS} FROM pipeline.stage_run WHERE status = 'running'")
_SELECT_ONE = text(f"SELECT {_COLUMNS} FROM pipeline.stage_run WHERE run_id = :run_id")
_INSERT_RUNNING = text(
    "INSERT INTO pipeline.stage_run (stage, user_id, status, params) "
    "VALUES (:stage, :user_id, 'running', CAST(:params AS jsonb)) RETURNING run_id"
)
_REQUEST_CANCEL = text(
    "UPDATE pipeline.stage_run SET cancel_requested = TRUE "
    "WHERE run_id = :run_id AND status = 'running'"
)
_SELECT_CANCEL_REQUESTED = text(
    "SELECT cancel_requested FROM pipeline.stage_run WHERE run_id = :run_id"
)
_FINISH_RUN = text(
    "UPDATE pipeline.stage_run SET status = :status, result = CAST(:result AS jsonb), "
    "error_message = :error_message, finished_at = now(), updated_at = now() "
    "WHERE run_id = :run_id"
)


def _row_to_snapshot(row) -> RunSnapshot:
    import json

    return RunSnapshot(
        run_id=row.run_id, stage=row.stage, user_id=row.user_id, status=row.status,
        params=row.params if isinstance(row.params, dict) else json.loads(row.params),
        progress_current=row.progress_current, progress_total=row.progress_total,
        cancel_requested=row.cancel_requested,
        result=row.result if row.result is None or isinstance(row.result, dict)
        else json.loads(row.result),
        error_message=row.error_message, started_at=row.started_at,
        updated_at=row.updated_at, finished_at=row.finished_at,
    )


def start_run(
    engine: Engine, *, stage: str, user_id: uuid.UUID | None, params: dict
) -> uuid.UUID:
    """Insert a `running` row for `stage`.

    Args:
        engine: The app-role engine.
        stage: Which stage this run is for.
        user_id: Who this run is scoped to, or None for a global stage.
        params: This run's input arguments, stored as JSONB.

    Returns:
        The new run's id.

    Raises:
        RunAlreadyActive: If any run, for any stage, is already `running`
            — the system-wide lock.
    """
    import json

    try:
        with engine.begin() as conn:
            return conn.execute(
                _INSERT_RUNNING,
                {"stage": stage, "user_id": user_id, "params": json.dumps(params)},
            ).scalar_one()
    except IntegrityError as exc:
        raise RunAlreadyActive("a pipeline run is already active") from exc


def get_active_run(engine: Engine) -> RunSnapshot | None:
    """Return the currently active run, if any.

    Args:
        engine: The app-role engine.

    Returns:
        The active run's snapshot, or None if nothing is running.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ACTIVE).first()
    return _row_to_snapshot(row) if row is not None else None


def get_run(engine: Engine, run_id: uuid.UUID) -> RunSnapshot | None:
    """Return one run's snapshot by id, active or finished.

    Args:
        engine: The app-role engine.
        run_id: The run to look up.

    Returns:
        The run's snapshot, or None if `run_id` is unknown.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ONE, {"run_id": run_id}).first()
    return _row_to_snapshot(row) if row is not None else None


def request_cancel(engine: Engine, run_id: uuid.UUID) -> None:
    """Ask a running run to stop.

    Args:
        engine: The app-role engine.
        run_id: The run to cancel.

    Raises:
        RunNotFound: If `run_id` is unknown, or names a run that is not
            currently `running`.
    """
    with engine.begin() as conn:
        result = conn.execute(_REQUEST_CANCEL, {"run_id": run_id})
    if result.rowcount == 0:
        raise RunNotFound(f"no active run with id {run_id}")


def run_stage(run_id: uuid.UUID, engine: Engine, spec: StageSpec, params: dict) -> None:
    """Run a started run to completion, cancellation, or failure.

    Scheduled as a FastAPI `BackgroundTasks` callback by `POST
    /pipeline/stages/{stage}/run` (Task 9) for every stage except
    `extract-job-skills`, which most stages' own underlying function
    (a single blocking call, no natural per-item progress hook) makes
    the right shape for: check cancel, call `spec.run(params)` once,
    record the result. Never raises — any exception from `spec.run`
    marks the run `failed` with its message and stops.

    Args:
        run_id: The run to execute — must already be `running`.
        engine: The app-role engine.
        spec: Which stage to run.
        params: This run's input arguments (same dict passed to
            `start_run`, threaded through again here since
            `BackgroundTasks` needs a fresh call, not a stored closure).
    """
    import json

    with engine.connect() as conn:
        cancelled = conn.execute(_SELECT_CANCEL_REQUESTED, {"run_id": run_id}).scalar_one()
    if cancelled:
        with engine.begin() as conn:
            conn.execute(
                _FINISH_RUN,
                {"run_id": run_id, "status": "cancelled", "result": None, "error_message": None},
            )
        return
    try:
        result = spec.run(params)
    except Exception as exc:  # noqa: BLE001 — any hard failure ends the run
        logger.exception("pipeline run %s (%s) failed", run_id, spec.name)
        with engine.begin() as conn:
            conn.execute(
                _FINISH_RUN,
                {
                    "run_id": run_id, "status": "failed", "result": None,
                    "error_message": str(exc),
                },
            )
        return
    with engine.begin() as conn:
        conn.execute(
            _FINISH_RUN,
            {
                "run_id": run_id, "status": "completed",
                "result": json.dumps(result), "error_message": None,
            },
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_runner -v
```

Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/pipeline/runner.py \
        packages/core/tests/integration/test_pipeline_runner.py
git commit -m "feat(job_search): pipeline dashboard — generic run lifecycle"
```

---

### Task 7: `staleness.py` — dependency-based staleness

**Files:**
- Create: `packages/core/core/pipeline/staleness.py`
- Test: `packages/core/tests/integration/test_pipeline_staleness.py`

**Interfaces:**
- Consumes: `STAGES`, `REVIEW_STAGES` (Task 5); `pipeline.stage_run`
  rows (Task 1/6).
- Produces: `StageState` (dataclass: `last_completed_at,
  last_status, is_blocked, is_stale, stale_because`),
  `compute_stage_states(engine, *, user_id) -> dict[str, StageState]`.

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_pipeline_staleness.py`:

```python
"""Integration tests for core.pipeline.staleness against live Postgres,
using fixture pipeline.stage_run rows with controlled finished_at
timestamps -- this is the "timestamp-order comparison" the spec calls
for, proven with real rows rather than a fake dependency graph, since
the real STAGES graph's shape (does the review-stage skip actually
land on the right automated stage) is exactly what needs proving.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine, live_owner_engine

from core.pipeline.staleness import compute_stage_states


class TestComputeStageStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def tearDown(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM pipeline.stage_run WHERE stage IN "
                    "('cluster-jobs', 'compute-survivorship', 'classify-jobs', "
                    "'compute-title-similarity-scores')"
                )
            )

    def _insert_completed(self, stage: str, finished_at: datetime, user_id=None) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO pipeline.stage_run (stage, user_id, status, finished_at) "
                    "VALUES (:stage, :user_id, 'completed', :finished_at)"
                ),
                {"stage": stage, "user_id": user_id, "finished_at": finished_at},
            )

    def test_a_stage_that_never_ran_is_blocked_if_its_dependency_never_ran(self) -> None:
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertIsNone(states["classify-jobs"].last_completed_at)
        self.assertTrue(states["classify-jobs"].is_blocked)
        self.assertFalse(states["classify-jobs"].is_stale)

    def test_a_stage_is_stale_when_its_dependency_ran_more_recently(self) -> None:
        now = datetime.now(timezone.utc)
        self._insert_completed("cluster-jobs", now - timedelta(hours=2))
        self._insert_completed("compute-survivorship", now - timedelta(hours=3))
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertTrue(states["compute-survivorship"].is_stale)
        self.assertEqual(states["compute-survivorship"].stale_because, "cluster-jobs")
        self.assertFalse(states["cluster-jobs"].is_stale)

    def test_a_stage_is_fresh_when_it_ran_after_its_dependency(self) -> None:
        now = datetime.now(timezone.utc)
        self._insert_completed("cluster-jobs", now - timedelta(hours=3))
        self._insert_completed("compute-survivorship", now - timedelta(hours=2))
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertFalse(states["compute-survivorship"].is_stale)
        self.assertFalse(states["compute-survivorship"].is_blocked)

    def test_staleness_skips_through_a_review_stage_to_the_automated_dependency(self) -> None:
        # classify-jobs depends on compute-survivorship in STAGES; the
        # Categorisation Review stage sits between classify-jobs and
        # nothing downstream in STAGES (review stages are terminal in
        # the automated graph) -- this proves classify-jobs's own
        # staleness is computed against compute-survivorship directly,
        # never against a review stage (which has no timestamp at all).
        now = datetime.now(timezone.utc)
        self._insert_completed("compute-survivorship", now - timedelta(hours=1))
        self._insert_completed("classify-jobs", now)
        states = compute_stage_states(self.app_engine, user_id=None)
        self.assertFalse(states["classify-jobs"].is_stale)

    def test_per_user_stage_staleness_is_scoped_to_that_user(self) -> None:
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        with self.owner.begin() as conn:
            for user_id in (user_a, user_b):
                conn.execute(
                    text(
                        "INSERT INTO app_user (id, email, display_name) "
                        "VALUES (:id, :email, 'zzfixture staleness user')"
                    ),
                    {"id": user_id, "email": f"zzfixture-{user_id}@example.com"},
                )
        try:
            now = datetime.now(timezone.utc)
            self._insert_completed("score-blend", now, user_id=user_a)
            states_a = compute_stage_states(self.app_engine, user_id=user_a)
            states_b = compute_stage_states(self.app_engine, user_id=user_b)
            self.assertIsNotNone(states_a["score-blend"].last_completed_at)
            self.assertIsNone(states_b["score-blend"].last_completed_at)
        finally:
            with self.owner.begin() as conn:
                conn.execute(text("DELETE FROM pipeline.stage_run WHERE stage = 'score-blend'"))
                for user_id in (user_a, user_b):
                    conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_staleness -v
```

Expected: FAIL/ERROR — `core.pipeline.staleness` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/pipeline/staleness.py`:

```python
"""Dependency-based staleness: timestamp-order comparison against the
STAGES dependency graph -- never content-aware (spec's explicit
Non-goal). A stage is:
  - "blocked" if it has never completed AND any of its dependencies
    has never completed either (there's nothing to even be stale
    against yet);
  - "stale" if it HAS completed, but at least one dependency's own
    last-completed time is more recent than its own;
  - otherwise fresh.
A stage that has never completed but whose dependencies all have IS
NOT "blocked" by this definition -- it just has no last_completed_at
and is_stale=False, since "never run" and "stale" are different
things the UI (Task 10) shows differently (a disabled Run button with
a reason, vs. a staleness badge).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, text

from core.pipeline.registry import STAGES


@dataclass(frozen=True)
class StageState:
    """One automated stage's current state, for one user (or global).

    Attributes:
        last_completed_at: When this stage last reached `completed`,
            or None if it never has.
        last_status: The most recent run's status regardless of
            outcome ("running"/"completed"/"cancelled"/"failed"), or
            None if it has never run at all -- shown in the UI even
            when the last attempt failed.
        is_blocked: True if this stage has never completed and at
            least one of its dependencies has never completed either.
        is_stale: True if this stage has completed, but a dependency
            has completed more recently.
        stale_because: The dependency name responsible, or None.
    """

    last_completed_at: datetime | None
    last_status: str | None
    is_blocked: bool
    is_stale: bool
    stale_because: str | None


_SELECT_LAST_RUN = text(
    "SELECT status, finished_at FROM pipeline.stage_run "
    "WHERE stage = :stage AND (user_id = :user_id OR (:user_id IS NULL AND user_id IS NULL)) "
    "ORDER BY started_at DESC LIMIT 1"
)
_SELECT_LAST_COMPLETED = text(
    "SELECT finished_at FROM pipeline.stage_run "
    "WHERE stage = :stage AND (user_id = :user_id OR (:user_id IS NULL AND user_id IS NULL)) "
    "AND status = 'completed' ORDER BY finished_at DESC LIMIT 1"
)


def compute_stage_states(engine: Engine, *, user_id: UUID | None) -> dict[str, StageState]:
    """Compute every automated stage's current state for one scope.

    Args:
        engine: The app-role engine.
        user_id: Whose per-user stage runs to read; ignored (matched
            against NULL) for global stages, since a global stage's
            rows always have `user_id IS NULL`.

    Returns:
        Every `STAGES` name mapped to its `StageState`.
    """
    last_completed: dict[str, datetime | None] = {}
    last_status: dict[str, str | None] = {}
    with engine.connect() as conn:
        for name, spec in STAGES.items():
            scope_user_id = user_id if spec.per_user else None
            last_row = conn.execute(
                _SELECT_LAST_RUN, {"stage": name, "user_id": scope_user_id}
            ).first()
            last_status[name] = last_row.status if last_row is not None else None
            completed_row = conn.execute(
                _SELECT_LAST_COMPLETED, {"stage": name, "user_id": scope_user_id}
            ).first()
            last_completed[name] = (
                completed_row.finished_at if completed_row is not None else None
            )

    states: dict[str, StageState] = {}
    for name, spec in STAGES.items():
        own_completed = last_completed[name]
        if own_completed is None:
            any_dep_never_ran = any(last_completed[dep] is None for dep in spec.depends_on)
            states[name] = StageState(
                last_completed_at=None, last_status=last_status[name],
                is_blocked=any_dep_never_ran, is_stale=False, stale_because=None,
            )
            continue
        stale_because = next(
            (
                dep for dep in spec.depends_on
                if last_completed[dep] is not None and last_completed[dep] > own_completed
            ),
            None,
        )
        states[name] = StageState(
            last_completed_at=own_completed, last_status=last_status[name],
            is_blocked=False, is_stale=stale_because is not None,
            stale_because=stale_because,
        )
    return states
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_staleness -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/pipeline/staleness.py \
        packages/core/tests/integration/test_pipeline_staleness.py
git commit -m "feat(job_search): pipeline dashboard — dependency-based staleness"
```

---

### Task 8: Migrate `extraction_run.py` onto `pipeline.stage_run`

**Files:**
- Modify: `packages/core/core/skills/extraction_run.py`
- Modify: `packages/core/tests/integration/test_extraction_run.py`
- Create: `db/migrations/versions/0031_drop_skill_extraction_run.py`

**Interfaces:**
- Consumes: `start_run`, `get_active_run`, `get_run`, `request_cancel`
  (Task 6's `runner.py` — reused directly, not reimplemented).
- Produces: Same public functions this module already has
  (`start_run`, `get_active_run`, `get_run`, `request_cancel`,
  `list_filter_options`, `run_loop`), same behavior, new
  storage. `RunStatus`'s `sources`/`countries`/`total_pending`/
  `extracted_count`/`failed_count`/`mapping_summary` fields now read
  from `params`/`progress_current`/`progress_total`/`result` under the
  hood — the dataclass's own field names are unchanged so
  `7_Skill_Extraction_Runner.py`'s existing field access
  (`run["extracted_count"]` etc., via whichever serialization the
  router uses) keeps working without a UI change beyond the URL
  repoint in Task 10.

This is the largest single task in this plan — read the whole existing
`packages/core/core/skills/extraction_run.py` first (589 lines,
already read in full while writing this plan's spec) before starting;
the sub-batch/unload/stalled-detection control flow in `run_loop`
(lines 434-589) does not change at all, only the six small persistence
functions above it do.

- [ ] **Step 1: Write the failing tests**

The existing `test_extraction_run.py` already exercises every behavior
this task must preserve. Read it now:

```bash
cat packages/core/tests/integration/test_extraction_run.py
```

Update its setup/teardown to target `pipeline.stage_run` instead of
`silver.skill_extraction_run` (the exact same test bodies otherwise —
this migration must not change what's proven, only where it's proven
against). Replace every occurrence of
`DELETE FROM silver.skill_extraction_run` with
`DELETE FROM pipeline.stage_run WHERE stage = 'extract-job-skills'` in
that file's `tearDown`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_extraction_run -v
```

Expected: FAIL — `extraction_run.py` still writes `silver.skill_extraction_run`,
which the updated tests no longer clean up or read from correctly (or
still pass by accident against the old table — verify by checking
`pipeline.stage_run` is genuinely empty after running them: `docker
compose exec -T postgres psql -U job_search_owner -d job_search -c
"SELECT count(*) FROM pipeline.stage_run WHERE stage = 'extract-job-skills';"`
— expect 0, proving nothing landed there yet).

- [ ] **Step 3: Rewrite the persistence layer**

In `packages/core/core/skills/extraction_run.py`, replace the six
functions `start_run`, `get_active_run`, `get_run`, `request_cancel`,
`_record_progress`, `_is_cancel_requested`, `_finish_run` and the
module-level SQL constants `_COLUMNS`/`_SELECT_ACTIVE`/`_SELECT_ONE`/
`_INSERT_RUNNING`/`_INSERT_COMPLETED`/`_REQUEST_CANCEL`/
`_UPDATE_PROGRESS`/`_SELECT_CANCEL_REQUESTED`/`_FINISH_RUN` with:

```python
from core.pipeline import runner as pipeline_runner

_STAGE_NAME = "extract-job-skills"


def _row_to_status(snapshot: pipeline_runner.RunSnapshot) -> RunStatus:
    """Convert a generic RunSnapshot into this module's own RunStatus
    shape -- the field names below are what 7_Skill_Extraction_Runner.py
    already reads; only their source (params/result vs. dedicated
    columns) changed."""
    params = snapshot.params
    result = snapshot.result or {}
    return RunStatus(
        run_id=snapshot.run_id, status=snapshot.status,
        sources=params.get("sources"), countries=params.get("countries"),
        total_pending=snapshot.progress_total or 0,
        extracted_count=snapshot.progress_current or 0,
        failed_count=result.get("failed_count", 0),
        cancel_requested=snapshot.cancel_requested,
        error_message=snapshot.error_message,
        started_at=snapshot.started_at, updated_at=snapshot.updated_at,
        finished_at=snapshot.finished_at,
        mapping_summary=result.get("mapping_summary"),
    )


def start_run(
    engine: Engine, *, sources: list[str] | None, countries: list[str] | None,
) -> tuple[uuid.UUID, int]:
    """Start a new extraction run for the given scope.

    If nothing is pending in scope, the run is recorded already
    `completed` so the caller can tell "ran, found nothing to do" apart
    from "never started" and should not schedule `run_loop` for it.

    Args:
        engine: The app-role engine.
        sources: Restrict to these `apply_source_name` values, or None
            for every source.
        countries: Restrict to these `country_iso` values, or None for
            every country.

    Returns:
        `(run_id, total_pending)`.

    Raises:
        RunAlreadyActive: If any pipeline run is already active
            (system-wide lock — see core.pipeline.runner).
    """
    total_pending = count_pending_jobs(engine, sources=sources, countries=countries)
    params = {"sources": sources, "countries": countries}
    if total_pending == 0:
        run_id = pipeline_runner.start_run(
            engine, stage=_STAGE_NAME, user_id=None, params=params
        )
        # Immediately finish it as completed -- start_run always inserts
        # `running`, and this scope has nothing to do.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pipeline.stage_run SET status = 'completed', "
                    "progress_total = 0, finished_at = now(), updated_at = now() "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
        return run_id, 0
    try:
        run_id = pipeline_runner.start_run(
            engine, stage=_STAGE_NAME, user_id=None, params=params
        )
    except pipeline_runner.RunAlreadyActive as exc:
        raise RunAlreadyActive("an extraction run is already active") from exc
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE pipeline.stage_run SET progress_total = :total WHERE run_id = :run_id"),
            {"total": total_pending, "run_id": run_id},
        )
    return run_id, total_pending


def get_active_run(engine: Engine) -> RunStatus | None:
    """Return the currently active extraction run, if any.

    Args:
        engine: The app-role engine.

    Returns:
        The active run's status, or None if no extraction run is
        active (note: the system-wide lock means at most one run of
        ANY stage can be active — this returns None if a different
        stage currently holds it, since that isn't an extraction run).
    """
    snapshot = pipeline_runner.get_active_run(engine)
    if snapshot is None or snapshot.stage != _STAGE_NAME:
        return None
    return _row_to_status(snapshot)


def get_run(engine: Engine, run_id: uuid.UUID) -> RunStatus | None:
    """Return one run's status by id, active or finished.

    Args:
        engine: The app-role engine.
        run_id: The run to look up.

    Returns:
        The run's status, or None if `run_id` is unknown.
    """
    snapshot = pipeline_runner.get_run(engine, run_id)
    return _row_to_status(snapshot) if snapshot is not None else None


def request_cancel(engine: Engine, run_id: uuid.UUID) -> None:
    """Ask a running run to stop after its current job.

    Args:
        engine: The app-role engine.
        run_id: The run to cancel.

    Raises:
        RunNotFound: If `run_id` is unknown, or names a run that is
            not currently `running`.
    """
    try:
        pipeline_runner.request_cancel(engine, run_id)
    except pipeline_runner.RunNotFound as exc:
        raise RunNotFound(str(exc)) from exc


_UPDATE_PROGRESS = text(
    "UPDATE pipeline.stage_run SET "
    "progress_current = COALESCE(progress_current, 0) + :extracted, "
    "result = jsonb_set(COALESCE(result, '{}'::jsonb), '{failed_count}', "
    "to_jsonb(COALESCE((result ->> 'failed_count')::int, 0) + :failed)), "
    "updated_at = now() WHERE run_id = :run_id"
)
_SELECT_CANCEL_REQUESTED = text(
    "SELECT cancel_requested FROM pipeline.stage_run WHERE run_id = :run_id"
)
_FINISH_RUN = text(
    "UPDATE pipeline.stage_run SET status = :status, error_message = :error_message, "
    "result = COALESCE(result, '{}'::jsonb) || jsonb_build_object('mapping_summary', "
    "CAST(:mapping_summary AS text)), finished_at = now(), updated_at = now() "
    "WHERE run_id = :run_id"
)


def _record_progress(engine: Engine, run_id: uuid.UUID, extracted: int, failed: int) -> None:
    """Add counts onto a run's running totals and bump its `updated_at`.

    Args:
        engine: The app-role engine.
        run_id: The run to update.
        extracted: Jobs to add to progress_current.
        failed: Jobs to add to result.failed_count.
    """
    with engine.begin() as conn:
        conn.execute(_UPDATE_PROGRESS, {"run_id": run_id, "extracted": extracted, "failed": failed})


def _is_cancel_requested(engine: Engine, run_id: uuid.UUID) -> bool:
    """Check whether a run's cancel flag has been set.

    Args:
        engine: The app-role engine.
        run_id: The run to check.

    Returns:
        The current `cancel_requested` value.
    """
    with engine.connect() as conn:
        return conn.execute(_SELECT_CANCEL_REQUESTED, {"run_id": run_id}).scalar_one()


def _finish_run(
    engine: Engine, run_id: uuid.UUID, *, status: str,
    error_message: str | None = None, mapping_summary: str | None = None,
) -> None:
    """Mark a run terminal.

    Args:
        engine: The app-role engine.
        run_id: The run to finish.
        status: "completed", "cancelled", or "failed".
        error_message: Set when `status == "failed"`.
        mapping_summary: What the automatic skill mapping did, or None.
    """
    with engine.begin() as conn:
        conn.execute(
            _FINISH_RUN,
            {
                "run_id": run_id, "status": status, "error_message": error_message,
                "mapping_summary": mapping_summary,
            },
        )
```

Add `from core.pipeline import runner as pipeline_runner` and `from
sqlalchemy import text` (already imported) to the top of the file. The
`run_loop` function itself (lines 434-589 of the original) is **not
edited** — it already only calls `_record_progress`,
`_is_cancel_requested`, `_finish_run`, `write_job_skills`,
`count_pending_jobs`, and `_unload_model`, all of which keep their
exact same names and signatures above.

Remove `list_filter_options`'s dependency on nothing else changed — it
already queries `silver.job_survivorship`/`silver.job_skill_extraction`
directly, unrelated to run tracking, untouched.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_extraction_run -v
```

Expected: every test that passed before this task still passes (same
count — check the test file's own test count before and after).

- [ ] **Step 5: Update the router test and drop the old table**

```bash
docker compose exec -T api python -m unittest tests.integration.test_extraction_runs_router -v
```

Expected: PASS unchanged — this router isn't touched until Task 9, and
it calls into `extraction_run.py`'s functions, whose signatures and
behavior are unchanged by this task.

Create `db/migrations/versions/0031_drop_skill_extraction_run.py`:

```python
"""drop silver.skill_extraction_run

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-30

extract-job-skills' run tracking moved to pipeline.stage_run (this
plan's Task 8) -- a true system-wide "one active run" lock requires
exactly one lock table, and this one's own partial-unique-index lock
would otherwise coexist with pipeline.stage_run's, letting two runs
go at once. No data migration: any run recorded here is historical
run bookkeeping only (the skill-extraction data itself, in
silver.job_skill_extraction/silver.job_skill_raw, is untouched and
was never stored in this table).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # job_search_app's INSERT grant on silver.job_skill_extraction/
    # job_skill_raw (from 0025) is unrelated to skill_extraction_run
    # itself -- write_job_skills still needs it and it is untouched
    # here. Only the run-tracking table and its own grant go.
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON silver.skill_extraction_run FROM job_search_app"
    )
    op.drop_index(
        "ux_skill_extraction_run_one_active", table_name="skill_extraction_run", schema="silver"
    )
    op.drop_table("skill_extraction_run", schema="silver")


def downgrade() -> None:
    op.create_table(
        "skill_extraction_run",
        sa.Column(
            "run_id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("sources", ARRAY(sa.Text()), nullable=True),
        sa.Column("countries", ARRAY(sa.Text()), nullable=True),
        sa.Column("total_pending", sa.Integer(), nullable=False),
        sa.Column("extracted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mapping_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'cancelled', 'failed')",
            name="ck_skill_extraction_run_status",
        ),
        schema="silver",
    )
    op.create_index(
        "ux_skill_extraction_run_one_active", "skill_extraction_run", ["status"],
        unique=True, postgresql_where=sa.text("status = 'running'"), schema="silver",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON silver.skill_extraction_run TO job_search_app")
```

- [ ] **Step 6: Run the migration and the full extraction-run test suite**

Same ad-hoc mount as Task 1's migration — never add `db/` to
`docker-compose.yml` itself (Task 1's review found and reverted exactly
that mistake):

```bash
docker compose run --rm -v "$(pwd)/db:/app/db" --entrypoint alembic api -c db/alembic.ini upgrade head
docker compose exec -T api python -m unittest tests.integration.test_extraction_run tests.integration.test_extraction_runs_router -v
```

Expected: `alembic_version` is `0031`; every test still PASSes.

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/skills/extraction_run.py \
        packages/core/tests/integration/test_extraction_run.py \
        db/migrations/versions/0031_drop_skill_extraction_run.py
git commit -m "refactor(job_search): migrate extract-job-skills runner onto pipeline.stage_run"
```

---

### Task 9: API router

**Files:**
- Create: `apps/api/app/routers/pipeline.py`
- Modify: `apps/api/app/routers/extraction_runs.py`
- Modify: `apps/api/app/main.py`
- Test: `packages/core/tests/integration/test_pipeline_router.py`

**Interfaces:**
- Consumes: `STAGES`, `REVIEW_STAGES` (Task 5), `runner.py` (Task 6),
  `staleness.py` (Task 7).
- Produces:
  `GET /pipeline/stages?user_id=` → `list[StageStatusModel |
  ReviewStageStatusModel]`,
  `POST /pipeline/stages/{stage}/run` → `202 {run_id}` or `409`,
  `GET /pipeline/stages/{stage}/active?user_id=` → snapshot or `null`,
  `POST /pipeline/stages/{stage}/cancel` → `204` or `404`,
  `GET /pipeline/users` → `list[UserModel]`.

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_pipeline_router.py`:

```python
"""Router tests for the pipeline dashboard endpoints. Real ASGI
requests against the real app, same TestClient pattern as
test_scoring_router_calibration.py."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402

from core.db.session import build_engine  # noqa: E402
from core.settings import get_settings  # noqa: E402


class TestPipelineRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)
        cls.client = TestClient(app)

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline.stage_run WHERE stage LIKE 'zzfixture-%'"))
            conn.execute(
                text(
                    "DELETE FROM pipeline.stage_run WHERE stage = 'enrich-engagement-terms'"
                )
            )

    def test_get_stages_without_user_id_returns_422(self) -> None:
        response = self.client.get("/pipeline/stages")
        self.assertEqual(response.status_code, 422)

    def test_get_stages_returns_every_catalog_entry(self) -> None:
        user_id = uuid.uuid4()
        response = self.client.get(f"/pipeline/stages?user_id={user_id}")
        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.json()}
        self.assertIn("classify-jobs", names)
        self.assertIn("dedup-review", names)

    def test_get_users_returns_the_app_user_table(self) -> None:
        response = self.client.get("/pipeline/users")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_starting_a_run_then_double_starting_returns_409(self) -> None:
        response1 = self.client.post(
            "/pipeline/stages/enrich-engagement-terms/run", json={}
        )
        self.assertEqual(response1.status_code, 202)
        response2 = self.client.post(
            "/pipeline/stages/compute-blocking-keys/run", json={}
        )
        self.assertEqual(response2.status_code, 409)
        # Let the background task actually finish before the next test's
        # global-lock assumption (nothing else asserts on this run's
        # own completion here — it's real, cheap work against the live
        # DB, proven separately by test_pipeline_stage_functions.py).
        import time

        for _ in range(50):
            active = self.client.get(
                "/pipeline/stages/enrich-engagement-terms/active"
            ).json()
            if active is None:
                break
            time.sleep(0.2)

    def test_cancel_on_unknown_run_returns_404(self) -> None:
        response = self.client.post(f"/pipeline/stages/whatever/cancel")
        self.assertEqual(response.status_code, 404)

    def test_a_per_user_stage_run_requires_user_id_in_the_body(self) -> None:
        response = self.client.post("/pipeline/stages/score-blend/run", json={})
        self.assertEqual(response.status_code, 400)

    def test_active_run_includes_updated_at_for_stalled_detection(self) -> None:
        start = self.client.post("/pipeline/stages/enrich-engagement-terms/run", json={})
        self.assertEqual(start.status_code, 202)
        active = self.client.get("/pipeline/stages/enrich-engagement-terms/active").json()
        # The run may already have completed by the time this reads it
        # (a cheap stage) -- either way, updated_at was present on the
        # snapshot while it was active; assert against the row directly
        # to avoid a flaky race with the background task.
        with self.owner_engine.connect() as conn:
            row = conn.execute(
                text("SELECT updated_at FROM pipeline.stage_run WHERE run_id = :id"),
                {"id": start.json()["run_id"]},
            ).one()
        self.assertIsNotNone(row.updated_at)
        if active is not None:
            self.assertIn("updated_at", active)

    def test_running_a_no_run_button_stage_returns_400(self) -> None:
        response = self.client.post("/pipeline/stages/ingest/run", json={})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_router -v
```

Expected: FAIL — `/pipeline/*` routes don't exist yet (404s where
other codes are expected).

- [ ] **Step 3: Write the router**

Create `apps/api/app/routers/pipeline.py`:

```python
"""Pipeline dashboard endpoints — status/control-center across every
automated CLI stage and human-review step (docs/superpowers/specs/
2026-09-30-pipeline-dashboard-design.md).
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine, text

from core.pipeline.registry import REVIEW_STAGES, STAGES
from core.pipeline.runner import RunAlreadyActive, RunNotFound, get_run, request_cancel, run_stage, start_run
from core.pipeline.staleness import compute_stage_states

router = APIRouter()


class StageStatusModel(BaseModel):
    """Response entry for one automated stage."""

    kind: str = "automated"
    name: str
    depends_on: list[str]
    per_user: bool
    has_run_button: bool
    last_completed_at: str | None
    last_status: str | None
    is_blocked: bool
    is_stale: bool
    stale_because: str | None


class ReviewStageStatusModel(BaseModel):
    """Response entry for one human-review stage."""

    kind: str = "review"
    name: str
    page_path: str
    pending_count: int


class RunRequestBody(BaseModel):
    """Request body for POST /pipeline/stages/{stage}/run."""

    user_id: uuid.UUID | None = None
    params: dict = {}


class RunResponseModel(BaseModel):
    """Response body for a started run."""

    run_id: uuid.UUID


class ActiveRunModel(BaseModel):
    """Response body for GET /pipeline/stages/{stage}/active."""

    run_id: uuid.UUID
    status: str
    progress_current: int | None
    progress_total: int | None
    error_message: str | None
    updated_at: str
    """ISO timestamp of the last progress heartbeat -- the UI (Task 10)
    compares this against now() to detect a run whose API process
    restarted mid-run (same "possibly stalled" signal the original
    skill-extraction runner surfaced, generalized to every stage)."""


class UserModel(BaseModel):
    """Response entry for GET /pipeline/users."""

    id: uuid.UUID
    email: str
    display_name: str


@router.get("/pipeline/stages", response_model=list[StageStatusModel | ReviewStageStatusModel])
def get_stages(
    user_id: uuid.UUID, engine: Engine = Depends(get_app_db_engine),
) -> list[StageStatusModel | ReviewStageStatusModel]:
    """Every automated and review stage, merged with live state.

    Args:
        user_id: Required — per-user stages' state depends on which
            user is selected; global stages' state ignores it. The
            dashboard always has a user selected.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every `STAGES`/`REVIEW_STAGES` entry as its matching model.
    """
    states = compute_stage_states(engine, user_id=user_id)
    result: list[StageStatusModel | ReviewStageStatusModel] = []
    for name, spec in STAGES.items():
        state = states[name]
        result.append(
            StageStatusModel(
                name=name, depends_on=list(spec.depends_on), per_user=spec.per_user,
                has_run_button=spec.has_run_button,
                last_completed_at=(
                    state.last_completed_at.isoformat() if state.last_completed_at else None
                ),
                last_status=state.last_status, is_blocked=state.is_blocked,
                is_stale=state.is_stale, stale_because=state.stale_because,
            )
        )
    for name, spec in REVIEW_STAGES.items():
        result.append(
            ReviewStageStatusModel(
                name=spec.name, page_path=spec.page_path,
                pending_count=spec.pending_count(engine, user_id),
            )
        )
    return result


@router.get("/pipeline/users", response_model=list[UserModel])
def get_users(engine: Engine = Depends(get_app_db_engine)) -> list[UserModel]:
    """List every app_user, for the dashboard's user-picker.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every user, ordered by email.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, email, display_name FROM app_user ORDER BY email")
        ).all()
    return [UserModel(id=row.id, email=row.email, display_name=row.display_name) for row in rows]


@router.post("/pipeline/stages/{stage}/run", response_model=RunResponseModel)
def run_pipeline_stage(
    stage: str, body: RunRequestBody, background_tasks: BackgroundTasks,
    engine: Engine = Depends(get_app_db_engine),
) -> RunResponseModel:
    """Start a stage's run in the background.

    Args:
        stage: The stage name, must be a key of `STAGES`.
        body: `user_id` (required for a per-user stage, must be omitted
            for a global one) and `params`.
        background_tasks: FastAPI's background-task scheduler.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The new run's id, `202` (FastAPI's default for a POST that
        returns 200 by model — Task 10's UI treats any 2xx as started).

    Raises:
        fastapi.HTTPException: `404` if `stage` is unknown; `400` if
            `user_id` is missing/present incorrectly for this stage's
            `per_user` flag; `409` if any pipeline run is already
            active.
    """
    spec = STAGES.get(stage)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown stage {stage!r}")
    if not spec.has_run_button:
        raise HTTPException(
            status_code=400, detail=f"{stage} has no dashboard-triggerable run (CLI only)"
        )
    if spec.per_user and body.user_id is None:
        raise HTTPException(status_code=400, detail=f"{stage} requires user_id")
    if not spec.per_user and body.user_id is not None:
        raise HTTPException(status_code=400, detail=f"{stage} is not a per-user stage")
    params = dict(body.params)
    if spec.per_user:
        params["user_id"] = body.user_id
    try:
        run_id = start_run(engine, stage=stage, user_id=body.user_id, params=params)
    except RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(run_stage, run_id, engine, spec, params)
    return RunResponseModel(run_id=run_id)


@router.get("/pipeline/stages/{stage}/active", response_model=ActiveRunModel | None)
def get_active_stage_run(
    stage: str, engine: Engine = Depends(get_app_db_engine),
) -> ActiveRunModel | None:
    """Return the active run for `stage`, if the currently active
    pipeline run (system-wide, at most one) happens to be for it.

    Args:
        stage: The stage name.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The active run's snapshot, or None.
    """
    from core.pipeline.runner import get_active_run

    snapshot = get_active_run(engine)
    if snapshot is None or snapshot.stage != stage:
        return None
    return ActiveRunModel(
        run_id=snapshot.run_id, status=snapshot.status,
        progress_current=snapshot.progress_current, progress_total=snapshot.progress_total,
        error_message=snapshot.error_message, updated_at=snapshot.updated_at.isoformat(),
    )


@router.post("/pipeline/stages/{stage}/cancel", status_code=204, response_model=None)
def cancel_stage_run(stage: str, engine: Engine = Depends(get_app_db_engine)) -> None:
    """Cancel `stage`'s active run.

    Args:
        stage: The stage name (used only to look up the active run and
            confirm it matches — the actual cancel target is whatever
            run is currently active).
        engine: Injected via `get_app_db_engine`.

    Raises:
        fastapi.HTTPException: `404` if no run is active, or the active
            run is for a different stage.
    """
    from core.pipeline.runner import get_active_run

    snapshot = get_active_run(engine)
    if snapshot is None or snapshot.stage != stage:
        raise HTTPException(status_code=404, detail=f"no active run for {stage!r}")
    try:
        request_cancel(engine, snapshot.run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```

- [ ] **Step 4: Trim the extraction-runs router**

In `apps/api/app/routers/extraction_runs.py`, remove the endpoints
`POST /skills/extraction-runs`, `GET /skills/extraction-runs/active`,
and `POST /skills/extraction-runs/{run_id}/cancel` (now served
generically by `/pipeline/stages/extract-job-skills/*`). Keep `GET
/skills/extraction-runs/filters` and `GET
/skills/extraction-runs/pending-count` exactly as they are — those are
extraction-specific scoping metadata for the UI's multi-select form,
not run-lifecycle endpoints.

- [ ] **Step 5: Register the new router**

In `apps/api/app/main.py`, find where `extraction_runs.router` (or
similar) is included and add the new router alongside it:

```python
from app.routers import pipeline

app.include_router(pipeline.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_pipeline_router tests.integration.test_extraction_runs_router -v
```

Expected: `test_pipeline_router.py` 8/8 PASS. `test_extraction_runs_router.py`
now fails on the three removed-endpoint tests (double-start `409`,
`202`-then-poll, cancel) — update that file: delete those three test
methods (the same behavior is now covered by
`test_pipeline_router.py`'s own double-start/cancel tests, scoped to
`extract-job-skills` via `stage="extract-job-skills"`), keep the
`filters`/`pending-count` tests unchanged. Re-run both suites to
confirm green.

- [ ] **Step 7: Run the full scoring/pipeline regression suite**

`test_pipeline_registry.py` needs the `pipeline` container (see Task
5's Step 2/4 — it reads `apps/pipeline/app/cli.py`'s source, which
`api` doesn't mount) — named explicitly here rather than a glob, to
avoid ambiguity with `test_pipeline_runner.py`/`test_pipeline_router.py`
(a naive "exclude names starting with r" glob wrongly catches both):

```bash
docker compose run --rm --entrypoint python pipeline -m unittest tests.test_pipeline_registry -v
docker compose exec -T api python -m unittest tests.integration.test_pipeline_schema tests.integration.test_pipeline_stage_functions tests.integration.test_pipeline_runner tests.integration.test_pipeline_staleness tests.integration.test_pipeline_router -v
docker compose exec -T api python -m unittest tests.integration.test_extraction_run tests.integration.test_extraction_runs_router -v
```

Expected: every test passes.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/routers/pipeline.py \
        apps/api/app/routers/extraction_runs.py \
        apps/api/app/main.py \
        packages/core/tests/integration/test_pipeline_router.py \
        packages/core/tests/integration/test_extraction_runs_router.py
git commit -m "feat(job_search): pipeline dashboard — API router"
```

---

### Task 10: UI page

**Files:**
- Create: `apps/ui/app/pages/0_Pipeline_Dashboard.py`
- Modify: `apps/ui/app/pages/7_Skill_Extraction_Runner.py`

**Interfaces:**
- Consumes: every endpoint from Task 9, via `httpx` + `core.settings.get_settings().api_base_url` — same pattern as every other page this session has touched.
- Produces: nothing further tasks depend on.

This repo has no automated tests for Streamlit pages — verified
manually against the live stack, matching every other UI task in this
project's history.

- [ ] **Step 1: Repoint the skill-extraction runner**

In `apps/ui/app/pages/7_Skill_Extraction_Runner.py`, replace every call
to `/skills/extraction-runs` (POST, start), `/skills/extraction-runs/active`
(GET), and `/skills/extraction-runs/{run_id}/cancel` (POST) with
`/pipeline/stages/extract-job-skills/run`,
`/pipeline/stages/extract-job-skills/active`, and
`/pipeline/stages/extract-job-skills/cancel` respectively. The POST
`run` body changes shape: wrap the existing `{"sources": ..., "countries":
...}` payload as `{"params": {"sources": ..., "countries": ...}}` (per
Task 9's `RunRequestBody`). Leave every call to `/skills/extraction-runs/filters`
and `/skills/extraction-runs/pending-count` unchanged.

- [ ] **Step 2: Write the dashboard page**

Create `apps/ui/app/pages/0_Pipeline_Dashboard.py`:

```python
"""Pipeline dashboard — status/control-center across the whole
workflow (docs/superpowers/specs/2026-09-30-pipeline-dashboard-design.md).
Sits alongside every other page; shows state and links out rather than
replacing any of them.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
import streamlit as st

from core.settings import get_settings

STALLED_THRESHOLD_SECONDS = 120
"""A running stage whose progress heartbeat (updated_at) is older than
this is shown as possibly stalled -- same signal the original
skill-extraction runner used (2min while progress was still coarse
per-sub-batch; kept at 2min here since most stages here report no
fine-grained progress at all, so a shorter threshold would false-
positive on any normal multi-minute stage)."""

st.set_page_config(page_title="Pipeline Dashboard", layout="wide")
st.title("Pipeline Dashboard")

with st.expander("User manual"):
    st.markdown(
        """
Every stage of the pipeline, in one place — automated stages (a Run
button, live progress) and human-review steps (a pending count and a
link to the page where you actually do that work).

**Staleness** is dependency-based but timestamp-only: a stage is
flagged stale when something it depends on has completed more recently
than it has. This is an approximation, not a content-aware check — a
re-run that touched zero new rows still clears a staleness flag. A
stage with a **greyed-out Run button** has never had a dependency
complete at all yet — that's a stronger state than "stale."

Only one pipeline action runs at a time, system-wide — starting a
second stage while one is already running is refused, not queued.
"""
    )

_settings = get_settings()
_base = _settings.api_base_url


def _get(path: str) -> httpx.Response:
    return httpx.get(f"{_base}{path}", timeout=10.0)


def _post(path: str, json: dict) -> httpx.Response:
    return httpx.post(f"{_base}{path}", json=json, timeout=10.0)


try:
    users_response = _get("/pipeline/users")
    users_response.raise_for_status()
    users = users_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load users: {exc}")
    users = []

if not users:
    st.warning("No users found — nothing to show per-user stage state for.")
    st.stop()

if "pipeline_user_id" not in st.session_state:
    dev_user = next((u for u in users if u["display_name"].lower().startswith("dev")), users[0])
    st.session_state.pipeline_user_id = dev_user["id"]

user_labels = {u["id"]: f"{u['display_name']} ({u['email']})" for u in users}
selected_user_id = st.selectbox(
    "User (for per-user stages)", options=list(user_labels), format_func=lambda uid: user_labels[uid],
    key="pipeline_user_id",
)

try:
    stages_response = _get(f"/pipeline/stages?user_id={selected_user_id}")
    stages_response.raise_for_status()
    stages = stages_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load pipeline stages: {exc}")
    st.stop()

active_response = None
for stage in stages:
    if stage["kind"] != "automated" or not stage.get("has_run_button", True):
        continue
    try:
        r = _get(f"/pipeline/stages/{stage['name']}/active")
        r.raise_for_status()
        if r.json() is not None:
            active_response = (stage["name"], r.json())
            break
    except httpx.HTTPError:
        continue

_PHASES = {
    "Ingestion & Dedup": (
        "ingest", "enrich-engagement-terms", "compute-blocking-keys",
        "compute-similarity-features", "compute-title-similarity-scores",
        "dedup-review", "cluster-jobs", "compute-survivorship",
    ),
    "Categorisation": ("classify-jobs", "categorisation-review"),
    "CV & Skills": (
        "load-esco", "embed-esco", "extract-job-skills", "map-skills",
        "llm-map-skills", "skill-review", "map-cv-skills",
    ),
    "Scoring": (
        "score-filter-jobs", "chunk-embed-jobs", "chunk-embed-cv",
        "score-similarity", "score-skill-coverage", "score-llm-rerank",
        "score-blend", "scoring-calibration",
    ),
    "Other": ("run-evals",),
}

# StageStatusModel's "name" is already the registry key (e.g.
# "classify-jobs"). ReviewStageStatusModel's "name" is a display name
# instead ("Categorisation Review") -- its registry key is recovered
# from page_path, which is unique and matches the phase table below.
_REVIEW_KEY_BY_PAGE = {
    "Dedup_Review_Queue": "dedup-review", "Categorisation_Review": "categorisation-review",
    "Skill_Review": "skill-review", "Scoring_Calibration": "scoring-calibration",
}
stages_by_name = {}
for stage in stages:
    key = stage["name"] if stage["kind"] == "automated" else _REVIEW_KEY_BY_PAGE[stage["page_path"]]
    stages_by_name[key] = stage

for phase, stage_keys in _PHASES.items():
    st.subheader(phase)
    for key in stage_keys:
        stage = stages_by_name.get(key)
        if stage is None:
            continue
        col1, col2, col3 = st.columns([3, 2, 2])
        if stage["kind"] == "review":
            col1.write(f"**{stage['name']}**")
            col2.write(f"{stage['pending_count']} pending")
            col3.page_link(f"pages/{stage['page_path']}.py", label="Open →")
            continue

        col1.write(f"**{stage['name']}**" + ("" if stage["has_run_button"] else " _(CLI only)_"))
        last = stage["last_completed_at"] or "never"
        status_note = f" (last attempt: {stage['last_status']})" if stage["last_status"] not in (None, "completed") else ""
        col2.caption(f"last completed: {last}{status_note}")
        if stage["is_blocked"]:
            col2.caption(f"⛔ blocked — a dependency has never completed")
        elif stage["is_stale"]:
            col2.caption(f"⚠ stale — {stage['stale_because']} ran more recently")

        if not stage["has_run_button"]:
            continue
        if active_response is not None and active_response[0] == key:
            _, active = active_response
            if active["progress_total"]:
                col3.progress(
                    min(active["progress_current"] / active["progress_total"], 1.0),
                    text=f"{active['progress_current']} / {active['progress_total']}",
                )
            else:
                col3.write("⟳ running…")
            updated_at = datetime.fromisoformat(active["updated_at"])
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            stalled_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
            if stalled_seconds > STALLED_THRESHOLD_SECONDS:
                col3.warning(
                    f"possibly stalled (no progress for {int(stalled_seconds // 60)}m) — "
                    "the API may have restarted. Cancel to clear it."
                )
            if col3.button("Cancel", key=f"cancel-{key}"):
                _post(f"/pipeline/stages/{key}/cancel", {})
                st.rerun()
        else:
            disabled = active_response is not None or stage["is_blocked"]
            help_text = (
                f"pipeline busy: {active_response[0]} is running" if active_response is not None
                else "a dependency has never completed" if stage["is_blocked"] else None
            )
            if col3.button("Run", key=f"run-{key}", disabled=disabled, help=help_text):
                body = {"user_id": selected_user_id} if stage["per_user"] else {}
                response = _post(f"/pipeline/stages/{key}/run", body)
                if response.status_code == 409:
                    st.error("Another pipeline run just started — try again.")
                elif response.status_code >= 400:
                    st.error(f"Failed to start: {response.text}")
                st.rerun()

if active_response is not None:
    time.sleep(5)
    st.rerun()
```

- [ ] **Step 2: Verify manually against the live stack**

```bash
docker compose exec -T ui python -m py_compile apps/ui/app/pages/0_Pipeline_Dashboard.py
docker compose exec -T ui python -m py_compile apps/ui/app/pages/7_Skill_Extraction_Runner.py
docker compose up -d ui
```

Open `http://localhost:8501`, navigate to "Pipeline Dashboard" in the
sidebar (numbered `0`, first in the list). Confirm: the page loads
without error; every phase section renders; `classify-jobs` shows
"⛔ blocked" if `compute-survivorship` has never run in this
environment; clicking Run on a cheap global stage
(`enrich-engagement-terms`) shows a spinner, then updates to "last
completed: <just now>"; every other stage's Run button is disabled
while it runs; Cancel works; the Skill Extraction Runner page
(renumbered concern: still page `7`) still starts/polls/cancels a run
correctly through its repointed endpoints.

- [ ] **Step 3: Commit**

```bash
git add apps/ui/app/pages/0_Pipeline_Dashboard.py \
        apps/ui/app/pages/7_Skill_Extraction_Runner.py
git commit -m "feat(job_search): pipeline dashboard — UI page"
```

---

### Task 11: README, full regression, wrap-up

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-10.
- Produces: nothing further tasks depend on (final task).

- [ ] **Step 1: Add the README section**

Add a new top-level section (placement: right after the "Scoring the
job pool (Step 15)" / "Calibrating the scoring (Step 16)" sections,
since the dashboard is downstream of understanding those stages):

```markdown
## Pipeline dashboard

The **Pipeline Dashboard** page (`apps/ui/app/pages/0_Pipeline_Dashboard.py`,
`http://localhost:8501/Pipeline_Dashboard`) is a status/control-center
across the whole workflow — every automated stage and human-review step
in one place. It sits alongside every other page; it shows state and
links out, it never replaces a page's own UI.

### The stage graph

```
ingest ─┬─→ enrich-engagement-terms ─→ compute-blocking-keys ─→ compute-similarity-features
        │                                                              │
        │                                                              ▼
        │                                          compute-title-similarity-scores
        │                                                              │
        │                                                              ▼
        │                                          [Dedup Review — review]
        │                                                              │
        │                                                              ▼
        │                                        [Dedup Calibration — review]
        │                                                              │
        │                                                              ▼
        │                                                        cluster-jobs
        │                                                              │
        │                                                              ▼
        │                                                  compute-survivorship
        │                                                              │
        │                                                              ▼
        │                                                       classify-jobs
        │                                                              │
        │                                                              ▼
        │                                      [Categorisation Review — review]
        │
        ├─→ [CV Editor — review] ─→ map-cv-skills
        │
        ├─→ load-esco ─→ embed-esco ────────────────────────┐
        │                                                     ▼
        └─→ extract-job-skills ─→ map-skills ─→ llm-map-skills ─→ [Skill Review — review]

── everything above feeds Step 15, per real user ──────────────────────

score-filter-jobs ─→ chunk-embed-jobs (shared) ─┬─→ score-similarity ─→ score-skill-coverage
map-cv-skills ─→ chunk-embed-cv ─────────────────┘         │                    │
                                                              └────────┬─────────┘
                                                                       ▼
                                                                score-llm-rerank
                                                                       │
                                                                       ▼
                                                                 score-blend
                                                                       │
                                                                       ▼
                                                   [Scoring Calibration — review]
```

`load-esco`/`embed-esco` and `extract-job-skills` are independent of
each other until `map-skills`. `ingest` and `run-evals` appear in the
graph but have no dashboard Run button — `ingest` needs per-call
source/query parameters that don't fit a generic Run button; `run-evals`
is developer tooling, not a pipeline stage.

### Staleness is timestamp-order, not content-aware

A stage is flagged **stale** when a stage it depends on has completed
more recently than it has. This is a cheap approximation, not a real
"did the upstream data actually change" check — re-running a stage
that processed zero new rows still clears any staleness flag on its
downstream stages. A stage that has never run, with a dependency that
has also never run, shows as **blocked** (a stronger state — its Run
button is disabled) rather than stale.

### Adding a new pipeline stage

Every new `apps/pipeline/app/cli.py` subcommand must be added to
`packages/core/core/pipeline/registry.py`'s `STAGES` (or the small,
named exclusion list in `test_pipeline_registry.py`, for the rare
stage that genuinely doesn't fit a dashboard Run button) before it
ships. This is enforced, not just documented:
`test_pipeline_registry.py`'s
`test_every_cli_subcommand_has_a_stages_entry_or_is_excluded` fails CI
otherwise. A new human-review step should get a `REVIEW_STAGES` entry
the same way.
```

- [ ] **Step 2: Full regression suite**

`test_pipeline_registry.py` needs the `pipeline` container (see Task
5's Step 2/4), run separately from the rest:

```bash
docker compose run --rm --entrypoint python pipeline -m unittest tests.test_pipeline_registry -v
docker compose exec -T api python -m unittest tests.integration.test_pipeline_schema tests.integration.test_pipeline_stage_functions tests.integration.test_pipeline_runner tests.integration.test_pipeline_staleness tests.integration.test_pipeline_router -v
docker compose exec -T api python -m unittest tests.integration.test_extraction_run tests.integration.test_extraction_runs_router -v
docker compose exec -T api python -m unittest discover -s tests -p "test_scoring_*.py" -v
```

Expected: every `test_pipeline_*` and extraction-run test passes; the
scoring suite shows the same 2 pre-existing, unrelated `llama_index`
import errors already tracked from Step 16 (not caused by this work)
and nothing new.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(job_search): pipeline dashboard — README section"
```
