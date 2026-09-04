# Step 4b — Jooble Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth connector, `JoobleConnector`, on the exact same
`Connector`/`run_connector` infrastructure as Adzuna/Reed/Greenhouse
(Step 4), and wire it into the CLI's now-registry-driven
`_CONNECTOR_BUILDERS`/`_QUERY_BUILDERS` pattern.

**Architecture:** `JoobleConnector` follows `AdzunaConnector`'s shape
exactly: single external source, page-number pagination, no try/except
around the page fetch (a failed page propagates so the shared runner's
`retry_with_backoff` retries the whole fetch), a per-record
`try/except KeyError` guard around `RawJob` construction (the fix Step 4's
final review required for Adzuna/Reed). The one genuine difference: auth
is neither query-param (Adzuna) nor HTTP Basic (Reed) but a POST JSON
body with the API key embedded directly in the URL path — Jooble's real
third shape.

**Tech Stack:** Python 3.11, `httpx`, `unittest` + `coverage`.

**Spec:** `PLAN.md`'s "full source roster" table (Jooble: free key, 60+
countries, EU breadth, POST JSON) and `plan/backlog.yml`'s `STEP-04B`
entry (`jira_key: JOB-440`, subtasks JOB-441–443).

## Real API shape below is live-verified, not recalled

Confirmed live during planning, using the real registered `JOOBLE_KEY`:
`POST https://jooble.org/api/{key}` with a JSON body `{"keywords": str,
"location": str, "page": str}`; response `{"totalCount": int, "jobs":
[...]}`; each job carries `title`, `location`, `snippet`, `salary`,
`source`, `type`, `link` (a `jooble.org/jdp/{id}` redirect URL, not the
original posting — same aggregator-redirect pattern as Adzuna),
`company`, `updated` (an ISO-8601 timestamp with no UTC offset — e.g.
`"2026-08-05T07:54:35.6100000"` — and up to 7 fractional-second digits,
which `datetime.fromisoformat` parses fine in Python 3.11, silently
truncating past microsecond precision), and `id` (a signed 64-bit
integer — **can be negative**, e.g. `-471887246143262713`; `str(id)`
handles this correctly, but do not assume `id` is always positive
anywhere). Pagination is page-number-based (`"page": "1"`, `"page":
"2"`, ...), a fixed 30 results per page confirmed by comparing two real
page fetches with zero ID overlap — there is no `results_per_page`-style
control to request a different page size, unlike Adzuna.

## Global Constraints

- Python 3.11, `black` (88 cols) + `isort` (profile black) + `ruff` +
  `mypy`.
- `unittest` + `coverage`, never `pytest`.
- Docstrings: Google-style (Args/Returns/Raises), on every function/class.
- Type hints required on all public function signatures; `from __future__
  import annotations`.
- Do not touch `core.ingestion.runner.run_connector`, `Connector`,
  `RawJob`, `AdzunaConnector`, `ReedConnector`, or `GreenhouseConnector`.
- Unit tests inject fake fetch functions — no real network. One separate
  live integration test makes real calls and skips cleanly on missing
  credentials or network failure (the exact pattern Step 4's final
  review required for the Adzuna/Reed live tests — replicate it here from
  the start, don't repeat that finding).
- `docker compose up -d postgres` must be running for Task 3's live
  Docker-based verification.

---

### Task 1: JoobleConnector

**Files:**
- Create: `packages/core/core/ingestion/jooble_connector.py`
- Test: `packages/core/tests/test_jooble_connector.py`

**Interfaces:**
- Consumes: `core.ingestion.connector.Connector`, `core.ingestion.
  raw_job.RawJob`, `core.ingestion.url_utils.canonicalise_url`.
- Produces: `JoobleQuery` (frozen dataclass: `keywords: str`, `location:
  str | None = None`, `max_pages: int = 5`), `JoobleConnector` —
  consumed by Task 2 (cli.py wiring).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_jooble_connector.py`:

```python
"""Tests for JoobleConnector."""

from __future__ import annotations

import datetime
import unittest

import httpx

from core.ingestion.jooble_connector import JoobleConnector, JoobleQuery


def _result(job_id: int, title: str, updated: str) -> dict[str, object]:
    return {
        "id": job_id,
        "title": title,
        "link": f"https://jooble.org/jdp/{job_id}",
        "company": "Acme",
        "location": "United Kingdom",
        "updated": updated,
    }


class TestJoobleConnector(unittest.TestCase):
    """Unit tests — every HTTP call is injected, no real network."""

    def setUp(self) -> None:
        """Provide a real (but unused) httpx.Client for the connector."""
        self.http_client = httpx.Client()
        self.calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        """Close the httpx.Client."""
        self.http_client.close()

    def _make_connector(self, pages: list[list[dict[str, object]]]) -> JoobleConnector:
        """Build a connector whose fetch_page_fn returns `pages` in order,
        recording every call's kwargs into self.calls."""

        def fake_fetch_page(http_client, *, api_key, keywords, location, page):
            self.calls.append(
                {"keywords": keywords, "location": location, "page": page}
            )
            index = page - 1
            results = pages[index] if index < len(pages) else []
            return {"jobs": results, "totalCount": sum(len(p) for p in pages)}

        return JoobleConnector(
            http_client=self.http_client,
            api_key="test-key",
            fetch_page_fn=fake_fetch_page,
        )

    def test_fetch_maps_one_page_of_results_to_raw_jobs(self) -> None:
        """A single short page yields one RawJob per result, then stops."""
        connector = self._make_connector(
            [[_result(1, "Data Engineer", "2026-09-01T00:00:00.0000000")]]
        )
        jobs = list(
            connector.fetch(JoobleQuery(keywords="data engineer"), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "jooble")
        self.assertEqual(jobs[0].source_job_id, "1")
        self.assertEqual(jobs[0].run_id, "run-1")
        self.assertEqual(len(self.calls), 1)

    def test_fetch_paginates_until_a_short_page(self) -> None:
        """A full 30-result page triggers a second fetch; a short page stops."""
        full_page = [
            _result(i, f"Job {i}", "2026-09-01T00:00:00.0000000") for i in range(30)
        ]
        short_page = [_result(100, "Last Job", "2026-09-01T00:00:00.0000000")]
        connector = self._make_connector([full_page, short_page])
        jobs = list(
            connector.fetch(JoobleQuery(keywords="data engineer"), None, run_id="run-1")
        )
        self.assertEqual(len(jobs), 31)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["page"], 1)
        self.assertEqual(self.calls[1]["page"], 2)

    def test_fetch_stops_at_max_pages_even_with_full_pages(self) -> None:
        """max_pages caps the number of API calls, even if every page is full."""
        full_page = [
            _result(i, f"Job {i}", "2026-09-01T00:00:00.0000000") for i in range(30)
        ]
        connector = self._make_connector([full_page] * 10)
        jobs = list(
            connector.fetch(
                JoobleQuery(keywords="x", max_pages=2), None, run_id="run-1"
            )
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(jobs), 60)

    def test_fetch_stops_immediately_on_empty_first_page(self) -> None:
        """Zero results on page 1 yields nothing and makes exactly one call."""
        connector = self._make_connector([[]])
        jobs = list(
            connector.fetch(
                JoobleQuery(keywords="nonexistent role xyz"), None, run_id="run-1"
            )
        )
        self.assertEqual(jobs, [])
        self.assertEqual(len(self.calls), 1)

    def test_location_is_passed_through_when_given(self) -> None:
        """query.location reaches fetch_page_fn unchanged."""
        connector = self._make_connector(
            [[_result(1, "Job", "2026-09-01T00:00:00.0000000")]]
        )
        list(
            connector.fetch(
                JoobleQuery(keywords="x", location="Manchester"),
                None,
                run_id="run-1",
            )
        )
        self.assertEqual(self.calls[0]["location"], "Manchester")

    def test_since_filters_out_jobs_updated_before_it(self) -> None:
        """A job whose `updated` is before `since` is excluded."""
        connector = self._make_connector(
            [
                [
                    _result(1, "Old", "2026-01-01T00:00:00.0000000"),
                    _result(2, "New", "2026-09-01T00:00:00.0000000"),
                ]
            ]
        )
        since = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        jobs = list(
            connector.fetch(JoobleQuery(keywords="x"), since, run_id="run-1")
        )
        self.assertEqual([j.source_job_id for j in jobs], ["2"])

    def test_negative_job_id_is_handled_correctly(self) -> None:
        """Jooble ids can be negative — str() must still produce a clean id."""
        connector = self._make_connector(
            [[_result(-471887246143262713, "Job", "2026-09-01T00:00:00.0000000")]]
        )
        jobs = list(
            connector.fetch(JoobleQuery(keywords="x"), None, run_id="run-1")
        )
        self.assertEqual(jobs[0].source_job_id, "-471887246143262713")

    def test_malformed_result_is_skipped_and_fetch_continues(self) -> None:
        """A result missing `link` is skipped; surrounding good results still yield."""
        good1 = _result(1, "Good One", "2026-09-01T00:00:00.0000000")
        bad = _result(2, "Bad", "2026-09-01T00:00:00.0000000")
        del bad["link"]
        good2 = _result(3, "Good Two", "2026-09-01T00:00:00.0000000")
        connector = self._make_connector([[good1, bad, good2]])
        jobs = list(
            connector.fetch(JoobleQuery(keywords="x"), None, run_id="run-1")
        )
        self.assertEqual([j.source_job_id for j in jobs], ["1", "3"])

    def test_payload_sha256_changes_when_result_content_changes(self) -> None:
        """Two fetches of the same id with different titles hash differently."""
        connector_v1 = self._make_connector(
            [[_result(1, "Data Engineer", "2026-09-01T00:00:00.0000000")]]
        )
        connector_v2 = self._make_connector(
            [[_result(1, "Senior Data Engineer", "2026-09-01T00:00:00.0000000")]]
        )
        job_v1 = list(
            connector_v1.fetch(JoobleQuery(keywords="x"), None, run_id="run-1")
        )[0]
        job_v2 = list(
            connector_v2.fetch(JoobleQuery(keywords="x"), None, run_id="run-1")
        )[0]
        self.assertNotEqual(job_v1.payload_sha256, job_v2.payload_sha256)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_jooble_connector -v
```

Expected: FAIL / ImportError — `core.ingestion.jooble_connector` does not
exist yet.

- [ ] **Step 3: Write the implementation**

`packages/core/core/ingestion/jooble_connector.py`:

```python
"""JoobleConnector — keyed POST JSON with page-number pagination
(PLAN.md Step 4b).

Jooble's auth is neither a query param (Adzuna) nor HTTP Basic (Reed):
the API key is embedded directly in the URL path
(`https://jooble.org/api/{key}`), and every request is a POST with a
JSON body, not a GET with query params — genuinely the third shape this
project's connectors have needed. Page size is fixed at 30 with no
caller-configurable results-per-page control, unlike Adzuna.

Like AdzunaConnector and ReedConnector, fetch() does not catch exceptions
around the page fetch itself — a failed page propagates so the shared
runner's retry_with_backoff can retry the whole fetch. It does guard the
per-record RawJob construction with try/except KeyError, matching the
fix Step 4's final review required for Adzuna and Reed.
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

_JOOBLE_SEARCH_URL = "https://jooble.org/api/{api_key}"
_DEFAULT_MAX_PAGES = 5
_PAGE_SIZE = 30


@dataclass(frozen=True)
class JoobleQuery:
    """JoobleConnector's `query` type.

    Attributes:
        keywords: Free-text search terms, e.g. "data engineer".
        location: Optional location filter, e.g. "UK", "Manchester".
            `None` means no location filter.
        max_pages: Safety cap on API calls per fetch() — pagination also
            stops early on a short (< 30-result) page.
    """

    keywords: str
    location: str | None = None
    max_pages: int = _DEFAULT_MAX_PAGES


def _hash_result(result: dict[str, object]) -> str:
    """Hash one Jooble result dict for dedup/change detection.

    Args:
        result: One entry from Jooble's `jobs` array.

    Returns:
        The SHA-256 hex digest of the result's canonical JSON form.
    """
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _parse_jooble_updated(value: object) -> datetime.datetime | None:
    """Parse Jooble's `updated` field into an aware datetime.

    Args:
        value: The raw `updated` value from a Jooble result dict —
            typically an offset-less ISO-8601 timestamp like
            "2026-08-05T07:54:35.6100000".

    Returns:
        The parsed datetime, normalised to UTC if it was naive, or `None`
        if `value` is missing or unparseable.
    """
    if not isinstance(value, str):
        return None
    try:
        result = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=datetime.UTC)
    return result


def _fetch_jooble_page(
    http_client: httpx.Client,
    *,
    api_key: str,
    keywords: str,
    location: str | None,
    page: int,
) -> dict[str, object]:
    """Fetch one page of Jooble search results.

    Args:
        http_client: The shared HTTP client.
        api_key: Jooble API key, embedded in the URL path.
        keywords: Free-text keyword query.
        location: Optional location filter.
        page: 1-indexed page number.

    Returns:
        The parsed JSON response body (`{"jobs": [...], "totalCount": N}`).

    Raises:
        httpx.HTTPStatusError: On a non-2xx response.
    """
    body: dict[str, object] = {"keywords": keywords, "page": str(page)}
    if location:
        body["location"] = location

    response = http_client.post(
        _JOOBLE_SEARCH_URL.format(api_key=api_key), json=body, timeout=15.0
    )
    response.raise_for_status()
    return response.json()


class JoobleConnector:
    """Fetches job postings from Jooble's keyed, paginated search API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        api_key: str,
        fetch_page_fn: Callable[..., dict[str, object]] = _fetch_jooble_page,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for every page fetch.
            api_key: Jooble API key.
            fetch_page_fn: Injectable per-page fetch — defaults to the
                real Jooble API call.
        """
        self._http_client = http_client
        self._api_key = api_key
        self._fetch_page_fn = fetch_page_fn

    def fetch(
        self, query: JoobleQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield job postings matching `query`, paginating as needed.

        Args:
            query: `JoobleQuery` — keywords, optional location, pagination
                cap.
            since: Only yield postings whose `updated` is at or after
                this time. `None` means no filter.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            One `RawJob` per result, across every fetched page.
        """
        for page in range(1, query.max_pages + 1):
            body = self._fetch_page_fn(
                self._http_client,
                api_key=self._api_key,
                keywords=query.keywords,
                location=query.location,
                page=page,
            )
            results = list(body.get("jobs", []))
            if not results:
                return

            fetched_at = datetime.datetime.now(datetime.UTC)
            for result in results:
                updated = _parse_jooble_updated(result.get("updated"))
                if since is not None and updated is not None and updated < since:
                    continue

                try:
                    job_url = str(result["link"])
                    canonical_url = canonicalise_url(job_url)
                    source_job_id = str(result["id"])
                except KeyError:
                    _logger.warning(
                        "Jooble result missing a required field (link/id) for "
                        "keywords=%s page=%s; skipping this record",
                        query.keywords,
                        page,
                        exc_info=True,
                    )
                    continue

                yield RawJob(
                    source_name="jooble",
                    source_job_id=source_job_id,
                    job_url=job_url,
                    job_url_canonical=canonical_url,
                    payload=dict(result),
                    fetched_at=fetched_at,
                    run_id=run_id,
                    request_params={
                        "keywords": query.keywords,
                        "location": query.location,
                        "page": page,
                    },
                    payload_sha256=_hash_result(result),
                )

            if len(results) < _PAGE_SIZE:
                return
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_jooble_connector -v
```

Expected: 9 tests, all PASS.

- [ ] **Step 5: Run the quality gate**

```bash
cd job_search
python3.11 -m black packages/core/core/ingestion/jooble_connector.py \
  packages/core/tests/test_jooble_connector.py
python3.11 -m isort packages/core/core/ingestion/jooble_connector.py \
  packages/core/tests/test_jooble_connector.py
python3.11 -m ruff check packages/core/core/ingestion/jooble_connector.py \
  packages/core/tests/test_jooble_connector.py
python3.11 -m mypy packages/core/core
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/ingestion/jooble_connector.py \
  packages/core/tests/test_jooble_connector.py
git commit -m "feat(job_search): add JoobleConnector"
```

---

### Task 2: Wire JoobleConnector into the CLI, `sources.yml`, and the live-test suite

**Files:**
- Modify: `apps/pipeline/app/cli.py`
- Modify: `config/sources.yml`
- Test: `packages/core/tests/test_pipeline_cli.py`
- Create: `packages/core/tests/integration/test_jooble_connector_live.py`

**Interfaces:**
- Consumes: `JoobleConnector`/`JoobleQuery` (Task 1), `core.settings.
  Settings.jooble_key: str | None` (already exists on `Settings`,
  currently unused), the existing `_ConnectorBuildContext`,
  `_CONNECTOR_BUILDERS`, `_QUERY_BUILDERS` registries (already
  registry-driven after Step 4's final-review fix wave — this task adds
  one more entry to each, nothing structural).

Read the current full `apps/pipeline/app/cli.py` first (`cat
apps/pipeline/app/cli.py`) — after Step 4's final review, `_QUERY_
BUILDERS` and `_CONNECTOR_BUILDERS` are both dict-literal registries kept
in sync by an assertion; adding Jooble means one entry in each, plus one
new query-builder function. There is no `if/elif` chain left to extend.

- [ ] **Step 1: Write the failing tests**

Append to `TestPipelineCli` in `packages/core/tests/test_pipeline_cli.py`:

```python
    def test_ingest_subcommand_jooble_requires_settings_key(self) -> None:
        """--source jooble with no Jooble key configured reports a clean error."""
        with (
            mock.patch.dict(os.environ, {"JOOBLE_KEY": ""}, clear=False),
            mock.patch("sys.stdout", new_callable=StringIO),
            mock.patch("sys.stderr", new_callable=StringIO) as stderr,
        ):
            get_settings.cache_clear()
            exit_code = main(
                ["ingest", "--source", "jooble", "--query", "data engineer"]
            )
            get_settings.cache_clear()
        self.assertEqual(exit_code, 1)
        self.assertIn("jooble", stderr.getvalue().lower())
        self.assertNotIn("Traceback", stderr.getvalue())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/core
python3.11 -m unittest tests.test_pipeline_cli -v
```

Expected: FAIL — `jooble` isn't a known source yet.

- [ ] **Step 3: Add the Jooble builder to `_CONNECTOR_BUILDERS` and `_QUERY_BUILDERS`**

Add near the other `_build_*_connector` functions:

```python
def _build_jooble_connector(ctx: _ConnectorBuildContext) -> Connector:
    """Build the Jooble connector.

    Args:
        ctx: The shared connector-build context.

    Returns:
        A `JoobleConnector` instance.

    Raises:
        ValueError: If `JOOBLE_KEY` isn't configured.
    """
    if not ctx.settings.jooble_key:
        raise ValueError("source=jooble requires JOOBLE_KEY to be set in .env")
    return JoobleConnector(http_client=ctx.http_client, api_key=ctx.settings.jooble_key)
```

Add `"jooble": _build_jooble_connector,` to `_CONNECTOR_BUILDERS`.

Add a query-builder near the others:

```python
def _build_jooble_query(raw_query: str, region: str | None) -> JoobleQuery:
    """Build a JoobleQuery from --query and --region.

    Args:
        raw_query: The `--query` argument's raw string value (keywords).
        region: The `--region` argument's raw string value, used as an
            optional location filter.

    Returns:
        The `JoobleQuery`.
    """
    return JoobleQuery(keywords=raw_query, location=region)
```

Add `"jooble": _build_jooble_query,` to `_QUERY_BUILDERS`. The existing
`assert _QUERY_BUILDERS.keys() == _CONNECTOR_BUILDERS.keys()` continues to
hold as long as both entries are added — verify it does.

Add `from core.ingestion.jooble_connector import JoobleConnector,
JoobleQuery` to the imports.

- [ ] **Step 4: Add the real `sources.yml` block**

Add to `config/sources.yml`'s `sources:` mapping, after the existing
three:

```yaml
  jooble:
    enabled: true
    auth: {key: "${JOOBLE_KEY}"}
    calls_per_hour: 40
    concurrency: 1
    backoff: {base: 2, max_retries: 5}
    regions: [gb]
```

(Note the `"${JOOBLE_KEY}"` value is quoted, matching the fix already
applied to the other three sources' auth blocks — an earlier task
discovered unquoted `${VAR}` inside a YAML flow mapping is invalid YAML.)

- [ ] **Step 5: Write the live integration test**

`packages/core/tests/integration/test_jooble_connector_live.py`:

```python
"""Live integration test — makes a real call to the Jooble API.

Skips cleanly if JOOBLE_KEY isn't configured or the network is
unreachable.
"""

from __future__ import annotations

import unittest

import httpx

from core.ingestion.jooble_connector import JoobleConnector, JoobleQuery
from core.settings import get_settings


class TestJoobleConnectorLive(unittest.TestCase):
    """Proves JoobleConnector works against the real Jooble API."""

    @classmethod
    def setUpClass(cls) -> None:
        """Skip the whole class if a Jooble API key isn't configured."""
        settings = get_settings()
        if not settings.jooble_key:
            raise unittest.SkipTest(
                "JOOBLE_KEY not set in .env — skipping the live Jooble test."
            )
        cls.api_key = settings.jooble_key

    def test_fetch_returns_real_results_for_a_common_query(self) -> None:
        """A broad, common query returns at least one real, well-formed RawJob."""
        with httpx.Client() as client:
            connector = JoobleConnector(http_client=client, api_key=self.api_key)
            try:
                jobs = list(
                    connector.fetch(
                        JoobleQuery(keywords="data engineer", location="UK", max_pages=1),
                        None,
                        run_id="live-test-run",
                    )
                )
            except httpx.HTTPError as exc:
                raise unittest.SkipTest(f"Network unreachable: {exc}") from None
        self.assertGreater(len(jobs), 0)
        first = jobs[0]
        self.assertEqual(first.source_name, "jooble")
        self.assertTrue(first.job_url.startswith("http"))
        self.assertTrue(first.source_job_id)
        self.assertEqual(len(first.payload_sha256), 64)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd packages/core
python3.11 -m unittest tests.test_pipeline_cli -v
```

Expected: every test PASSES, including the new one and every pre-existing
one unchanged.

- [ ] **Step 7: Run the live test**

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_test" \
python3.11 -m unittest tests.integration.test_jooble_connector_live -v
```

Expected: PASS (or a clean SKIP with a clear reason — never a hard FAIL).

- [ ] **Step 8: Run the quality gate**

```bash
cd job_search
python3.11 -m black apps/pipeline/app/cli.py \
  packages/core/tests/test_pipeline_cli.py \
  packages/core/tests/integration/test_jooble_connector_live.py
python3.11 -m isort apps/pipeline/app/cli.py \
  packages/core/tests/test_pipeline_cli.py \
  packages/core/tests/integration/test_jooble_connector_live.py
python3.11 -m ruff check apps/pipeline/app/cli.py \
  packages/core/tests/test_pipeline_cli.py \
  packages/core/tests/integration/test_jooble_connector_live.py
python3.11 -m mypy apps/pipeline/app
```

Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add apps/pipeline/app/cli.py config/sources.yml \
  packages/core/tests/test_pipeline_cli.py \
  packages/core/tests/integration/test_jooble_connector_live.py
git commit -m "feat(job_search): wire JoobleConnector into the ingest CLI"
```

---

### Task 3: Full-stack verification (controller-run, not dispatched)

- [ ] **Step 1: Confirm Postgres is up**

```bash
cd job_search
docker compose up -d postgres
docker compose exec -T postgres pg_isready
```

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

- [ ] **Step 3: Also run it with Postgres stopped**

```bash
cd job_search
docker compose down
cd packages/core
coverage run -m unittest discover
cd ../..
docker compose up -d postgres
```

Expected: completes quickly, no hangs — the exact class of regression
Step 4's final review caught once already; re-verify it hasn't
recurred for this new connector's tests.

- [ ] **Step 4: Run the acceptance command for real, via Docker**

```bash
cd job_search
docker compose --profile cli run --rm pipeline \
  ingest --source jooble --query "data engineer" --region gb
```

Expected: prints `ingest complete: source=jooble records=N run_id=...`
with `N > 0`. Confirm:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM bronze.raw_jobs WHERE source_name = 'jooble';"
```

Expected: count > 0.

- [ ] **Step 5: Quality gate, one last time**

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

- [ ] **Step 6: Tear down**

```bash
docker compose down
```

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage:** JOB-441 (implement connector, pagination) → Task 1;
  JOB-442 (wire into cli.py registries + sources.yml) → Task 2; JOB-443
  (verify landed records carry source_name/job_url) → Task 3, structurally
  guaranteed by `RawJob`'s non-optional fields plus `bronze.raw_jobs`'s
  `NOT NULL` constraints, same reasoning Step 4 used.
- **Placeholder scan:** none found — every code block is complete,
  transcribed from the live-verified probe, not reconstructed from memory.
- **Type consistency:** `JoobleConnector.fetch(self, query: JoobleQuery,
  ...)` follows the same narrower-than-`object` pattern already confirmed
  to type-check for `AdzunaConnector`/`ReedConnector`/`GreenhouseConnector`/
  `ManualConnector` — no new risk.
- Deliberately did NOT add a try/except around Jooble's page fetch (single-
  source paginated connector, same reasoning as Adzuna/Reed) — Task 1's
  brief explicitly states this so the implementer doesn't "fix" it by
  analogy with Greenhouse.
- The live test's `try/except httpx.HTTPError → SkipTest` pattern is
  included from the start, unlike the original Step 4 plan (which
  introduced it only as a post-hoc final-review fix for Adzuna/Reed) —
  applying that lesson forward rather than repeating the gap.
