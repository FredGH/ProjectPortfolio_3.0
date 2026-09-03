# Job Search — Step 2 (Manual Job Entry, End to End) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the manual job-entry vertical slice — a Streamlit paste form, through a FastAPI endpoint, into the immutable landing zone, through a dlt load, into `bronze.raw_jobs` — proving one job flows end to end without any API keys or external dependency.

**Architecture:** A new `core.ingestion` package holds every pure/injectable piece (URL canonicalisation, run-id generation, the landing-zone writer, the dlt bronze loader, LLM structured extraction, and the orchestration function that composes them). The FastAPI endpoint and the Streamlit form are thin clients over `ingest_manual_job()` — all logic lives in `core`, per `DECISIONS.md` §2.4. Every I/O boundary (HTTP redirect resolution, the LLM adapters, the bronze loader) is dependency-injected with a default real implementation, so unit tests never need a live network, Ollama, or Postgres — only the final verification task does.

**Tech Stack:** Adds to the existing stack: `dlt[postgres]==1.5.0` (matches the pinned version already used elsewhere in this user's data-eng projects per the root `CLAUDE.md`), `python-ulid==1.1.0` (run-id generation, matching PLAN.md's `run_id=01J...` ULID format).

**Spec:** `job_search/PLAN.md` Step 2 (lines 238–318), `job_search/plan/backlog.yml` `STEP-02` / `JOB-45` (subtasks JOB-46–JOB-57), `job_search/DECISIONS.md` §2.4 (FastAPI holds all logic), §7 (two-zone tenancy — `bronze.raw_jobs` is SHARED job data, no `user_id`, no RLS).

## Global Constraints

- Python 3.11; `from __future__ import annotations`; `list[T]`/`dict[K,V]` not `List`/`Dict`; type hints on public signatures; Google-style docstrings everywhere (public and private, incl. tests) with Args/Returns/Raises.
- `black` (88 cols) + `isort` (profile black, `known_first_party=["core"]`) + `ruff` (no `"I"` selected — isort owns import order, per the ruling already recorded on this branch) + `mypy` — all four must stay clean; run per-target for `apps/api/app` and `apps/pipeline/app` (two packages both named `app`).
- Tests: `unittest` + `coverage`, never `pytest`. No mocking the database or Postgres — integration tests use a real connection, gated with the `_live_migration_engine()`-style skip-if-unreachable pattern already established in `packages/core/tests/integration/`. External I/O (HTTP redirects, LLM calls) is not "the database" — those are dependency-injected with fakes in unit tests, real implementations exercised only in the final live-verification task.
- `bronze.raw_jobs` is a **SHARED** table (two-zone rule, `docs/tenancy.md`): no `user_id`, no RLS. It is written only by the pipeline/migration role (`database_url`, the owner DSN) — this is batch/pipeline work, not a live per-user request, so it does not go through `job_search_app`/RLS at all. Document this explicitly in the migration's comment so nobody "fixes" it into a per-user table later.
- SQL in migrations: keywords UPPERCASE, snake_case identifiers, `_at` suffix for timestamps, per `.claude/rules/sql-style.md`.
- Every I/O-boundary function takes its collaborators as explicit parameters (an `httpx.Client`, an `adapters` dict, a `load_to_bronze_fn` callable) with a real default — this is the pattern already used throughout `core.llm` and `core.db`; follow it exactly, don't invent a different injection style.
- The acceptance criterion is narrow and literal (PLAN.md Step 2 "Done when"): *pasting a real LinkedIn posting produces a bronze row with correct `source_name`, `job_url_canonical`, `payload.raw_text`, and `payload_sha256`*. LLM-extracted fields (title/company/location/etc.) are **not** part of this criterion — extraction is explicitly a best-effort derived field ("if extraction is wrong, you re-run it from landing without asking the user to paste again"). Every task must keep extraction failures non-fatal to ingestion.

---

## File Structure

```
job_search/
  packages/core/core/ingestion/
    __init__.py
    url_utils.py          # canonicalise_url, extract_source_job_id
    run_id.py             # generate_run_id
    landing.py            # write_landing_record
    bronze.py             # load_to_bronze (dlt)
    extraction.py         # ExtractedJobFields, extract_job_fields, apply_user_overrides
    manual.py             # ManualIngestResult, ingest_manual_job (orchestration)
  packages/core/tests/
    test_url_utils.py
    test_run_id_and_landing.py
    test_extraction.py
    test_manual_ingest.py
    integration/
      test_bronze_loader.py
  apps/api/app/
    dependencies.py        # get_http_client, get_llm_adapters (FastAPI Depends)
    routers/__init__.py
    routers/ingest.py      # POST /ingest/manual
  packages/core/tests/test_api_ingest.py
  apps/ui/app/pages/
    1_Manual_Job_Entry.py  # Streamlit paste form
  db/migrations/versions/
    0004_create_bronze_raw_jobs.py
  config/llm_tasks.yml     # + manual_entry_parse entry
  requirements.txt         # + dlt[postgres], python-ulid
```

---

## Task 1: URL canonicalisation and `source_job_id` extraction

**Files:**
- Create: `job_search/packages/core/core/ingestion/__init__.py`
- Create: `job_search/packages/core/core/ingestion/url_utils.py`
- Create: `job_search/packages/core/tests/test_url_utils.py`

**Interfaces:**
- Produces: `def canonicalise_url(url: str, *, http_client: httpx.Client | None = None) -> str` and `def extract_source_job_id(canonical_url: str) -> str`. Task 6's orchestration calls both with these exact signatures.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_url_utils.py
from __future__ import annotations

import hashlib
import unittest

import httpx

from core.ingestion.url_utils import canonicalise_url, extract_source_job_id


class TestCanonicaliseUrl(unittest.TestCase):
    """Tests for canonicalise_url's normalisation and redirect resolution."""

    def test_lowercases_host_and_strips_tracking_params(self) -> None:
        """utm_*, ref, src, trk, aff params and trailing slash are stripped."""
        result = canonicalise_url(
            "HTTPS://WWW.LinkedIn.com/jobs/view/12345/"
            "?utm_source=li&ref=abc&trk=xyz&aff=1"
        )
        self.assertEqual(result, "https://www.linkedin.com/jobs/view/12345")

    def test_strips_session_like_params_but_keeps_real_ones(self) -> None:
        """Session-ish params are stripped; a real query param survives."""
        result = canonicalise_url(
            "https://example.com/job?id=42&PHPSESSID=abc123&utm_campaign=x"
        )
        self.assertEqual(result, "https://example.com/job?id=42")

    def test_no_client_skips_redirect_resolution(self) -> None:
        """Without an http_client, the URL is normalised as-is, no network."""
        result = canonicalise_url("https://example.com/job/")
        self.assertEqual(result, "https://example.com/job")

    def test_resolves_one_redirect_hop_when_client_given(self) -> None:
        """A single 301/302 hop is followed before normalisation."""

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == "https://old.example.com/job":
                return httpx.Response(
                    301, headers={"Location": "https://new.example.com/job/?ref=x"}
                )
            raise AssertionError(f"unexpected request to {request.url}")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = canonicalise_url("https://old.example.com/job", http_client=client)
        self.assertEqual(result, "https://new.example.com/job")

    def test_redirect_failure_falls_back_to_original_url(self) -> None:
        """A network error during redirect resolution is swallowed (best-effort)."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = canonicalise_url("https://example.com/job/", http_client=client)
        self.assertEqual(result, "https://example.com/job")


class TestExtractSourceJobId(unittest.TestCase):
    """Tests for extract_source_job_id's LinkedIn pattern and hash fallback."""

    def test_extracts_linkedin_numeric_id(self) -> None:
        """LinkedIn's /jobs/view/{id} pattern yields the numeric id directly."""
        result = extract_source_job_id("https://www.linkedin.com/jobs/view/3812345")
        self.assertEqual(result, "3812345")

    def test_falls_back_to_sha256_for_unknown_patterns(self) -> None:
        """A non-LinkedIn URL falls back to sha256(canonical_url)."""
        url = "https://example.com/job/42"
        result = extract_source_job_id(url)
        self.assertEqual(result, hashlib.sha256(url.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_url_utils -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/__init__.py`**

```python
"""Job ingestion: URL canonicalisation, the landing zone, and the manual
paste-form pipeline (PLAN.md Step 2)."""
```

- [ ] **Step 4: Write `job_search/packages/core/core/ingestion/url_utils.py`**

```python
"""URL canonicalisation and source_job_id extraction (PLAN.md Step 2).

Two sources pointing at the same canonical URL is an instant dedup match,
so getting this normalisation right costs nothing and buys a lot.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

_STRIP_EXACT = {"ref", "src", "trk", "aff"}
_STRIP_PREFIXES = ("utm_",)
_LINKEDIN_JOB_ID = re.compile(r"/jobs/view/(\d+)")


def _should_strip_param(name: str) -> bool:
    """Decide whether a query parameter is tracking/session noise.

    Args:
        name: The query parameter's name, as parsed from the URL.

    Returns:
        True if the parameter should be dropped during canonicalisation.
    """
    lowered = name.lower()
    if lowered in _STRIP_EXACT:
        return True
    if lowered.startswith(_STRIP_PREFIXES):
        return True
    return "session" in lowered


def _resolve_one_redirect(url: str, http_client: httpx.Client) -> str:
    """Follow a single redirect hop, best-effort.

    Args:
        url: The URL to check for a redirect.
        http_client: The client to issue the (non-following) request with.

    Returns:
        The `Location` target if the server returned a 3xx with one;
        otherwise the original `url` unchanged. Any request failure is
        swallowed and the original `url` is returned — redirect resolution
        is a nice-to-have, never a hard requirement for ingestion.
    """
    try:
        response = http_client.request("HEAD", url, follow_redirects=False)
    except httpx.HTTPError:
        return url
    if response.is_redirect:
        location = response.headers.get("location")
        if location:
            return str(httpx.URL(url).join(location))
    return url


def _normalise(url: str) -> str:
    """Lowercase host/scheme, strip tracking params, strip a trailing slash.

    Args:
        url: The URL to normalise.

    Returns:
        The normalised URL.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query_pairs = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _should_strip_param(name)
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def canonicalise_url(url: str, *, http_client: httpx.Client | None = None) -> str:
    """Canonicalise a job posting URL.

    Args:
        url: The raw URL as pasted or received.
        http_client: When given, used to resolve a single redirect hop
            before normalising. When omitted, no network call is made —
            the URL is normalised as-is.

    Returns:
        The canonicalised URL: lowercase scheme/host, tracking/session
        query params stripped, no trailing slash, at most one redirect hop
        resolved.
    """
    resolved = url if http_client is None else _resolve_one_redirect(url, http_client)
    return _normalise(resolved)


def extract_source_job_id(canonical_url: str) -> str:
    """Extract a stable job id from a canonical URL.

    Args:
        canonical_url: The output of `canonicalise_url`.

    Returns:
        The LinkedIn numeric job id when the URL matches LinkedIn's
        `/jobs/view/{id}` pattern; otherwise `sha256(canonical_url)`.
    """
    if "linkedin.com" in canonical_url:
        match = _LINKEDIN_JOB_ID.search(canonical_url)
        if match:
            return match.group(1)
    return hashlib.sha256(canonical_url.encode()).hexdigest()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_url_utils -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/__init__.py packages/core/core/ingestion/url_utils.py \
  packages/core/tests/test_url_utils.py
git commit -m "feat(job_search): add URL canonicalisation and source_job_id extraction"
```

---

## Task 2: `run_id` generation and the landing-zone writer

**Files:**
- Create: `job_search/packages/core/core/ingestion/run_id.py`
- Create: `job_search/packages/core/core/ingestion/landing.py`
- Create: `job_search/packages/core/tests/test_run_id_and_landing.py`
- Modify: `job_search/requirements.txt`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `def generate_run_id() -> str` (a ULID string). `def write_landing_record(landing_uri: str, *, source_name: str, run_id: str, record: dict[str, object], fetched_at: datetime.datetime) -> str` returning the path written. Task 6 calls both with these exact signatures.

- [ ] **Step 1: Add the dependency**

Add to `job_search/requirements.txt` (after `pyyaml==6.0.2`):

```
python-ulid==1.1.0
dlt[postgres]==1.5.0
fsspec==2024.10.0
```

Run: `cd job_search && python3.11 -m pip install -r requirements.txt 2>&1 | tail -20`
Expected: installs cleanly. Note in your report exactly which driver `dlt[postgres]` pulled in (it may add `psycopg2-binary` alongside the existing `psycopg`, which is fine — they're independent packages).

- [ ] **Step 2: Write the failing tests**

```python
# job_search/packages/core/tests/test_run_id_and_landing.py
from __future__ import annotations

import datetime
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from core.ingestion.landing import write_landing_record
from core.ingestion.run_id import generate_run_id


class TestGenerateRunId(unittest.TestCase):
    """Tests for generate_run_id's ULID shape and uniqueness."""

    def test_returns_a_26_character_ulid(self) -> None:
        """A ULID string is always 26 Crockford-base32 characters."""
        run_id = generate_run_id()
        self.assertEqual(len(run_id), 26)

    def test_successive_calls_are_unique(self) -> None:
        """Two calls never collide."""
        self.assertNotEqual(generate_run_id(), generate_run_id())


class TestWriteLandingRecord(unittest.TestCase):
    """Tests for write_landing_record's path layout and gzip-JSONL content."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()

    def test_writes_gzip_jsonl_at_the_expected_path(self) -> None:
        """The record lands at landing/source=.../dt=.../run_id=.../part-0001.jsonl.gz."""
        fetched_at = datetime.datetime(2026, 8, 23, 12, 0, tzinfo=datetime.timezone.utc)
        path = write_landing_record(
            self.landing_uri,
            source_name="linkedin_manual",
            run_id="01J000000000000000000000",
            record={"hello": "world"},
            fetched_at=fetched_at,
        )
        expected_suffix = (
            "source=linkedin_manual/dt=2026-08-23/"
            "run_id=01J000000000000000000000/part-0001.jsonl.gz"
        )
        self.assertTrue(path.endswith(expected_suffix))

        local_path = Path(self._tmp_dir.name) / expected_suffix
        with gzip.open(local_path, "rt") as handle:
            lines = handle.readlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {"hello": "world"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_run_id_and_landing -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.run_id'`.

- [ ] **Step 4: Write `job_search/packages/core/core/ingestion/run_id.py`**

```python
"""Run-id generation — a sortable ULID, shared by every ingestion path."""

from __future__ import annotations

from ulid import ULID


def generate_run_id() -> str:
    """Generate a new run id.

    Returns:
        A 26-character Crockford-base32 ULID string — lexicographically
        sortable by creation time, matching the `run_id=01J...` format in
        PLAN.md's landing-zone path convention.
    """
    return str(ULID())
```

- [ ] **Step 5: Write `job_search/packages/core/core/ingestion/landing.py`**

```python
"""The immutable landing zone (PLAN.md Step 2).

One JSONL line per record, gzip-compressed, at a fixed
`source=.../dt=.../run_id=.../part-0001.jsonl.gz` path. Immutable and
replayable — bronze can always be rebuilt from here without re-hitting a
single API.
"""

from __future__ import annotations

import datetime
import gzip
import json

import fsspec


def write_landing_record(
    landing_uri: str,
    *,
    source_name: str,
    run_id: str,
    record: dict[str, object],
    fetched_at: datetime.datetime,
) -> str:
    """Write one record as a gzip JSONL file in the landing zone.

    Args:
        landing_uri: Root URI of the landing zone (`file://...` locally,
            `gs://...` in GCP — this function doesn't branch on which).
        source_name: The source this record came from, e.g. "linkedin_manual".
        run_id: The ULID identifying this ingestion run.
        record: The JSON-serialisable record to write, verbatim.
        fetched_at: When this record was captured — determines the `dt=`
            partition.

    Returns:
        The full path written to.
    """
    dt = fetched_at.date().isoformat()
    path = (
        f"{landing_uri.rstrip('/')}/source={source_name}/dt={dt}/"
        f"run_id={run_id}/part-0001.jsonl.gz"
    )
    with fsspec.open(path, "wb") as raw_handle:
        with gzip.GzipFile(fileobj=raw_handle, mode="wb") as gz_handle:
            gz_handle.write((json.dumps(record) + "\n").encode())
    return path
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_run_id_and_landing -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
cd job_search
git add requirements.txt packages/core/core/ingestion/run_id.py \
  packages/core/core/ingestion/landing.py packages/core/tests/test_run_id_and_landing.py
git commit -m "feat(job_search): add run-id generation and the landing-zone writer"
```

---

## Task 3: `bronze.raw_jobs` migration

**Files:**
- Create: `job_search/db/migrations/versions/0004_create_bronze_raw_jobs.py`

**Interfaces:**
- Consumes: nothing at the Python level. Depends on Postgres being up with migrations 0001–0003 applied (Task 3's revision chains onto `down_revision = "0003"`).
- Produces: the `bronze.raw_jobs` table Task 4's dlt loader writes into.

- [ ] **Step 1: Write `job_search/db/migrations/versions/0004_create_bronze_raw_jobs.py`**

```python
"""create bronze.raw_jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03

bronze.raw_jobs is SHARED job-posting data (PLAN.md's two-zone rule,
docs/tenancy.md): collected once, identical for every user. It carries no
user_id and has no row-level security. It is WRITTEN only by the
migration/owner role via the batch ingestion pipeline (dlt), never by a
live per-user API request — see core.ingestion.bronze.load_to_bronze,
which connects with the owner DSN, not the RLS-enforced app role.

It is READ by the app role, though: Task 7's GET /sources endpoint needs
a live, request-serving read of distinct source_name values, and (per the
project's general principle of keeping the owner/migration credential out
of request-serving code wherever possible) that read goes through
job_search_app, not the owner DSN — safe here because the table carries
no per-user data and no RLS to bypass.

`_dlt_load_id`/`_dlt_id` are dlt's own bookkeeping columns, created here
explicitly so dlt's load finds them already present rather than having to
evolve the schema itself.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS bronze")

    op.create_table(
        "raw_jobs",
        sa.Column("_dlt_load_id", sa.Text(), nullable=True),
        sa.Column("_dlt_id", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=False),
        sa.Column("job_url", sa.Text(), nullable=False),
        sa.Column("job_url_canonical", sa.Text(), nullable=False),
        sa.Column("entry_method", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("request_params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "entry_method IN ('api', 'manual', 'scraped')",
            name="ck_bronze_raw_jobs_entry_method",
        ),
        sa.UniqueConstraint(
            "source_name",
            "source_job_id",
            "payload_sha256",
            name="uq_bronze_raw_jobs_dedup",
        ),
        schema="bronze",
    )

    op.execute("GRANT USAGE ON SCHEMA bronze TO job_search_app")
    op.execute("GRANT SELECT ON bronze.raw_jobs TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON bronze.raw_jobs FROM job_search_app")
    op.execute("REVOKE USAGE ON SCHEMA bronze FROM job_search_app")
    op.drop_table("raw_jobs", schema="bronze")
    op.execute("DROP SCHEMA IF EXISTS bronze")
```

- [ ] **Step 2: Fix the same `sa.dialects.postgresql` import bug caught in Task 11**

Replace `import sqlalchemy as sa` usage of `sa.dialects.postgresql.JSONB` — this repo already hit and fixed this exact `AttributeError` (bare `sa.dialects.postgresql.X` is not auto-populated by `import sqlalchemy as sa`). Add the explicit import and use the bare name, matching `0001_create_app_user.py`'s fix:

```python
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
```

and change both `sa.dialects.postgresql.JSONB()` occurrences to `JSONB()`.

- [ ] **Step 3: Run the migration against a live Postgres**

Run: `cd job_search && docker compose up -d postgres && sleep 3`
Run: `cd job_search/db && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search alembic upgrade head`
Expected: `Running upgrade 0003 -> 0004, create bronze.raw_jobs`.

Run: `cd job_search && docker compose exec postgres psql -U job_search_owner -d job_search -c "\d bronze.raw_jobs"`
Expected: table description showing all columns, the `ck_bronze_raw_jobs_entry_method` check, and the `uq_bronze_raw_jobs_dedup` unique constraint. **No RLS policies** — confirm none are listed (this table is shared).

Run: `docker compose exec postgres psql -U job_search_app -d job_search -c "SELECT COUNT(*) FROM bronze.raw_jobs"`
Expected: `0` (an empty, successful `SELECT` — proves the read grant works; the app role has no INSERT/UPDATE grant on this table by design, only SELECT).

Leave Postgres UP and migrated — Task 4 continues from this state.

- [ ] **Step 4: Commit**

```bash
cd job_search
git add db/migrations/versions/0004_create_bronze_raw_jobs.py
git commit -m "feat(job_search): create the bronze.raw_jobs table"
```

---

## Task 4: The dlt bronze loader

**Files:**
- Create: `job_search/packages/core/core/ingestion/bronze.py`
- Create: `job_search/packages/core/tests/integration/test_bronze_loader.py`

**Interfaces:**
- Consumes: the `bronze.raw_jobs` table from Task 3.
- Produces: `def load_to_bronze(*, database_url: str, source_name: str, source_job_id: str, job_url: str, job_url_canonical: str, entry_method: str, fetched_at: datetime.datetime, run_id: str, request_params: dict[str, object], payload: dict[str, object], payload_sha256: str) -> None`. Task 6's orchestration calls this with these exact keyword arguments.

**This is the riskiest task in the plan.** dlt's exact schema-evolution and merge behavior against a table Alembic already created must be verified live against real Postgres, not assumed from documentation. You are authorized and expected to debug against real dlt output rather than treat the starting code below as gospel — if `pipeline.run(...)` errors or behaves unexpectedly, read the actual error/behavior, adjust, and document precisely what you found and changed in your report. This is exactly the kind of task where a fresh implementer discovering a real library-behavior gap and adapting is the expected, correct outcome — not a sign something went wrong.

- [ ] **Step 1: Write the failing integration test**

```python
# job_search/packages/core/tests/integration/test_bronze_loader.py
from __future__ import annotations

import datetime
import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine, session_scope
from core.ingestion.bronze import load_to_bronze
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


class TestLoadToBronze(unittest.TestCase):
    """Tests for load_to_bronze's dedup-on-unchanged-payload merge semantics."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()

    def setUp(self) -> None:
        self.source_name = f"test_source_{uuid.uuid4().hex[:8]}"
        self.source_job_id = "test-job-1"
        self.fetched_at = datetime.datetime.now(datetime.timezone.utc)

    def tearDown(self) -> None:
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM bronze.raw_jobs WHERE source_name = :name"),
                {"name": self.source_name},
            )

    def _load(self, *, payload_sha256: str, payload: dict[str, object]) -> None:
        load_to_bronze(
            database_url=get_settings().database_url,
            source_name=self.source_name,
            source_job_id=self.source_job_id,
            job_url="https://example.com/job",
            job_url_canonical="https://example.com/job",
            entry_method="manual",
            fetched_at=self.fetched_at,
            run_id="01J000000000000000000000",
            request_params={},
            payload=payload,
            payload_sha256=payload_sha256,
        )

    def _row_count(self) -> int:
        with session_scope(self.migration_engine) as conn:
            result = conn.execute(
                text(
                    "SELECT COUNT(*) FROM bronze.raw_jobs WHERE source_name = :name"
                ),
                {"name": self.source_name},
            )
            return result.scalar_one()

    def test_reloading_the_same_payload_is_a_no_op(self) -> None:
        """Two loads with identical payload_sha256 leave exactly one row."""
        self._load(payload_sha256="abc123", payload={"raw_text": "hello"})
        self._load(payload_sha256="abc123", payload={"raw_text": "hello"})
        self.assertEqual(self._row_count(), 1)

    def test_a_changed_payload_produces_a_new_version_row(self) -> None:
        """A different payload_sha256 for the same job adds a second row."""
        self._load(payload_sha256="abc123", payload={"raw_text": "hello"})
        self._load(payload_sha256="def456", payload={"raw_text": "hello, edited"})
        self.assertEqual(self._row_count(), 2)

    def test_payload_is_stored_as_a_navigable_jsonb_object_not_flattened(self) -> None:
        """payload lands as one JSONB column, not flattened into subcolumns."""
        self._load(
            payload_sha256="jsonb-check",
            payload={"raw_text": "hello", "nested": {"a": 1}},
        )
        with session_scope(self.migration_engine) as conn:
            result = conn.execute(
                text(
                    "SELECT payload->>'raw_text', payload->'nested'->>'a' "
                    "FROM bronze.raw_jobs "
                    "WHERE source_name = :name AND payload_sha256 = 'jsonb-check'"
                ),
                {"name": self.source_name},
            )
            raw_text, nested_a = result.one()
        self.assertEqual(raw_text, "hello")
        self.assertEqual(nested_a, "1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search python3.11 -m unittest tests.integration.test_bronze_loader -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.bronze'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/bronze.py`**

```python
"""The dlt load from a landing record into bronze.raw_jobs (PLAN.md Step 2).

Append-only with a dedup twist: unique on (source_name, source_job_id,
payload_sha256) means a re-fetch with an unchanged payload is a no-op,
and a changed payload becomes a new version row — free posting-change
history for Step 21a's lifecycle metric.
"""

from __future__ import annotations

import datetime

import dlt


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
    }

    pipeline = dlt.pipeline(
        pipeline_name="job_search_bronze",
        destination=dlt.destinations.postgres(credentials=database_url),
        dataset_name="bronze",
    )
    pipeline.run(
        [record],
        table_name="raw_jobs",
        write_disposition="merge",
        primary_key=("source_name", "source_job_id", "payload_sha256"),
        columns={
            "request_params": {"data_type": "json"},
            "payload": {"data_type": "json"},
        },
    )
```

- [ ] **Step 4: Run test to verify it passes — debug against real dlt behavior if it doesn't**

Run: `cd job_search/packages/core && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search python3.11 -m unittest tests.integration.test_bronze_loader -v`

**If this fails**, common real-world causes and how to resolve each — try in order, and document which one applied:

1. **dlt complains the destination table's schema doesn't match its expectations** (e.g. about `_dlt_id`/`_dlt_load_id`, or a NOT NULL/type mismatch): check the actual error. dlt may need `_dlt_id` to have a default rather than being caller-supplied — if so, either let dlt supply it during the load (it does, automatically, as part of `pipeline.run`) or adjust migration 0004 in a follow-up commit on this task's branch (same task, not a separate one) to match what dlt actually requires, and clearly document the corrected DDL in your report.
2. **`payload`/`request_params` still get flattened into subcolumns** instead of staying as one JSONB column: the `columns={...}` hint above should prevent this; if it doesn't, try passing the hint via `dlt.resource(...)` with an explicit `columns` argument and `max_table_nesting=0`, or apply the hint via `pipeline.run(..., schema_contract="evolve")`. Get the JSONB-not-flattened test passing — this is a hard requirement, not a nice-to-have (the acceptance criterion literally requires `payload.raw_text` to be queryable as nested JSON).
3. **Merge doesn't dedup as expected** (test 1 sees 2 rows instead of 1): check whether dlt's Postgres merge strategy is doing a true delete-and-reinsert keyed correctly on all three `primary_key` columns — if dlt merged on a subset, the test will reveal it directly; fix by confirming `primary_key=("source_name", "source_job_id", "payload_sha256")` is being passed through to the actual merge SQL (add temporary print/logging if needed to confirm, then remove it).

Whatever the real fix turns out to be, all three tests must genuinely PASS against live Postgres before this task is done — do not weaken the tests to make them pass.

- [ ] **Step 5: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/bronze.py packages/core/tests/integration/test_bronze_loader.py
# If migration 0004 needed a correction, amend that into this commit's diff too —
# git add db/migrations/versions/0004_create_bronze_raw_jobs.py if you changed it.
git commit -m "feat(job_search): add the dlt bronze loader with merge dedup semantics"
```

---

## Task 5: LLM extraction and field-source override merging

**Files:**
- Create: `job_search/packages/core/core/ingestion/extraction.py`
- Create: `job_search/packages/core/tests/test_extraction.py`
- Modify: `job_search/config/llm_tasks.yml`

**Interfaces:**
- Consumes: `complete`, `LLMAdapter`, `LLMResponse` from `core.llm` (built in the Step 1 plan).
- Produces: `class ExtractedJobFields(BaseModel)` with fields `title`, `company`, `location`, `contract`, `salary`, `seniority` (all `str | None`, default `None`). `def extract_job_fields(raw_text: str, *, adapters: dict[str, LLMAdapter], config_path: Path | None = None, prompt_version: str = "local.v1") -> ExtractedJobFields`. `def apply_user_overrides(extracted: ExtractedJobFields, overrides: dict[str, str | None]) -> tuple[ExtractedJobFields, dict[str, str]]` returning the merged fields and a `{field_name: "user"}` map for every field actually overridden. Task 6 calls all three with these exact signatures.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_extraction.py
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.ingestion.extraction import (
    ExtractedJobFields,
    apply_user_overrides,
    extract_job_fields,
)
from core.llm.types import LLMResponse

_SAMPLE_YAML = """
tasks:
  manual_entry_parse:
    provider: fake
    model: fake-model-v1
    prompt_family: local
"""


class _FakeAdapter:
    """A test double standing in for a real LLM adapter."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Return the pre-baked response, ignoring the prompt content."""
        return LLMResponse(
            text=self._response_text,
            provider="fake",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class _RaisingAdapter:
    """A test double that always fails, to prove callers degrade gracefully."""

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Always raise, simulating an unreachable LLM provider."""
        raise RuntimeError("provider unreachable")


class TestExtractJobFields(unittest.TestCase):
    """Tests for extract_job_fields's structured-output parsing."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        tmp.write(_SAMPLE_YAML)
        tmp.close()
        self.config_path = Path(tmp.name)

    def tearDown(self) -> None:
        self.config_path.unlink(missing_ok=True)

    def test_parses_a_well_formed_structured_response(self) -> None:
        """A valid JSON completion parses into ExtractedJobFields."""
        payload = json.dumps(
            {
                "title": "Senior Data Engineer",
                "company": "Acme Ltd",
                "location": "London, UK",
                "contract": "permanent",
                "salary": "£90,000",
                "seniority": "senior",
            }
        )
        adapters = {"fake": _FakeAdapter(payload)}
        result = extract_job_fields(
            "some raw JD text", adapters=adapters, config_path=self.config_path
        )
        self.assertEqual(result.title, "Senior Data Engineer")
        self.assertEqual(result.company, "Acme Ltd")

    def test_partial_response_leaves_missing_fields_none(self) -> None:
        """Fields the model omits default to None rather than erroring."""
        payload = json.dumps({"title": "Data Engineer"})
        adapters = {"fake": _FakeAdapter(payload)}
        result = extract_job_fields(
            "some raw JD text", adapters=adapters, config_path=self.config_path
        )
        self.assertEqual(result.title, "Data Engineer")
        self.assertIsNone(result.company)


class TestApplyUserOverrides(unittest.TestCase):
    """Tests for apply_user_overrides's merge and field-source tagging."""

    def test_user_value_wins_and_is_tagged(self) -> None:
        """An override present in the user's input replaces the parsed value."""
        extracted = ExtractedJobFields(title="Data Engineer", company="Parsed Co")
        merged, field_source = apply_user_overrides(
            extracted, {"company": "User Co", "title": None, "location": None}
        )
        self.assertEqual(merged.company, "User Co")
        self.assertEqual(merged.title, "Data Engineer")
        self.assertEqual(field_source, {"company": "user"})

    def test_no_overrides_leaves_extraction_untouched_and_field_source_empty(
        self,
    ) -> None:
        """With nothing overridden, the parsed values and empty map survive."""
        extracted = ExtractedJobFields(title="Data Engineer")
        merged, field_source = apply_user_overrides(
            extracted, {"company": None, "title": None, "location": None}
        )
        self.assertEqual(merged.title, "Data Engineer")
        self.assertEqual(field_source, {})


class TestExtractionResilience(unittest.TestCase):
    """Tests proving a broken LLM call is the caller's problem, not silent
    corruption — extract_job_fields itself still raises; graceful
    degradation is Task 6's orchestration's job, tested there."""

    def test_adapter_failure_propagates(self) -> None:
        """A raising adapter's exception is not swallowed at this layer."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml") as tmp:
            tmp.write(_SAMPLE_YAML)
            tmp.flush()
            adapters = {"fake": _RaisingAdapter()}
            with self.assertRaises(RuntimeError):
                extract_job_fields(
                    "text", adapters=adapters, config_path=Path(tmp.name)
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_extraction -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.extraction'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/extraction.py`**

```python
"""LLM structured extraction from a pasted job spec (PLAN.md Step 2).

The parse is a derived field, never authoritative over the raw text — if
extraction is wrong, it's re-run from landing, never re-requested from the
user. Extraction failures here propagate; it's the orchestration layer's
job (core.ingestion.manual) to decide that a failed parse should not fail
the whole ingest request.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from core.llm.gateway import complete
from core.llm.types import LLMAdapter

_PROMPT_TEMPLATE = """Extract the following fields from this job posting as \
a JSON object with exactly these keys: title, company, location, contract, \
salary, seniority. Use null for any field not stated in the text. Respond \
with JSON only, no other text.

Job posting:
{raw_text}
"""


class ExtractedJobFields(BaseModel):
    """Structured fields parsed from a job spec's raw text.

    Attributes:
        title: The job title, as stated.
        company: The employer name, as stated.
        location: The stated location.
        contract: The engagement type as stated in free text (e.g.
            "permanent", "contract") — the structured `engagement_type`
            enum lives in Step 5a, not here.
        salary: The stated salary/rate, as free text.
        seniority: The stated seniority level.
    """

    title: str | None = None
    company: str | None = None
    location: str | None = None
    contract: str | None = None
    salary: str | None = None
    seniority: str | None = None


def extract_job_fields(
    raw_text: str,
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    prompt_version: str = "local.v1",
) -> ExtractedJobFields:
    """Extract structured fields from a job spec's raw text via the LLM gateway.

    Args:
        raw_text: The verbatim pasted job spec.
        adapters: Every available adapter, keyed by provider name.
        config_path: Path to the task-config YAML. Defaults to
            `config/llm_tasks.yml` at the repository root.
        prompt_version: The versioned prompt identifier for the call log.

    Returns:
        The parsed `ExtractedJobFields`.

    Raises:
        Exception: Whatever the underlying adapter or JSON parsing raises.
            Not caught here — callers decide whether a failure should be
            fatal (see core.ingestion.manual.ingest_manual_job).
    """
    response = complete(
        "manual_entry_parse",
        _PROMPT_TEMPLATE.format(raw_text=raw_text),
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
    )
    return ExtractedJobFields.model_validate_json(response.text)


def apply_user_overrides(
    extracted: ExtractedJobFields,
    overrides: dict[str, str | None],
) -> tuple[ExtractedJobFields, dict[str, str]]:
    """Merge user-supplied field overrides over the parsed extraction.

    Args:
        extracted: The LLM's parsed fields.
        overrides: User-supplied values for `company`, `title`, and
            `location` — `None` means "no override for this field".

    Returns:
        A tuple of (merged fields, field_source), where field_source maps
        every field name that was actually overridden to `"user"`. Fields
        the user didn't override are absent from field_source (their
        source is implicitly the parser).
    """
    field_source: dict[str, str] = {}
    merged = extracted.model_copy()
    for field_name, value in overrides.items():
        if value:
            setattr(merged, field_name, value)
            field_source[field_name] = "user"
    return merged, field_source
```

- [ ] **Step 4: Add the task-config entry to `job_search/config/llm_tasks.yml`**

Add under the existing `tasks:` key (alongside `fabrication_critic`):

```yaml
  manual_entry_parse:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
```

(Per `DECISIONS.md` §1's task-split table, JD parsing is local-only and never migrates to the target provider.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_extraction -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/extraction.py packages/core/tests/test_extraction.py \
  config/llm_tasks.yml
git commit -m "feat(job_search): add LLM job-field extraction and override merging"
```

---

## Task 6: Orchestration — `ingest_manual_job`

**Files:**
- Create: `job_search/packages/core/core/ingestion/manual.py`
- Create: `job_search/packages/core/tests/test_manual_ingest.py`

**Interfaces:**
- Consumes: `canonicalise_url`, `extract_source_job_id` (Task 1); `generate_run_id`, `write_landing_record` (Task 2); `load_to_bronze` (Task 4); `ExtractedJobFields`, `extract_job_fields`, `apply_user_overrides` (Task 5).
- Produces: `@dataclass(frozen=True) class ManualIngestResult` with fields `source_name, job_url, job_url_canonical, source_job_id, run_id, landing_path, payload_sha256, extracted: ExtractedJobFields, field_source: dict[str, str]`. `def ingest_manual_job(*, source_name: str, job_url: str, job_spec: str, posted_date: datetime.date | None = None, company: str | None = None, title: str | None = None, location: str | None = None, notes: str | None = None, landing_uri: str, database_url: str, http_client: httpx.Client, llm_adapters: dict[str, LLMAdapter], load_to_bronze_fn: Callable[..., None] = load_to_bronze, extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields) -> ManualIngestResult`. Task 7's FastAPI endpoint calls this with these exact keyword arguments.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_manual_ingest.py
from __future__ import annotations

import datetime
import tempfile
import unittest

import httpx

from core.ingestion.extraction import ExtractedJobFields
from core.ingestion.manual import ingest_manual_job


class TestIngestManualJob(unittest.TestCase):
    """Tests for ingest_manual_job's orchestration and error resilience."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"
        self.http_client = httpx.Client(
            transport=httpx.MockTransport(_no_redirect_handler)
        )
        self.bronze_calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()
        self.http_client.close()

    def _fake_load_to_bronze(self, **kwargs: object) -> None:
        self.bronze_calls.append(kwargs)

    def _fake_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        return ExtractedJobFields(title="Data Engineer", company="Parsed Co")

    def _raising_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        raise RuntimeError("provider unreachable")

    def test_writes_landing_and_loads_bronze_with_the_raw_text_preserved(
        self,
    ) -> None:
        """The landing record and bronze payload both carry raw_text verbatim."""
        result = ingest_manual_job(
            source_name="linkedin_manual",
            job_url="https://www.linkedin.com/jobs/view/12345/?utm_source=li",
            job_spec="Full job posting text here.",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._fake_extract,
        )
        self.assertEqual(result.job_url_canonical, "https://www.linkedin.com/jobs/view/12345")
        self.assertEqual(result.source_job_id, "12345")
        self.assertEqual(len(self.bronze_calls), 1)
        self.assertEqual(
            self.bronze_calls[0]["payload"]["raw_text"], "Full job posting text here."
        )

    def test_user_overrides_win_and_are_tagged(self) -> None:
        """A user-supplied company override reaches the merged result."""
        result = ingest_manual_job(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            company="User-Supplied Co",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._fake_extract,
        )
        self.assertEqual(result.extracted.company, "User-Supplied Co")
        self.assertEqual(result.field_source, {"company": "user"})

    def test_extraction_failure_does_not_block_ingestion(self) -> None:
        """A broken LLM provider still lands the job — extraction degrades
        to an all-None result rather than failing the whole request."""
        result = ingest_manual_job(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._raising_extract,
        )
        self.assertIsNone(result.extracted.title)
        self.assertEqual(len(self.bronze_calls), 1)

    def test_reingesting_identical_input_produces_the_same_payload_sha256(
        self,
    ) -> None:
        """Dedup identity is stable across repeated calls with the same input."""
        kwargs = dict(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._fake_extract,
        )
        first = ingest_manual_job(**kwargs)
        second = ingest_manual_job(**kwargs)
        self.assertEqual(first.payload_sha256, second.payload_sha256)

    def test_payload_sha256_is_independent_of_extraction_result(self) -> None:
        """Identity hash is stable even when extraction fails on one call."""
        common_kwargs = dict(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
        )
        first = ingest_manual_job(**common_kwargs, extract_fn=self._fake_extract)
        second = ingest_manual_job(**common_kwargs, extract_fn=self._raising_extract)
        self.assertEqual(first.payload_sha256, second.payload_sha256)


def _no_redirect_handler(request: httpx.Request) -> httpx.Response:
    """Every request resolves to a plain 200 — nothing here redirects.

    ingest_manual_job's http_client is a required parameter, so it is
    always passed through to canonicalise_url, which always attempts one
    redirect-resolution HEAD request. A 200 response means
    _resolve_one_redirect finds `response.is_redirect` False and returns
    the URL unchanged — this handler exists to make that real code path
    exercised-but-inert, not to prove it's never called.
    """
    return httpx.Response(200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_manual_ingest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.manual'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/manual.py`**

```python
"""Orchestrates the manual job-entry pipeline end to end (PLAN.md Step 2).

canonicalise_url -> extract_source_job_id -> write to landing ->
best-effort LLM extraction -> load to bronze. Every collaborator is
injected with a real default, matching the pattern used throughout
core.llm and core.db — tests never need live network, Ollama, or Postgres.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from core.ingestion.bronze import load_to_bronze
from core.ingestion.extraction import ExtractedJobFields, extract_job_fields
from core.ingestion.landing import write_landing_record
from core.ingestion.run_id import generate_run_id
from core.ingestion.url_utils import canonicalise_url, extract_source_job_id
from core.llm.types import LLMAdapter

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManualIngestResult:
    """The outcome of one manual job-entry ingestion.

    Attributes:
        source_name: The source this record came from.
        job_url: The original (uncanonicalised) job URL.
        job_url_canonical: The canonicalised job URL.
        source_job_id: The extracted or hashed job identifier.
        run_id: The ULID identifying this ingestion run.
        landing_path: The path written to in the landing zone.
        payload_sha256: SHA-256 hex digest of the dedup-relevant payload.
        extracted: The (possibly all-None, on extraction failure) parsed
            fields, merged with any user overrides.
        field_source: Maps each user-overridden field name to `"user"`.
    """

    source_name: str
    job_url: str
    job_url_canonical: str
    source_job_id: str
    run_id: str
    landing_path: str
    payload_sha256: str
    extracted: ExtractedJobFields
    field_source: dict[str, str]


def _hash_payload(source_payload: dict[str, object]) -> str:
    """Hash the dedup-relevant payload, independent of extraction results.

    Args:
        source_payload: The pre-extraction record content (raw text, user
            overrides, posted date, notes) — never the parsed fields,
            which can change between reruns without the source changing.

    Returns:
        The SHA-256 hex digest of the payload's canonical JSON form.
    """
    canonical = json.dumps(source_payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def ingest_manual_job(
    *,
    source_name: str,
    job_url: str,
    job_spec: str,
    posted_date: datetime.date | None = None,
    company: str | None = None,
    title: str | None = None,
    location: str | None = None,
    notes: str | None = None,
    landing_uri: str,
    database_url: str,
    http_client: httpx.Client,
    llm_adapters: dict[str, LLMAdapter],
    load_to_bronze_fn: Callable[..., None] = load_to_bronze,
    extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields,
) -> ManualIngestResult:
    """Run the manual job-entry pipeline end to end.

    Args:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text, stored verbatim and never
            overwritten — the parse is a derived field.
        posted_date: When the job was posted, if known.
        company: User-supplied company override.
        title: User-supplied title override.
        location: User-supplied location override.
        notes: Free-text notes travelling with the job.
        landing_uri: Root URI of the landing zone.
        database_url: The migration/owner Postgres DSN for the bronze load.
        http_client: Used for the canonical URL's redirect resolution.
        llm_adapters: Every available LLM adapter, keyed by provider.
        load_to_bronze_fn: Injectable bronze loader — defaults to the real
            dlt-backed `load_to_bronze`.
        extract_fn: Injectable extraction function — defaults to the real
            `extract_job_fields`.

    Returns:
        The `ManualIngestResult` describing what was ingested.
    """
    canonical_url = canonicalise_url(job_url, http_client=http_client)
    source_job_id = extract_source_job_id(canonical_url)
    run_id = generate_run_id()
    fetched_at = datetime.datetime.now(datetime.timezone.utc)

    source_payload: dict[str, object] = {
        "raw_text": job_spec,
        "posted_date": posted_date.isoformat() if posted_date else None,
        "notes": notes,
        "overrides": {"company": company, "title": title, "location": location},
    }
    payload_sha256 = _hash_payload(source_payload)

    landing_record = {
        "_source_name": source_name,
        "_source_job_id": source_job_id,
        "_job_url": job_url,
        "_fetched_at": fetched_at.isoformat(),
        "_run_id": run_id,
        "_request_params": {},
        "_payload_sha256": payload_sha256,
        **source_payload,
    }
    landing_path = write_landing_record(
        landing_uri,
        source_name=source_name,
        run_id=run_id,
        record=landing_record,
        fetched_at=fetched_at,
    )

    try:
        extracted = extract_fn(job_spec, adapters=llm_adapters)
    except Exception:  # noqa: BLE001 — extraction is best-effort by design
        _logger.warning(
            "LLM extraction failed for source_name=%s job_url=%s; "
            "proceeding with an unextracted record (re-runnable from landing)",
            source_name,
            job_url,
            exc_info=True,
        )
        extracted = ExtractedJobFields()

    from core.ingestion.extraction import apply_user_overrides

    merged, field_source = apply_user_overrides(
        extracted, {"company": company, "title": title, "location": location}
    )

    bronze_payload = {
        **source_payload,
        "parsed": merged.model_dump(),
        "field_source": field_source,
    }
    load_to_bronze_fn(
        database_url=database_url,
        source_name=source_name,
        source_job_id=source_job_id,
        job_url=job_url,
        job_url_canonical=canonical_url,
        entry_method="manual",
        fetched_at=fetched_at,
        run_id=run_id,
        request_params={},
        payload=bronze_payload,
        payload_sha256=payload_sha256,
    )

    return ManualIngestResult(
        source_name=source_name,
        job_url=job_url,
        job_url_canonical=canonical_url,
        source_job_id=source_job_id,
        run_id=run_id,
        landing_path=landing_path,
        payload_sha256=payload_sha256,
        extracted=merged,
        field_source=field_source,
    )
```

- [ ] **Step 4: Move the `apply_user_overrides` import to the top of the file**

The inline `from core.ingestion.extraction import apply_user_overrides` above is written mid-function to keep the Step 3 code block's diff obvious against Task 5. Move it to the top-level imports alongside the existing `from core.ingestion.extraction import ExtractedJobFields, extract_job_fields` line (merge into one import statement) before running tests — a mid-function import is not idiomatic here and there's no circular-import reason for it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_manual_ingest -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the full unit suite so far**

Run: `cd job_search/packages/core && python3.11 -m unittest discover 2>&1 | tail -10`
Expected: all non-Postgres-dependent tests pass; integration tests requiring Postgres SKIP cleanly if it isn't running right now (that's fine for this task).

- [ ] **Step 7: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/manual.py packages/core/tests/test_manual_ingest.py
git commit -m "feat(job_search): add the manual-ingest orchestration function"
```

---

## Task 7: FastAPI endpoints — `POST /ingest/manual` and `GET /sources`

**Files:**
- Create: `job_search/apps/api/app/dependencies.py`
- Create: `job_search/apps/api/app/routers/__init__.py`
- Create: `job_search/apps/api/app/routers/ingest.py`
- Modify: `job_search/apps/api/app/main.py`
- Create: `job_search/packages/core/tests/test_api_ingest.py`

**Interfaces:**
- Consumes: `ingest_manual_job`, `ManualIngestResult` (Task 6); `build_engine` (from `core.db.session`, built in the Step 1 plan); the `job_search_app`-role `SELECT` grant on `bronze.raw_jobs` (Task 3).
- Produces: the `POST /ingest/manual` and `GET /sources` routes Task 8's Streamlit form calls. `GET /sources` exists because PLAN.md's form spec requires "dropdown of prior values + free text" for the source-name field — the dropdown needs a values source, and per `DECISIONS.md` §2.4 the UI never queries the database directly.

- [ ] **Step 1: Write `job_search/apps/api/app/dependencies.py`**

```python
"""FastAPI dependency providers — the app's collaborator seams."""

from __future__ import annotations

from functools import lru_cache

import httpx

from sqlalchemy import Engine

from core.db.session import build_engine
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.settings import get_settings


@lru_cache
def get_app_db_engine() -> Engine:
    """Return the process-wide, RLS-enforced app-role database engine.

    Returns:
        An `Engine` built from `settings.app_database_url`, reused across
        requests. Used for reads that don't need per-user scoping (e.g.
        `GET /sources` against the shared `bronze.raw_jobs` table) — never
        for the migration/owner DSN, which stays out of request-serving
        code entirely.
    """
    return build_engine(get_settings().app_database_url)


@lru_cache
def get_http_client() -> httpx.Client:
    """Return the process-wide HTTP client for outbound requests.

    Returns:
        A shared `httpx.Client`, reused across requests rather than
        rebuilt per call.
    """
    return httpx.Client(timeout=10.0)


@lru_cache
def get_llm_adapters() -> dict[str, LLMAdapter]:
    """Build the process-wide LLM adapter registry.

    Returns:
        A dict keyed by provider name. "ollama" is always present.
        "anthropic" is present only when an API key is configured — a
        task routed to a provider with no adapter here raises `KeyError`
        at call time, per `core.llm.gateway.complete`'s documented
        behaviour.
    """
    settings = get_settings()
    adapters: dict[str, LLMAdapter] = {
        "ollama": OllamaAdapter(
            base_url=settings.ollama_base_url, client=get_http_client()
        ),
    }
    if settings.anthropic_api_key:
        import anthropic

        adapters["anthropic"] = AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            client=anthropic.Anthropic(api_key=settings.anthropic_api_key),
        )
    return adapters
```

- [ ] **Step 2: Write `job_search/apps/api/app/routers/__init__.py`**

```python
"""FastAPI routers, one module per resource."""
```

- [ ] **Step 3: Write `job_search/apps/api/app/routers/ingest.py`**

```python
"""POST /ingest/manual — the manual job-entry endpoint (PLAN.md Step 2)."""

from __future__ import annotations

import datetime

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Engine, text

from app.dependencies import get_app_db_engine, get_http_client, get_llm_adapters
from core.ingestion.manual import ingest_manual_job
from core.llm.types import LLMAdapter
from core.settings import get_settings

router = APIRouter()


class ManualIngestRequest(BaseModel):
    """The paste-form's submission payload.

    Attributes:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text.
        posted_date: When the job was posted, if known.
        company: User-supplied company override.
        title: User-supplied title override.
        location: User-supplied location override.
        notes: Free-text notes travelling with the job.
    """

    source_name: str
    job_url: str
    job_spec: str
    posted_date: datetime.date | None = None
    company: str | None = None
    title: str | None = None
    location: str | None = None
    notes: str | None = None


class ManualIngestResponse(BaseModel):
    """What the caller learns about a completed ingestion.

    Attributes:
        source_job_id: The extracted or hashed job identifier.
        job_url_canonical: The canonicalised job URL.
        payload_sha256: SHA-256 hex digest of the dedup-relevant payload.
        landing_path: The path written to in the landing zone.
        extracted: The parsed (and user-overridden) fields.
        field_source: Maps each user-overridden field name to `"user"`.
    """

    source_job_id: str
    job_url_canonical: str
    payload_sha256: str
    landing_path: str
    extracted: dict[str, str | None]
    field_source: dict[str, str]


@router.post("/ingest/manual", response_model=ManualIngestResponse)
def ingest_manual(
    request: ManualIngestRequest,
    http_client: httpx.Client = Depends(get_http_client),
    llm_adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> ManualIngestResponse:
    """Ingest one manually-pasted job posting.

    Args:
        request: The paste-form's submission payload.
        http_client: Injected via `get_http_client`.
        llm_adapters: Injected via `get_llm_adapters`.

    Returns:
        The `ManualIngestResponse` describing what was ingested.
    """
    settings = get_settings()
    result = ingest_manual_job(
        source_name=request.source_name,
        job_url=request.job_url,
        job_spec=request.job_spec,
        posted_date=request.posted_date,
        company=request.company,
        title=request.title,
        location=request.location,
        notes=request.notes,
        landing_uri=settings.landing_uri,
        database_url=settings.database_url,
        http_client=http_client,
        llm_adapters=llm_adapters,
    )
    return ManualIngestResponse(
        source_job_id=result.source_job_id,
        job_url_canonical=result.job_url_canonical,
        payload_sha256=result.payload_sha256,
        landing_path=result.landing_path,
        extracted=result.extracted.model_dump(),
        field_source=result.field_source,
    )


@router.get("/sources", response_model=list[str])
def list_sources(
    engine: Engine = Depends(get_app_db_engine),
) -> list[str]:
    """List distinct source names already used, for the paste form's dropdown.

    Args:
        engine: Injected via `get_app_db_engine` — the RLS-enforced app
            role, which has a `SELECT`-only grant on `bronze.raw_jobs`
            (Task 3's migration).

    Returns:
        Distinct `source_name` values from `bronze.raw_jobs`, sorted
        alphabetically. Empty on a fresh database — the form falls back to
        free text only in that case.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT DISTINCT source_name FROM bronze.raw_jobs ORDER BY source_name")
        )
        return [row.source_name for row in result]
```

- [ ] **Step 4: Modify `job_search/apps/api/app/main.py`**

Add the router import and registration. The file currently has `/health` and `/whoami` — keep both, add:

```python
from app.routers import ingest

app.include_router(ingest.router)
```

(placed after `app = FastAPI(...)`, before or after the existing route definitions — order doesn't matter for `include_router`).

- [ ] **Step 5: Write the failing test, then verify it passes (this task has no separate RED step — the endpoint composes already-tested pieces, so this is a single integration-style TestClient test, not TDD in the strict sense)**

```python
# job_search/packages/core/tests/test_api_ingest.py
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import get_http_client, get_llm_adapters  # noqa: E402
from app.main import app  # noqa: E402
from core.ingestion.extraction import ExtractedJobFields  # noqa: E402
from core.ingestion.manual import ManualIngestResult  # noqa: E402
from core.llm.types import LLMAdapter, LLMResponse  # noqa: E402


class _FakeAdapter(LLMAdapter):
    """A test double so this test never touches a real LLM provider."""

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Return a fixed structured-extraction response."""
        return LLMResponse(
            text='{"title": "Data Engineer"}',
            provider="fake",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestIngestManualEndpoint(unittest.TestCase):
    """Tests for POST /ingest/manual's request/response wiring."""

    def setUp(self) -> None:
        app.dependency_overrides[get_http_client] = lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="unused")
            )
        )
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": _FakeAdapter()
        }
        self.client = TestClient(app)

    def tearDown(self) -> None:
        del app.dependency_overrides[get_http_client]
        del app.dependency_overrides[get_llm_adapters]

    def test_ingests_a_pasted_job_and_returns_the_canonical_identity(self) -> None:
        """A valid POST returns 200 with the canonicalised identity fields."""
        response = self.client.post(
            "/ingest/manual",
            json={
                "source_name": "linkedin_manual",
                "job_url": "https://www.linkedin.com/jobs/view/999/?utm_source=x",
                "job_spec": "Full posting text.",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["job_url_canonical"], "https://www.linkedin.com/jobs/view/999")
        self.assertEqual(body["source_job_id"], "999")
        self.assertIn("payload_sha256", body)

    def test_missing_required_field_returns_422(self) -> None:
        """Omitting job_spec (required) is a validation error."""
        response = self.client.post(
            "/ingest/manual",
            json={"source_name": "linkedin_manual", "job_url": "https://example.com"},
        )
        self.assertEqual(response.status_code, 422)

    def test_sources_lists_a_previously_ingested_source_name(self) -> None:
        """A source_name used in a prior ingest appears in GET /sources."""
        unique_source = f"test_source_{uuid.uuid4().hex[:8]}"
        self.client.post(
            "/ingest/manual",
            json={
                "source_name": unique_source,
                "job_url": "https://example.com/job",
                "job_spec": "text",
            },
        )
        response = self.client.get("/sources")
        self.assertEqual(response.status_code, 200)
        self.assertIn(unique_source, response.json())


if __name__ == "__main__":
    unittest.main()
```

Note: this test exercises the real `ingest_manual_job` (not a fake), which means it will attempt a REAL `load_to_bronze` call against `settings.database_url` inside the request. If Postgres isn't running when you run this test, expect it to fail at that point — that's expected and correct (this test genuinely needs live Postgres, same as the integration tests). Run it with Postgres up:

Run: `docker compose up -d postgres` (from `job_search/`, if not already up)
Run: `cd job_search/packages/core && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search python3.11 -m unittest tests.test_api_ingest -v`
Expected: PASS (3 tests). If you'd rather these tests not depend on live Postgres, that's a legitimate design improvement — but do not weaken it silently; if you make that change, note it clearly in your report as a deviation and explain the injection mechanism you added (e.g. overriding `ingest_manual_job`'s `load_to_bronze_fn` via a request-scoped dependency) so the reviewer can judge it.

- [ ] **Step 6: Commit**

```bash
cd job_search
git add apps/api/app/dependencies.py apps/api/app/routers/__init__.py \
  apps/api/app/routers/ingest.py apps/api/app/main.py packages/core/tests/test_api_ingest.py
git commit -m "feat(job_search): add POST /ingest/manual and GET /sources"
```

---

## Task 8: Streamlit paste form

**Files:**
- Create: `job_search/apps/ui/app/pages/1_Manual_Job_Entry.py`

**Interfaces:**
- Consumes: `POST /ingest/manual` and `GET /sources` (Task 7) over HTTP.
- Produces: nothing consumed by a later task — this is the outermost client.

- [ ] **Step 1: Write `job_search/apps/ui/app/pages/1_Manual_Job_Entry.py`**

```python
"""Manual job entry — the paste form (PLAN.md Step 2)."""

from __future__ import annotations

import datetime

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Manual Job Entry", layout="wide")
st.title("Manual Job Entry")
st.write("Paste a job posting you found by browsing — LinkedIn has no usable API.")

_settings = get_settings()

try:
    _sources_response = httpx.get(f"{_settings.api_base_url}/sources", timeout=10.0)
    _sources_response.raise_for_status()
    _known_sources: list[str] = _sources_response.json()
except httpx.HTTPError:
    _known_sources = []

_NEW_SOURCE_SENTINEL = "+ Add new source"
_source_options = [*_known_sources, _NEW_SOURCE_SENTINEL]

with st.form("manual_job_entry", clear_on_submit=True):
    source_choice = st.selectbox(
        "Source name",
        options=_source_options,
        index=len(_source_options) - 1 if _known_sources else 0,
        help="Pick a prior source, or add a new one below.",
    )
    new_source_name = st.text_input(
        "New source name",
        placeholder="linkedin_manual, otta, recruiter_email...",
        disabled=source_choice != _NEW_SOURCE_SENTINEL,
    )
    source_name = (
        new_source_name if source_choice == _NEW_SOURCE_SENTINEL else source_choice
    )
    job_url = st.text_input("Job URL")
    job_spec = st.text_area("Job spec", height=300, help="Paste the full posting.")

    col1, col2, col3 = st.columns(3)
    with col1:
        posted_date = st.date_input("Posted date", value=datetime.date.today())
    with col2:
        company = st.text_input("Company (override)")
    with col3:
        title = st.text_input("Title (override)")

    location = st.text_input("Location (override)")
    notes = st.text_area("Notes", placeholder="via recruiter X, referral through Y...")

    submitted = st.form_submit_button("Ingest")

if submitted:
    if not source_name or not job_url or not job_spec:
        st.error("Source name, job URL, and job spec are all required.")
    else:
        payload = {
            "source_name": source_name,
            "job_url": job_url,
            "job_spec": job_spec,
            "posted_date": posted_date.isoformat(),
            "company": company or None,
            "title": title or None,
            "location": location or None,
            "notes": notes or None,
        }
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/ingest/manual", json=payload, timeout=30.0
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            st.error(f"Ingestion failed: {exc}")
        else:
            body = response.json()
            st.success(
                f"Ingested — canonical URL: {body['job_url_canonical']} "
                f"(source_job_id: {body['source_job_id']})"
            )
            st.json(body)
```

- [ ] **Step 2: Verify the page imports cleanly (manual — no automated test for Streamlit UI, matching the precedent set for `apps/ui/app/Home.py`)**

Run: `cd job_search && python3.11 -c "import ast; ast.parse(open('apps/ui/app/pages/1_Manual_Job_Entry.py').read())"`
Expected: no output (valid Python syntax).

- [ ] **Step 3: Commit**

```bash
cd job_search
git add apps/ui/app/pages/1_Manual_Job_Entry.py
git commit -m "feat(job_search): add the manual job-entry Streamlit form"
```

---

## Task 9: Full-stack verification and acceptance sign-off

**Files:** none created — this task runs Step 2's literal acceptance criterion end to end and records the result.

**Interfaces:** none — verification only.

- [ ] **Step 1: Bring the full stack up clean, migrated**

Run: `cd job_search && docker compose down -v && docker compose up -d --build`
Run: `cd job_search/db && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search alembic upgrade head`
Expected: all four migrations apply, including 0004.

- [ ] **Step 2: Paste a real LinkedIn-shaped posting through the real HTTP endpoint**

Run (adjust the job text to a real posting you have on hand, or a realistic fabricated one — the point is exercising the real pipeline, not the specific content):

```bash
curl -s -X POST http://localhost:8000/ingest/manual \
  -H "Content-Type: application/json" \
  -d '{
    "source_name": "linkedin_manual",
    "job_url": "https://www.linkedin.com/jobs/view/4012345678/?utm_source=share&ref=abc",
    "job_spec": "Senior Data Engineer at Acme Ltd, London. Permanent, £90,000-£110,000. We are looking for an experienced data engineer to join our platform team...",
    "company": "Acme Ltd Override"
  }' | python3.11 -m json.tool
```

Expected: `200` with a JSON body containing `job_url_canonical` (no `utm_source`/`ref`), `source_job_id` (the numeric `4012345678`), and `payload_sha256`.

- [ ] **Step 3: Verify Step 2's literal acceptance criterion directly against Postgres**

Run: `docker compose exec postgres psql -U job_search_owner -d job_search -c "SELECT source_name, job_url_canonical, payload->>'raw_text' AS raw_text, payload_sha256 FROM bronze.raw_jobs WHERE source_name = 'linkedin_manual' ORDER BY fetched_at DESC LIMIT 1;"`

Expected: one row with `source_name = 'linkedin_manual'`, `job_url_canonical` matching the tracking-stripped URL, `raw_text` matching the pasted job spec verbatim, and a non-null `payload_sha256`. **This is the literal proof of Step 2's "Done when."**

- [ ] **Step 4: Verify extraction resilience — the pipeline survives Ollama being unreachable or unloaded**

Run: `docker compose stop ollama`
Run the same `curl` from Step 2 again (with a different `job_url` so it's a genuinely new record, e.g. change the numeric id).
Expected: still `200`, still a bronze row (per Step 3's query) — `extracted` in the response body may be all-`null` since the LLM call failed, and that's correct: extraction is best-effort, never fatal to ingestion.
Run: `docker compose start ollama` (restore for the rest of the stack).

- [ ] **Step 5: Verify re-ingesting an identical payload doesn't duplicate**

Re-run the exact same `curl` from Step 2 (same `job_url`, same `job_spec`, same `company`) a second time.
Run: `docker compose exec postgres psql -U job_search_owner -d job_search -c "SELECT COUNT(*) FROM bronze.raw_jobs WHERE source_job_id = '4012345678';"`
Expected: `1` — the merge/dedup on `(source_name, source_job_id, payload_sha256)` held.

- [ ] **Step 6: Run the full test suite and coverage**

Run: `cd job_search/packages/core && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search coverage run -m unittest discover && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search coverage report -m`
Expected: every test passes, including the new integration tests (not skipped).

- [ ] **Step 7: Run the full project quality gate**

Run: `cd job_search && python3.11 -m black --check . && python3.11 -m isort --check-only . && python3.11 -m ruff check .`
Run: `python3.11 -m mypy packages/core/core && python3.11 -m mypy apps/api/app && python3.11 -m mypy apps/pipeline/app` (per-target, per the known duplicate-`app`-module limitation)
Expected: all clean. Fix and re-run if anything fails.

- [ ] **Step 8: Tear down**

Run: `cd job_search && docker compose down`

- [ ] **Step 9: Open the PR**

Use the `commit-push-pr` skill on branch `feat/JOB-45-manual-job-entry`. Reference JOB-45 and its subtasks JOB-46–JOB-57 in the PR description, and paste the Step 3 psql output (the literal acceptance proof) and the coverage percentage from Step 6 into the PR body.
