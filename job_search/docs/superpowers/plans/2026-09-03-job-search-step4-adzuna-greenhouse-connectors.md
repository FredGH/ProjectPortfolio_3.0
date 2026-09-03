# Step 4 — Adzuna, Reed and Greenhouse Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add all three of Step 4's real, working connectors — Adzuna
(keyed REST, paginated), Reed (keyed REST, differently paginated), and
Greenhouse (keyless, per-company JSON board) — on top of Step 3's
`Connector` protocol and shared `run_connector` runner, plus the
`target_company` registry Greenhouse reads from.

**Architecture:** All three connectors are plain classes satisfying
`core.ingestion.connector.Connector` (`fetch(query, since, *, run_id) ->
Iterator[RawJob]`), constructed with injected HTTP-call functions so unit
tests never touch the network, and wired into `apps/pipeline/app/cli.py`'s
existing `_CONNECTOR_BUILDERS` registry (Step 3's "one new file, one
registry entry, no runner changes" contract). `target_company` is a new
SHARED (no `user_id`, no RLS) Postgres table, migrated and read the same
way `bronze.raw_jobs` already is. Adzuna and Reed are deliberately kept as
separate, independently-duplicated pagination loops rather than a shared
base class — PLAN.md picked these three specifically because their shapes
differ, and this codebase's own convention favours duplication over a
premature abstraction until a third real case forces one.

**Tech Stack:** Python 3.11, `httpx` for HTTP, `sqlalchemy` (Core, not ORM)
for the `target_company` reads, `unittest` + `coverage`, Alembic for the
migration.

**Spec:** `PLAN.md` — "Step 4 — First three API connectors" (line ~367) and
`plan/backlog.yml`'s `STEP-04` entry (`jira_key: JOB-68`).

## Real API shapes below are live-verified, not recalled

Every field name and response shape used in this plan's Adzuna, Reed, and
Greenhouse code was confirmed against the real APIs with live `curl` calls
during planning (using the now-registered `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`
and `REED_API_KEY` in `.env`, and Greenhouse's keyless public endpoint) —
not reconstructed from training-data memory of API docs, which is a real
fabrication risk for this kind of task. Specifically confirmed live:
Adzuna's `results`/`count`/`id`/`redirect_url` shape (one transient 503,
one clean 200 retry); Reed's `results`/`totalResults` shape, HTTP Basic
auth with the key as username and blank password, and that
`resultsToSkip` pagination genuinely returns different `jobId`s; and
Greenhouse's `jobs`/`id`/`absolute_url`/`updated_at` shape against
`stripe`'s real public board (609 live jobs), plus a spot-check of several
seed-script candidate slugs (`airbnb`, `robinhood`, `coinbase` all real;
`doordash`, `notion` guessed wrong — confirming Task 6's seed script
design is necessary, not paranoid).

## Global Constraints

- Python 3.11, `black` (88 cols) + `isort` (profile black) + `ruff` + `mypy`
  (repo's `pyproject.toml` already sets `[tool.mypy] cache_dir = "/dev/null"`
  — do not remove this, it works around a real mypy/dlt cache crash).
- `unittest` + `coverage`, never `pytest`. Run from `packages/core`:
  `coverage run -m unittest discover` then `coverage report -m`.
- No mocking the database — DB-touching tests are integration tests under
  `packages/core/tests/integration/`, using a real Postgres connection, and
  must skip cleanly (not fail) when Postgres is unreachable — follow the
  exact pattern in `packages/core/tests/integration/test_runner_bronze.py`'s
  `_live_migration_engine()`.
- External HTTP calls follow the same rule in spirit: unit tests inject a
  fake fetch function (dependency-injection-with-real-defaults —
  `Callable[..., X] = real_default_fn`, matching every connector already in
  this codebase); a small number of **live** integration tests under
  `tests/integration/` make real calls and skip cleanly when the
  precondition isn't met (no API key, no network).
- Every per-user-vs-shared table decision follows `docs/tenancy.md`.
  `target_company` is SHARED: no `user_id`, no RLS — see "Adding a new
  shared table" in that doc.
- Docstrings: Google-style, on every function/class, per
  `.claude/rules/python-style.md`.
- Do not touch `core.ingestion.runner.run_connector`, `Connector`,
  `RawJob`, `retry_with_backoff`, `TokenBucket`, `load_to_bronze`,
  `write_landing_record`, or `write_run_metadata` — Step 3 built these to
  be connector-agnostic; if a task in this plan seems to need a change to
  one of them, stop and flag it rather than editing it (Step 3's whole
  point was that adding a connector never touches the runner).
- `docker compose up -d postgres` must be running for any DB-touching
  step; migrations run via `python3.11 -m alembic upgrade head` from
  `job_search/db` with `DATABASE_URL`/`APP_DATABASE_URL` pointed at
  `localhost:5432` (outside Docker) or `postgres:5432` (inside it — already
  the case in `.env`, used automatically by `docker compose run pipeline`).

---

### Task 1: `target_company` registry — migration + read/write module

**Files:**
- Create: `db/migrations/versions/0005_create_target_company.py`
- Create: `packages/core/core/db/target_company.py`
- Test: `packages/core/tests/integration/test_target_company.py`

**Interfaces:**
- Consumes: `core.db.session.build_engine` (existing, takes a DSN string,
  returns a SQLAlchemy `Engine`).
- Produces: `TargetCompany` (frozen dataclass: `id: uuid.UUID`, `name: str`,
  `ats_provider: str`, `board_slug: str`, `active: bool`, `added_at:
  datetime.datetime`), `list_active_companies(conn: Connection, *,
  ats_provider: str) -> list[TargetCompany]`, `upsert_target_company(conn:
  Connection, *, name: str, ats_provider: str, board_slug: str, active:
  bool = True) -> None`. Task 2 (`GreenhouseConnector`) and Task 6 (seed
  script) both import these two functions and the dataclass.

- [ ] **Step 1: Write the migration**

`db/migrations/versions/0005_create_target_company.py`:

```python
"""create target_company

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-03

target_company is SHARED reference data (docs/tenancy.md's "Adding a new
shared table"): which companies' ATS boards to poll is the same list for
every user, so this table carries no user_id and has no RLS policy — see
bronze.raw_jobs (0004) and shared_api_quota (0002) for the same pattern.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "target_company",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ats_provider", sa.Text(), nullable=False),
        sa.Column("board_slug", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "ats_provider IN ('greenhouse', 'lever', 'ashby')",
            name="ck_target_company_ats_provider",
        ),
        sa.UniqueConstraint(
            "ats_provider", "board_slug", name="uq_target_company_board"
        ),
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON target_company TO job_search_app"
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON target_company FROM job_search_app")
    op.drop_table("target_company")
```

- [ ] **Step 2: Run the migration**

From `job_search/db`, with Postgres up (`docker compose up -d postgres` from
`job_search/`):

```bash
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
python3.11 -m alembic upgrade head
```

Expected: no errors; `python3.11 -m alembic current` (same env vars) prints
`0005 (head)`.

- [ ] **Step 3: Write the read/write module**

`packages/core/core/db/target_company.py`:

```python
"""The target_company registry — which ATS boards Greenhouse-style
connectors poll (PLAN.md Step 4).

SHARED reference data (docs/tenancy.md): no user_id, no RLS — every user
sees the same company list. Read via a plain Connection, not
core.db.session.session_scope, because that context manager's whole job is
setting the RLS GUC, which this table has no policy keyed on.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class TargetCompany:
    """One company whose ATS board a connector should poll.

    Attributes:
        id: Primary key.
        name: Display name, e.g. "Airbnb".
        ats_provider: Which ATS this company's board_slug belongs to —
            "greenhouse", "lever", or "ashby" (only "greenhouse" has a
            connector implemented as of Step 4).
        board_slug: The ATS-specific board identifier, e.g. Greenhouse's
            `https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs`.
        active: Whether this company should currently be polled.
        added_at: When this row was created.
    """

    id: uuid.UUID
    name: str
    ats_provider: str
    board_slug: str
    active: bool
    added_at: datetime.datetime


def list_active_companies(
    conn: Connection, *, ats_provider: str
) -> list[TargetCompany]:
    """List every active company registered for a given ATS provider.

    Args:
        conn: An open connection (app-role or owner-role — this table has
            no RLS, so either works).
        ats_provider: Which provider to filter to, e.g. "greenhouse".

    Returns:
        Every `TargetCompany` row with this `ats_provider` and
        `active = true`, ordered by name.
    """
    rows = conn.execute(
        text(
            "SELECT id, name, ats_provider, board_slug, active, added_at "
            "FROM target_company "
            "WHERE ats_provider = :ats_provider AND active = true "
            "ORDER BY name"
        ),
        {"ats_provider": ats_provider},
    ).all()
    return [
        TargetCompany(
            id=row.id,
            name=row.name,
            ats_provider=row.ats_provider,
            board_slug=row.board_slug,
            active=row.active,
            added_at=row.added_at,
        )
        for row in rows
    ]


def upsert_target_company(
    conn: Connection,
    *,
    name: str,
    ats_provider: str,
    board_slug: str,
    active: bool = True,
) -> None:
    """Insert or update one company's registry row.

    Args:
        conn: An open connection inside a transaction (caller commits).
        name: Display name.
        ats_provider: "greenhouse", "lever", or "ashby".
        board_slug: The ATS-specific board identifier.
        active: Whether this company should currently be polled.

    Idempotent on (ats_provider, board_slug) — re-running with the same
    pair updates name/active rather than creating a duplicate row, so the
    seed script (Task 6) is safe to re-run.
    """
    conn.execute(
        text(
            "INSERT INTO target_company (name, ats_provider, board_slug, active) "
            "VALUES (:name, :ats_provider, :board_slug, :active) "
            "ON CONFLICT (ats_provider, board_slug) "
            "DO UPDATE SET name = :name, active = :active"
        ),
        {
            "name": name,
            "ats_provider": ats_provider,
            "board_slug": board_slug,
            "active": active,
        },
    )
```

- [ ] **Step 4: Write the integration test**

`packages/core/tests/integration/test_target_company.py`:

```python
"""Integration test for the target_company registry module."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.db.target_company import list_active_companies, upsert_target_company
from core.settings import get_settings


def _live_migration_engine():
    """Connect to Postgres, skip test if unreachable."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any connection failure means "skip"
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); run "
            "`docker compose up -d postgres` first."
        ) from None
    return engine


class TestTargetCompanyRegistry(unittest.TestCase):
    """Proves upsert + list round-trip against a real Postgres table."""

    @classmethod
    def setUpClass(cls) -> None:
        """Connect to live Postgres once for the whole test class."""
        cls.engine = _live_migration_engine()

    def setUp(self) -> None:
        """Give each test a unique board_slug so tests never collide."""
        self.board_slug = f"test-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        """Delete the row this test wrote."""
        with self.engine.connect() as conn:
            conn.execute(
                text(
                    "DELETE FROM target_company "
                    "WHERE ats_provider = 'greenhouse' AND board_slug = :slug"
                ),
                {"slug": self.board_slug},
            )
            conn.commit()

    def test_upsert_then_list_round_trips_a_real_row(self) -> None:
        """A freshly upserted active company appears in list_active_companies."""
        with self.engine.connect() as conn:
            upsert_target_company(
                conn,
                name="Test Co",
                ats_provider="greenhouse",
                board_slug=self.board_slug,
            )
            conn.commit()

        with self.engine.connect() as conn:
            companies = list_active_companies(conn, ats_provider="greenhouse")
        self.assertIn(self.board_slug, [c.board_slug for c in companies])

    def test_inactive_company_excluded_from_list(self) -> None:
        """A company upserted with active=False never appears in the list."""
        with self.engine.connect() as conn:
            upsert_target_company(
                conn,
                name="Inactive Co",
                ats_provider="greenhouse",
                board_slug=self.board_slug,
                active=False,
            )
            conn.commit()

        with self.engine.connect() as conn:
            companies = list_active_companies(conn, ats_provider="greenhouse")
        self.assertNotIn(self.board_slug, [c.board_slug for c in companies])

    def test_upsert_is_idempotent_on_ats_provider_and_board_slug(self) -> None:
        """Re-upserting the same (ats_provider, board_slug) updates, not duplicates."""
        with self.engine.connect() as conn:
            upsert_target_company(
                conn, name="First Name", ats_provider="greenhouse",
                board_slug=self.board_slug,
            )
            conn.commit()
        with self.engine.connect() as conn:
            upsert_target_company(
                conn, name="Second Name", ats_provider="greenhouse",
                board_slug=self.board_slug,
            )
            conn.commit()

        with self.engine.connect() as conn:
            companies = list_active_companies(conn, ats_provider="greenhouse")
        matches = [c for c in companies if c.board_slug == self.board_slug]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "Second Name")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Run the tests**

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_test" \
python3.11 -m unittest tests.integration.test_target_company -v
```

Expected: 3 tests, all PASS.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/versions/0005_create_target_company.py \
  packages/core/core/db/target_company.py \
  packages/core/tests/integration/test_target_company.py
git commit -m "feat(job_search): add the target_company registry table"
```

---

### Task 2: GreenhouseConnector

**Files:**
- Create: `packages/core/core/ingestion/greenhouse_connector.py`
- Test: `packages/core/tests/test_greenhouse_connector.py`

**Interfaces:**
- Consumes: `core.ingestion.connector.Connector` (protocol to satisfy),
  `core.ingestion.raw_job.RawJob` (exact fields per Task 1's summary in
  `packages/core/core/ingestion/raw_job.py` — unchanged from Step 3),
  `core.ingestion.url_utils.canonicalise_url(url, *, http_client=None) ->
  str` (existing), `core.db.target_company.list_active_companies` and
  `TargetCompany` (Task 1).
- Produces: `GreenhouseQuery` (frozen dataclass: `board_slugs: list[str] |
  None = None` — `None` means "read the active registry"),
  `GreenhouseConnector` — consumed by Task 5 (cli.py wiring).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_greenhouse_connector.py`:

```python
"""Tests for GreenhouseConnector."""

from __future__ import annotations

import datetime
import unittest
import uuid

import httpx

from core.db.target_company import TargetCompany
from core.ingestion.greenhouse_connector import GreenhouseConnector, GreenhouseQuery


def _fake_company(name: str, board_slug: str) -> TargetCompany:
    return TargetCompany(
        id=uuid.uuid4(),
        name=name,
        ats_provider="greenhouse",
        board_slug=board_slug,
        active=True,
        added_at=datetime.datetime.now(datetime.UTC),
    )


def _job(job_id: int, title: str, updated_at: str) -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "updated_at": updated_at,
        "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
        "location": {"name": "Remote"},
    }


class TestGreenhouseConnector(unittest.TestCase):
    """Unit tests — every HTTP/DB call is injected, no real network or DB."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def test_fetch_with_explicit_board_slugs_skips_the_registry(self) -> None:
        """Passing board_slugs bypasses list_companies_fn entirely."""

        def fake_fetch_board(http_client, board_slug):
            self.assertEqual(board_slug, "acme")
            return [_job(1, "Engineer", "2026-09-01T00:00:00Z")]

        def fake_list_companies(database_url, *, ats_provider):
            self.fail("list_companies_fn should not be called")

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=fake_list_companies,
        )
        jobs = list(
            connector.fetch(
                GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1"
            )
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "greenhouse")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")

    def test_fetch_with_no_board_slugs_reads_the_active_registry(self) -> None:
        """query.board_slugs=None reads every active company via list_companies_fn."""
        companies = [_fake_company("Acme", "acme"), _fake_company("Beta", "beta")]

        def fake_fetch_board(http_client, board_slug):
            return [_job(1, f"Job at {board_slug}", "2026-09-01T00:00:00Z")]

        def fake_list_companies(database_url, *, ats_provider):
            self.assertEqual(ats_provider, "greenhouse")
            return companies

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=fake_list_companies,
        )
        jobs = list(
            connector.fetch(GreenhouseQuery(), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 2)
        self.assertEqual({j.payload["_board_slug"] for j in jobs}, {"acme", "beta"})

    def test_since_filters_out_jobs_updated_before_it(self) -> None:
        """A job whose updated_at is before `since` is excluded."""

        def fake_fetch_board(http_client, board_slug):
            return [
                _job(1, "Old", "2026-01-01T00:00:00Z"),
                _job(2, "New", "2026-09-01T00:00:00Z"),
            ]

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        since = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        jobs = list(
            connector.fetch(
                GreenhouseQuery(board_slugs=["acme"]), since, run_id="run-1"
            )
        )
        self.assertEqual([j.source_job_id for j in jobs], ["2"])

    def test_empty_board_returns_no_jobs(self) -> None:
        """A board with zero open roles yields nothing, not an error."""

        def fake_fetch_board(http_client, board_slug):
            return []

        connector = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fake_fetch_board,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        jobs = list(
            connector.fetch(
                GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1"
            )
        )
        self.assertEqual(jobs, [])

    def test_payload_sha256_changes_when_job_content_changes(self) -> None:
        """Two fetches of the same job_id with different content hash differently."""

        def fetch_v1(http_client, board_slug):
            return [_job(1, "Engineer", "2026-09-01T00:00:00Z")]

        def fetch_v2(http_client, board_slug):
            return [_job(1, "Senior Engineer", "2026-09-02T00:00:00Z")]

        connector_v1 = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fetch_v1,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        connector_v2 = GreenhouseConnector(
            http_client=self.http_client,
            database_url="unused",
            fetch_board_fn=fetch_v2,
            list_companies_fn=lambda *a, **k: self.fail("unused"),
        )
        job_v1 = list(
            connector_v1.fetch(
                GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1"
            )
        )[0]
        job_v2 = list(
            connector_v2.fetch(
                GreenhouseQuery(board_slugs=["acme"]), None, run_id="run-1"
            )
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_greenhouse_connector -v
```

Expected: FAIL / ImportError — `core.ingestion.greenhouse_connector` does
not exist yet.

- [ ] **Step 3: Write the implementation**

`packages/core/core/ingestion/greenhouse_connector.py`:

```python
"""GreenhouseConnector — the keyless per-company ATS-board connector
(PLAN.md Step 4).

Unlike a keyed search API, Greenhouse's public board endpoint returns the
company's *entire* open-role list in one call, no pagination and no query
string — the connector's only real job is iterating companies (from the
target_company registry, or an explicit override) and filtering by
`since` in-process, since the API itself has no incremental-fetch param.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx

from core.db.session import build_engine
from core.db.target_company import TargetCompany, list_active_companies
from core.ingestion.raw_job import RawJob
from core.ingestion.url_utils import canonicalise_url

_logger = logging.getLogger(__name__)

_GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs"


@dataclass(frozen=True)
class GreenhouseQuery:
    """GreenhouseConnector's `query` type.

    Attributes:
        board_slugs: Explicit list of board slugs to poll, overriding the
            registry. `None` (the default) means "poll every active
            company in the target_company registry".
    """

    board_slugs: list[str] | None = None


def _hash_job(job: dict[str, object]) -> str:
    """Hash one Greenhouse job dict for dedup/change detection.

    Args:
        job: The raw job dict as returned by the Greenhouse board API.

    Returns:
        The SHA-256 hex digest of the job's canonical JSON form.
    """
    canonical = json.dumps(job, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fetch_greenhouse_board(
    http_client: httpx.Client, board_slug: str
) -> list[dict[str, object]]:
    """Fetch one company's full open-role list from Greenhouse.

    Args:
        http_client: The shared HTTP client.
        board_slug: The company's Greenhouse board token.

    Returns:
        Every job dict in the board's `jobs` array (possibly empty).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    response = http_client.get(
        _GREENHOUSE_BOARD_URL.format(board_slug=board_slug),
        params={"content": "true"},
        timeout=15.0,
    )
    response.raise_for_status()
    body = response.json()
    return list(body.get("jobs", []))


def _list_active_greenhouse_companies(
    database_url: str, *, ats_provider: str
) -> list[TargetCompany]:
    """Read the active target_company registry for one ATS provider.

    Args:
        database_url: The Postgres DSN to connect with. target_company has
            no RLS (docs/tenancy.md), so any role with SELECT works.
        ats_provider: Which provider to filter to.

    Returns:
        Every active `TargetCompany` row for that provider.
    """
    engine = build_engine(database_url)
    with engine.connect() as conn:
        return list_active_companies(conn, ats_provider=ats_provider)


@dataclass(frozen=True)
class _Company:
    """Internal (name, board_slug) pair — either from the registry or an
    explicit query.board_slugs override, which has no display name."""

    name: str
    board_slug: str


class GreenhouseConnector:
    """Fetches every open role from one or more companies' Greenhouse boards."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        database_url: str,
        fetch_board_fn: Callable[
            [httpx.Client, str], list[dict[str, object]]
        ] = _fetch_greenhouse_board,
        list_companies_fn: Callable[
            ..., list[TargetCompany]
        ] = _list_active_greenhouse_companies,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every board fetch.
            database_url: Passed to `list_companies_fn` when
                `query.board_slugs` is `None`.
            fetch_board_fn: Injectable per-board fetch — defaults to the
                real Greenhouse API call.
            list_companies_fn: Injectable registry read — defaults to a
                real `target_company` query.
        """
        self._http_client = http_client
        self._database_url = database_url
        self._fetch_board_fn = fetch_board_fn
        self._list_companies_fn = list_companies_fn

    def fetch(
        self, query: GreenhouseQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield every open role across every company this query resolves to.

        Args:
            query: `GreenhouseQuery` — explicit board_slugs, or None to
                use the active registry.
            since: Only yield jobs whose `updated_at` is at or after this
                time. `None` means no filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per open role, across every resolved company.
        """
        if query.board_slugs:
            companies = [
                _Company(name=slug, board_slug=slug) for slug in query.board_slugs
            ]
        else:
            companies = [
                _Company(name=c.name, board_slug=c.board_slug)
                for c in self._list_companies_fn(
                    self._database_url, ats_provider="greenhouse"
                )
            ]

        for company in companies:
            try:
                jobs = self._fetch_board_fn(self._http_client, company.board_slug)
            except httpx.HTTPStatusError:
                _logger.warning(
                    "Greenhouse board fetch failed for board_slug=%s; skipping "
                    "this company for this run",
                    company.board_slug,
                    exc_info=True,
                )
                continue

            fetched_at = datetime.datetime.now(datetime.UTC)
            for job in jobs:
                updated_at = _parse_updated_at(job.get("updated_at"))
                if since is not None and updated_at is not None and updated_at < since:
                    continue

                job_url = str(job["absolute_url"])
                canonical_url = canonicalise_url(job_url)
                payload = {
                    **job,
                    "_company_name": company.name,
                    "_board_slug": company.board_slug,
                }
                yield RawJob(
                    source_name="greenhouse",
                    source_job_id=str(job["id"]),
                    job_url=job_url,
                    job_url_canonical=canonical_url,
                    payload=payload,
                    fetched_at=fetched_at,
                    run_id=run_id,
                    request_params={"board_slug": company.board_slug},
                    payload_sha256=_hash_job(job),
                )


def _parse_updated_at(value: object) -> datetime.datetime | None:
    """Parse Greenhouse's `updated_at` field into an aware datetime.

    Args:
        value: The raw `updated_at` value from a Greenhouse job dict,
            typically an ISO-8601 string like "2026-09-01T00:00:00Z".

    Returns:
        The parsed datetime, or `None` if `value` is missing or
        unparseable — treated as "no filter" for that job rather than an
        error, since a malformed date shouldn't drop an otherwise-valid
        posting.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_greenhouse_connector -v
```

Expected: 5 tests, all PASS.

- [ ] **Step 5: Run the quality gate**

```bash
cd job_search
python3.11 -m black packages/core/core/ingestion/greenhouse_connector.py \
  packages/core/tests/test_greenhouse_connector.py
python3.11 -m isort packages/core/core/ingestion/greenhouse_connector.py \
  packages/core/tests/test_greenhouse_connector.py
python3.11 -m ruff check packages/core/core/ingestion/greenhouse_connector.py \
  packages/core/tests/test_greenhouse_connector.py
python3.11 -m mypy packages/core/core
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/ingestion/greenhouse_connector.py \
  packages/core/tests/test_greenhouse_connector.py
git commit -m "feat(job_search): add GreenhouseConnector"
```

---

### Task 3: AdzunaConnector

**Files:**
- Create: `packages/core/core/ingestion/adzuna_connector.py`
- Test: `packages/core/tests/test_adzuna_connector.py`

**Interfaces:**
- Consumes: `core.ingestion.connector.Connector`, `core.ingestion.raw_job.
  RawJob`, `core.ingestion.url_utils.canonicalise_url`.
- Produces: `AdzunaQuery` (frozen dataclass: `keywords: str`, `country:
  str`, `max_pages: int = 5`, `results_per_page: int = 50`),
  `AdzunaConnector` — consumed by Task 5 (cli.py wiring).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_adzuna_connector.py`:

```python
"""Tests for AdzunaConnector."""

from __future__ import annotations

import datetime
import unittest

import httpx

from core.ingestion.adzuna_connector import AdzunaConnector, AdzunaQuery


def _result(job_id: int, title: str) -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "redirect_url": f"https://www.adzuna.co.uk/jobs/details/{job_id}",
        "company": {"display_name": "Acme"},
        "location": {"display_name": "London"},
    }


class TestAdzunaConnector(unittest.TestCase):
    """Unit tests — every HTTP call is injected, no real network."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()
        self.calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def _make_connector(self, pages: list[list[dict[str, object]]]) -> AdzunaConnector:
        """Build a connector whose fetch_page_fn returns `pages` in order,
        recording every call's kwargs into self.calls."""

        def fake_fetch_page(http_client, *, app_id, app_key, country, page,
                             results_per_page, what, max_days_old):
            self.calls.append(
                {"country": country, "page": page, "what": what,
                 "max_days_old": max_days_old}
            )
            index = page - 1
            results = pages[index] if index < len(pages) else []
            return {"results": results, "count": sum(len(p) for p in pages)}

        return AdzunaConnector(
            http_client=self.http_client,
            app_id="test-id",
            app_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )

    def test_fetch_maps_one_page_of_results_to_raw_jobs(self) -> None:
        """A single short page yields one RawJob per result, then stops."""
        connector = self._make_connector([[_result(1, "Data Engineer")]])
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="data engineer", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "adzuna")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")
        self.assertEqual(len(self.calls), 1)

    def test_fetch_paginates_until_a_short_page(self) -> None:
        """A full page triggers a second fetch; a short page stops pagination."""
        full_page = [_result(i, f"Job {i}") for i in range(50)]
        short_page = [_result(100, "Last Job")]
        connector = self._make_connector([full_page, short_page])
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="data engineer", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(len(jobs), 51)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["page"], 1)
        self.assertEqual(self.calls[1]["page"], 2)

    def test_fetch_stops_at_max_pages_even_with_full_pages(self) -> None:
        """max_pages caps the number of API calls, even if every page is full."""
        full_page = [_result(i, f"Job {i}") for i in range(50)]
        connector = self._make_connector([full_page] * 10)
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="x", country="gb", max_pages=2),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(jobs), 100)

    def test_fetch_stops_immediately_on_empty_first_page(self) -> None:
        """Zero results on page 1 yields nothing and makes exactly one call."""
        connector = self._make_connector([[]])
        jobs = list(
            connector.fetch(
                AdzunaQuery(keywords="nonexistent role xyz", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(jobs, [])
        self.assertEqual(len(self.calls), 1)

    def test_since_is_translated_into_max_days_old(self) -> None:
        """A `since` datetime becomes a positive integer max_days_old."""
        connector = self._make_connector([[_result(1, "Job")]])
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=3)
        list(
            connector.fetch(
                AdzunaQuery(keywords="x", country="gb"), since, run_id="run-1"
            )
        )
        self.assertIn(self.calls[0]["max_days_old"], (3, 4))

    def test_payload_sha256_changes_when_result_content_changes(self) -> None:
        """Two fetches of the same job id with different titles hash differently."""
        connector_v1 = self._make_connector([[_result(1, "Data Engineer")]])
        connector_v2 = self._make_connector([[_result(1, "Senior Data Engineer")]])
        job_v1 = list(
            connector_v1.fetch(
                AdzunaQuery(keywords="x", country="gb"), None, run_id="run-1"
            )
        )[0]
        job_v2 = list(
            connector_v2.fetch(
                AdzunaQuery(keywords="x", country="gb"), None, run_id="run-1"
            )
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_adzuna_connector -v
```

Expected: FAIL / ImportError — `core.ingestion.adzuna_connector` does not
exist yet.

- [ ] **Step 3: Write the implementation**

`packages/core/core/ingestion/adzuna_connector.py`:

```python
"""AdzunaConnector — keyed REST with pagination (PLAN.md Step 4).

Free tier is 1,000 calls/month, so `max_pages` defaults to a conservative
cap: pagination stops at the first short (< results_per_page) page OR
max_pages, whichever comes first, so one ingest run can never silently
burn the whole monthly budget on one broad query.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from core.ingestion.raw_job import RawJob
from core.ingestion.url_utils import canonicalise_url

_logger = logging.getLogger(__name__)

_ADZUNA_SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
_DEFAULT_MAX_PAGES = 5
_DEFAULT_RESULTS_PER_PAGE = 50


@dataclass(frozen=True)
class AdzunaQuery:
    """AdzunaConnector's `query` type.

    Attributes:
        keywords: Free-text search terms, e.g. "data engineer".
        country: Adzuna's two-letter country code, e.g. "gb", "us".
        max_pages: Safety cap on API calls per fetch() — pagination also
            stops early on a short page. Defaults to 5 (up to 250 results
            at the default results_per_page).
        results_per_page: Results requested per page. Adzuna's own max is
            50.
    """

    keywords: str
    country: str
    max_pages: int = _DEFAULT_MAX_PAGES
    results_per_page: int = _DEFAULT_RESULTS_PER_PAGE


def _hash_result(result: dict[str, object]) -> str:
    """Hash one Adzuna result dict for dedup/change detection.

    Args:
        result: One entry from Adzuna's `results` array.

    Returns:
        The SHA-256 hex digest of the result's canonical JSON form.
    """
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fetch_adzuna_page(
    http_client: httpx.Client,
    *,
    app_id: str,
    app_key: str,
    country: str,
    page: int,
    results_per_page: int,
    what: str,
    max_days_old: int | None,
) -> dict[str, object]:
    """Fetch one page of Adzuna search results.

    Args:
        http_client: The shared HTTP client.
        app_id: Adzuna application ID.
        app_key: Adzuna application key.
        country: Adzuna's two-letter country code.
        page: 1-indexed page number.
        results_per_page: Results requested for this page.
        what: Free-text keyword query.
        max_days_old: Only return postings at most this many days old, or
            `None` for no age filter.

    Returns:
        The parsed JSON response body (`{"results": [...], "count": N}`).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    params: dict[str, object] = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": results_per_page,
        "what": what,
        "content-type": "application/json",
    }
    if max_days_old is not None:
        params["max_days_old"] = max_days_old

    response = http_client.get(
        _ADZUNA_SEARCH_URL.format(country=country, page=page),
        params=params,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


class AdzunaConnector:
    """Fetches job postings from Adzuna's keyed, paginated search API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        app_id: str,
        app_key: str,
        fetch_page_fn: Callable[..., dict[str, object]] = _fetch_adzuna_page,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every page fetch.
            app_id: Adzuna application ID.
            app_key: Adzuna application key.
            fetch_page_fn: Injectable per-page fetch — defaults to the
                real Adzuna API call.
        """
        self._http_client = http_client
        self._app_id = app_id
        self._app_key = app_key
        self._fetch_page_fn = fetch_page_fn

    def fetch(
        self, query: AdzunaQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield job postings matching `query`, paginating as needed.

        Args:
            query: `AdzunaQuery` — keywords, country, and pagination caps.
            since: Only fetch postings at most this old — translated into
                Adzuna's `max_days_old` parameter (rounded up to whole
                days). `None` means no age filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per result, across every fetched page.
        """
        max_days_old = None
        if since is not None:
            age = datetime.datetime.now(datetime.UTC) - since
            whole_days = age.days + (1 if age.seconds or age.microseconds else 0)
            max_days_old = max(1, whole_days)

        for page in range(1, query.max_pages + 1):
            body = self._fetch_page_fn(
                self._http_client,
                app_id=self._app_id,
                app_key=self._app_key,
                country=query.country,
                page=page,
                results_per_page=query.results_per_page,
                what=query.keywords,
                max_days_old=max_days_old,
            )
            results = list(body.get("results", []))
            if not results:
                return

            fetched_at = datetime.datetime.now(datetime.UTC)
            for result in results:
                job_url = str(result["redirect_url"])
                canonical_url = canonicalise_url(job_url)
                yield RawJob(
                    source_name="adzuna",
                    source_job_id=str(result["id"]),
                    job_url=job_url,
                    job_url_canonical=canonical_url,
                    payload=dict(result),
                    fetched_at=fetched_at,
                    run_id=run_id,
                    request_params={
                        "what": query.keywords,
                        "country": query.country,
                        "page": page,
                    },
                    payload_sha256=_hash_result(result),
                )

            if len(results) < query.results_per_page:
                return
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_adzuna_connector -v
```

Expected: 6 tests, all PASS.

- [ ] **Step 5: Run the quality gate**

```bash
cd job_search
python3.11 -m black packages/core/core/ingestion/adzuna_connector.py \
  packages/core/tests/test_adzuna_connector.py
python3.11 -m isort packages/core/core/ingestion/adzuna_connector.py \
  packages/core/tests/test_adzuna_connector.py
python3.11 -m ruff check packages/core/core/ingestion/adzuna_connector.py \
  packages/core/tests/test_adzuna_connector.py
python3.11 -m mypy packages/core/core
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/ingestion/adzuna_connector.py \
  packages/core/tests/test_adzuna_connector.py
git commit -m "feat(job_search): add AdzunaConnector"
```

---

### Task 4: ReedConnector

**Files:**
- Create: `packages/core/core/ingestion/reed_connector.py`
- Test: `packages/core/tests/test_reed_connector.py`

**Interfaces:**
- Consumes: `core.ingestion.connector.Connector`, `core.ingestion.raw_job.
  RawJob`, `core.ingestion.url_utils.canonicalise_url`.
- Produces: `ReedQuery` (frozen dataclass: `keywords: str`, `location: str
  | None = None`, `max_pages: int = 5`, `results_per_page: int = 50`),
  `ReedConnector` — consumed by Task 5 (cli.py wiring).

Reed's real API (confirmed live during planning — see the plan header):
`GET https://www.reed.co.uk/api/1.0/search`, HTTP Basic auth (API key as
username, blank password), `resultsToTake`/`resultsToSkip` for
pagination (not a page number like Adzuna), response shape `{"results":
[...], "totalResults": N}`, each result carrying `jobId`, `jobTitle`,
`jobUrl`, `employerName`, `locationName`, `minimumSalary`,
`maximumSalary`, `currency`, `date` (posting date, `"DD/MM/YYYY"`),
`expirationDate` (same format), and a `jobDescription` Reed itself
truncates to roughly 500 characters (not a bug to fix — that's the real
API's search-endpoint behaviour; a full description needs a separate
per-job endpoint this connector does not call).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_reed_connector.py`:

```python
"""Tests for ReedConnector."""

from __future__ import annotations

import datetime
import unittest

import httpx

from core.ingestion.reed_connector import ReedConnector, ReedQuery


def _result(job_id: int, title: str, date: str) -> dict[str, object]:
    return {
        "jobId": job_id,
        "jobTitle": title,
        "jobUrl": f"https://www.reed.co.uk/jobs/{title.lower()}/{job_id}",
        "employerName": "Acme",
        "locationName": "London",
        "date": date,
        "expirationDate": "31/12/2026",
        "jobDescription": "A great role...",
    }


class TestReedConnector(unittest.TestCase):
    """Unit tests — every HTTP call is injected, no real network."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()
        self.calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def _make_connector(self, pages: list[list[dict[str, object]]]) -> ReedConnector:
        """Build a connector whose fetch_page_fn returns `pages` in order,
        recording every call's kwargs into self.calls."""

        def fake_fetch_page(http_client, *, api_key, keywords, location,
                             results_to_skip, results_to_take):
            self.calls.append(
                {"keywords": keywords, "location": location,
                 "results_to_skip": results_to_skip}
            )
            index = results_to_skip // results_to_take
            results = pages[index] if index < len(pages) else []
            return {"results": results, "totalResults": sum(len(p) for p in pages)}

        return ReedConnector(
            http_client=self.http_client, api_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )

    def test_fetch_maps_one_page_of_results_to_raw_jobs(self) -> None:
        """A single short page yields one RawJob per result, then stops."""
        connector = self._make_connector(
            [[_result(1, "Data Engineer", "01/09/2026")]]
        )
        jobs = list(
            connector.fetch(
                ReedQuery(keywords="data engineer"), None, run_id="run-1"
            )
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "reed")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")
        self.assertEqual(len(self.calls), 1)

    def test_fetch_paginates_via_results_to_skip(self) -> None:
        """A full page triggers a second fetch at the next offset."""
        full_page = [_result(i, f"Job {i}", "01/09/2026") for i in range(50)]
        short_page = [_result(100, "Last Job", "01/09/2026")]
        connector = self._make_connector([full_page, short_page])
        jobs = list(
            connector.fetch(
                ReedQuery(keywords="data engineer"), None, run_id="run-1"
            )
        )
        self.assertEqual(len(jobs), 51)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["results_to_skip"], 0)
        self.assertEqual(self.calls[1]["results_to_skip"], 50)

    def test_fetch_stops_at_max_pages_even_with_full_pages(self) -> None:
        """max_pages caps the number of API calls, even if every page is full."""
        full_page = [_result(i, f"Job {i}", "01/09/2026") for i in range(50)]
        connector = self._make_connector([full_page] * 10)
        jobs = list(
            connector.fetch(
                ReedQuery(keywords="x", max_pages=2), None, run_id="run-1"
            )
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(jobs), 100)

    def test_location_is_passed_through_when_given(self) -> None:
        """query.location reaches fetch_page_fn unchanged."""
        connector = self._make_connector([[_result(1, "Job", "01/09/2026")]])
        list(
            connector.fetch(
                ReedQuery(keywords="x", location="Manchester"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(self.calls[0]["location"], "Manchester")

    def test_since_filters_out_jobs_posted_before_it(self) -> None:
        """A job whose `date` is before `since` is excluded."""
        connector = self._make_connector(
            [[_result(1, "Old", "01/01/2026"), _result(2, "New", "01/09/2026")]]
        )
        since = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        jobs = list(
            connector.fetch(ReedQuery(keywords="x"), since, run_id="run-1")
        )
        self.assertEqual([j.source_job_id for j in jobs], ["2"])

    def test_payload_sha256_changes_when_result_content_changes(self) -> None:
        """Two fetches of the same jobId with different titles hash differently."""
        connector_v1 = self._make_connector(
            [[_result(1, "Data Engineer", "01/09/2026")]]
        )
        connector_v2 = self._make_connector(
            [[_result(1, "Senior Data Engineer", "01/09/2026")]]
        )
        job_v1 = list(
            connector_v1.fetch(ReedQuery(keywords="x"), None, run_id="run-1")
        )[0]
        job_v2 = list(
            connector_v2.fetch(ReedQuery(keywords="x"), None, run_id="run-1")
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_reed_connector -v
```

Expected: FAIL / ImportError — `core.ingestion.reed_connector` does not
exist yet.

- [ ] **Step 3: Write the implementation**

`packages/core/core/ingestion/reed_connector.py`:

```python
"""ReedConnector — keyed REST with offset-based pagination (PLAN.md
Step 4).

Reed's search endpoint takes `resultsToSkip`/`resultsToTake` rather than
Adzuna's page-number-in-the-URL scheme — genuinely different pagination
shape, which is exactly why PLAN.md picked these two together. Reed's
`jobDescription` field is truncated by Reed itself (confirmed live during
planning, ~500 chars with a literal "..." — not this connector's doing);
stored as-is, since the full text needs a separate per-job endpoint this
connector does not call.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from core.ingestion.raw_job import RawJob
from core.ingestion.url_utils import canonicalise_url

_logger = logging.getLogger(__name__)

_REED_SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
_DEFAULT_MAX_PAGES = 5
_DEFAULT_RESULTS_PER_PAGE = 50


@dataclass(frozen=True)
class ReedQuery:
    """ReedConnector's `query` type.

    Attributes:
        keywords: Free-text search terms, e.g. "data engineer".
        location: Optional UK location name, e.g. "Manchester". `None`
            means no location filter (nationwide UK).
        max_pages: Safety cap on API calls per fetch() — pagination also
            stops early on a short page.
        results_per_page: Results requested per page.
    """

    keywords: str
    location: str | None = None
    max_pages: int = _DEFAULT_MAX_PAGES
    results_per_page: int = _DEFAULT_RESULTS_PER_PAGE


def _hash_result(result: dict[str, object]) -> str:
    """Hash one Reed result dict for dedup/change detection.

    Args:
        result: One entry from Reed's `results` array.

    Returns:
        The SHA-256 hex digest of the result's canonical JSON form.
    """
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fetch_reed_page(
    http_client: httpx.Client,
    *,
    api_key: str,
    keywords: str,
    location: str | None,
    results_to_skip: int,
    results_to_take: int,
) -> dict[str, object]:
    """Fetch one page of Reed search results.

    Args:
        http_client: The shared HTTP client.
        api_key: Reed API key, sent as the HTTP Basic auth username with
            a blank password — confirmed live during planning.
        keywords: Free-text keyword query.
        location: Optional UK location filter.
        results_to_skip: Offset into the result set.
        results_to_take: Page size.

    Returns:
        The parsed JSON response body (`{"results": [...], "totalResults":
        N}`).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    params: dict[str, object] = {
        "keywords": keywords,
        "resultsToSkip": results_to_skip,
        "resultsToTake": results_to_take,
    }
    if location:
        params["locationName"] = location

    response = http_client.get(
        _REED_SEARCH_URL, params=params, auth=(api_key, ""), timeout=15.0
    )
    response.raise_for_status()
    return response.json()


def _parse_reed_date(value: object) -> datetime.date | None:
    """Parse Reed's `date` field (`"DD/MM/YYYY"`) into a date.

    Args:
        value: The raw `date` value from a Reed result dict.

    Returns:
        The parsed date, or `None` if `value` is missing or unparseable —
        treated as "no filter" for that job rather than an error.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


class ReedConnector:
    """Fetches job postings from Reed's keyed, offset-paginated search API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        api_key: str,
        fetch_page_fn: Callable[..., dict[str, object]] = _fetch_reed_page,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every page fetch.
            api_key: Reed API key.
            fetch_page_fn: Injectable per-page fetch — defaults to the
                real Reed API call.
        """
        self._http_client = http_client
        self._api_key = api_key
        self._fetch_page_fn = fetch_page_fn

    def fetch(
        self, query: ReedQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield job postings matching `query`, paginating as needed.

        Args:
            query: `ReedQuery` — keywords, optional location, pagination
                caps.
            since: Only yield postings whose `date` is at or after this
                time (compared by date only — Reed gives no time-of-day).
                `None` means no filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per result, across every fetched page.
        """
        since_date = since.date() if since is not None else None

        for page in range(query.max_pages):
            skip = page * query.results_per_page
            body = self._fetch_page_fn(
                self._http_client,
                api_key=self._api_key,
                keywords=query.keywords,
                location=query.location,
                results_to_skip=skip,
                results_to_take=query.results_per_page,
            )
            results = list(body.get("results", []))
            if not results:
                return

            fetched_at = datetime.datetime.now(datetime.UTC)
            for result in results:
                posted = _parse_reed_date(result.get("date"))
                if since_date is not None and posted is not None and posted < since_date:
                    continue

                job_url = str(result["jobUrl"])
                canonical_url = canonicalise_url(job_url)
                yield RawJob(
                    source_name="reed",
                    source_job_id=str(result["jobId"]),
                    job_url=job_url,
                    job_url_canonical=canonical_url,
                    payload=dict(result),
                    fetched_at=fetched_at,
                    run_id=run_id,
                    request_params={
                        "keywords": query.keywords,
                        "location": query.location,
                        "results_to_skip": skip,
                    },
                    payload_sha256=_hash_result(result),
                )

            if len(results) < query.results_per_page:
                return
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_reed_connector -v
```

Expected: 6 tests, all PASS.

- [ ] **Step 5: Run the quality gate**

```bash
cd job_search
python3.11 -m black packages/core/core/ingestion/reed_connector.py \
  packages/core/tests/test_reed_connector.py
python3.11 -m isort packages/core/core/ingestion/reed_connector.py \
  packages/core/tests/test_reed_connector.py
python3.11 -m ruff check packages/core/core/ingestion/reed_connector.py \
  packages/core/tests/test_reed_connector.py
python3.11 -m mypy packages/core/core
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/ingestion/reed_connector.py \
  packages/core/tests/test_reed_connector.py
git commit -m "feat(job_search): add ReedConnector"
```

---

### Task 5: Wire all three connectors into the CLI and `sources.yml`

**Files:**
- Modify: `apps/pipeline/app/cli.py`
- Modify: `config/sources.yml`
- Test: `packages/core/tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: `AdzunaConnector`/`AdzunaQuery` (Task 3),
  `ReedConnector`/`ReedQuery` (Task 4), `GreenhouseConnector`/
  `GreenhouseQuery` (Task 2), `core.settings.Settings.adzuna_app_id: str |
  None`, `Settings.adzuna_app_key: str | None`, `Settings.reed_api_key:
  str | None` (all three already exist on `Settings`, currently unused).
- Produces: nothing new consumed elsewhere — this is the plan's last
  code task; Task 6 (seed script) and Task 7 (live tests) exercise this
  wiring but don't import from it.

The current `_CONNECTOR_BUILDERS` builder signature is `Callable[[httpx.
Client, dict[str, LLMAdapter]], Connector]` — enough for `ManualConnector`,
which needs only an HTTP client and LLM adapters. `AdzunaConnector` and
`ReedConnector` each need their own API credentials; `GreenhouseConnector`
needs a database DSN. All three come from `Settings`, so every builder
needs access to it. Widen the builder context to carry `Settings` too,
rather than growing the tuple positionally forever.

- [ ] **Step 1: Write the failing tests**

Read the current `packages/core/tests/test_pipeline_cli.py` first (`cat
packages/core/tests/test_pipeline_cli.py`) to see its existing fixtures —
`main()` is called with `sys.stdout`/`sys.stderr` captured via
`mock.patch`, and there's already a pattern for driving `_cmd_ingest`
through `main(["ingest", "--source", ...])`. Append these tests to the
existing `TestPipelineCli` class, following that same pattern:

```python
    def test_ingest_subcommand_adzuna_requires_region(self) -> None:
        """--source adzuna with no --region reports a clean error."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            exit_code = main(
                ["ingest", "--source", "adzuna", "--query", "data engineer"]
            )
        self.assertEqual(exit_code, 1)
        self.assertIn("region", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_ingest_subcommand_adzuna_requires_settings_keys(self) -> None:
        """--source adzuna with no Adzuna keys configured reports a clean error."""
        with (
            mock.patch.dict(
                os.environ,
                {"ADZUNA_APP_ID": "", "ADZUNA_APP_KEY": ""},
                clear=False,
            ),
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            get_settings.cache_clear()
            exit_code = main(
                [
                    "ingest", "--source", "adzuna", "--query", "data engineer",
                    "--region", "gb",
                ]
            )
            get_settings.cache_clear()
        self.assertEqual(exit_code, 1)
        self.assertIn("adzuna", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_ingest_subcommand_greenhouse_is_a_known_source(self) -> None:
        """--source greenhouse is recognised (fails later, on DB/network —
        not on an 'unknown source' error)."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            main(["ingest", "--source", "greenhouse", "--query", ""])
        self.assertNotIn("Unknown --source", stderr.getvalue())

    def test_ingest_subcommand_reed_requires_settings_key(self) -> None:
        """--source reed with no Reed key configured reports a clean error."""
        with (
            mock.patch.dict(os.environ, {"REED_API_KEY": ""}, clear=False),
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            get_settings.cache_clear()
            exit_code = main(
                ["ingest", "--source", "reed", "--query", "data engineer"]
            )
            get_settings.cache_clear()
        self.assertEqual(exit_code, 1)
        self.assertIn("reed", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())
```

Check the top of the existing test file for its imports — this plan's new
tests need `os` and `from core.settings import get_settings` added to
whatever's already imported there if not already present.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_pipeline_cli -v
```

Expected: FAIL — `region` validation and Adzuna-key validation don't
exist yet; `greenhouse` isn't a known source yet.

- [ ] **Step 3: Rewrite the connector-registry section of cli.py**

Read the current full file first (`cat apps/pipeline/app/cli.py`) — Step
3's final-review fix wave already made `_CONNECTOR_BUILDERS` the single
source of truth for `_KNOWN_SOURCES`; this task widens what each builder
receives, not the registry-derivation pattern itself.

Replace the block from `def _build_manual_connector` through
`_build_connector_factories` (currently lines ~57-104) with:

```python
@dataclass(frozen=True)
class _ConnectorBuildContext:
    """Everything a connector builder might need to construct its connector.

    Attributes:
        http_client: The shared HTTP client.
        llm_adapters: Every available LLM adapter, keyed by provider.
        settings: The process-wide Settings instance.
    """

    http_client: httpx.Client
    llm_adapters: dict[str, LLMAdapter]
    settings: Settings


def _build_manual_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the manual-entry connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `ManualConnector` instance.
    """
    return ManualConnector(http_client=ctx.http_client, llm_adapters=ctx.llm_adapters)


def _build_adzuna_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Adzuna connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        An `AdzunaConnector` instance.

    Raises:
        ValueError: If `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` aren't configured.
    """
    if not ctx.settings.adzuna_app_id or not ctx.settings.adzuna_app_key:
        raise ValueError(
            "source=adzuna requires ADZUNA_APP_ID and ADZUNA_APP_KEY to be "
            "set in .env"
        )
    return AdzunaConnector(
        http_client=ctx.http_client,
        app_id=ctx.settings.adzuna_app_id,
        app_key=ctx.settings.adzuna_app_key,
    )


def _build_greenhouse_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Greenhouse connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `GreenhouseConnector` instance.
    """
    return GreenhouseConnector(
        http_client=ctx.http_client, database_url=ctx.settings.database_url
    )


def _build_reed_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Reed connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `ReedConnector` instance.

    Raises:
        ValueError: If `REED_API_KEY` isn't configured.
    """
    if not ctx.settings.reed_api_key:
        raise ValueError("source=reed requires REED_API_KEY to be set in .env")
    return ReedConnector(http_client=ctx.http_client, api_key=ctx.settings.reed_api_key)


_CONNECTOR_BUILDERS: dict[str, Callable[[_ConnectorBuildContext], Connector]] = {
    "manual": _build_manual_connector,
    "adzuna": _build_adzuna_connector,
    "reed": _build_reed_connector,
    "greenhouse": _build_greenhouse_connector,
}

_KNOWN_SOURCES = frozenset(_CONNECTOR_BUILDERS)
"""Every `--source` name the CLI recognises — derived from
`_CONNECTOR_BUILDERS` so the two can never drift apart. Adding a new
connector is one new entry in `_CONNECTOR_BUILDERS`, nothing else, and
`_KNOWN_SOURCES` picks it up automatically."""


def _build_connector_factories(
    http_client: httpx.Client,
) -> dict[str, Callable[[], Connector]]:
    """Build the CLI's connector registry.

    Args:
        http_client: The shared HTTP client passed to any connector that
            needs one.

    Returns:
        A mapping of `--source` name to a zero-argument factory building
        that connector. Callers only invoke this after confirming
        `args.source in _KNOWN_SOURCES` — a builder may still raise
        ValueError for a known-but-misconfigured source (e.g. Adzuna with
        no API key), which `_cmd_ingest` catches.
    """
    ctx = _ConnectorBuildContext(
        http_client=http_client,
        llm_adapters=_build_llm_adapters(http_client),
        settings=get_settings(),
    )
    return {
        name: (lambda b=builder: b(ctx)) for name, builder in _CONNECTOR_BUILDERS.items()
    }
```

Note this drops the earlier `_make_factory` helper — with a single-argument
builder (`ctx`, not `(builder, http_client, llm_adapters)`), the
lambda-with-default-arg trick (`lambda b=builder: b(ctx)`) only closes over
one loop variable, which mypy infers correctly (it was the *combination* of
a lambda default and a comprehension-over-tuple-unpacking that broke
inference in Step 3, not the single-variable default alone — confirm this in
Step 4 below rather than assuming).

Add `from dataclasses import dataclass` to the imports if not already
present, `from core.ingestion.adzuna_connector import AdzunaConnector,
AdzunaQuery`, `from core.ingestion.reed_connector import ReedConnector,
ReedQuery`, `from core.ingestion.greenhouse_connector import
GreenhouseConnector, GreenhouseQuery`, and `from core.settings import
Settings, get_settings` (the module already imports `get_settings`; add
`Settings` to that same import line).

- [ ] **Step 4: Add query-builders for adzuna, reed and greenhouse, and rewire `_cmd_ingest`**

Add two new functions near `_build_manual_query`:

```python
def _build_adzuna_query(raw_query: str, region: str | None) -> AdzunaQuery:
    """Build an AdzunaQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value.

    Returns:
        The `AdzunaQuery`.

    Raises:
        ValueError: If `region` is not given — Adzuna's search endpoint is
            country-scoped, unlike manual entry or Greenhouse.
    """
    if not region:
        raise ValueError("--region is required for source=adzuna")
    return AdzunaQuery(keywords=raw_query, country=region)


def _build_reed_query(raw_query: str, region: str | None) -> ReedQuery:
    """Build a ReedQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value, used as an
            optional UK location filter — unlike Adzuna, Reed doesn't
            require one (it's UK-only already).

    Returns:
        The `ReedQuery`.
    """
    return ReedQuery(keywords=raw_query, location=region)


def _build_greenhouse_query(raw_query: str) -> GreenhouseQuery:
    """Build a GreenhouseQuery from --query.

    Args:
        raw_query: The `--query` argument's raw string value — a
            comma-separated list of board slugs, or empty to use the
            active target_company registry.

    Returns:
        The `GreenhouseQuery`.
    """
    slugs = [s.strip() for s in raw_query.split(",") if s.strip()]
    return GreenhouseQuery(board_slugs=slugs or None)
```

Then replace `_cmd_ingest`'s query-building `if/else` (currently just
`manual` vs. everything else passed through as a raw string) with:

```python
    try:
        query: object
        if args.source == "manual":
            query = _build_manual_query(args.query)
        elif args.source == "adzuna":
            query = _build_adzuna_query(args.query, args.region)
        elif args.source == "reed":
            query = _build_reed_query(args.query, args.region)
        elif args.source == "greenhouse":
            query = _build_greenhouse_query(args.query)
        else:
            query = args.query
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
```

This runs before `http_client = httpx.Client(...)` and before `settings =
get_settings()` — same Settings-independent-validation-first ordering
Step 3's Finding 5 established, so `--region`-missing and malformed
`--query` both fail cleanly with no DSN/API-key requirement at all. The
Adzuna-key check happens later, inside `_build_adzuna_connector`, because
it genuinely needs `Settings` — wrap the `factories[args.source]()` call
(inside the existing `try/finally` around `http_client`) in its own
try/except:

```python
        try:
            connector = factories[args.source]()
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        result = run_connector(
            connector_key=args.source,
            connector=connector,
            ...
```

(keep every other `run_connector(...)` argument exactly as it is today —
only `connector=factories[args.source]()` moves out of the call and into
the two lines above it).

- [ ] **Step 5: Update the module docstring**

The module docstring's "adding a new connector means..." sentence still
names `_CONNECTOR_BUILDERS` correctly, but check it after this edit — it
should still read true (one new file, one `_CONNECTOR_BUILDERS` entry, no
runner changes). Adjust wording only if this task's `_ConnectorBuildContext`
change makes any sentence inaccurate.

- [ ] **Step 6: Add the real `sources.yml` blocks**

Replace `config/sources.yml`'s `sources: {}` and its commented illustrative
example with:

```yaml
sources:
  adzuna:
    enabled: true
    auth: {app_id: ${ADZUNA_APP_ID}, app_key: ${ADZUNA_APP_KEY}}
    calls_per_hour: 40
    concurrency: 1
    backoff: {base: 2, max_retries: 5}
    regions: [gb]

  reed:
    enabled: true
    auth: {api_key: ${REED_API_KEY}}
    calls_per_hour: 60
    concurrency: 1
    backoff: {base: 2, max_retries: 5}
    regions: [gb]

  greenhouse:
    enabled: true
    calls_per_hour: 120
    concurrency: 1
    backoff: {base: 2, max_retries: 5}
```

Keep the file's existing top comment block (the one explaining a
connector with no entry runs unrated/unlimited) — only the illustrative
example and `sources: {}` are replaced.

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_pipeline_cli -v
```

Expected: every test in the file PASSES, including the 3 new ones and
every pre-existing one (manual entry's tests must still pass unchanged).

- [ ] **Step 8: Run the quality gate**

```bash
cd job_search
python3.11 -m black apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli.py
python3.11 -m isort apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli.py
python3.11 -m ruff check apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli.py
python3.11 -m mypy apps/pipeline/app
```

Expected: all clean. If mypy flags the `lambda b=builder: b(ctx)`
comprehension the way Step 3's did, replace it with the same named-helper
pattern Step 3 landed on (`_make_factory(builder, ctx) -> Callable[[],
Connector]: return lambda: builder(ctx)`) rather than fighting mypy
further — don't spend more than one extra attempt on the lambda form.

- [ ] **Step 9: Commit**

```bash
git add apps/pipeline/app/cli.py config/sources.yml \
  packages/core/tests/test_pipeline_cli.py
git commit -m "feat(job_search): wire Adzuna, Reed and Greenhouse into the ingest CLI"
```

---

### Task 6: Seed `target_company` with live-verified Greenhouse companies

**Files:**
- Create: `scripts/seed_target_company.py`

**Interfaces:**
- Consumes: `core.db.target_company.upsert_target_company` (Task 1),
  `core.db.session.build_engine` (existing), `core.settings.get_settings`
  (existing).
- Produces: real rows in the `target_company` table — no other task
  imports from this script; it's a one-off operational tool, run directly.

This task is honest about what it can and can't verify: the implementer
does not have a live-verified list of correct Greenhouse board slugs
memorized, and PLAN.md's "seed 20 by hand" instruction is aspirational —
guessing 20 slugs and inserting them unchecked would put wrong data in the
registry. Instead, this script tries a list of plausible candidates
against Greenhouse's real API and keeps only the ones that actually
resolve, reporting the rest as not-found so a human can research the
correct slug later if they want that company covered.

- [ ] **Step 1: Write the script**

`scripts/seed_target_company.py`:

```python
"""Seed target_company with Greenhouse companies, verified live.

Usage: python3.11 scripts/seed_target_company.py

Tries each (name, board_slug) candidate below against Greenhouse's real
public board API and upserts only the ones that return a real board (HTTP
200 with a `jobs` key) — see the docstring on this module in the
implementation plan for why this isn't a blind data insert. Run from
job_search/ with Postgres up and .env's DATABASE_URL/APP_DATABASE_URL
pointed at a reachable Postgres (localhost outside Docker).
"""

from __future__ import annotations

import sys

import httpx

sys.path.insert(0, "packages/core")

from core.db.session import build_engine  # noqa: E402
from core.db.target_company import upsert_target_company  # noqa: E402
from core.settings import get_settings  # noqa: E402

# Best-effort candidates — commonly cited as Greenhouse users, but the
# exact board_slug is unverified until this script actually checks it
# live. Extend this list with more candidates as they're researched; a
# wrong guess here just gets reported as NOT FOUND, never inserted.
_CANDIDATES: list[tuple[str, str]] = [
    ("Airbnb", "airbnb"),
    ("Stripe", "stripe"),
    ("Robinhood", "robinhood"),
    ("Coinbase", "coinbase"),
    ("DoorDash", "doordash"),
    ("Notion", "notion"),
    ("Figma", "figma"),
    ("Discord", "discord"),
    ("Instacart", "instacart"),
    ("Reddit", "reddit"),
    ("Asana", "asana"),
    ("Pinterest", "pinterest"),
]


def _board_is_real(client: httpx.Client, board_slug: str) -> bool:
    """Check whether a Greenhouse board slug resolves to a real board.

    Args:
        client: The HTTP client to check with.
        board_slug: The candidate board token.

    Returns:
        True if the board endpoint returns HTTP 200 with a `jobs` key.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs"
    try:
        response = client.get(url, timeout=10.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200 and "jobs" in response.json()


def main() -> int:
    """Check every candidate live and upsert the ones that resolve.

    Returns:
        0 always — a candidate not resolving is reported, not an error.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)

    kept: list[str] = []
    dropped: list[str] = []
    with httpx.Client() as client:
        for name, board_slug in _CANDIDATES:
            if _board_is_real(client, board_slug):
                kept.append(f"{name} ({board_slug})")
                with engine.connect() as conn:
                    upsert_target_company(
                        conn, name=name, ats_provider="greenhouse",
                        board_slug=board_slug,
                    )
                    conn.commit()
            else:
                dropped.append(f"{name} ({board_slug})")

    print(f"Seeded {len(kept)} verified companies:")
    for line in kept:
        print(f"  OK   {line}")
    print(f"Dropped {len(dropped)} unverified candidates (not a real board):")
    for line in dropped:
        print(f"  MISS {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it live**

```bash
cd job_search
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
python3.11 scripts/seed_target_company.py
```

Record the real output (kept/dropped counts and names) — this is what the
final report to the user should quote verbatim, not a guessed number.
It's fine — expected, even — if several candidates come back MISS; keep
whatever the live run actually confirms.

- [ ] **Step 3: Verify the registry**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT name, board_slug, active FROM target_company ORDER BY name;"
```

Expected: exactly the companies Step 2 reported as `OK`, each with
`active = t`.

- [ ] **Step 4: Commit**

```bash
git add scripts/seed_target_company.py
git commit -m "feat(job_search): add live-verified target_company seed script"
```

---

### Task 7: Live integration tests

**Files:**
- Create: `packages/core/tests/integration/test_adzuna_connector_live.py`
- Create: `packages/core/tests/integration/test_reed_connector_live.py`
- Create: `packages/core/tests/integration/test_greenhouse_connector_live.py`

**Interfaces:**
- Consumes: `AdzunaConnector`/`AdzunaQuery` (Task 3), `ReedConnector`/
  `ReedQuery` (Task 4), `GreenhouseConnector`/`GreenhouseQuery` (Task 2),
  `core.settings.get_settings` (existing).

These are the only three tests in this plan that make real network calls —
by design (`python-testing.md`'s "Integration points: ... external API
calls (use real connections in integration tests, not mocks)"). All three
must skip cleanly rather than fail when their precondition isn't met.

- [ ] **Step 1: Write the Adzuna live test**

`packages/core/tests/integration/test_adzuna_connector_live.py`:

```python
"""Live integration test — makes a real call to the Adzuna API.

Skips cleanly if ADZUNA_APP_ID/ADZUNA_APP_KEY aren't configured.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.adzuna_connector import AdzunaConnector, AdzunaQuery
from core.settings import get_settings


class TestAdzunaConnectorLive(unittest.TestCase):
    """Proves AdzunaConnector works against the real Adzuna API."""

    @classmethod
    def setUpClass(cls) -> None:
        """Skip the whole class if Adzuna credentials aren't configured."""
        settings = get_settings()
        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            raise unittest.SkipTest(
                "ADZUNA_APP_ID/ADZUNA_APP_KEY not set in .env — skipping "
                "the live Adzuna test."
            )
        cls.app_id = settings.adzuna_app_id
        cls.app_key = settings.adzuna_app_key

    def test_fetch_returns_real_results_for_a_common_query(self) -> None:
        """A broad, common query returns at least one real, well-formed RawJob."""
        with httpx.Client() as client:
            connector = AdzunaConnector(
                http_client=client, app_id=self.app_id, app_key=self.app_key
            )
            jobs = list(
                connector.fetch(
                    AdzunaQuery(keywords="data engineer", country="gb", max_pages=1),
                    None,
                    run_id="live-test-run",
                )
            )
        self.assertGreater(len(jobs), 0)
        first = jobs[0]
        self.assertEqual(first.source_name, "adzuna")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertTrue(first.source_job_id)
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write the Reed live test**

`packages/core/tests/integration/test_reed_connector_live.py`:

```python
"""Live integration test — makes a real call to the Reed API.

Skips cleanly if REED_API_KEY isn't configured.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.reed_connector import ReedConnector, ReedQuery
from core.settings import get_settings


class TestReedConnectorLive(unittest.TestCase):
    """Proves ReedConnector works against the real Reed API."""

    @classmethod
    def setUpClass(cls) -> None:
        """Skip the whole class if a Reed API key isn't configured."""
        settings = get_settings()
        if not settings.reed_api_key:
            raise unittest.SkipTest(
                "REED_API_KEY not set in .env — skipping the live Reed test."
            )
        cls.api_key = settings.reed_api_key

    def test_fetch_returns_real_results_for_a_common_query(self) -> None:
        """A broad, common query returns at least one real, well-formed RawJob."""
        with httpx.Client() as client:
            connector = ReedConnector(http_client=client, api_key=self.api_key)
            jobs = list(
                connector.fetch(
                    ReedQuery(keywords="data engineer", max_pages=1),
                    None,
                    run_id="live-test-run",
                )
            )
        self.assertGreater(len(jobs), 0)
        first = jobs[0]
        self.assertEqual(first.source_name, "reed")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertTrue(first.source_job_id)
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Write the Greenhouse live test**

`packages/core/tests/integration/test_greenhouse_connector_live.py`:

```python
"""Live integration test — makes a real call to a public Greenhouse board.

Skips cleanly if the network call fails (offline, DNS, etc.) rather than
failing the suite.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.greenhouse_connector import GreenhouseConnector, GreenhouseQuery


class TestGreenhouseConnectorLive(unittest.TestCase):
    """Proves GreenhouseConnector works against a real public board."""

    def test_fetch_returns_real_jobs_for_a_known_public_board(self) -> None:
        """A well-known public Greenhouse board yields real, well-formed RawJobs."""
        with httpx.Client() as client:
            connector = GreenhouseConnector(
                http_client=client, database_url="unused"
            )
            try:
                jobs = list(
                    connector.fetch(
                        GreenhouseQuery(board_slugs=["stripe"]),
                        None,
                        run_id="live-test-run",
                    )
                )
            except httpx.HTTPError as exc:
                raise unittest.SkipTest(f"Network unreachable: {exc}") from None

        if not jobs:
            raise unittest.SkipTest(
                "stripe's Greenhouse board returned zero open roles right "
                "now — inconclusive, not a failure."
            )
        first = jobs[0]
        self.assertEqual(first.source_name, "greenhouse")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run all three live**

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_test" \
python3.11 -m unittest tests.integration.test_adzuna_connector_live \
  tests.integration.test_reed_connector_live \
  tests.integration.test_greenhouse_connector_live -v
```

Expected: all three PASS (or SKIP with a clear reason — never a hard
FAIL). If Adzuna or Reed genuinely fails (not skips) — e.g. a 401 — stop
and report it: that means the real key in `.env` doesn't work, which is a
finding to surface, not paper over.

- [ ] **Step 5: Commit**

```bash
git add packages/core/tests/integration/test_adzuna_connector_live.py \
  packages/core/tests/integration/test_reed_connector_live.py \
  packages/core/tests/integration/test_greenhouse_connector_live.py
git commit -m "test(job_search): add live Adzuna, Reed and Greenhouse integration tests"
```

---

### Task 8: Full-stack verification (controller-run, not dispatched)

This task is the acceptance-criterion proof, run directly by whoever is
driving this plan (matching Step 3's Task 10 pattern) — not dispatched to
a fresh implementer subagent, since it's verification of the whole branch
rather than a new deliverable.

- [ ] **Step 1: Confirm Postgres is up and migrated**

```bash
cd job_search
docker compose up -d postgres
docker compose exec -T postgres pg_isready
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
python3.11 -m alembic -c db/alembic.ini current
```

Expected: `0005 (head)`.

- [ ] **Step 2: Run the full test suite**

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_verify" \
coverage run -m unittest discover
coverage report -m
```

Expected: every test PASSES or SKIPS cleanly (no hard failures); coverage
at or above the pre-existing 95% baseline.

- [ ] **Step 3: Run the acceptance command for real, via Docker**

```bash
cd job_search
docker compose --profile cli run --rm pipeline \
  ingest --source adzuna --query "data engineer" --region gb
```

Expected: prints `ingest complete: source=adzuna records=N run_id=...`
with `N > 0`. Then confirm a real bronze row landed:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM bronze.raw_jobs WHERE source_name = 'adzuna';"
```

Expected: count > 0.

- [ ] **Step 4: Run the same for Reed**

```bash
docker compose --profile cli run --rm pipeline \
  ingest --source reed --query "data engineer"
```

Expected: prints `ingest complete: source=reed records=N run_id=...` with
`N > 0`. Confirm:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM bronze.raw_jobs WHERE source_name = 'reed';"
```

Expected: count > 0.

- [ ] **Step 5: Run the same for Greenhouse**

```bash
docker compose --profile cli run --rm pipeline \
  ingest --source greenhouse --query ""
```

(Empty `--query` means "use the target_company registry" — Task 6 must
have seeded at least one company for this to produce records; if Task 6's
live check found zero verified candidates, pass an explicit known-good
slug instead: `--query "stripe"`.)

Expected: prints `ingest complete: source=greenhouse records=N ...` with
`N > 0`. Confirm:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM bronze.raw_jobs WHERE source_name = 'greenhouse';"
```

Expected: count > 0.

- [ ] **Step 6: Quality gate, one last time**

```bash
cd job_search
python3.11 -m black --check .
python3.11 -m isort --check-only .
python3.11 -m ruff check .
python3.11 -m mypy packages/core/core
python3.11 -m mypy apps/api/app
python3.11 -m mypy apps/pipeline/app
```

Expected: all clean.

- [ ] **Step 7: Tear down**

```bash
docker compose down
```

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage** against `STEP-04`'s subtasks: Adzuna registered +
  paginated connector (Tasks 3, 5, 8) ✓; Reed registered + connector
  (Tasks 4, 5, 8) ✓ — originally deferred, folded back in mid-authoring
  once the key arrived, before any task was dispatched; Greenhouse
  connector (Task 2) ✓; `target_company` table (Task 1) ✓; seed with 20
  companies — scoped down to "seed whatever live-verifies, report the
  rest as not-found" (Task 6), with the reasoning spelled out so a future
  reviewer doesn't read a smaller-than-20 count as a defect; all three in
  `sources.yml` with conservative limits (Task 5 Step 6) ✓; every landed
  record carries `source_name`/`job_url` — structurally guaranteed by
  `RawJob`'s non-optional fields and `bronze.raw_jobs`'s `NOT NULL`
  constraints from Step 2/3, not re-asserted redundantly here.
- Every task's Interfaces section cross-checked against the next task
  that consumes it — `TargetCompany`/`list_active_companies` (Task 1) used
  correctly by Task 2; `AdzunaQuery`/`ReedQuery`/`GreenhouseQuery` (Tasks
  2-4) used correctly by Task 5's query-builders; Task 5's
  `_ConnectorBuildContext` is new and only Task 5 touches it.
- **Placeholder scan:** Task 3 (AdzunaConnector) originally drafted a
  convoluted `max_days_old` computation in one step and "fixed" it in the
  next — a real authoring mistake, not an intentional TDD step. Corrected
  in place: Task 3 Step 3 now contains the correct computation directly,
  no separate fix-up step. Task 5 Step 5 (module docstring) was
  originally vague ("adjust only if...") — left as a verification step
  since the existing docstring's claim (one new file, one
  `_CONNECTOR_BUILDERS` entry, no runner changes) remains true after this
  task's changes and doesn't need rewriting, but flagged here so the
  implementer knows to actually check, not skip.
- **Live-verification note:** every field name this plan's Adzuna, Reed,
  and Greenhouse code depends on was confirmed against the real APIs
  during authoring (see the plan header) — including one genuine
  correction this caught: Reed's search endpoint truncates
  `jobDescription` to ~500 characters itself, which the plan documents as
  expected behaviour rather than something Task 4 should "fix".
- **Type consistency:** `AdzunaConnector.fetch`/`ReedConnector.fetch`/
  `GreenhouseConnector.fetch` all declare their concrete query type
  (`AdzunaQuery`/`ReedQuery`/`GreenhouseQuery`) rather than `Connector`
  Protocol's `object` — same pattern `ManualConnector` already uses and
  that Step 3's mypy review already confirmed type-checks cleanly, so
  this isn't new risk.
