# Step 4a — Discovery Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second collection channel, `collection_channel` (`targeted` |
`discovery`), that isn't bound by a frozen keyword matrix — so a role
title nobody thought to search for can still surface. Discovery mode
reuses the existing connectors: `GreenhouseConnector` already dumps a
company's *entire* board with no keyword filter (structurally already
"discovery" behaviour), and `AdzunaConnector` gains a category-sweep mode
as a genuinely new capability.

**Architecture:** `collection_channel` is a new `run_connector`/
`load_to_bronze` parameter (mirrors the existing `entry_method` parameter
exactly — same shape, same "stamped onto every bronze row" job, defaulted
to `"targeted"` so none of the ~10 existing call sites across Step 3/4's
already-reviewed tests need to change). Adzuna's category-sweep is a new
optional field on `AdzunaQuery`, wired through the CLI's existing
`_QUERY_BUILDERS`/`_CONNECTOR_BUILDERS` registries — no new registry
mechanism, no runner-agnostic redesign.

**Tech Stack:** Python 3.11, `httpx`, `unittest` + `coverage`, Alembic.

**Spec:** `PLAN.md`'s "Step 4a — Discovery corpus" section and
`plan/backlog.yml`'s `STEP-04A` entry (`jira_key: JOB-76`).

## Real API shape below is live-verified, not recalled

Adzuna's category API was confirmed live during planning using the real
registered credentials: `GET /v1/api/jobs/{country}/categories` returns
`{"results": [{"tag": "it-jobs", "label": "IT Jobs"}, ...]}`; the search
endpoint accepts `category=<tag>` as an alternative to (not requiring)
`what=<keywords>` — a category-only search for `it-jobs` returned
`count: 46833` real results with no `what` param sent at all, confirming
the "wide and shallow" design intent for real (a single keyword query
returns nowhere near that volume).

## Scope note — what this plan builds vs. documents

`STEP-04A`'s 8 subtasks split into two kinds. Five are genuinely
implementable now and are built by Tasks 1-4 below: the `collection_channel`
column, Adzuna's category sweep, the CLI wiring (including a volume cap
and a diversity-yield log line), and growing `target_company`. Three are
not code this project is ready to write yet, and this plan does not
fabricate premature infrastructure for them — each is handled by
documenting the decision, not skipping it silently:

- **"Lower frequency than targeted collection (weekly, not daily)"** — no
  scheduler/orchestrator exists in this codebase yet (that's Phase 6,
  much later in `PLAN.md`). Documented as an operational note in
  `config/sources.yml`'s comments and the CLI's `--collection-channel`
  help text, not enforced in code that has nothing to run against yet.
- **"Exclude discovery records from all targeted-channel trend metrics by
  default"** — no trend-metric dbt models exist yet (Phase 3+). Adding
  `collection_channel` as a real, queryable, NOT NULL bronze column
  *is* what makes that future exclusion possible — there is no metrics
  code to touch today. Task 5 confirms the column round-trips correctly
  as the acceptance proof.
- **"Grow the target-company registry aggressively"** is an ongoing,
  open-ended operational task, not a one-time deliverable. Task 4 makes
  concrete, honest progress (extends the seed script's live-verified
  candidate list and re-runs it), not a claim of "done."

## Global Constraints

- Python 3.11, `black` (88 cols) + `isort` (profile black) + `ruff` +
  `mypy`.
- `unittest` + `coverage`, never `pytest`.
- Docstrings: Google-style (Args/Returns/Raises), on every function/class.
- Type hints required on all public function signatures; `from __future__
  import annotations`.
- `collection_channel` defaults to `"targeted"` everywhere it's threaded
  through — this is a backward-compatible addition. Do not touch the ~10
  existing `run_connector(...)` call sites across `test_runner.py`/
  `test_runner_bronze.py` that don't pass it; they must keep passing
  unmodified.
- `docker compose up -d postgres` must be running for Task 1's migration
  and Task 5's live verification.

---

### Task 1: `collection_channel` — migration, bronze, runner

**Files:**
- Create: `db/migrations/versions/0006_add_collection_channel.py`
- Modify: `packages/core/core/ingestion/bronze.py`
- Modify: `packages/core/core/ingestion/runner.py`
- Test: `packages/core/tests/test_runner.py` (append, don't modify existing)
- Test: `packages/core/tests/integration/test_runner_bronze.py` (append)

**Interfaces:**
- Produces: `load_to_bronze(..., collection_channel: str = "targeted")`,
  `run_connector(..., collection_channel: str = "targeted")` — consumed
  by Task 3 (cli.py wiring).

- [ ] **Step 1: Write the migration**

`db/migrations/versions/0006_add_collection_channel.py`:

```python
"""add collection_channel to bronze.raw_jobs

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04

collection_channel distinguishes the frozen-keyword-matrix "targeted"
channel from the wide-and-shallow "discovery" channel (PLAN.md Step 4a) —
the same job entry_method (0004) already does for a different axis.
NOT NULL with a server default of 'targeted' so every pre-existing row
(all of it collected before this column existed, all of it via keyword-
bound queries) is correctly backfilled without a data migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_jobs",
        sa.Column(
            "collection_channel",
            sa.Text(),
            nullable=False,
            server_default="targeted",
        ),
        schema="bronze",
    )
    op.create_check_constraint(
        "ck_bronze_raw_jobs_collection_channel",
        "raw_jobs",
        "collection_channel IN ('targeted', 'discovery')",
        schema="bronze",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_bronze_raw_jobs_collection_channel", "raw_jobs", schema="bronze"
    )
    op.drop_column("raw_jobs", "collection_channel", schema="bronze")
```

- [ ] **Step 2: Run the migration**

From `job_search/db`, with Postgres up:

```bash
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
python3.11 -m alembic upgrade head
```

Expected: no errors; `alembic current` (same env vars) prints `0006 (head)`.

- [ ] **Step 3: Update `bronze.py`**

In `packages/core/core/ingestion/bronze.py`, add `collection_channel: str
= "targeted"` as a new keyword parameter to `load_to_bronze`, document it
in the docstring's Args (mirroring the existing `entry_method` line), add
it to the `record` dict, and — this is the only part requiring care —
widen `primary_key` to keep `collection_channel` OUT of the merge key
(it must not become part of dedup identity; a job seen via both channels
is still the same posting). No other line changes.

```python
def load_to_bronze(
    *,
    database_url: str,
    source_name: str,
    source_job_id: str,
    job_url: str,
    job_url_canonical: str,
    entry_method: str,
    fetched_at: datetime.datetime,
    run_id: str,
    request_params: dict[str, object],
    payload: dict[str, object],
    payload_sha256: str,
    collection_channel: str = "targeted",
) -> None:
    """Load one raw-job record into bronze.raw_jobs via a dlt merge load.

    Args:
        database_url: The migration/owner Postgres DSN. This is a batch
            pipeline operation, not a live per-user query — see the
            two-zone rule in docs/tenancy.md — so it connects with the
            same trust level as Alembic, never the RLS-enforced app role.
        source_name: The source this record came from, e.g. "linkedin_manual".
        source_job_id: The extracted or hashed job identifier.
        job_url: The original (uncanonicalised) job URL.
        job_url_canonical: The canonicalised job URL.
        entry_method: "manual", "api", or "scraped".
        fetched_at: When this record was captured.
        run_id: The ULID identifying this ingestion run.
        request_params: Any request parameters that produced this record
            (empty for manual entry).
        payload: The full record payload, including `raw_text`.
        payload_sha256: SHA-256 hex digest of the payload's dedup-relevant
            content — together with source_name/source_job_id, this is the
            merge key that gives the no-op/new-version-row behaviour.
        collection_channel: "targeted" (frozen keyword matrix, PLAN.md
            Step 21a) or "discovery" (wide/shallow, PLAN.md Step 4a). Not
            part of the merge key — the same posting seen via both
            channels stays one row.

    Raises:
        Exception: Whatever dlt raises on a genuine load failure (network,
            schema conflict). Not caught here — the caller decides whether
            a bronze-load failure should fail the whole ingest request.
    """
    record = {
        "source_name": source_name,
        "source_job_id": source_job_id,
        "job_url": job_url,
        "job_url_canonical": job_url_canonical,
        "entry_method": entry_method,
        "fetched_at": fetched_at,
        "run_id": run_id,
        "request_params": request_params,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "collection_channel": collection_channel,
    }

    resource = dlt.resource(
        [record],
        name="raw_jobs",
        table_name="raw_jobs",
        write_disposition="merge",
        primary_key=("source_name", "source_job_id", "payload_sha256"),
        columns={
            "request_params": {"data_type": "json"},
            "payload": {"data_type": "json"},
        },
        max_table_nesting=0,
    )

    pipeline = dlt.pipeline(
        pipeline_name="job_search_bronze",
        destination=dlt.destinations.postgres(credentials=_to_dlt_dsn(database_url)),
        dataset_name="bronze",
    )
    pipeline.run(resource)
```

- [ ] **Step 4: Update `runner.py`**

In `packages/core/core/ingestion/runner.py`, add `collection_channel: str
= "targeted"` as a new keyword-only parameter to `run_connector` (place it
right after `entry_method` in both the signature and the docstring's
Args, matching the existing ordering), and pass it through in the single
`load_to_bronze_fn(...)` call:

```python
def run_connector(
    *,
    connector_key: str,
    connector: Connector,
    query: object,
    since: datetime.datetime | None,
    entry_method: str,
    collection_channel: str = "targeted",
    landing_uri: str,
    database_url: str,
    rate_limiter: TokenBucket | None = None,
    retry_base: float = 2.0,
    retry_max_retries: int = 5,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    load_to_bronze_fn: Callable[..., None] = load_to_bronze,
    write_landing_record_fn: Callable[..., str] = write_landing_record,
    write_run_metadata_fn: Callable[..., str] = write_run_metadata,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = random.random,
) -> RunResult:
```

Add to the docstring's Args, right after the `entry_method` line:

```
        collection_channel: "targeted" (default) or "discovery" — stamped
            onto every bronze row this run produces, alongside
            entry_method. See PLAN.md Step 4a.
```

And in the `load_to_bronze_fn(...)` call inside the per-`raw_job` loop,
add the one new argument:

```python
        load_to_bronze_fn(
            database_url=database_url,
            source_name=raw_job.source_name,
            source_job_id=raw_job.source_job_id,
            job_url=raw_job.job_url,
            job_url_canonical=raw_job.job_url_canonical,
            entry_method=entry_method,
            collection_channel=collection_channel,
            fetched_at=raw_job.fetched_at,
            run_id=raw_job.run_id,
            request_params=raw_job.request_params,
            payload=raw_job.payload,
            payload_sha256=raw_job.payload_sha256,
        )
```

No other line in `runner.py` changes. Every existing call site that
constructs `run_connector(...)` without `collection_channel` keeps
working unchanged (defaults to `"targeted"`).

- [ ] **Step 5: Write the new unit tests**

Read `packages/core/tests/test_runner.py`'s existing fixtures first (`cat
packages/core/tests/test_runner.py`) — it already has a `_FakeConnector`,
a `_fake_load_to_bronze` capturing calls into `self.load_to_bronze_calls`,
and a `TestRunConnector` class. Append these two tests to that class,
following its existing structure exactly:

```python
    def test_collection_channel_defaults_to_targeted(self) -> None:
        """A call with no collection_channel argument stamps 'targeted'."""
        connector = _FakeConnector([_sample_job()])
        run_connector(
            connector_key="fake",
            connector=connector,
            query="my query",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(self.load_to_bronze_calls[0]["collection_channel"], "targeted")

    def test_collection_channel_discovery_is_threaded_through(self) -> None:
        """An explicit collection_channel="discovery" reaches load_to_bronze_fn."""
        connector = _FakeConnector([_sample_job()])
        run_connector(
            connector_key="fake",
            connector=connector,
            query="my query",
            since=None,
            entry_method="api",
            collection_channel="discovery",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(self.load_to_bronze_calls[0]["collection_channel"], "discovery")
```

If `self.load_to_bronze_calls` isn't already how the existing
`_fake_load_to_bronze` records its kwargs, read the actual helper and
adapt these two tests to match its real recording mechanism — the
assertions' intent (default is "targeted", explicit value is threaded
through) must hold regardless of the exact fixture shape.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_runner -v
```

Expected: every test in the file passes — the pre-existing ones
unmodified, plus the 2 new ones.

- [ ] **Step 7: Write the new integration test**

Read `packages/core/tests/integration/test_runner_bronze.py`'s existing
`TestRunConnectorBronzeIntegration` class first. Append one test:

```python
    def test_discovery_collection_channel_lands_in_bronze(self) -> None:
        """A discovery-channel run's bronze row carries collection_channel."""
        result = run_connector(
            connector_key="runner_it_test",
            connector=_OneJobConnector(self.source_job_id),
            query="integration-test-query",
            since=None,
            entry_method="api",
            collection_channel="discovery",
            landing_uri=self.landing_uri,
            database_url=get_settings().database_url,
        )
        self.assertEqual(len(result.raw_jobs), 1)

        with session_scope(self.migration_engine) as conn:
            row = conn.execute(
                text(
                    "SELECT collection_channel FROM bronze.raw_jobs "
                    "WHERE source_name = 'runner_it_test' AND source_job_id = :sjid"
                ),
                {"sjid": self.source_job_id},
            ).one()
        self.assertEqual(row.collection_channel, "discovery")
```

- [ ] **Step 8: Run the integration test**

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_test" \
python3.11 -m unittest tests.integration.test_runner_bronze -v
```

Expected: 2 tests, both PASS (the pre-existing one, plus this new one).

- [ ] **Step 9: Run the quality gate**

```bash
cd job_search
python3.11 -m black db/migrations/versions/0006_add_collection_channel.py \
  packages/core/core/ingestion/bronze.py \
  packages/core/core/ingestion/runner.py \
  packages/core/tests/test_runner.py \
  packages/core/tests/integration/test_runner_bronze.py
python3.11 -m isort db/migrations/versions/0006_add_collection_channel.py \
  packages/core/core/ingestion/bronze.py \
  packages/core/core/ingestion/runner.py \
  packages/core/tests/test_runner.py \
  packages/core/tests/integration/test_runner_bronze.py
python3.11 -m ruff check db/migrations/versions/0006_add_collection_channel.py \
  packages/core/core/ingestion/bronze.py \
  packages/core/core/ingestion/runner.py \
  packages/core/tests/test_runner.py \
  packages/core/tests/integration/test_runner_bronze.py
python3.11 -m mypy packages/core/core
```

Expected: all clean.

- [ ] **Step 10: Commit**

```bash
git add db/migrations/versions/0006_add_collection_channel.py \
  packages/core/core/ingestion/bronze.py \
  packages/core/core/ingestion/runner.py \
  packages/core/tests/test_runner.py \
  packages/core/tests/integration/test_runner_bronze.py
git commit -m "feat(job_search): add collection_channel to run_connector and bronze"
```

---

### Task 2: AdzunaConnector category-sweep mode

**Files:**
- Modify: `packages/core/core/ingestion/adzuna_connector.py`
- Test: `packages/core/tests/test_adzuna_connector.py` (append)

**Interfaces:**
- Produces: `AdzunaQuery(keywords: str = "", category: str | None = None,
  ...)` — the widened dataclass — consumed by Task 3's `_build_adzuna_query`.

Read the current full `adzuna_connector.py` first (`cat
packages/core/core/ingestion/adzuna_connector.py`) — this task widens it,
it doesn't replace it.

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_adzuna_connector.py`'s
`TestAdzunaConnector` class:

```python
    def test_category_sweep_sends_category_param_without_what(self) -> None:
        """A category-only query omits `what` and sends `category`."""

        def fake_fetch_page(http_client, *, app_id, app_key, country, page,
                             results_per_page, what, category, max_days_old):
            self.calls.append({"what": what, "category": category})
            return {"results": [_result(1, "Some Role")], "count": 1}

        connector = AdzunaConnector(
            http_client=self.http_client, app_id="test-id", app_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )
        list(
            connector.fetch(
                AdzunaQuery(keywords="", category="it-jobs", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(self.calls[0]["what"], "")
        self.assertEqual(self.calls[0]["category"], "it-jobs")

    def test_targeted_query_sends_what_without_category(self) -> None:
        """The pre-existing keyword-search path is unaffected — category is None."""

        def fake_fetch_page(http_client, *, app_id, app_key, country, page,
                             results_per_page, what, category, max_days_old):
            self.calls.append({"what": what, "category": category})
            return {"results": [_result(1, "Data Engineer")], "count": 1}

        connector = AdzunaConnector(
            http_client=self.http_client, app_id="test-id", app_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )
        list(
            connector.fetch(
                AdzunaQuery(keywords="data engineer", country="gb"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(self.calls[0]["what"], "data engineer")
        self.assertIsNone(self.calls[0]["category"])
```

Note these two tests widen every existing `fake_fetch_page` closure in
this file's other tests too — `_fetch_page_fn` is now always called with
a `category` keyword argument (even when `None`), so every pre-existing
test's `fake_fetch_page` signature (`def fake_fetch_page(http_client, *,
app_id, app_key, country, page, results_per_page, what, max_days_old)`)
must gain `category,` in its parameter list too, or those tests will
start raising `TypeError: fake_fetch_page() got an unexpected keyword
argument 'category'`. Update every existing `fake_fetch_page` definition
in this file to accept `category` (it can just ignore the value in tests
that don't care about it).

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_adzuna_connector -v
```

Expected: FAIL — `AdzunaQuery` has no `category` field yet, and
`fetch_page_fn` isn't called with a `category` kwarg yet.

- [ ] **Step 3: Widen the implementation**

In `packages/core/core/ingestion/adzuna_connector.py`:

Widen `AdzunaQuery`:

```python
@dataclass(frozen=True)
class AdzunaQuery:
    """AdzunaConnector's `query` type.

    Attributes:
        keywords: Free-text search terms, e.g. "data engineer". Empty
            string is valid when `category` is given — see `category`.
        country: Adzuna's two-letter country code, e.g. "gb", "us".
        category: An Adzuna category tag (e.g. "it-jobs", "engineering-jobs"
            — the full list is at Adzuna's `/categories` endpoint, not
            called by this connector). When given, this is a discovery-
            style category sweep instead of a keyword search — live-
            verified during planning to work with `keywords=""` (no
            `what` param sent at all). `None` (the default) means a
            normal keyword search.
        max_pages: Safety cap on API calls per fetch() — pagination also
            stops early on a short page. Defaults to 5 (up to 250 results
            at the default results_per_page).
        results_per_page: Results requested per page. Adzuna's own max is
            50.
    """

    keywords: str = ""
    country: str = ""
    category: str | None = None
    max_pages: int = _DEFAULT_MAX_PAGES
    results_per_page: int = _DEFAULT_RESULTS_PER_PAGE
```

Note `country` gained a default too, purely so `category` (which has a
default) can follow `keywords` (which also needs one now) without
violating "non-default argument follows default argument" — every
existing call site already passes `country=` explicitly by keyword, so
this is not a behavioural change.

Widen `_fetch_adzuna_page`:

```python
def _fetch_adzuna_page(
    http_client: httpx.Client,
    *,
    app_id: str,
    app_key: str,
    country: str,
    page: int,
    results_per_page: int,
    what: str,
    category: str | None,
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
        what: Free-text keyword query. Omitted from the request entirely
            when empty (a category-only sweep sends no `what` at all).
        category: An Adzuna category tag for a category sweep, or `None`
            for a normal keyword search.
        max_days_old: Only return postings at most this many days old, or
            `None` for no age filter.

    Returns:
        The parsed JSON response body (`{"results": [...], "count": N}`).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    params: dict[str, str | int] = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": results_per_page,
        "content-type": "application/json",
    }
    if what:
        params["what"] = what
    if category:
        params["category"] = category
    if max_days_old is not None:
        params["max_days_old"] = max_days_old

    response = http_client.get(
        _ADZUNA_SEARCH_URL.format(country=country, page=page),
        params=params,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()
```

In `AdzunaConnector.fetch()`, add `category=query.category,` to the
`self._fetch_page_fn(...)` call (right after `what=query.keywords,`), and
add `"category": query.category,` to the `request_params` dict passed to
`RawJob(...)` (right after `"what": query.keywords,`). No other line in
`fetch()` changes.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_adzuna_connector -v
```

Expected: every test in the file PASSES — the 6 pre-existing ones
(updated for the new `category` kwarg per Step 1's note) plus the 2 new
ones. 8 total.

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
git commit -m "feat(job_search): add category-sweep mode to AdzunaConnector"
```

---

### Task 3: CLI wiring — `--collection-channel`, discovery-mode volume cap, diversity-yield logging

**Files:**
- Modify: `apps/pipeline/app/cli.py`
- Modify: `config/sources.yml`
- Test: `packages/core/tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: `AdzunaQuery.category` (Task 2), `run_connector(...,
  collection_channel=...)` (Task 1).

Read the current full `apps/pipeline/app/cli.py` first — this widens the
existing `_QUERY_BUILDERS` uniform signature from `(raw_query, region) ->
object` to `(raw_query, region, collection_channel) -> object`, the same
"every builder accepts it, only the one that needs it uses it" pattern
already established for `region` (which `manual`/`greenhouse` already
ignore).

- [ ] **Step 1: Write the failing tests**

Append to `TestPipelineCli` in `packages/core/tests/test_pipeline_cli.py`:

```python
    def test_ingest_subcommand_defaults_collection_channel_to_targeted(
        self,
    ) -> None:
        """--collection-channel omitted defaults to 'targeted', accepted cleanly."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            main(["ingest", "--source", "greenhouse", "--query", ""])
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_ingest_subcommand_rejects_unknown_collection_channel(self) -> None:
        """An invalid --collection-channel value is rejected by argparse."""
        with (
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
            self.assertRaises(SystemExit),
        ):
            main(
                [
                    "ingest", "--source", "greenhouse", "--query", "",
                    "--collection-channel", "nonsense",
                ]
            )
        self.assertIn("collection-channel", stderr.getvalue().lower())

    def test_adzuna_discovery_mode_interprets_query_as_category(self) -> None:
        """--collection-channel discovery treats --query as an Adzuna category tag."""
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
                    "ingest", "--source", "adzuna", "--query", "it-jobs",
                    "--region", "gb", "--collection-channel", "discovery",
                ]
            )
            get_settings.cache_clear()
        # Reaches the (missing-key) connector-build error, not a query-
        # building error — proves --query was accepted as a category, not
        # rejected as empty/invalid keywords.
        self.assertEqual(exit_code, 1)
        self.assertIn("adzuna", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_pipeline_cli -v
```

Expected: FAIL — `--collection-channel` isn't a recognised argument yet.

- [ ] **Step 3: Add the `--collection-channel` argparse flag**

In `main()`, add after the existing `--region` argument:

```python
    ingest_parser.add_argument(
        "--collection-channel",
        default="targeted",
        choices=["targeted", "discovery"],
        help=(
            "'targeted' (default, frozen keyword matrix) or 'discovery' "
            "(wide/shallow, PLAN.md Step 4a). Run discovery sweeps at most "
            "weekly by hand — no scheduler exists yet to enforce this."
        ),
    )
```

- [ ] **Step 4: Widen `_QUERY_BUILDERS` to a 3-argument uniform signature**

Change every query-builder function's signature from `(raw_query: str,
region: str | None)` to `(raw_query: str, region: str | None,
collection_channel: str)`, and update each one's docstring to document
the new parameter — for the four that ignore it (`manual`, `reed`,
`greenhouse`, `jooble`), the same one-line "unused, present only for
uniform signature" pattern already used for `region` in
`_build_manual_query`/`_build_greenhouse_query`.

`_build_adzuna_query` is the one that uses it:

```python
def _build_adzuna_query(
    raw_query: str, region: str | None, collection_channel: str
) -> AdzunaQuery:
    """Build an AdzunaQuery from --query, --region, and --collection-channel.

    Args:
        raw_query: The `--query` argument's raw string value — keywords
            in targeted mode, an Adzuna category tag in discovery mode
            (see `collection_channel`).
        region: The `--region` argument's raw string value.
        collection_channel: "targeted" or "discovery". In discovery mode,
            `raw_query` is interpreted as a category tag (e.g.
            "it-jobs") instead of free-text keywords, and pagination is
            capped lower (2 pages instead of the default 5) — a category
            sweep returns far more results per page than a keyword
            search, so the existing per-page cap alone isn't a
            meaningful volume limit for this mode.

    Returns:
        The `AdzunaQuery`.

    Raises:
        ValueError: If `region` is not given, or if `raw_query` is empty
            in targeted mode (a category tag is required in discovery
            mode instead, and empty is valid there via `--query ""`... but
            for clarity this implementation still requires a non-empty
            `raw_query` in BOTH modes — an empty string is never a
            meaningful category tag either).
    """
    if not region:
        raise ValueError("--region is required for source=adzuna")
    if not raw_query:
        raise ValueError(
            "--query is required for source=adzuna (keywords in targeted "
            "mode, a category tag like 'it-jobs' in discovery mode)"
        )
    if collection_channel == "discovery":
        return AdzunaQuery(
            keywords="", category=raw_query, country=region, max_pages=2
        )
    return AdzunaQuery(keywords=raw_query, country=region)
```

Update `_QUERY_BUILDERS`'s type annotation and the `assert` line stays
unchanged (still keyed the same way, just a wider `Callable`):

```python
_QUERY_BUILDERS: dict[str, Callable[[str, str | None, str], object]] = {
    "manual": _build_manual_query,
    "adzuna": _build_adzuna_query,
    "reed": _build_reed_query,
    "greenhouse": _build_greenhouse_query,
    "jooble": _build_jooble_query,
}

assert (
    _QUERY_BUILDERS.keys() == _CONNECTOR_BUILDERS.keys()
), "_QUERY_BUILDERS and _CONNECTOR_BUILDERS must register the same sources"
```

- [ ] **Step 5: Update `_cmd_ingest`**

Change the query-building call to pass the new argument:

```python
    try:
        query = _QUERY_BUILDERS[args.source](
            args.query, args.region, args.collection_channel
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
```

Add `collection_channel=args.collection_channel,` to the `run_connector(
...)` call (right after `entry_method=...,`).

After `run_connector(...)` returns and before the existing `print(...)`,
add diversity-yield logging for discovery runs:

```python
        if args.collection_channel == "discovery":
            titles = {
                title
                for raw_job in result.raw_jobs
                if (title := _extract_title(raw_job.payload)) is not None
            }
            print(
                f"discovery yield: {len(result.raw_jobs)} records, "
                f"{len(titles)} distinct titles"
            )

        print(
            f"ingest complete: source={args.source} "
            f"records={result.run_metadata.records} "
            f"run_id={result.run_metadata.run_id}"
        )
```

Add the small helper function near the top of the file (after
`_build_llm_adapters`, before `_ConnectorBuildContext`):

```python
def _extract_title(payload: dict[str, object]) -> str | None:
    """Best-effort title extraction across every connector's raw payload shape.

    Args:
        payload: One `RawJob.payload` dict — connector-specific, never
            normalised at this layer (normalisation is a later dbt/staging
            concern per PLAN.md Phase 1).

    Returns:
        The title string if a recognisable field is present (Adzuna/
        Greenhouse/Jooble all use "title"; Reed uses "jobTitle"), else
        `None`.
    """
    title = payload.get("title") or payload.get("jobTitle")
    return str(title) if title else None
```

- [ ] **Step 6: Add the `config/sources.yml` operational note**

Add a comment above the `sources:` key (or extend the existing top
comment block) noting the discovery-cadence policy:

```yaml
# Discovery-channel runs (--collection-channel discovery) should be run
# at most weekly, by hand — no scheduler exists in this project yet to
# enforce that automatically (PLAN.md Phase 6). Don't loop this in a cron
# job without first building real orchestration.
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_pipeline_cli -v
```

Expected: every test in the file PASSES, including the 3 new ones and
every pre-existing one unchanged.

- [ ] **Step 8: Run the quality gate**

```bash
cd job_search
python3.11 -m black apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli.py
python3.11 -m isort apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli.py
python3.11 -m ruff check apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli.py
python3.11 -m mypy apps/pipeline/app
```

Expected: all clean. If mypy flags anything about the widened
`_QUERY_BUILDERS` `Callable` type, this is the exact same class of issue
Step 4's final review already resolved once for `_CONNECTOR_BUILDERS` via
`# type: ignore[return-value]` — check first whether it actually recurs
here (the `_QUERY_BUILDERS` values return `object`, not a narrower
Protocol, so it likely does not) before reaching for that fix; don't
apply it speculatively.

- [ ] **Step 9: Commit**

```bash
git add apps/pipeline/app/cli.py config/sources.yml \
  packages/core/tests/test_pipeline_cli.py
git commit -m "feat(job_search): wire --collection-channel and Adzuna discovery sweeps into the CLI"
```

---

### Task 4: Grow the `target_company` registry

**Files:**
- Modify: `scripts/seed_target_company.py`

**Interfaces:** None new — reuses Task 1 (Step 4)'s existing
`upsert_target_company`/`build_engine`.

- [ ] **Step 1: Extend the candidate list**

Read the current `scripts/seed_target_company.py` first. Extend
`_CANDIDATES` with additional plausible Greenhouse-hosted companies —
candidates the implementer is not certain are correct (that's the whole
point of this script: verify live, keep only what resolves). Add at
least these to the existing list, without removing or reordering the
existing entries:

```python
    ("Anthropic", "anthropic"),
    ("Linear", "linear"),
    ("Vercel", "vercel"),
    ("Ramp", "ramp"),
    ("Brex", "brex"),
    ("Scale AI", "scaleai"),
    ("Plaid", "plaid"),
    ("Rippling", "rippling"),
```

- [ ] **Step 2: Run it live**

```bash
cd job_search
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
python3.11 scripts/seed_target_company.py
```

Record the real output verbatim — how many of the new candidates
verified as real, which didn't. As with the original Step 4 seed run,
it's fine if some are wrong guesses; report the true number, don't
round up.

- [ ] **Step 3: Verify the registry grew**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM target_company WHERE active = true;"
```

Expected: count > 10 (Step 4's original seed count) — confirms real
growth, not just a re-run of the same set.

- [ ] **Step 4: Run the quality gate**

```bash
cd job_search
python3.11 -m black scripts/seed_target_company.py
python3.11 -m isort scripts/seed_target_company.py
python3.11 -m ruff check scripts/seed_target_company.py
```

Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_target_company.py
git commit -m "feat(job_search): grow target_company with more live-verified companies"
```

---

### Task 5: Full-stack verification (controller-run, not dispatched)

- [ ] **Step 1: Confirm Postgres is up and migrated**

```bash
cd job_search
docker compose up -d postgres
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
python3.11 -m alembic -c db/alembic.ini current
```

Expected: `0006 (head)`.

- [ ] **Step 2: Run the full test suite**

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_verify" \
coverage run -m unittest discover
coverage report -m
```

Expected: every test PASSES or SKIPS cleanly; coverage at or above the
pre-existing baseline.

- [ ] **Step 3: Run a real discovery-mode Adzuna category sweep, via Docker**

```bash
cd job_search
docker compose --profile cli run --rm pipeline \
  ingest --source adzuna --query "it-jobs" --region gb --collection-channel discovery
```

Expected: prints a `discovery yield: N records, M distinct titles` line
followed by `ingest complete: ...`. Confirm:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM bronze.raw_jobs WHERE source_name = 'adzuna' AND collection_channel = 'discovery';"
```

Expected: count > 0.

- [ ] **Step 4: Run a real discovery-mode Greenhouse full-board-dump, via Docker**

```bash
docker compose --profile cli run --rm pipeline \
  ingest --source greenhouse --query "" --collection-channel discovery
```

Expected: prints a `discovery yield: ...` line. Confirm:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM bronze.raw_jobs WHERE source_name = 'greenhouse' AND collection_channel = 'discovery';"
```

Expected: count > 0.

- [ ] **Step 5: Confirm the discovery/targeted separation is real**

Compare a sample of the discovery-run titles against the targeted-run
titles already in bronze from Step 4's earlier acceptance runs:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT DISTINCT payload->>'title' FROM bronze.raw_jobs WHERE source_name = 'adzuna' AND collection_channel = 'discovery' LIMIT 20;"
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT DISTINCT payload->>'title' FROM bronze.raw_jobs WHERE source_name = 'adzuna' AND collection_channel = 'targeted' LIMIT 20;"
```

Expected: at least some titles in the discovery list don't appear in the
targeted list — the acceptance criterion ("discovery runs return titles
that appear nowhere in the targeted keyword matrix") demonstrated live,
not just structurally asserted.

- [ ] **Step 6: Quality gate, one last time**

```bash
cd job_search
python3.11 -m black --check .
python3.11 -m isort --check-only .
python3.11 -m ruff check .
python3.11 -m mypy packages/core/core
python3.11 -m mypy apps/api/app
python3.11 -m mypy apps/pipeline/app
python3.11 -m mypy packages/core/core apps/pipeline/app
```

The last, combined invocation specifically re-checks for the
Protocol-conformance discrepancy Step 4b's final review found — run it
even though the separate invocations above already passed.

Expected: all clean.

- [ ] **Step 7: Tear down**

```bash
docker compose down
```

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage:** all 8 `STEP-04A` subtasks accounted for — 5 built
  (collection_channel column, Adzuna category sweep, CLI wiring +
  volume cap + diversity logging, registry growth), 3 explicitly
  documented as not-yet-buildable with the reasoning stated (see "Scope
  note" above), none silently dropped.
- **Placeholder scan:** none found — every code block is complete and
  grounded in the actual current file contents read during planning.
- **Type consistency:** `run_connector`'s new `collection_channel`
  parameter is inserted between `entry_method` and `landing_uri` in the
  signature — verified this doesn't break keyword-only calling
  convention (everything after `*` already requires keywords, so
  insertion position doesn't matter for any existing call site).
  `_QUERY_BUILDERS`'s widened 3-arg signature was cross-checked against
  all 5 existing builder functions' current real signatures (read live
  during planning, not assumed) before writing the diffs.
- **Real-API grounding:** Adzuna's category-sweep behaviour (params,
  response shape, no-`what`-needed) was confirmed via a live call during
  planning, not recalled — see the plan header.
- Task 3 Step 4's docstring for `_build_adzuna_query` explicitly flags
  its own edge-case reasoning (empty `raw_query` rejected in both modes)
  rather than leaving it implicit, since a category-tag string being
  optional-but-empty was a real design fork during authoring.
