# Skill-Extraction Batch Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user trigger a scoped (source/country) skill-extraction batch from the Streamlit UI, running safely (bounded sub-batches + Ollama unload between them) whether Ollama is native or Docker-hosted, with progress visible and a run persisted across an API restart.

**Architecture:** A new `silver.skill_extraction_run` table tracks one row per run. `POST /skills/extraction-runs` inserts a `running` row and schedules a FastAPI `BackgroundTasks` call to `core.skills.extraction_run.run_loop`, which repeats `write_job_skills(limit=30, ...)` → record progress → unload Ollama over HTTP (`keep_alive: 0`) → 10s pause, until done/cancelled/failed. A new Streamlit page polls run status and lets the user Start/Stop.

**Tech Stack:** Python 3.11, FastAPI (`BackgroundTasks`), SQLAlchemy Core (raw `text()` queries, this codebase's existing style — no ORM), Alembic, Streamlit, `httpx`, Postgres.

**Spec:** [docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md](../specs/2026-09-23-skill-extraction-batch-runner-design.md)

## Global Constraints

- Batch size is fixed at 30 jobs per sub-batch, pause is fixed at 10s between sub-batches — no UI override (spec's Non-goals). These match `scripts/extract_in_batches.sh`'s defaults exactly.
- Only one `running` row may exist at a time, enforced by a partial unique index at the database level, not application logic.
- No mocking the database in tests (`.claude/rules/python-testing.md`) — integration tests hit the real dev Postgres via `live_owner_engine()` / `live_app_engine()`.
- Google-style docstrings with `Args`/`Returns`/`Raises` on every function, `black`/`isort`/`ruff` formatting, type hints on every public signature (`.claude/rules/python-style.md`).
- SQL keywords uppercase, 4-space indent, trailing commas (`.claude/rules/sql-style.md`) — applies to every raw `text()` query written here.
- Ollama unload uses `POST {ollama_base_url}/api/generate` with `{"model": ..., "keep_alive": 0}` and no `prompt` — verified in this project to work identically for native and Docker-hosted Ollama.
- The extraction model tag is never hardcoded — always resolved via `core.llm.task_config.load_task_config("skill_extraction").model`, the same source `core.llm.gateway.complete` uses.

---

## File Structure

| File | Responsibility |
|---|---|
| `db/migrations/versions/0025_create_skill_extraction_run.py` | New table, one-active-run index, grants |
| `packages/core/core/skills/extraction_run.py` | Run lifecycle (`start_run`, `get_active_run`, `request_cancel`, `list_filter_options`) and the batch loop (`run_loop`) |
| `packages/core/tests/integration/test_extraction_run.py` | Integration tests for the above, against live Postgres |
| `packages/core/tests/integration/skills_fixtures.py` | Modified: `purge_fixtures` also deletes fixture `skill_extraction_run` rows |
| `apps/api/app/routers/extraction_runs.py` | HTTP surface: filters, pending-count, start, active, cancel |
| `apps/api/app/main.py` | Modified: mount the new router |
| `packages/core/tests/integration/test_extraction_runs_router.py` | Integration tests for the router, `TestClient` + live DB |
| `apps/ui/app/pages/7_Skill_Extraction_Runner.py` | The Streamlit page |
| `README.md` | Modified: document the new UI-triggered path as the Docker-safe way to run big batches |
| `docs/esco.md` | Modified: point "Scoping extraction" at the new page first |

---

### Task 1: Migration — `silver.skill_extraction_run`

**Files:**
- Create: `db/migrations/versions/0025_create_skill_extraction_run.py`

**Interfaces:**
- Produces: table `silver.skill_extraction_run` with columns `run_id` (uuid pk), `status` (text, one of `running`/`completed`/`cancelled`/`failed`), `sources` (text[], nullable), `countries` (text[], nullable), `total_pending` (int), `extracted_count` (int, default 0), `failed_count` (int, default 0), `cancel_requested` (bool, default false), `error_message` (text, nullable), `started_at`/`updated_at` (timestamptz, default now()), `finished_at` (timestamptz, nullable). Unique partial index `ux_skill_extraction_run_one_active` on `status` `WHERE status = 'running'`. `job_search_app` granted `SELECT, INSERT, UPDATE` on the table, and `INSERT` on `silver.job_skill_extraction` / `silver.job_skill_raw` (previously owner-role-only — the API now writes these directly via `write_job_skills`).

- [ ] **Step 1: Write the migration**

```python
"""create silver.skill_extraction_run

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-23

Backs the skill-extraction batch runner (JOB-tbd, see docs/superpowers/
specs/2026-09-23-skill-extraction-batch-runner-design.md): one row per
UI-triggered extraction run, so its status survives an API restart. The
partial unique index on `status = 'running'` is what enforces "at most
one active run" — a second INSERT with status='running' fails with a
unique violation rather than needing an application-level lock.

job_search_app also gets INSERT on silver.job_skill_extraction and
silver.job_skill_raw here: those tables have been owner-role-write-only
since 0023 (only the pipeline CLI wrote them), but `run_loop` calls
`write_job_skills` from the API process, using the app-role engine.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_extraction_run",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("sources", ARRAY(sa.Text()), nullable=True),
        sa.Column("countries", ARRAY(sa.Text()), nullable=True),
        sa.Column("total_pending", sa.Integer(), nullable=False),
        sa.Column(
            "extracted_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
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
            name="ck_skill_extraction_run_status",
        ),
        schema="silver",
    )
    op.create_index(
        "ux_skill_extraction_run_one_active",
        "skill_extraction_run",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        schema="silver",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON silver.skill_extraction_run "
        "TO job_search_app"
    )
    op.execute(
        "GRANT INSERT ON silver.job_skill_extraction, silver.job_skill_raw "
        "TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE INSERT ON silver.job_skill_extraction, silver.job_skill_raw "
        "FROM job_search_app"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON silver.skill_extraction_run "
        "FROM job_search_app"
    )
    op.drop_index(
        "ux_skill_extraction_run_one_active",
        table_name="skill_extraction_run",
        schema="silver",
    )
    op.drop_table("skill_extraction_run", schema="silver")
```

- [ ] **Step 2: Apply the migration and smoke-check it**

Run (from `job_search/`, with `venv` active and `.env` loaded — same setup `scripts/extract_in_batches.sh` uses):

```bash
alembic -c db/alembic.ini upgrade head
psql "$DATABASE_URL" -c "\d silver.skill_extraction_run"
psql "$DATABASE_URL" -c "
INSERT INTO silver.skill_extraction_run (status, total_pending)
VALUES ('running', 5);
INSERT INTO silver.skill_extraction_run (status, total_pending)
VALUES ('running', 3);
"
```

Expected: the table description prints all twelve columns and the check
constraint; the first `INSERT` succeeds; the second fails with
`duplicate key value violates unique constraint "ux_skill_extraction_run_one_active"`.
Clean up the test rows: `psql "$DATABASE_URL" -c "DELETE FROM silver.skill_extraction_run;"`.

- [ ] **Step 3: Commit**

```bash
git add db/migrations/versions/0025_create_skill_extraction_run.py
git commit -m "feat(job_search): add silver.skill_extraction_run table and grants"
```

---

### Task 2: Core module — run lifecycle (`start_run`, `get_active_run`, `request_cancel`, `list_filter_options`)

**Files:**
- Create: `packages/core/core/skills/extraction_run.py`
- Test: `packages/core/tests/integration/test_extraction_run.py`
- Modify: `packages/core/tests/integration/skills_fixtures.py`

**Interfaces:**
- Consumes: `core.skills.write_job_skills.count_pending_jobs(engine, *, sources=None, categories=None, countries=None) -> int`; `core.db.session.build_engine`; `tests.integration.skills_fixtures.live_owner_engine`, `live_app_engine`, `purge_fixtures`.
- Produces (used by Task 3 and Task 4):
  - `class RunError(ValueError)`, `class RunAlreadyActive(RunError)`, `class RunNotFound(RunError)`
  - `@dataclass(frozen=True) class RunStatus` with fields `run_id: uuid.UUID`, `status: str`, `sources: list[str] | None`, `countries: list[str] | None`, `total_pending: int`, `extracted_count: int`, `failed_count: int`, `cancel_requested: bool`, `error_message: str | None`, `started_at: datetime`, `updated_at: datetime`, `finished_at: datetime | None`
  - `@dataclass(frozen=True) class FilterOptions` with fields `sources: list[str]`, `countries: list[str]`
  - `start_run(engine: Engine, *, sources: list[str] | None, countries: list[str] | None) -> tuple[uuid.UUID, int]` — returns `(run_id, total_pending)`; raises `RunAlreadyActive`
  - `get_active_run(engine: Engine) -> RunStatus | None`
  - `get_run(engine: Engine, run_id: uuid.UUID) -> RunStatus | None`
  - `request_cancel(engine: Engine, run_id: uuid.UUID) -> None` — raises `RunNotFound`
  - `list_filter_options(engine: Engine) -> FilterOptions`

**Note on `write_job_skills`'s own role:** `write_job_skills` and `count_pending_jobs` are called from `run_loop` (Task 3) using the **app-role** engine (`get_app_db_engine`), not the owner-role engine the CLI uses — that's why Task 1's migration grants `job_search_app` `INSERT` on `silver.job_skill_extraction`/`silver.job_skill_raw`. This task's own tests use `live_app_engine()` for every call to a function that will run under the API in production, and `live_owner_engine()` only to set up/verify fixture data (mirroring `test_skills_router.py`'s pattern).

- [ ] **Step 1: Extend `purge_fixtures` for the new table**

In `packages/core/tests/integration/skills_fixtures.py`, inside `purge_fixtures`, add (fixture runs always use `sources=['zzfixture-source']`, a value no real run would ever use):

```python
        conn.execute(
            text(
                "DELETE FROM silver.skill_extraction_run "
                "WHERE sources = ARRAY['zzfixture-source']"
            )
        )
```

- [ ] **Step 2: Write the failing tests**

Create `packages/core/tests/integration/test_extraction_run.py`:

```python
"""Integration tests for core.skills.extraction_run's lifecycle functions
(everything except run_loop, covered separately in
test_extraction_run_loop.py) against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from tests.integration.skills_fixtures import (
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)

from core.skills.extraction_run import (
    RunAlreadyActive,
    RunNotFound,
    get_active_run,
    get_run,
    list_filter_options,
    request_cancel,
    start_run,
)
from sqlalchemy import text

_FIXTURE_SOURCES = ["zzfixture-source"]


class TestStartRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def test_starts_a_running_row_when_jobs_are_pending(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run1', 'Python required.', "
                    "'zzfixture-source', 'src-1', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        run_id, total_pending = start_run(
            self.app, sources=_FIXTURE_SOURCES, countries=None
        )
        self.assertGreaterEqual(total_pending, 1)
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "running")
        self.assertEqual(status.sources, _FIXTURE_SOURCES)
        self.assertEqual(status.total_pending, total_pending)
        self.assertEqual(status.extracted_count, 0)
        self.assertFalse(status.cancel_requested)

    def test_starts_already_completed_when_nothing_is_pending(self) -> None:
        run_id, total_pending = start_run(
            self.app, sources=["zzfixture-nonexistent"], countries=None
        )
        self.assertEqual(total_pending, 0)
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertIsNotNone(status.finished_at)

    def test_a_second_start_while_one_is_active_raises(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run2', 'Python required.', "
                    "'zzfixture-source', 'src-2', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        with self.assertRaises(RunAlreadyActive):
            start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)

    def test_get_active_run_returns_none_when_nothing_is_running(self) -> None:
        self.assertIsNone(get_active_run(self.app))

    def test_get_active_run_returns_the_running_row(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run3', 'Python required.', "
                    "'zzfixture-source', 'src-3', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        active = get_active_run(self.app)
        self.assertEqual(active.run_id, run_id)


class TestRequestCancel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def test_sets_cancel_requested_on_a_running_run(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run4', 'Python required.', "
                    "'zzfixture-source', 'src-4', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        request_cancel(self.app, run_id)
        self.assertTrue(get_run(self.app, run_id).cancel_requested)

    def test_raises_for_an_unknown_run_id(self) -> None:
        with self.assertRaises(RunNotFound):
            request_cancel(self.app, uuid.uuid4())

    def test_raises_for_a_run_that_already_finished(self) -> None:
        run_id, _ = start_run(
            self.app, sources=["zzfixture-nonexistent"], countries=None
        )
        with self.assertRaises(RunNotFound):
            request_cancel(self.app, run_id)


class TestListFilterOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def test_lists_sources_and_countries_of_pending_jobs_only(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "('fixture-job-run5', 'Python required.', "
                    "'zzfixture-source', 'src-5', "
                    "'https://example.test/x', 'Data Engineer')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, country_iso) "
                    "VALUES ('fixture-job-run5', 'ZZ')"
                )
            )
        options = list_filter_options(self.app)
        self.assertIn("zzfixture-source", options.sources)
        self.assertIn("ZZ", options.countries)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `packages/core/`): `coverage run -m unittest tests.integration.test_extraction_run -v`
Expected: `ModuleNotFoundError: No module named 'core.skills.extraction_run'`

- [ ] **Step 4: Implement `extraction_run.py`'s lifecycle functions**

Create `packages/core/core/skills/extraction_run.py`:

```python
"""Skill-extraction batch run lifecycle (Step 14 follow-up): starting,
tracking and cancelling a scoped extraction run triggered from the UI.
`run_loop` — the actual sub-batch execution — lives here too but is
implemented and tested separately (see `test_extraction_run_loop.py`)
since it needs a fake LLM adapter and a fake Ollama transport rather
than just live Postgres.

silver.skill_extraction_run has at most one `status = 'running'` row,
enforced by a partial unique index (migration 0025) — `start_run`
relies on that index's IntegrityError rather than an application-level
lock. See docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from core.skills.write_job_skills import CURRENT_PROMPT_VERSION, count_pending_jobs


class RunError(ValueError):
    """Base error for an invalid run-lifecycle operation."""


class RunAlreadyActive(RunError):
    """Raised when `start_run` is called while one run is already active."""


class RunNotFound(RunError):
    """Raised when a run id names no run, or names one that already
    finished (for operations that only apply to an active run)."""


@dataclass(frozen=True)
class RunStatus:
    """One extraction run's full state.

    Attributes:
        run_id: The run's unique id.
        status: "running", "completed", "cancelled", or "failed".
        sources: The source scope this run was started with, or None
            for every source.
        countries: The country scope this run was started with, or
            None for every country.
        total_pending: How many jobs matched the scope when the run
            started.
        extracted_count: Jobs successfully extracted so far.
        failed_count: Jobs whose extraction failed so far (retried by
            a future run, same as `write_job_skills`' own semantics).
        cancel_requested: Whether a cancel has been requested.
        error_message: Set only when `status == "failed"`.
        started_at: When the run began.
        updated_at: When progress was last recorded.
        finished_at: When the run reached a terminal status, or None
            while still running.
    """

    run_id: uuid.UUID
    status: str
    sources: list[str] | None
    countries: list[str] | None
    total_pending: int
    extracted_count: int
    failed_count: int
    cancel_requested: bool
    error_message: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class FilterOptions:
    """Source/country values available to scope a new run.

    Attributes:
        sources: Distinct `apply_source_name` values among pending jobs.
        countries: Distinct `country_iso` values among pending jobs.
    """

    sources: list[str]
    countries: list[str]


_COLUMNS = (
    "run_id, status, sources, countries, total_pending, extracted_count, "
    "failed_count, cancel_requested, error_message, started_at, updated_at, "
    "finished_at"
)
_SELECT_ACTIVE = text(
    f"SELECT {_COLUMNS} FROM silver.skill_extraction_run WHERE status = 'running'"
)
_SELECT_ONE = text(
    f"SELECT {_COLUMNS} FROM silver.skill_extraction_run WHERE run_id = :run_id"
)
_INSERT_RUNNING = text(
    "INSERT INTO silver.skill_extraction_run "
    "(status, sources, countries, total_pending) "
    "VALUES ('running', CAST(:sources AS text[]), CAST(:countries AS text[]), "
    ":total_pending) RETURNING run_id"
)
_INSERT_COMPLETED = text(
    "INSERT INTO silver.skill_extraction_run "
    "(status, sources, countries, total_pending, finished_at) "
    "VALUES ('completed', CAST(:sources AS text[]), CAST(:countries AS text[]), "
    "0, now()) RETURNING run_id"
)
_REQUEST_CANCEL = text(
    "UPDATE silver.skill_extraction_run SET cancel_requested = TRUE "
    "WHERE run_id = :run_id AND status = 'running'"
)
_SELECT_PENDING_SOURCES = text(
    "SELECT DISTINCT js.apply_source_name AS value "
    "FROM silver.job_survivorship AS js "
    "LEFT JOIN silver.job_skill_extraction AS e "
    "ON e.job_group_id = js.job_group_id AND e.prompt_version = :prompt_version "
    "WHERE e.job_group_id IS NULL AND js.winning_description IS NOT NULL "
    "ORDER BY 1"
)
_SELECT_PENDING_COUNTRIES = text(
    "SELECT DISTINCT d.country_iso AS value "
    "FROM silver.job_survivorship AS js "
    "JOIN gold.dim_job AS d ON d.job_group_id = js.job_group_id "
    "LEFT JOIN silver.job_skill_extraction AS e "
    "ON e.job_group_id = js.job_group_id AND e.prompt_version = :prompt_version "
    "WHERE e.job_group_id IS NULL AND js.winning_description IS NOT NULL "
    "AND d.country_iso IS NOT NULL "
    "ORDER BY 1"
)


def _row_to_status(row) -> RunStatus:
    """Convert one `silver.skill_extraction_run` row into a `RunStatus`.

    Args:
        row: A row from `_SELECT_ACTIVE` or `_SELECT_ONE`.

    Returns:
        The row as a `RunStatus`.
    """
    return RunStatus(
        run_id=row.run_id,
        status=row.status,
        sources=row.sources,
        countries=row.countries,
        total_pending=row.total_pending,
        extracted_count=row.extracted_count,
        failed_count=row.failed_count,
        cancel_requested=row.cancel_requested,
        error_message=row.error_message,
        started_at=row.started_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def start_run(
    engine: Engine,
    *,
    sources: list[str] | None,
    countries: list[str] | None,
) -> tuple[uuid.UUID, int]:
    """Start a new extraction run for the given scope.

    If nothing is pending in scope, the run is recorded already
    `completed` (so it shows in history and the caller can tell "ran,
    found nothing to do" apart from "never started") and the caller
    should not schedule `run_loop` for it.

    Args:
        engine: The app-role engine.
        sources: Restrict to these `apply_source_name` values, or None
            for every source.
        countries: Restrict to these `country_iso` values, or None for
            every country.

    Returns:
        `(run_id, total_pending)`.

    Raises:
        RunAlreadyActive: If a run is already `running`.
    """
    total_pending = count_pending_jobs(engine, sources=sources, countries=countries)
    params = {"sources": sources, "countries": countries}
    if total_pending == 0:
        with engine.begin() as conn:
            run_id = conn.execute(_INSERT_COMPLETED, params).scalar_one()
        return run_id, 0
    try:
        with engine.begin() as conn:
            run_id = conn.execute(
                _INSERT_RUNNING, {**params, "total_pending": total_pending}
            ).scalar_one()
    except IntegrityError as exc:
        raise RunAlreadyActive(
            "an extraction run is already active"
        ) from exc
    return run_id, total_pending


def get_active_run(engine: Engine) -> RunStatus | None:
    """Return the currently active run, if any.

    Args:
        engine: The app-role engine.

    Returns:
        The active run's status, or None if no run is active.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ACTIVE).first()
    return _row_to_status(row) if row is not None else None


def get_run(engine: Engine, run_id: uuid.UUID) -> RunStatus | None:
    """Return one run's status by id, active or finished.

    Args:
        engine: The app-role engine.
        run_id: The run to look up.

    Returns:
        The run's status, or None if `run_id` is unknown.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ONE, {"run_id": run_id}).first()
    return _row_to_status(row) if row is not None else None


def request_cancel(engine: Engine, run_id: uuid.UUID) -> None:
    """Ask a running run to stop after its current sub-batch.

    Args:
        engine: The app-role engine.
        run_id: The run to cancel.

    Raises:
        RunNotFound: If `run_id` is unknown, or names a run that is
            not currently `running`.
    """
    with engine.begin() as conn:
        result = conn.execute(_REQUEST_CANCEL, {"run_id": run_id})
    if result.rowcount == 0:
        raise RunNotFound(f"no active run with id {run_id}")


def list_filter_options(engine: Engine) -> FilterOptions:
    """List the source/country values a new run could be scoped to.

    Only values seen among jobs still pending extraction — a source or
    country with nothing left to do would otherwise show as a choice
    that starts a run and immediately completes with nothing done.

    Args:
        engine: The app-role engine.

    Returns:
        The available `FilterOptions`.
    """
    params = {"prompt_version": CURRENT_PROMPT_VERSION}
    with engine.connect() as conn:
        sources = [
            row.value for row in conn.execute(_SELECT_PENDING_SOURCES, params)
        ]
        countries = [
            row.value for row in conn.execute(_SELECT_PENDING_COUNTRIES, params)
        ]
    return FilterOptions(sources=sources, countries=countries)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `coverage run -m unittest tests.integration.test_extraction_run -v`
Expected: all tests PASS. If `test_a_second_start_while_one_is_active_raises` fails with a permission error instead of the expected behaviour, re-check Task 1's migration was applied (`alembic -c db/alembic.ini current` should show `0025`).

- [ ] **Step 6: Lint and format**

```bash
ruff check packages/core/core/skills/extraction_run.py packages/core/tests/integration/test_extraction_run.py
isort packages/core/core/skills/extraction_run.py packages/core/tests/integration/test_extraction_run.py packages/core/tests/integration/skills_fixtures.py
black packages/core/core/skills/extraction_run.py packages/core/tests/integration/test_extraction_run.py packages/core/tests/integration/skills_fixtures.py
```

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/skills/extraction_run.py \
  packages/core/tests/integration/test_extraction_run.py \
  packages/core/tests/integration/skills_fixtures.py
git commit -m "feat(job_search): add extraction run lifecycle (start/status/cancel/filters)"
```

---

### Task 3: Core module — the batch loop (`run_loop`)

**Files:**
- Modify: `packages/core/core/skills/extraction_run.py`
- Test: `packages/core/tests/integration/test_extraction_run_loop.py`

**Interfaces:**
- Consumes: `start_run`, `get_run`, `RunStatus` (Task 2); `core.skills.write_job_skills.write_job_skills(engine, *, adapters, limit=None, sources=None, categories=None, countries=None) -> WriteSummary`; `core.skills.write_job_skills.count_pending_jobs`; `tests.skills_fakes.FakeAdapter`.
- Produces (used by Task 4):
  - `DEFAULT_BATCH_SIZE: int = 30`, `DEFAULT_PAUSE_SECONDS: float = 10.0` (module constants)
  - `run_loop(run_id: uuid.UUID, engine: Engine, *, adapters: dict[str, LLMAdapter], http_client: httpx.Client, ollama_base_url: str, model: str, sources: list[str] | None, countries: list[str] | None, batch_size: int = DEFAULT_BATCH_SIZE, pause_seconds: float = DEFAULT_PAUSE_SECONDS) -> None`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_extraction_run_loop.py`:

```python
"""Integration tests for core.skills.extraction_run.run_loop against live
Postgres, with a fake LLM adapter and a fake Ollama HTTP transport (no
real model, matching how test_skills_jd_extract.py avoids one)."""

from __future__ import annotations

import json
import unittest
import uuid

import httpx
from tests.integration.skills_fixtures import (
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)
from tests.skills_fakes import FakeAdapter

from core.skills.extraction_run import get_run, run_loop, start_run
from sqlalchemy import text

_FIXTURE_SOURCES = ["zzfixture-source"]


def _reply(*skills: str) -> str:
    return json.dumps(
        {"skills": [{"skill": s, "requirement_level": "must_have"} for s in skills]}
    )


class _UnloadRecordingTransport(httpx.MockTransport):
    """Records every unload POST and answers it like real Ollama does."""

    def __init__(self) -> None:
        self.unload_calls: list[dict] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.unload_calls.append(body)
        return httpx.Response(200, json={"done_reason": "unload"})


class TestRunLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)

    def tearDown(self) -> None:
        purge_fixtures(self.owner)

    def _add_survivor(self, job: str) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "(:g, 'Python required.', 'zzfixture-source', :g, "
                    "'https://example.test/x', 'Data Engineer')"
                ),
                {"g": job},
            )

    def test_runs_to_completion_and_unloads_between_sub_batches(self) -> None:
        for i in range(3):
            self._add_survivor(f"fixture-job-loop{i}")
        run_id, total_pending = start_run(
            self.app, sources=_FIXTURE_SOURCES, countries=None
        )
        self.assertEqual(total_pending, 3)
        adapter = FakeAdapter(_reply("Python"))
        transport = _UnloadRecordingTransport()
        http_client = httpx.Client(transport=transport)
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=30,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.extracted_count, 3)
        self.assertIsNotNone(status.finished_at)
        self.assertEqual(len(transport.unload_calls), 1)
        self.assertEqual(
            transport.unload_calls[0],
            {"model": "test-model", "keep_alive": 0},
        )

    def test_advances_across_multiple_sub_batches(self) -> None:
        for i in range(5):
            self._add_survivor(f"fixture-job-multi{i}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        adapter = FakeAdapter(_reply("Python"))
        http_client = httpx.Client(transport=_UnloadRecordingTransport())
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=2,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.extracted_count, 5)

    def test_a_cancel_requested_before_the_loop_starts_stops_it_after_one_sub_batch(
        self,
    ) -> None:
        for i in range(5):
            self._add_survivor(f"fixture-job-cancel{i}")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        with self.app.begin() as conn:
            conn.execute(
                text(
                    "UPDATE silver.skill_extraction_run "
                    "SET cancel_requested = TRUE WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        adapter = FakeAdapter(_reply("Python"))
        http_client = httpx.Client(transport=_UnloadRecordingTransport())
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=2,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual(status.extracted_count, 2)

    def test_an_unreachable_ollama_marks_the_run_failed(self) -> None:
        self._add_survivor("fixture-job-fail1")
        run_id, _ = start_run(self.app, sources=_FIXTURE_SOURCES, countries=None)
        adapter = FakeAdapter(_reply("Python"))

        def _raise(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        http_client = httpx.Client(transport=httpx.MockTransport(_raise))
        run_loop(
            run_id,
            self.app,
            adapters={"ollama": adapter},
            http_client=http_client,
            ollama_base_url="http://fake-ollama:11434",
            model="test-model",
            sources=_FIXTURE_SOURCES,
            countries=None,
            batch_size=30,
            pause_seconds=0,
        )
        status = get_run(self.app, run_id)
        self.assertEqual(status.status, "failed")
        self.assertIn("connection refused", status.error_message)
        # The job itself was still extracted and committed before the
        # unload call failed — only the run's own bookkeeping stops.
        self.assertEqual(status.extracted_count, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `coverage run -m unittest tests.integration.test_extraction_run_loop -v`
Expected: `ImportError: cannot import name 'run_loop' from 'core.skills.extraction_run'`

- [ ] **Step 3: Implement `run_loop`**

In `packages/core/core/skills/extraction_run.py`, replace the existing import block (everything from `from __future__ import annotations` down to the `RunError` class) with:

```python
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from core.llm.types import LLMAdapter
from core.skills.write_job_skills import (
    CURRENT_PROMPT_VERSION,
    count_pending_jobs,
    write_job_skills,
)
```

Then, at module level, after the existing `FilterOptions` dataclass and its `_COLUMNS`/`_SELECT_ACTIVE`/etc. constants (i.e. append the following to the end of the file):

```python
DEFAULT_BATCH_SIZE = 30
"""Jobs per sub-batch before the Ollama model is unloaded and the run
pauses — matches scripts/extract_in_batches.sh's proven-safe default.
Not user-configurable (spec's Non-goals): a higher value is what grew
Ollama's resident memory and froze the host machine once already."""

DEFAULT_PAUSE_SECONDS = 10.0
"""Pause after each unload before the next sub-batch — same value as
scripts/extract_in_batches.sh."""

_UPDATE_PROGRESS = text(
    "UPDATE silver.skill_extraction_run SET "
    "extracted_count = extracted_count + :extracted, "
    "failed_count = failed_count + :failed, "
    "updated_at = now() "
    "WHERE run_id = :run_id"
)
_SELECT_CANCEL_REQUESTED = text(
    "SELECT cancel_requested FROM silver.skill_extraction_run "
    "WHERE run_id = :run_id"
)
_FINISH_RUN = text(
    "UPDATE silver.skill_extraction_run SET status = :status, "
    "error_message = :error_message, finished_at = now(), updated_at = now() "
    "WHERE run_id = :run_id"
)


def _record_progress(engine: Engine, run_id: uuid.UUID, extracted: int, failed: int) -> None:
    """Add one sub-batch's counts onto a run's running totals.

    Args:
        engine: The app-role engine.
        run_id: The run to update.
        extracted: Jobs extracted in this sub-batch.
        failed: Jobs that failed in this sub-batch.
    """
    with engine.begin() as conn:
        conn.execute(
            _UPDATE_PROGRESS,
            {"run_id": run_id, "extracted": extracted, "failed": failed},
        )


def _is_cancel_requested(engine: Engine, run_id: uuid.UUID) -> bool:
    """Check whether a run's cancel flag has been set.

    Args:
        engine: The app-role engine.
        run_id: The run to check.

    Returns:
        The current `cancel_requested` value.
    """
    with engine.connect() as conn:
        return conn.execute(
            _SELECT_CANCEL_REQUESTED, {"run_id": run_id}
        ).scalar_one()


def _finish_run(
    engine: Engine, run_id: uuid.UUID, *, status: str, error_message: str | None = None
) -> None:
    """Mark a run terminal.

    Args:
        engine: The app-role engine.
        run_id: The run to finish.
        status: "completed", "cancelled", or "failed".
        error_message: Set when `status == "failed"`.
    """
    with engine.begin() as conn:
        conn.execute(
            _FINISH_RUN,
            {"run_id": run_id, "status": status, "error_message": error_message},
        )


def _unload_model(http_client: httpx.Client, ollama_base_url: str, model: str) -> None:
    """Ask Ollama to free the model's memory immediately.

    Sends `keep_alive: 0` with no `prompt`, which unloads rather than
    running a completion — verified to behave identically whether
    Ollama is native or Docker-hosted, since it's a plain HTTP call.

    Args:
        http_client: The client to issue the request with.
        ollama_base_url: Ollama's base URL, e.g. "http://ollama:11434".
        model: The model tag to unload, e.g. "llama3.1:8b".

    Raises:
        httpx.HTTPError: If Ollama is unreachable or returns an error.
    """
    response = http_client.post(
        f"{ollama_base_url}/api/generate",
        json={"model": model, "keep_alive": 0},
    )
    response.raise_for_status()


def run_loop(
    run_id: uuid.UUID,
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    http_client: httpx.Client,
    ollama_base_url: str,
    model: str,
    sources: list[str] | None,
    countries: list[str] | None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
) -> None:
    """Run a started extraction run to completion, cancellation, or failure.

    Scheduled as a FastAPI `BackgroundTasks` callback by `POST
    /skills/extraction-runs` — runs off the request/response cycle.
    Repeats a bounded `write_job_skills` sub-batch, records its counts,
    unloads the Ollama model, and pauses, until nothing is left pending
    in scope or a cancel is requested. Never raises — any exception
    from a sub-batch (a hard failure like a lost DB connection or an
    unreachable Ollama; an individual job's own failure is already
    handled inside `write_job_skills` and never raises) marks the run
    `failed` and stops.

    Args:
        run_id: The run to execute — must already be `running` (i.e.
            `start_run` returned a non-zero `total_pending`).
        engine: The app-role engine.
        adapters: Every available LLM adapter, keyed by provider.
        http_client: The client used for the Ollama unload call.
        ollama_base_url: Ollama's base URL.
        model: The extraction model tag to unload between sub-batches —
            resolve via `core.llm.task_config.load_task_config
            ("skill_extraction").model`, never hardcoded.
        sources: The run's source scope.
        countries: The run's country scope.
        batch_size: Jobs per sub-batch.
        pause_seconds: Pause after each unload.
    """
    try:
        while True:
            summary = write_job_skills(
                engine,
                adapters=adapters,
                limit=batch_size,
                sources=sources,
                countries=countries,
            )
            _record_progress(
                engine, run_id, summary.extracted_jobs, summary.failed_jobs
            )
            _unload_model(http_client, ollama_base_url, model)
            time.sleep(pause_seconds)
            if _is_cancel_requested(engine, run_id):
                _finish_run(engine, run_id, status="cancelled")
                return
            remaining = count_pending_jobs(engine, sources=sources, countries=countries)
            if remaining == 0:
                _finish_run(engine, run_id, status="completed")
                return
    except Exception as exc:  # noqa: BLE001 — any hard failure ends the run
        _finish_run(engine, run_id, status="failed", error_message=str(exc))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `coverage run -m unittest tests.integration.test_extraction_run_loop -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full Step 14 test suite to check for regressions**

Run (from `packages/core/`): `coverage run -m unittest discover && coverage report -m`
Expected: no failures; `core/skills/extraction_run.py` shows in the coverage report.

- [ ] **Step 6: Lint and format**

```bash
ruff check packages/core/core/skills/extraction_run.py packages/core/tests/integration/test_extraction_run_loop.py
isort packages/core/core/skills/extraction_run.py packages/core/tests/integration/test_extraction_run_loop.py
black packages/core/core/skills/extraction_run.py packages/core/tests/integration/test_extraction_run_loop.py
```

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/skills/extraction_run.py \
  packages/core/tests/integration/test_extraction_run_loop.py
git commit -m "feat(job_search): add run_loop, the batch runner's sub-batch/unload/pause cycle"
```

---

### Task 4: API router — `apps/api/app/routers/extraction_runs.py`

**Files:**
- Create: `apps/api/app/routers/extraction_runs.py`
- Modify: `apps/api/app/main.py`
- Test: `packages/core/tests/integration/test_extraction_runs_router.py`

**Interfaces:**
- Consumes: `core.skills.extraction_run.{start_run, get_active_run, get_run, request_cancel, list_filter_options, run_loop, RunAlreadyActive, RunNotFound, RunStatus, FilterOptions}` (Tasks 2–3); `app.dependencies.{get_app_db_engine, get_llm_adapters, get_http_client}`; `core.llm.task_config.load_task_config`; `core.settings.get_settings`.
- Produces (used by Task 5):
  - `GET /skills/extraction-runs/filters` → `{"sources": [...], "countries": [...]}`
  - `GET /skills/extraction-runs/pending-count?sources=&countries=` → `{"pending": int}`
  - `POST /skills/extraction-runs` (body `{"sources": [...] | null, "countries": [...] | null}`) → `202 {"run_id": "..."}`, or `409` if one is active
  - `GET /skills/extraction-runs/active` → a run-status object, or `null`
  - `POST /skills/extraction-runs/{run_id}/cancel` → `{"status": "ok"}`, or `404`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/integration/test_extraction_runs_router.py`:

```python
"""Integration tests for the skill-extraction-run API (no mocking the
database — .claude/rules/python-testing.md)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import (  # noqa: E402
    get_app_db_engine,
    get_http_client,
    get_llm_adapters,
)
from app.routers import extraction_runs  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.integration.skills_fixtures import (  # noqa: E402
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)
from tests.skills_fakes import FakeAdapter  # noqa: E402

from core.skills.extraction_run import start_run  # noqa: E402

app = FastAPI()
app.include_router(extraction_runs.router)

_FIXTURE_SOURCES = ["zzfixture-source"]


def _fake_ollama_unload_response(request: httpx.Request) -> httpx.Response:
    """Answer an Ollama unload POST the way a real server would.

    `run_loop` runs as a real FastAPI `BackgroundTasks` callback in
    these tests (not called directly, unlike
    `test_extraction_run_loop.py`), so its unload call must be faked
    at the `get_http_client` dependency, not just skipped, or it would
    try to reach a real network address and fail every run.

    Args:
        request: The intercepted request.

    Returns:
        A 200 response shaped like Ollama's own unload reply.
    """
    json.loads(request.content)  # sanity: body is valid JSON
    return httpx.Response(200, json={"done_reason": "unload"})


class TestExtractionRunsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": FakeAdapter('{"skills": []}')
        }
        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(_fake_ollama_unload_response)
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        purge_fixtures(self.owner)

    def _add_survivor(self, job: str) -> None:
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship (job_group_id, "
                    "winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, "
                    "apply_title_for_display) VALUES "
                    "(:g, 'Python required.', 'zzfixture-source', :g, "
                    "'https://example.test/x', 'Data Engineer')"
                ),
                {"g": job},
            )

    def test_filters_lists_pending_sources(self) -> None:
        self._add_survivor("fixture-job-api1")
        body = self.client.get("/skills/extraction-runs/filters").json()
        self.assertIn("zzfixture-source", body["sources"])

    def test_pending_count_reflects_scope(self) -> None:
        self._add_survivor("fixture-job-api2")
        body = self.client.get(
            "/skills/extraction-runs/pending-count",
            params={"sources": _FIXTURE_SOURCES},
        ).json()
        self.assertEqual(body["pending"], 1)

    def test_start_completes_synchronously_and_updates_the_run_row(self) -> None:
        # Starlette's TestClient runs a BackgroundTasks callback before
        # the triggering request returns (same behaviour
        # test_cv_router.py's POST /cv/extract tests already rely on
        # — no polling needed, unlike a real deployment where run_loop
        # keeps going after the response is sent).
        self._add_survivor("fixture-job-api3")
        start = self.client.post(
            "/skills/extraction-runs", json={"sources": _FIXTURE_SOURCES}
        )
        self.assertEqual(start.status_code, 202)
        run_id = start.json()["run_id"]

        self.assertIsNone(self.client.get("/skills/extraction-runs/active").json())

        with self.owner.connect() as conn:
            status, extracted = conn.execute(
                text(
                    "SELECT status, extracted_count "
                    "FROM silver.skill_extraction_run WHERE run_id = :r"
                ),
                {"r": run_id},
            ).one()
        self.assertEqual(status, "completed")
        self.assertEqual(extracted, 1)

    def test_start_while_one_is_active_returns_409(self) -> None:
        # Starts the first run directly through the core function
        # (bypassing the router) so it's left `running` deterministically
        # — going through the router's own POST would run it to
        # completion synchronously (see the test above) before a
        # "second" call could ever race it.
        self._add_survivor("fixture-job-api4")
        start_run(self.app_engine, sources=_FIXTURE_SOURCES, countries=None)

        response = self.client.post(
            "/skills/extraction-runs", json={"sources": _FIXTURE_SOURCES}
        )
        self.assertEqual(response.status_code, 409)

    def test_cancel_unknown_run_returns_404(self) -> None:
        response = self.client.post(
            "/skills/extraction-runs/"
            "00000000-0000-0000-0000-000000000000/cancel"
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `coverage run -m unittest tests.integration.test_extraction_runs_router -v`
Expected: `ModuleNotFoundError: No module named 'app.routers.extraction_runs'`

- [ ] **Step 3: Implement the router**

Create `apps/api/app/routers/extraction_runs.py`:

```python
"""Skill-extraction batch run endpoints (Step 14 follow-up): scope
selection, starting a run, its live status, and cancellation. See
docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
from app.dependencies import get_app_db_engine, get_http_client, get_llm_adapters
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Engine

from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.settings import get_settings
from core.skills.extraction_run import (
    FilterOptions,
    RunAlreadyActive,
    RunNotFound,
    RunStatus,
    get_active_run,
    list_filter_options,
    request_cancel,
    run_loop,
    start_run,
)
from core.skills.write_job_skills import count_pending_jobs

router = APIRouter(prefix="/skills/extraction-runs")


class FilterOptionsModel(BaseModel):
    """Available scope values (see `core.skills.extraction_run.FilterOptions`)."""

    sources: list[str]
    countries: list[str]


class StartRunRequest(BaseModel):
    """A new run's requested scope.

    Attributes:
        sources: Restrict to these sources, or None for every source.
        countries: Restrict to these countries, or None for every country.
    """

    sources: list[str] | None = None
    countries: list[str] | None = None


class StartRunResponse(BaseModel):
    """The id of a newly started run."""

    run_id: uuid.UUID


class RunStatusModel(BaseModel):
    """One run's status over the wire (see `core.skills.extraction_run.RunStatus`)."""

    run_id: uuid.UUID
    status: str
    sources: list[str] | None
    countries: list[str] | None
    total_pending: int
    extracted_count: int
    failed_count: int
    cancel_requested: bool
    error_message: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None


def _to_model(status: RunStatus) -> RunStatusModel:
    """Convert a `RunStatus` dataclass into its API model.

    Args:
        status: The run status to convert.

    Returns:
        The equivalent `RunStatusModel`.
    """
    return RunStatusModel(
        run_id=status.run_id,
        status=status.status,
        sources=status.sources,
        countries=status.countries,
        total_pending=status.total_pending,
        extracted_count=status.extracted_count,
        failed_count=status.failed_count,
        cancel_requested=status.cancel_requested,
        error_message=status.error_message,
        started_at=status.started_at,
        updated_at=status.updated_at,
        finished_at=status.finished_at,
    )


@router.get("/filters", response_model=FilterOptionsModel)
def get_filters(engine: Engine = Depends(get_app_db_engine)) -> FilterOptionsModel:
    """List the source/country values a new run could be scoped to.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The available scope values.
    """
    options: FilterOptions = list_filter_options(engine)
    return FilterOptionsModel(sources=options.sources, countries=options.countries)


@router.get("/pending-count")
def get_pending_count(
    sources: list[str] | None = Query(default=None),
    countries: list[str] | None = Query(default=None),
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, int]:
    """Count how many jobs a run with this scope would process.

    Args:
        sources: Restrict to these sources, or None for every source.
        countries: Restrict to these countries, or None for every country.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"pending": <count>}`.
    """
    pending = count_pending_jobs(engine, sources=sources, countries=countries)
    return {"pending": pending}


@router.post("", response_model=StartRunResponse, status_code=202)
def post_start_run(
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
    http_client: httpx.Client = Depends(get_http_client),
) -> StartRunResponse:
    """Start a scoped extraction run in the background.

    Args:
        request: The requested scope.
        background_tasks: Injected by FastAPI — schedules `run_loop`
            after this response is sent.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.
        http_client: Injected via `get_http_client`, used for the
            Ollama unload call between sub-batches.

    Returns:
        The new run's id.

    Raises:
        fastapi.HTTPException: 409 if a run is already active.
    """
    try:
        run_id, total_pending = start_run(
            engine, sources=request.sources, countries=request.countries
        )
    except RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if total_pending > 0:
        task_config = load_task_config("skill_extraction")
        background_tasks.add_task(
            run_loop,
            run_id,
            engine,
            adapters=adapters,
            http_client=http_client,
            ollama_base_url=get_settings().ollama_base_url,
            model=task_config.model,
            sources=request.sources,
            countries=request.countries,
        )
    return StartRunResponse(run_id=run_id)


@router.get("/active", response_model=RunStatusModel | None)
def get_active(engine: Engine = Depends(get_app_db_engine)) -> RunStatusModel | None:
    """Return the currently active run, if any.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The active run's status, or None.
    """
    active = get_active_run(engine)
    return _to_model(active) if active is not None else None


@router.post("/{run_id}/cancel")
def post_cancel(
    run_id: uuid.UUID, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Ask an active run to stop after its current sub-batch.

    Args:
        run_id: The run to cancel.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"status": "ok"}`.

    Raises:
        fastapi.HTTPException: 404 if `run_id` is unknown or not active.
    """
    try:
        request_cancel(engine, run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}
```

- [ ] **Step 4: Mount the router**

In `apps/api/app/main.py`, add the import and mount:

```python
from app.routers import classification, cv, dedup, extraction_runs, ingest, skills
```

```python
app.include_router(extraction_runs.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `coverage run -m unittest tests.integration.test_extraction_runs_router -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the full Step 14 test suite to check for regressions**

Run (from `packages/core/`): `coverage run -m unittest discover && coverage report -m`
Expected: no failures.

- [ ] **Step 7: Lint and format**

```bash
ruff check apps/api/app/routers/extraction_runs.py apps/api/app/main.py packages/core/tests/integration/test_extraction_runs_router.py
isort apps/api/app/routers/extraction_runs.py apps/api/app/main.py packages/core/tests/integration/test_extraction_runs_router.py
black apps/api/app/routers/extraction_runs.py apps/api/app/main.py packages/core/tests/integration/test_extraction_runs_router.py
```

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/routers/extraction_runs.py apps/api/app/main.py \
  packages/core/tests/integration/test_extraction_runs_router.py
git commit -m "feat(job_search): add the skill-extraction-run API"
```

---

### Task 5: UI — `apps/ui/app/pages/7_Skill_Extraction_Runner.py`

**Files:**
- Create: `apps/ui/app/pages/7_Skill_Extraction_Runner.py`

**Interfaces:**
- Consumes: `GET /skills/extraction-runs/filters`, `GET /skills/extraction-runs/pending-count`, `POST /skills/extraction-runs`, `GET /skills/extraction-runs/active`, `POST /skills/extraction-runs/{run_id}/cancel` (Task 4); `core.settings.get_settings().api_base_url` (same pattern as `6_Skill_Review.py` / `5_CV_Editor.py`).

This is a manual-verification-only deliverable (no UI test precedent in this repo — same as `5_CV_Editor.py`); Task 6 covers the manual check.

- [ ] **Step 1: Write the page**

Create `apps/ui/app/pages/7_Skill_Extraction_Runner.py`:

```python
"""Skill-extraction batch runner (Step 14 follow-up) — trigger a scoped
extraction run from the UI instead of a shell command, safe on both
native and Docker-hosted Ollama. See docs/superpowers/specs/
2026-09-23-skill-extraction-batch-runner-design.md.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Skill Extraction Runner", layout="wide")
st.title("Skill Extraction Runner")

_API = get_settings().api_base_url
_STALE_AFTER_SECONDS = 120

_USER_GUIDE = """
Runs `extract-job-skills` for the sources/countries you pick, in
bounded batches of 30 jobs with a pause between each — the same
protection `scripts/extract_in_batches.sh` uses, so it's safe to run
even though it can take a long time (a local model on CPU takes
minutes per job).

Only one run can be active at a time. **Stop** finishes the job
currently in progress, then halts — nothing already extracted is
undone, and a stopped run can always be restarted later to pick up
where it left off.

If a run shows as **possibly stalled**, the API process restarted
while it was running (its progress hasn't moved in over two minutes).
The jobs it already extracted are safe; **Cancel** just clears the
stuck row so a new run can start.
"""


def _get(path: str, params: dict | None = None) -> dict:
    """GET a JSON endpoint on the API.

    Args:
        path: The endpoint path.
        params: Query parameters.

    Returns:
        The parsed JSON body.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    response = httpx.get(f"{_API}{path}", params=params, timeout=10.0)
    response.raise_for_status()
    return response.json()


def _post(path: str, payload: dict | None = None) -> httpx.Response:
    """POST an action to the API.

    Args:
        path: The endpoint path.
        payload: The JSON body, if any.

    Returns:
        The raw response (the caller decides how to interpret status).
    """
    return httpx.post(f"{_API}{path}", json=payload, timeout=10.0)


def _error_message(exc: httpx.HTTPStatusError) -> str:
    """Build a readable message from an HTTP error response.

    Args:
        exc: The status error raised for the response.

    Returns:
        The API's `detail`, or the exception text if there isn't one.
    """
    try:
        detail = exc.response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else str(exc)


def _is_stale(updated_at: str) -> bool:
    """Check whether a run's progress hasn't moved recently.

    Args:
        updated_at: The run's `updated_at` timestamp, ISO 8601.

    Returns:
        True if more than `_STALE_AFTER_SECONDS` have passed since
        `updated_at` — most likely because the API process restarted
        mid-run.
    """
    last_update = datetime.fromisoformat(updated_at)
    age = datetime.now(timezone.utc) - last_update
    return age.total_seconds() > _STALE_AFTER_SECONDS


def _render_active_run(run: dict) -> None:
    """Render a running (or possibly stalled) run's progress.

    Args:
        run: A `GET /skills/extraction-runs/active` response body.
    """
    total = run["total_pending"] or 1
    done = run["extracted_count"] + run["failed_count"]
    st.progress(
        min(done / total, 1.0),
        text=f"{run['extracted_count']} extracted, "
        f"{run['failed_count']} failed, out of {run['total_pending']}",
    )
    if _is_stale(run["updated_at"]):
        st.warning(
            "This run's progress hasn't updated in over two minutes — the "
            "API may have restarted. Already-extracted jobs are safe; "
            "Cancel to clear this run so a new one can start."
        )
    if st.button("Stop", key="stop_run"):
        response = _post(f"/skills/extraction-runs/{run['run_id']}/cancel")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            st.error(f"Failed to stop: {_error_message(exc)}")
        else:
            st.rerun()


def _render_start_form() -> None:
    """Render the source/country scope picker and Start button."""
    try:
        filters = _get("/skills/extraction-runs/filters")
    except httpx.HTTPError as exc:
        st.error(f"Failed to load filter options: {exc}")
        return
    sources = st.multiselect("Sources", options=filters["sources"])
    countries = st.multiselect("Countries", options=filters["countries"])
    params = {}
    if sources:
        params["sources"] = sources
    if countries:
        params["countries"] = countries
    try:
        pending = _get("/skills/extraction-runs/pending-count", params=params)
        st.write(f"{pending['pending']} job(s) match this scope.")
    except httpx.HTTPError as exc:
        st.error(f"Failed to count pending jobs: {exc}")
        return
    if st.button("Start", key="start_run", disabled=pending["pending"] == 0):
        response = _post(
            "/skills/extraction-runs",
            {"sources": sources or None, "countries": countries or None},
        )
        if response.status_code == 409:
            st.error("A run is already active.")
        else:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                st.error(f"Failed to start: {_error_message(exc)}")
            else:
                st.rerun()


with st.expander("User Guide", expanded=False):
    st.markdown(_USER_GUIDE)

try:
    active_run = _get("/skills/extraction-runs/active")
except httpx.HTTPError as exc:
    st.error(f"Failed to load run status: {exc}")
    active_run = None

if active_run is not None:
    _render_active_run(active_run)
    if active_run["status"] == "running":
        time.sleep(5)
        st.rerun()
else:
    _render_start_form()
```

- [ ] **Step 2: Lint and format**

```bash
ruff check apps/ui/app/pages/7_Skill_Extraction_Runner.py
isort apps/ui/app/pages/7_Skill_Extraction_Runner.py
black apps/ui/app/pages/7_Skill_Extraction_Runner.py
```

- [ ] **Step 3: Commit**

```bash
git add apps/ui/app/pages/7_Skill_Extraction_Runner.py
git commit -m "feat(job_search): add the Skill Extraction Runner UI page"
```

---

### Task 6: Manual verification and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/esco.md`

**Interfaces:** None — this task verifies Tasks 1–5 end-to-end and documents the result.

- [ ] **Step 1: Start the full Docker stack and verify against Docker-hosted Ollama**

```bash
docker compose up -d postgres api ui ollama
docker compose exec ollama ollama pull llama3.1:8b   # if not already pulled
alembic -c db/alembic.ini upgrade head
```

Open `http://localhost:8501/Skill_Extraction_Runner`. Confirm:
- The page loads, the User Guide expander shows the text from Task 5.
- Sources/countries multi-selects are populated from real pending data (compare against `docker compose run --rm pipeline python -m app.cli extract-job-skills --limit 0` style scope counts, or query `silver.job_skill_mapping`/`gold.dim_job` directly if there's nothing pending — in that case, temporarily clear one job's `silver.job_skill_extraction` row in a scratch/dev DB to create pending work for this check, then restore it).
- Picking a scope updates the "N job(s) match" line.
- Start begins a run; the page shows a progress bar that advances every ~5s.
- Opening a second browser tab to the same page and clicking Start there shows the 409 error, not a second run.
- Stop halts the run after its current sub-batch; `extracted_count` stops climbing and `status` becomes `cancelled` (check via `psql "$DATABASE_URL" -c "SELECT status, extracted_count FROM silver.skill_extraction_run ORDER BY started_at DESC LIMIT 1;"`).
- Restarting the run picks up where it left off (fewer pending jobs than the first run's `total_pending`).

- [ ] **Step 2: Verify the stale-run indicator**

While a run is `running`, restart the api container (`docker compose restart api`) and reload the page within the next two minutes — the run should still show as running (not yet stale), then, after two minutes with no progress update, show the "possibly stalled" warning. Click Cancel and confirm a new run can then be started.

- [ ] **Step 3: Update `README.md`**

Add a new subsection near the existing Ollama/batch-restart documentation (locate it via `grep -n "extract_in_batches" README.md`), explaining:
- The Skill Extraction Runner page is now the primary way to run a scoped extraction batch, and the only way that's safe when Ollama runs in Docker (`scripts/extract_in_batches.sh` remains for a native-Ollama, CLI-only workflow — it is unchanged and still works).
- It uses the same bounded-sub-batch-plus-unload protection, over HTTP (`keep_alive: 0`) instead of the `ollama` CLI, which is why it works for both native and Docker-hosted Ollama.
- Batch size (30) and pause (10s) are fixed, not configurable from the UI, by design.
- A run's status is persisted in `silver.skill_extraction_run`, so it survives an API restart — but see the "possibly stalled" note in the page's own User Guide.
- Link to `docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md` for the full design.

- [ ] **Step 4: Update `docs/esco.md`**

In the "Scoping extraction" section (`grep -n "Scoping extraction" docs/esco.md`), add a pointer at the top: "The Skill Extraction Runner UI page is the recommended way to run a scoped batch interactively; the `--source`/`--category`/`--country` CLI flags documented below remain for scripted/CI use."

- [ ] **Step 5: Commit**

```bash
git add README.md docs/esco.md
git commit -m "docs(job_search): document the Skill Extraction Runner UI page"
```

---

## Self-Review Notes

- **Spec coverage:** data model (Task 1), core lifecycle + run_loop (Tasks 2–3), API (Task 4), UI (Task 5), error handling / stale-run UX (Tasks 3, 5), testing (every task), README/docs cross-reference (Task 6) — every spec section maps to a task.
- **Permissions gap caught during planning:** the spec didn't originally call out that `job_search_app` lacked `INSERT` on `silver.job_skill_extraction`/`silver.job_skill_raw` (only the owner role wrote them before now) — Task 1's migration grants it; Task 2/3's tests deliberately use `live_app_engine()` for every extraction-path call specifically to catch a regression here.
- **Type consistency:** `RunStatus`/`FilterOptions` field names match between `extraction_run.py` (Task 2/3) and `RunStatusModel`/`FilterOptionsModel` (Task 4) and the UI's dict access (Task 5) — `run_id`, `status`, `sources`, `countries`, `total_pending`, `extracted_count`, `failed_count`, `cancel_requested`, `error_message`, `started_at`, `updated_at`, `finished_at` used consistently throughout.
