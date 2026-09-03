# Job Search — Step 3 (Connector Contract and Shared Runner) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalise the `RawJob` envelope and `Connector` protocol, build the shared runner that owns rate limiting, retry, landing writes and run-metadata for every connector, retrofit Step 2's manual entry to go through it, and add a CLI entrypoint — so adding Step 4's real API connectors is one new file plus one `sources.yml` block, with zero changes to the runner.

**Architecture:** A `Connector` is anything with `fetch(query, since, *, run_id) -> Iterator[RawJob]`. The shared runner (`run_connector`) owns everything connector-agnostic: an optional rate-limit wait, retrying the whole fetch on failure, writing each yielded `RawJob` to the landing zone (reusing Step 2's `write_landing_record`) and to `bronze.raw_jobs` (reusing Step 2's `load_to_bronze`), and emitting run metadata. `ManualConnector` retrofits Step 2's URL-canonicalisation/extraction/override logic into `fetch()`, so `ingest_manual_job` becomes a thin caller of the runner — genuinely one code path into landing and bronze, not just a documented convention.

**Tech Stack:** No new dependencies — reuses `httpx`, `pyyaml`, `fsspec`, `dlt` from Steps 1–2. No new DB migration — `bronze.raw_jobs` (from Step 2) is reused as-is.

**Spec:** `job_search/PLAN.md` Step 3 (lines 320–365), `job_search/plan/backlog.yml` `STEP-03`/`JOB-58` (subtasks under that story), `job_search/DECISIONS.md` §2.4 (FastAPI/core holds all logic).

## Global Constraints

- Python 3.11; `from __future__ import annotations`; `list[T]`/`dict[K,V]` not `List`/`Dict`; type hints on public signatures; Google-style docstrings on every function/class, public and private, incl. tests.
- Tests: `unittest` + `coverage`, never `pytest`. No mocking the database — integration tests (anything touching live Postgres via `load_to_bronze`) use a real connection, gated with the established skip-if-unreachable pattern. Every I/O boundary (HTTP, sleep/time, the DB write functions) is dependency-injected with a real default, matching the pattern already used throughout `core.llm`, `core.db`, and Step 2's `core.ingestion` — no test needs live network, Postgres, or a real clock unless it's explicitly an integration test.
- `black` (88 cols) + `isort` (profile black, `known_first_party=["core"]`) + `ruff` (no `"I"` selected) + `mypy` (`cache_dir = "/dev/null"` already set — the `dlt` cache-crash workaround from Step 2 applies here too since `runner.py` transitively imports `load_to_bronze`) — all four must stay clean, run per-target for `apps/api/app`/`apps/pipeline/app` (duplicate-module limitation).
- **Session-hygiene note carried from Step 2's final review:** always pin absolute paths for `cd`, or verify `pwd` immediately before any command whose correctness depends on cwd — this session's Bash tool has silently reset cwd to the worktree root after backgrounded commands more than once.
- **Deliberate, disclosed deviation from the plan's literal `fetch(query, since) -> Iterator[RawJob]` text:** the actual signature is `fetch(self, query: object, since: datetime.datetime | None, *, run_id: str) -> Iterator[RawJob]`. `run_id` must be assigned ONCE per runner invocation (one landing-zone `run_id=` partition per batch, per PLAN.md's own landing-path convention `run_id=01J.../part-0001.jsonl.gz`), not once per yielded item — so the runner generates it and passes it in; the connector only stamps it onto each `RawJob` it builds. This doesn't change the "one new file, no runner changes" acceptance bar.
- SQL: none in this step (no migration).

---

## File Structure

```
job_search/
  packages/core/core/ingestion/
    raw_job.py             # RawJob dataclass
    connector.py            # Connector Protocol
    rate_limiter.py          # TokenBucket
    retry.py                  # retry_with_backoff
    sources_config.py          # SourceConfig, load_sources_config
    run_metadata.py             # RunMetadata, write_run_metadata
    runner.py                    # RunResult, run_connector
    manual_connector.py           # ManualJobQuery, ManualConnector
    manual.py                      # MODIFIED: ingest_manual_job routes through the runner
  packages/core/tests/
    test_raw_job_and_connector.py
    test_rate_limiter.py
    test_retry.py
    test_sources_config.py
    test_run_metadata.py
    test_runner.py
    test_manual_connector.py
    test_manual_ingest.py          # MODIFIED (existing, Step 2)
    integration/
      test_runner_bronze.py
  config/sources.yml               # new — schema doc + empty sources: {}
  apps/pipeline/app/cli.py         # MODIFIED: add the `ingest` subcommand
  packages/core/tests/test_pipeline_cli.py  # MODIFIED (existing, Step 1) — new subcommand test added alongside
```

---

## Task 1: `RawJob` envelope and the `Connector` protocol

**Files:**
- Create: `job_search/packages/core/core/ingestion/raw_job.py`
- Create: `job_search/packages/core/core/ingestion/connector.py`
- Create: `job_search/packages/core/tests/test_raw_job_and_connector.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `@dataclass(frozen=True) class RawJob` with fields `source_name: str, source_job_id: str, job_url: str, job_url_canonical: str, payload: dict[str, object], fetched_at: datetime.datetime, run_id: str, request_params: dict[str, object], payload_sha256: str`. `class Connector(Protocol)` with `def fetch(self, query: object, since: datetime.datetime | None, *, run_id: str) -> Iterator[RawJob]: ...`. Every later task imports both from these two modules.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_raw_job_and_connector.py
from __future__ import annotations

import datetime
import unittest
from collections.abc import Iterator

from core.ingestion.connector import Connector
from core.ingestion.raw_job import RawJob


class TestRawJob(unittest.TestCase):
    """Tests for the RawJob envelope's shape and immutability."""

    def test_constructs_with_all_required_fields(self) -> None:
        """RawJob accepts exactly the fields PLAN.md Step 3 specifies."""
        job = RawJob(
            source_name="adzuna",
            source_job_id="123",
            job_url="https://example.com/job/123",
            job_url_canonical="https://example.com/job/123",
            payload={"title": "Data Engineer"},
            fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
            run_id="01J000000000000000000000",
            request_params={"query": "data engineer"},
            payload_sha256="abc123",
        )
        self.assertEqual(job.source_name, "adzuna")
        self.assertEqual(job.payload, {"title": "Data Engineer"})

    def test_is_frozen(self) -> None:
        """RawJob instances are immutable, matching PLAN.md's immutable-landing philosophy."""
        job = RawJob(
            source_name="adzuna",
            source_job_id="123",
            job_url="https://example.com/job/123",
            job_url_canonical="https://example.com/job/123",
            payload={},
            fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
            run_id="01J000000000000000000000",
            request_params={},
            payload_sha256="abc123",
        )
        with self.assertRaises(AttributeError):
            job.source_name = "changed"  # type: ignore[misc]


class _FakeConnector:
    """A minimal Connector implementation, proving the Protocol is satisfiable
    with just one method — the whole point of Task 1's contract."""

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield one hardcoded RawJob, ignoring query/since."""
        yield RawJob(
            source_name="fake",
            source_job_id="1",
            job_url="https://example.com/1",
            job_url_canonical="https://example.com/1",
            payload={},
            fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
            run_id=run_id,
            request_params={},
            payload_sha256="x",
        )


class TestConnectorProtocol(unittest.TestCase):
    """Tests proving Connector is a genuine structural Protocol."""

    def test_a_class_with_a_matching_fetch_method_satisfies_the_protocol(self) -> None:
        """isinstance-style structural check: _FakeConnector IS-A Connector."""
        connector: Connector = _FakeConnector()
        jobs = list(connector.fetch(None, None, run_id="01J000000000000000000000"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_name, "fake")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_raw_job_and_connector -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.raw_job'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/raw_job.py`**

```python
"""The RawJob envelope — the one shape every connector yields (PLAN.md Step 3).

source_name and job_url are captured at extraction time, before any
parsing, so provenance survives even when a source's payload shape
changes or downstream parsing fails.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class RawJob:
    """One fetched job posting, in the shape every connector must produce.

    Attributes:
        source_name: Which source this came from, e.g. "adzuna" or a
            user-typed label for manual entries, e.g. "linkedin_manual".
        source_job_id: The source's own identifier for this posting, or a
            content hash where no stable identifier exists.
        job_url: The original (uncanonicalised) job URL.
        job_url_canonical: The canonicalised job URL.
        payload: The full record payload — untouched raw content plus
            whatever derived fields the connector chooses to attach.
        fetched_at: When this record was captured.
        run_id: The ULID identifying the run that produced this record —
            shared by every RawJob yielded in the same run_connector()
            call, never generated per-item.
        request_params: Whatever request parameters produced this record
            (empty for manual entry).
        payload_sha256: SHA-256 hex digest of the payload's dedup-relevant
            content — the runner's landing/bronze writes key on this.
    """

    source_name: str
    source_job_id: str
    job_url: str
    job_url_canonical: str
    payload: dict[str, object]
    fetched_at: datetime.datetime
    run_id: str
    request_params: dict[str, object]
    payload_sha256: str
```

- [ ] **Step 4: Write `job_search/packages/core/core/ingestion/connector.py`**

```python
"""The Connector protocol every source implements (PLAN.md Step 3).

Adding a connector means writing one class satisfying this Protocol plus
one config block in config/sources.yml — the shared runner never changes.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from typing import Protocol

from core.ingestion.raw_job import RawJob


class Connector(Protocol):
    """Structural interface: anything with a matching fetch() qualifies."""

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Fetch job postings matching `query`, updated since `since`.

        Args:
            query: Connector-specific query — a search string for an API
                connector, a `ManualJobQuery` for manual entry. Each
                connector defines and documents its own concrete type.
            since: Only return postings updated at or after this time, for
                connectors that support incremental fetching. `None` means
                "no incremental filter" — a full fetch.
            run_id: The ULID identifying this run, assigned once by the
                runner and stamped onto every yielded RawJob — connectors
                never generate their own run_id.

        Yields:
            One `RawJob` per matching posting.
        """
        ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_raw_job_and_connector -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/raw_job.py packages/core/core/ingestion/connector.py \
  packages/core/tests/test_raw_job_and_connector.py
git commit -m "feat(job_search): add the RawJob envelope and Connector protocol"
```

---

## Task 2: Token-bucket rate limiter

**Files:**
- Create: `job_search/packages/core/core/ingestion/rate_limiter.py`
- Create: `job_search/packages/core/tests/test_rate_limiter.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `class TokenBucket` with `__init__(self, *, capacity: int, refill_period_seconds: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None` and `def acquire(self) -> None`. Task 6 (the runner) constructs and calls this.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_rate_limiter.py
from __future__ import annotations

import unittest

from core.ingestion.rate_limiter import TokenBucket


class _FakeClock:
    """A controllable clock so tests never depend on real elapsed time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward."""
        self.now += seconds


class TestTokenBucket(unittest.TestCase):
    """Tests for TokenBucket's capacity, refill, and blocking behaviour."""

    def test_allows_calls_up_to_capacity_without_sleeping(self) -> None:
        """The first `capacity` acquires never sleep."""
        clock = _FakeClock()
        sleeps: list[float] = []
        bucket = TokenBucket(
            capacity=3, refill_period_seconds=60.0, clock=clock, sleep=sleeps.append
        )
        bucket.acquire()
        bucket.acquire()
        bucket.acquire()
        self.assertEqual(sleeps, [])

    def test_blocks_once_capacity_is_exhausted(self) -> None:
        """The (capacity + 1)th acquire sleeps until a token refills."""
        clock = _FakeClock()
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(seconds)

        bucket = TokenBucket(
            capacity=1, refill_period_seconds=60.0, clock=clock, sleep=fake_sleep
        )
        bucket.acquire()
        bucket.acquire()
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0)

    def test_refills_after_the_period_elapses(self) -> None:
        """Advancing the clock past refill_period_seconds allows another
        acquire without sleeping."""
        clock = _FakeClock()
        sleeps: list[float] = []
        bucket = TokenBucket(
            capacity=1, refill_period_seconds=60.0, clock=clock, sleep=sleeps.append
        )
        bucket.acquire()
        clock.advance(61.0)
        bucket.acquire()
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_rate_limiter -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.rate_limiter'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/rate_limiter.py`**

```python
"""A per-source token-bucket rate limiter, driven by config/sources.yml
(PLAN.md Step 3) — the runner owns one of these per connector so a
connector's own code never has to think about pacing itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    """A simple token-bucket limiter: `capacity` calls per `refill_period_seconds`.

    Attributes:
        capacity: Maximum tokens the bucket can hold (and the number of
            calls allowed in one refill period before blocking).
        refill_period_seconds: How long a full refill takes.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_period_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise the bucket, full.

        Args:
            capacity: Maximum tokens (and calls per period).
            refill_period_seconds: Seconds for the bucket to fully refill
                from empty.
            clock: Injectable monotonic clock, for deterministic tests.
            sleep: Injectable sleep function, for deterministic tests.
        """
        self.capacity = capacity
        self.refill_period_seconds = refill_period_seconds
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(capacity)
        self._last_refill = clock()

    def _refill(self) -> None:
        """Add tokens proportional to elapsed time, capped at capacity."""
        now = self._clock()
        elapsed = now - self._last_refill
        self._last_refill = now
        rate = self.capacity / self.refill_period_seconds
        self._tokens = min(self.capacity, self._tokens + elapsed * rate)

    def acquire(self) -> None:
        """Consume one token, sleeping first if none are available."""
        self._refill()
        if self._tokens < 1:
            rate = self.capacity / self.refill_period_seconds
            wait_seconds = (1 - self._tokens) / rate
            self._sleep(wait_seconds)
            self._refill()
        self._tokens -= 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_rate_limiter -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/rate_limiter.py packages/core/tests/test_rate_limiter.py
git commit -m "feat(job_search): add the TokenBucket rate limiter"
```

---

## Task 3: Retry with exponential backoff and jitter

**Files:**
- Create: `job_search/packages/core/core/ingestion/retry.py`
- Create: `job_search/packages/core/tests/test_retry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `def retry_with_backoff(fn: Callable[[], T], *, base: float, max_retries: int, sleep: Callable[[float], None] = time.sleep, jitter: Callable[[], float] = random.random, retry_on: tuple[type[Exception], ...] = (Exception,)) -> T`. Task 6 (the runner) calls this to wrap the whole `fetch()` consumption.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_retry.py
from __future__ import annotations

import unittest

from core.ingestion.retry import retry_with_backoff


class TestRetryWithBackoff(unittest.TestCase):
    """Tests for retry_with_backoff's retry count, backoff, and success paths."""

    def test_returns_the_result_on_first_success_with_no_sleep(self) -> None:
        """A function that succeeds immediately is never retried."""
        sleeps: list[float] = []
        result = retry_with_backoff(
            lambda: "ok", base=1.0, max_retries=3, sleep=sleeps.append, jitter=lambda: 0.0
        )
        self.assertEqual(result, "ok")
        self.assertEqual(sleeps, [])

    def test_retries_up_to_max_retries_then_raises(self) -> None:
        """Exhausting all retries re-raises the last exception."""
        calls = {"count": 0}

        def always_fails() -> None:
            calls["count"] += 1
            raise RuntimeError("boom")

        sleeps: list[float] = []
        with self.assertRaises(RuntimeError):
            retry_with_backoff(
                always_fails,
                base=1.0,
                max_retries=2,
                sleep=sleeps.append,
                jitter=lambda: 0.0,
            )
        self.assertEqual(calls["count"], 3)  # 1 initial + 2 retries
        self.assertEqual(len(sleeps), 2)

    def test_backoff_grows_exponentially_with_base(self) -> None:
        """Sleep durations grow as base * 2**attempt (jitter zeroed out)."""
        calls = {"count": 0}

        def fails_twice_then_succeeds() -> str:
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("boom")
            return "ok"

        sleeps: list[float] = []
        result = retry_with_backoff(
            fails_twice_then_succeeds,
            base=2.0,
            max_retries=5,
            sleep=sleeps.append,
            jitter=lambda: 0.0,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_jitter_is_added_to_each_sleep(self) -> None:
        """The jitter callable's return value is added to the base delay."""
        calls = {"count": 0}

        def fails_once_then_succeeds() -> str:
            calls["count"] += 1
            if calls["count"] < 2:
                raise RuntimeError("boom")
            return "ok"

        sleeps: list[float] = []
        retry_with_backoff(
            fails_once_then_succeeds,
            base=1.0,
            max_retries=3,
            sleep=sleeps.append,
            jitter=lambda: 0.5,
        )
        self.assertEqual(sleeps, [1.5])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_retry -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.retry'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/retry.py`**

```python
"""Retry with exponential backoff and jitter (PLAN.md Step 3).

The runner wraps a connector's whole fetch() consumption in this — a
transient failure retries the entire fetch, not individual pages. Real
per-page resumable retry is a Step 4+ refinement once a paginated
connector actually exists.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    base: float,
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Call `fn`, retrying on failure with exponential backoff plus jitter.

    Args:
        fn: The zero-argument function to call.
        base: Base delay in seconds; the Nth retry sleeps
            `base * 2**(N-1) + jitter()`.
        max_retries: Maximum number of retries after the first attempt.
        sleep: Injectable sleep function, for deterministic tests.
        jitter: Injectable function returning a random delay to add.
        retry_on: Exception types that trigger a retry. Anything else
            propagates immediately.

    Returns:
        `fn()`'s return value, from whichever attempt first succeeds.

    Raises:
        Exception: Whatever `fn` raised on its final attempt, once
            `max_retries` is exhausted.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except retry_on:
            if attempt >= max_retries:
                raise
            delay = base * (2**attempt) + jitter()
            sleep(delay)
            attempt += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_retry -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/retry.py packages/core/tests/test_retry.py
git commit -m "feat(job_search): add retry_with_backoff"
```

---

## Task 4: `sources.yml` schema and loader

**Files:**
- Create: `job_search/config/sources.yml`
- Create: `job_search/packages/core/core/ingestion/sources_config.py`
- Create: `job_search/packages/core/tests/test_sources_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `@dataclass(frozen=True) class SourceConfig` with fields `enabled: bool, calls_per_hour: int | None, concurrency: int | None, backoff_base: float | None, backoff_max_retries: int | None, regions: list[str] | None`. `def load_sources_config(path: Path | None = None) -> dict[str, SourceConfig]`. Task 9 (the CLI) uses this to look up rate-limit settings by connector key.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_sources_config.py
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.ingestion.sources_config import SourceConfig, load_sources_config

_SAMPLE_YAML = """
sources:
  adzuna:
    enabled: true
    auth:
      app_id: ${ADZUNA_APP_ID}
      app_key: ${ADZUNA_APP_KEY}
    calls_per_hour: 40
    concurrency: 2
    backoff:
      base: 2
      max_retries: 5
    regions: [gb, ie, fr, de, us]
  reed:
    enabled: false
"""


class TestLoadSourcesConfig(unittest.TestCase):
    """Tests for load_sources_config's parsing of the sources.yml schema."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        tmp.write(_SAMPLE_YAML)
        tmp.close()
        self.config_path = Path(tmp.name)

    def tearDown(self) -> None:
        self.config_path.unlink(missing_ok=True)

    def test_parses_a_fully_specified_source(self) -> None:
        """Every field of a fully-specified source is parsed correctly."""
        config = load_sources_config(self.config_path)
        adzuna = config["adzuna"]
        self.assertEqual(
            adzuna,
            SourceConfig(
                enabled=True,
                calls_per_hour=40,
                concurrency=2,
                backoff_base=2.0,
                backoff_max_retries=5,
                regions=["gb", "ie", "fr", "de", "us"],
            ),
        )

    def test_a_minimal_disabled_source_defaults_the_rest_to_none(self) -> None:
        """A source with only `enabled` set gets None for everything else."""
        config = load_sources_config(self.config_path)
        reed = config["reed"]
        self.assertEqual(reed.enabled, False)
        self.assertIsNone(reed.calls_per_hour)
        self.assertIsNone(reed.regions)

    def test_missing_file_returns_an_empty_mapping(self) -> None:
        """An empty (or absent) sources.yml is valid — no connectors configured yet."""
        empty_path = self.config_path.parent / "does_not_exist.yml"
        config = load_sources_config(empty_path)
        self.assertEqual(config, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_sources_config -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.sources_config'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/sources_config.py`**

```python
"""config/sources.yml schema and loader (PLAN.md Step 3).

This is what "adding a connector requires one new file plus one
sources.yml block" means concretely — the runner reads this file to build
a TokenBucket and retry policy per connector; a connector with no entry
here simply runs unrated/unlimited.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "sources.yml"


@dataclass(frozen=True)
class SourceConfig:
    """One connector's entry in config/sources.yml.

    Attributes:
        enabled: Whether this connector should be used. `auth` values
            themselves live in `.env`, referenced here only via
            `${VAR_NAME}` placeholders for documentation — this loader
            does not resolve them; that's the connector's own job at
            construction time via `core.settings`.
        calls_per_hour: Token-bucket capacity/refill-period-per-hour, or
            `None` for no rate limiting.
        concurrency: Maximum concurrent requests, or `None` for no cap
            (informational for now — Step 3's runner processes one
            connector at a time; a future step may use this for
            parallelism).
        backoff_base: Base delay in seconds for `retry_with_backoff`, or
            `None` to use the runner's default.
        backoff_max_retries: Max retries for `retry_with_backoff`, or
            `None` to use the runner's default.
        regions: Region codes this connector should be queried across, or
            `None` if not region-scoped.
    """

    enabled: bool
    calls_per_hour: int | None
    concurrency: int | None
    backoff_base: float | None
    backoff_max_retries: int | None
    regions: list[str] | None


def load_sources_config(path: Path | None = None) -> dict[str, SourceConfig]:
    """Load and parse config/sources.yml.

    Args:
        path: Path to the sources YAML file. Defaults to
            `config/sources.yml` at the repository root.

    Returns:
        A mapping of connector key to its `SourceConfig`. Returns an
        empty mapping if the file doesn't exist or has no `sources` key —
        both are valid states (no rate-limited connectors configured yet).
    """
    target = path or _DEFAULT_CONFIG_PATH
    if not target.exists():
        return {}

    raw = yaml.safe_load(target.read_text())
    sources = raw.get("sources", {}) if raw else {}

    result: dict[str, SourceConfig] = {}
    for key, entry in sources.items():
        entry = entry or {}
        backoff = entry.get("backoff") or {}
        result[key] = SourceConfig(
            enabled=bool(entry.get("enabled", False)),
            calls_per_hour=entry.get("calls_per_hour"),
            concurrency=entry.get("concurrency"),
            backoff_base=(
                float(backoff["base"]) if "base" in backoff else None
            ),
            backoff_max_retries=backoff.get("max_retries"),
            regions=entry.get("regions"),
        )
    return result
```

- [ ] **Step 4: Write `job_search/config/sources.yml`**

```yaml
# Per-connector configuration for the shared ingestion runner
# (core.ingestion.runner.run_connector, PLAN.md Step 3).
#
# A connector with no entry here runs with no rate limiting and the
# runner's default retry policy — that's the correct state for manual
# entry (core.ingestion.manual_connector.ManualConnector), which makes no
# external API calls and therefore needs neither.
#
# Schema, illustrated (not a live entry — Step 4 adds the real one when
# the AdzunaConnector class actually exists):
#
#   adzuna:
#     enabled: true
#     auth: {app_id: ${ADZUNA_APP_ID}, app_key: ${ADZUNA_APP_KEY}}
#     calls_per_hour: 40
#     concurrency: 2
#     backoff: {base: 2, max_retries: 5}
#     regions: [gb, ie, fr, de, us]

sources: {}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_sources_config -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd job_search
git add config/sources.yml packages/core/core/ingestion/sources_config.py \
  packages/core/tests/test_sources_config.py
git commit -m "feat(job_search): add sources.yml schema and loader"
```

---

## Task 5: Run metadata

**Files:**
- Create: `job_search/packages/core/core/ingestion/run_metadata.py`
- Create: `job_search/packages/core/tests/test_run_metadata.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `@dataclass(frozen=True) class RunMetadata` with fields `run_id: str, source_name: str, query: str, records: int, started_at: datetime.datetime, finished_at: datetime.datetime, status: str`. `def write_run_metadata(landing_uri: str, metadata: RunMetadata) -> str` returning the path written. Task 6 (the runner) calls this.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_run_metadata.py
from __future__ import annotations

import datetime
import json
import tempfile
import unittest
from pathlib import Path

from core.ingestion.run_metadata import RunMetadata, write_run_metadata


class TestWriteRunMetadata(unittest.TestCase):
    """Tests for write_run_metadata's path layout and JSON content."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()

    def test_writes_json_at_the_expected_path(self) -> None:
        """The metadata lands at _runs/{source_name}/{run_id}.json."""
        metadata = RunMetadata(
            run_id="01J000000000000000000000",
            source_name="adzuna",
            query="data engineer",
            records=12,
            started_at=datetime.datetime(2026, 9, 3, 10, 0, tzinfo=datetime.UTC),
            finished_at=datetime.datetime(2026, 9, 3, 10, 1, tzinfo=datetime.UTC),
            status="success",
        )
        path = write_run_metadata(self.landing_uri, metadata)
        expected_suffix = "_runs/adzuna/01J000000000000000000000.json"
        self.assertTrue(path.endswith(expected_suffix))

        local_path = Path(self._tmp_dir.name) / expected_suffix
        content = json.loads(local_path.read_text())
        self.assertEqual(content["records"], 12)
        self.assertEqual(content["status"], "success")
        self.assertEqual(content["query"], "data engineer")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_run_metadata -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.run_metadata'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/run_metadata.py`**

```python
"""Run metadata emission (PLAN.md Step 3): run_id, source, query, records,
started_at, finished_at, status — one JSON file per run, in the landing
zone alongside the data it describes.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass

import fsspec


@dataclass(frozen=True)
class RunMetadata:
    """Summary of one connector run.

    Attributes:
        run_id: The ULID identifying this run.
        source_name: The connector key this run was for, e.g. "adzuna"
            or "manual".
        query: A string representation of the query used — kept as a
            plain string rather than a generic serialisable type, since
            every connector's query shape differs.
        records: How many RawJobs this run produced.
        started_at: When the run began.
        finished_at: When the run ended (success or failure).
        status: `"success"` or `"failed"`.
    """

    run_id: str
    source_name: str
    query: str
    records: int
    started_at: datetime.datetime
    finished_at: datetime.datetime
    status: str


def write_run_metadata(landing_uri: str, metadata: RunMetadata) -> str:
    """Write one run's metadata as JSON in the landing zone.

    Args:
        landing_uri: Root URI of the landing zone.
        metadata: The `RunMetadata` to write.

    Returns:
        The full path written to.
    """
    path = (
        f"{landing_uri.rstrip('/')}/_runs/{metadata.source_name}/"
        f"{metadata.run_id}.json"
    )
    record = asdict(metadata)
    record["started_at"] = metadata.started_at.isoformat()
    record["finished_at"] = metadata.finished_at.isoformat()
    with fsspec.open(path, "wt") as handle:
        handle.write(json.dumps(record))
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_run_metadata -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/run_metadata.py packages/core/tests/test_run_metadata.py
git commit -m "feat(job_search): add run metadata emission"
```

---

## Task 6: The shared runner — `run_connector`

**Files:**
- Create: `job_search/packages/core/core/ingestion/runner.py`
- Create: `job_search/packages/core/tests/test_runner.py`
- Create: `job_search/packages/core/tests/integration/test_runner_bronze.py`

**Interfaces:**
- Consumes: `RawJob`, `Connector` (Task 1); `TokenBucket` (Task 2); `retry_with_backoff` (Task 3); `RunMetadata`, `write_run_metadata` (Task 5); `write_landing_record` (from Step 2's `core.ingestion.landing`); `load_to_bronze` (from Step 2's `core.ingestion.bronze`).
- Produces: `@dataclass(frozen=True) class RunResult` with fields `run_metadata: RunMetadata, raw_jobs: list[RawJob], landing_paths: list[str]`. `def run_connector(*, connector_key: str, connector: Connector, query: object, since: datetime.datetime | None, entry_method: str, landing_uri: str, database_url: str, rate_limiter: TokenBucket | None = None, retry_base: float = 2.0, retry_max_retries: int = 5, load_to_bronze_fn: Callable[..., None] = load_to_bronze, write_landing_record_fn: Callable[..., str] = write_landing_record, write_run_metadata_fn: Callable[..., str] = write_run_metadata, sleep_fn: Callable[[float], None] = time.sleep, jitter_fn: Callable[[], float] = random.random) -> RunResult`. Task 7's `ManualConnector` and Task 8's refactored `ingest_manual_job` depend on this; Task 9's CLI calls it directly.

This is the architecturally central task in this plan — every later task depends on it.

- [ ] **Step 1: Write the failing unit tests (fakes only, no live Postgres)**

```python
# job_search/packages/core/tests/test_runner.py
from __future__ import annotations

import datetime
import unittest
from collections.abc import Iterator

from core.ingestion.raw_job import RawJob
from core.ingestion.rate_limiter import TokenBucket
from core.ingestion.runner import run_connector


class _FakeConnector:
    """A test double yielding a fixed, injectable list of RawJobs."""

    def __init__(self, jobs: list[RawJob], *, fail_times: int = 0) -> None:
        self._jobs = jobs
        self._fail_times = fail_times
        self._calls = 0

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield the configured jobs, failing the first `fail_times` calls."""
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError("transient failure")
        for job in self._jobs:
            yield RawJob(
                source_name=job.source_name,
                source_job_id=job.source_job_id,
                job_url=job.job_url,
                job_url_canonical=job.job_url_canonical,
                payload=job.payload,
                fetched_at=job.fetched_at,
                run_id=run_id,
                request_params=job.request_params,
                payload_sha256=job.payload_sha256,
            )


def _sample_job(source_job_id: str = "1") -> RawJob:
    return RawJob(
        source_name="fake",
        source_job_id=source_job_id,
        job_url="https://example.com/job",
        job_url_canonical="https://example.com/job",
        payload={"raw_text": "hello"},
        fetched_at=datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
        run_id="unused",
        request_params={},
        payload_sha256="abc",
    )


class TestRunConnector(unittest.TestCase):
    """Tests for run_connector's wiring: rate limit, retry, landing, bronze, metadata."""

    def setUp(self) -> None:
        self.landing_calls: list[dict[str, object]] = []
        self.bronze_calls: list[dict[str, object]] = []
        self.metadata_calls: list[object] = []

    def _fake_write_landing_record(self, landing_uri: str, **kwargs: object) -> str:
        self.landing_calls.append(kwargs)
        return f"{landing_uri}/fake/path.jsonl.gz"

    def _fake_load_to_bronze(self, **kwargs: object) -> None:
        self.bronze_calls.append(kwargs)

    def _fake_write_run_metadata(self, landing_uri: str, metadata: object) -> str:
        self.metadata_calls.append(metadata)
        return f"{landing_uri}/fake/run.json"

    def test_writes_landing_and_bronze_for_every_yielded_job(self) -> None:
        """Each RawJob the connector yields gets one landing write and one bronze load."""
        connector = _FakeConnector([_sample_job("1"), _sample_job("2")])
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(len(self.landing_calls), 2)
        self.assertEqual(len(self.bronze_calls), 2)
        self.assertEqual(len(result.raw_jobs), 2)
        self.assertEqual(len(result.landing_paths), 2)

    def test_all_yielded_jobs_share_one_run_id_assigned_by_the_runner(self) -> None:
        """run_id is generated once by the runner, not per-item by the connector."""
        connector = _FakeConnector([_sample_job("1"), _sample_job("2")])
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        run_ids = {job.run_id for job in result.raw_jobs}
        self.assertEqual(len(run_ids), 1)
        self.assertEqual(result.run_metadata.run_id, run_ids.pop())

    def test_rate_limiter_is_acquired_once_per_run_when_given(self) -> None:
        """A configured rate limiter's acquire() is called exactly once."""
        acquire_calls = {"count": 0}

        class _CountingBucket:
            def acquire(self) -> None:
                acquire_calls["count"] += 1

        connector = _FakeConnector([_sample_job()])
        run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            rate_limiter=_CountingBucket(),  # type: ignore[arg-type]
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(acquire_calls["count"], 1)

    def test_no_rate_limiter_means_no_wait(self) -> None:
        """Omitting rate_limiter is valid — the run proceeds unthrottled."""
        connector = _FakeConnector([_sample_job()])
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(len(result.raw_jobs), 1)

    def test_transient_fetch_failure_is_retried_then_succeeds(self) -> None:
        """A connector that fails once then succeeds still completes the run."""
        connector = _FakeConnector([_sample_job()], fail_times=1)
        sleeps: list[float] = []
        result = run_connector(
            connector_key="fake",
            connector=connector,
            query="q",
            since=None,
            entry_method="api",
            landing_uri="file:///tmp/unused",
            database_url="unused",
            retry_base=0.01,
            retry_max_retries=3,
            load_to_bronze_fn=self._fake_load_to_bronze,
            write_landing_record_fn=self._fake_write_landing_record,
            write_run_metadata_fn=self._fake_write_run_metadata,
            sleep_fn=sleeps.append,
            jitter_fn=lambda: 0.0,
        )
        self.assertEqual(len(result.raw_jobs), 1)
        self.assertEqual(len(sleeps), 1)

    def test_exhausted_retries_writes_a_failed_run_metadata_and_reraises(self) -> None:
        """A connector that always fails writes status='failed' metadata, then raises."""
        connector = _FakeConnector([_sample_job()], fail_times=99)
        with self.assertRaises(RuntimeError):
            run_connector(
                connector_key="fake",
                connector=connector,
                query="q",
                since=None,
                entry_method="api",
                landing_uri="file:///tmp/unused",
                database_url="unused",
                retry_base=0.01,
                retry_max_retries=1,
                load_to_bronze_fn=self._fake_load_to_bronze,
                write_landing_record_fn=self._fake_write_landing_record,
                write_run_metadata_fn=self._fake_write_run_metadata,
                sleep_fn=lambda s: None,
                jitter_fn=lambda: 0.0,
            )
        self.assertEqual(len(self.metadata_calls), 1)
        self.assertEqual(self.metadata_calls[0].status, "failed")
        self.assertEqual(len(self.landing_calls), 0)
        self.assertEqual(len(self.bronze_calls), 0)

    def test_successful_run_writes_metadata_with_correct_record_count(self) -> None:
        """The success-path metadata's records count matches the number yielded."""
        connector = _FakeConnector([_sample_job("1"), _sample_job("2"), _sample_job("3")])
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
        self.assertEqual(self.metadata_calls[0].records, 3)
        self.assertEqual(self.metadata_calls[0].status, "success")
        self.assertEqual(self.metadata_calls[0].query, "my query")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_runner -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.runner'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/runner.py`**

```python
"""The shared ingestion runner (PLAN.md Step 3).

Owns everything connector-agnostic: an optional rate-limit wait, retrying
the whole fetch on failure, landing writes, bronze loads, and run
metadata. A connector's only job is fetch() — this is what makes adding
one "one new file plus one sources.yml block, no runner changes."
"""

from __future__ import annotations

import datetime
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.ingestion.bronze import load_to_bronze
from core.ingestion.connector import Connector
from core.ingestion.landing import write_landing_record
from core.ingestion.raw_job import RawJob
from core.ingestion.rate_limiter import TokenBucket
from core.ingestion.retry import retry_with_backoff
from core.ingestion.run_id import generate_run_id
from core.ingestion.run_metadata import RunMetadata, write_run_metadata


@dataclass(frozen=True)
class RunResult:
    """The outcome of one run_connector() call.

    Attributes:
        run_metadata: The `RunMetadata` this run produced.
        raw_jobs: Every `RawJob` the connector yielded, in order.
        landing_paths: The landing-zone path written for each `raw_jobs`
            entry, at the same index.
    """

    run_metadata: RunMetadata
    raw_jobs: list[RawJob]
    landing_paths: list[str]


def run_connector(
    *,
    connector_key: str,
    connector: Connector,
    query: object,
    since: datetime.datetime | None,
    entry_method: str,
    landing_uri: str,
    database_url: str,
    rate_limiter: TokenBucket | None = None,
    retry_base: float = 2.0,
    retry_max_retries: int = 5,
    load_to_bronze_fn: Callable[..., None] = load_to_bronze,
    write_landing_record_fn: Callable[..., str] = write_landing_record,
    write_run_metadata_fn: Callable[..., str] = write_run_metadata,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = random.random,
) -> RunResult:
    """Run one connector end to end: fetch, land, load to bronze, record.

    Args:
        connector_key: Identifies this connector for rate limiting and run
            metadata — e.g. "adzuna" or "manual". Distinct from any
            individual `RawJob.source_name`, which can vary per item
            (e.g. a user-typed label for manual entries).
        connector: The `Connector` to run.
        query: Passed through to `connector.fetch()` untouched.
        since: Passed through to `connector.fetch()` untouched.
        entry_method: "api", "manual", or "scraped" — stamped onto every
            bronze row this run produces.
        landing_uri: Root URI of the landing zone.
        database_url: The migration/owner Postgres DSN for bronze loads.
        rate_limiter: When given, `acquire()` is called once before the
            fetch — never per yielded item. `None` means unthrottled.
        retry_base: Base delay in seconds for retrying a failed fetch.
        retry_max_retries: Max retries for a failed fetch.
        load_to_bronze_fn: Injectable bronze loader.
        write_landing_record_fn: Injectable landing-zone writer.
        write_run_metadata_fn: Injectable run-metadata writer.
        sleep_fn: Injectable sleep, threaded into the retry policy.
        jitter_fn: Injectable jitter, threaded into the retry policy.

    Returns:
        The `RunResult` describing what this run produced.

    Raises:
        Exception: Whatever the connector's `fetch()` raised, once
            retries are exhausted. A `status="failed"` run metadata record
            is written before re-raising.
    """
    run_id = generate_run_id()
    started_at = datetime.datetime.now(datetime.UTC)

    if rate_limiter is not None:
        rate_limiter.acquire()

    def _do_fetch() -> list[RawJob]:
        return list(connector.fetch(query, since, run_id=run_id))

    try:
        raw_jobs = retry_with_backoff(
            _do_fetch,
            base=retry_base,
            max_retries=retry_max_retries,
            sleep=sleep_fn,
            jitter=jitter_fn,
        )
    except Exception:
        finished_at = datetime.datetime.now(datetime.UTC)
        write_run_metadata_fn(
            landing_uri,
            RunMetadata(
                run_id=run_id,
                source_name=connector_key,
                query=str(query),
                records=0,
                started_at=started_at,
                finished_at=finished_at,
                status="failed",
            ),
        )
        raise

    landing_paths: list[str] = []
    for raw_job in raw_jobs:
        landing_record = {
            "_source_name": raw_job.source_name,
            "_source_job_id": raw_job.source_job_id,
            "_job_url": raw_job.job_url,
            "_fetched_at": raw_job.fetched_at.isoformat(),
            "_run_id": raw_job.run_id,
            "_request_params": raw_job.request_params,
            "_payload_sha256": raw_job.payload_sha256,
            **raw_job.payload,
        }
        path = write_landing_record_fn(
            landing_uri,
            source_name=raw_job.source_name,
            run_id=raw_job.run_id,
            record=landing_record,
            fetched_at=raw_job.fetched_at,
        )
        landing_paths.append(path)

        load_to_bronze_fn(
            database_url=database_url,
            source_name=raw_job.source_name,
            source_job_id=raw_job.source_job_id,
            job_url=raw_job.job_url,
            job_url_canonical=raw_job.job_url_canonical,
            entry_method=entry_method,
            fetched_at=raw_job.fetched_at,
            run_id=raw_job.run_id,
            request_params=raw_job.request_params,
            payload=raw_job.payload,
            payload_sha256=raw_job.payload_sha256,
        )

    finished_at = datetime.datetime.now(datetime.UTC)
    metadata = RunMetadata(
        run_id=run_id,
        source_name=connector_key,
        query=str(query),
        records=len(raw_jobs),
        started_at=started_at,
        finished_at=finished_at,
        status="success",
    )
    write_run_metadata_fn(landing_uri, metadata)

    return RunResult(run_metadata=metadata, raw_jobs=raw_jobs, landing_paths=landing_paths)
```

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_runner -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Write the live-Postgres integration test**

```python
# job_search/packages/core/tests/integration/test_runner_bronze.py
from __future__ import annotations

import datetime
import tempfile
import unittest
import uuid
from collections.abc import Iterator

from sqlalchemy import text

from core.db.session import build_engine, session_scope
from core.ingestion.raw_job import RawJob
from core.ingestion.runner import run_connector
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


class _OneJobConnector:
    """A connector yielding exactly one hardcoded RawJob, for a real
    end-to-end proof that run_connector's bronze write actually lands."""

    def __init__(self, source_job_id: str) -> None:
        self._source_job_id = source_job_id

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield one job, ignoring query/since."""
        yield RawJob(
            source_name="runner_it_test",
            source_job_id=self._source_job_id,
            job_url="https://example.com/job",
            job_url_canonical="https://example.com/job",
            payload={"raw_text": "integration test payload"},
            fetched_at=datetime.datetime.now(datetime.UTC),
            run_id=run_id,
            request_params={},
            payload_sha256=f"sha-{self._source_job_id}",
        )


class TestRunConnectorBronzeIntegration(unittest.TestCase):
    """Proves run_connector's bronze write actually lands, end to end."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.landing_uri = f"file://{self._tmp_dir.name}"
        self.source_job_id = f"test-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text(
                    "DELETE FROM bronze.raw_jobs WHERE source_name = 'runner_it_test' "
                    "AND source_job_id = :sjid"
                ),
                {"sjid": self.source_job_id},
            )

    def test_run_connector_lands_a_real_bronze_row(self) -> None:
        """A real connector run produces exactly one queryable bronze row."""
        result = run_connector(
            connector_key="runner_it_test",
            connector=_OneJobConnector(self.source_job_id),
            query="integration-test-query",
            since=None,
            entry_method="api",
            landing_uri=self.landing_uri,
            database_url=get_settings().database_url,
        )
        self.assertEqual(len(result.raw_jobs), 1)

        with session_scope(self.migration_engine) as conn:
            row = conn.execute(
                text(
                    "SELECT payload->>'raw_text' AS raw_text, entry_method "
                    "FROM bronze.raw_jobs "
                    "WHERE source_name = 'runner_it_test' AND source_job_id = :sjid"
                ),
                {"sjid": self.source_job_id},
            ).one()
        self.assertEqual(row.raw_text, "integration test payload")
        self.assertEqual(row.entry_method, "api")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run the integration test against live Postgres**

Run: `docker compose up -d postgres` (from `job_search/`)
Run: `cd job_search/packages/core && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search python3.11 -m unittest tests.integration.test_runner_bronze -v`
Expected: PASS (1 test, genuinely against live Postgres, not skipped).

Leave Postgres UP afterward — later tasks continue from this state.

- [ ] **Step 7: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/runner.py packages/core/tests/test_runner.py \
  packages/core/tests/integration/test_runner_bronze.py
git commit -m "feat(job_search): add the shared ingestion runner"
```

---

## Task 7: `ManualConnector`

**Files:**
- Create: `job_search/packages/core/core/ingestion/manual_connector.py`
- Create: `job_search/packages/core/tests/test_manual_connector.py`

**Interfaces:**
- Consumes: `canonicalise_url`, `extract_source_job_id` (Step 2's `core.ingestion.url_utils`); `ExtractedJobFields`, `extract_job_fields`, `apply_user_overrides` (Step 2's `core.ingestion.extraction`); `RawJob` (Task 1).
- Produces: `@dataclass(frozen=True) class ManualJobQuery` with fields `source_name: str, job_url: str, job_spec: str, posted_date: datetime.date | None = None, company: str | None = None, title: str | None = None, location: str | None = None, notes: str | None = None`. `class ManualConnector` implementing `Connector`, constructed with `__init__(self, *, http_client: httpx.Client, llm_adapters: dict[str, LLMAdapter], extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields) -> None`. Task 8's refactored `ingest_manual_job` constructs and calls this.

- [ ] **Step 1: Write the failing tests**

```python
# job_search/packages/core/tests/test_manual_connector.py
from __future__ import annotations

import unittest

import httpx

from core.ingestion.extraction import ExtractedJobFields
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery


def _no_redirect_handler(request: httpx.Request) -> httpx.Response:
    """Every request resolves to a plain 200 — nothing here redirects.

    fetch()'s http_client is always passed to canonicalise_url, which
    always attempts one redirect-resolution HEAD request — a 200 means
    "nothing to follow," matching the pattern from Step 2's test suite.
    """
    return httpx.Response(200)


class TestManualConnector(unittest.TestCase):
    """Tests for ManualConnector.fetch()'s single-RawJob output."""

    def setUp(self) -> None:
        self.http_client = httpx.Client(
            transport=httpx.MockTransport(_no_redirect_handler)
        )

    def tearDown(self) -> None:
        self.http_client.close()

    def _fake_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        return ExtractedJobFields(title="Data Engineer", company="Parsed Co")

    def _raising_extract(self, raw_text: str, **kwargs: object) -> ExtractedJobFields:
        raise RuntimeError("provider unreachable")

    def test_yields_exactly_one_raw_job_with_canonicalised_url_and_stamped_run_id(
        self,
    ) -> None:
        """fetch() yields one RawJob; job_url_canonical and run_id are set correctly."""
        connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        query = ManualJobQuery(
            source_name="linkedin_manual",
            job_url="https://www.linkedin.com/jobs/view/12345/?utm_source=li",
            job_spec="Full job posting text here.",
        )
        jobs = list(connector.fetch(query, None, run_id="01J000000000000000000000"))
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.job_url_canonical, "https://www.linkedin.com/jobs/view/12345")
        self.assertEqual(job.source_job_id, "12345")
        self.assertEqual(job.run_id, "01J000000000000000000000")
        self.assertEqual(job.payload["raw_text"], "Full job posting text here.")

    def test_payload_includes_parsed_fields_and_field_source(self) -> None:
        """The enriched payload carries both the extraction and override tags."""
        connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        query = ManualJobQuery(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            company="User-Supplied Co",
        )
        job = next(iter(connector.fetch(query, None, run_id="01J000000000000000000000")))
        self.assertEqual(job.payload["parsed"]["company"], "User-Supplied Co")
        self.assertEqual(job.payload["field_source"], {"company": "user"})

    def test_extraction_failure_still_yields_one_job_with_null_parsed_fields(
        self,
    ) -> None:
        """A broken LLM provider still lands the job — extraction is best-effort."""
        connector = ManualConnector(
            http_client=self.http_client,
            llm_adapters={},
            extract_fn=self._raising_extract,
        )
        query = ManualJobQuery(
            source_name="linkedin_manual", job_url="https://example.com/job", job_spec="text"
        )
        jobs = list(connector.fetch(query, None, run_id="01J000000000000000000000"))
        self.assertEqual(len(jobs), 1)
        self.assertIsNone(jobs[0].payload["parsed"]["title"])

    def test_payload_sha256_is_independent_of_extraction_result(self) -> None:
        """Dedup identity is stable even when extraction fails on one call."""
        query = ManualJobQuery(
            source_name="linkedin_manual", job_url="https://example.com/job", job_spec="text"
        )
        ok_connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        failing_connector = ManualConnector(
            http_client=self.http_client,
            llm_adapters={},
            extract_fn=self._raising_extract,
        )
        job_ok = next(
            iter(ok_connector.fetch(query, None, run_id="01J000000000000000000000"))
        )
        job_failed = next(
            iter(failing_connector.fetch(query, None, run_id="01J000000000000000000000"))
        )
        self.assertEqual(job_ok.payload_sha256, job_failed.payload_sha256)

    def test_reingesting_identical_input_produces_the_same_payload_sha256(self) -> None:
        """Dedup identity is stable across repeated calls with the same input."""
        connector = ManualConnector(
            http_client=self.http_client, llm_adapters={}, extract_fn=self._fake_extract
        )
        query = ManualJobQuery(
            source_name="linkedin_manual", job_url="https://example.com/job", job_spec="text"
        )
        first = next(
            iter(connector.fetch(query, None, run_id="01J000000000000000000000"))
        )
        second = next(
            iter(connector.fetch(query, None, run_id="01J000000000000000000001"))
        )
        self.assertEqual(first.payload_sha256, second.payload_sha256)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_manual_connector -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ingestion.manual_connector'`.

- [ ] **Step 3: Write `job_search/packages/core/core/ingestion/manual_connector.py`**

```python
"""ManualConnector — Step 2's manual-entry logic, retrofitted onto the
Connector protocol (PLAN.md Step 3), so it goes through the exact same
shared runner as every future API connector.

payload_sha256 is still computed from the pre-extraction "source payload"
only (raw_text, posted_date, notes, overrides) — never from extraction's
output — preserving Step 2's dedup-identity-independent-of-extraction
invariant. Unlike Step 2's original code, the enriched (parsed +
field_source) result is embedded directly into the yielded RawJob's
payload rather than requiring a second bronze write — landing gaining a
snapshot of a since-superseded extraction attempt doesn't violate
"raw_text is never overwritten": raw_text itself is always present and
untouched, so a later re-extraction pass can always read it back out.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from core.ingestion.extraction import (
    ExtractedJobFields,
    apply_user_overrides,
    extract_job_fields,
)
from core.ingestion.raw_job import RawJob
from core.ingestion.url_utils import canonicalise_url, extract_source_job_id
from core.llm.types import LLMAdapter

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManualJobQuery:
    """One manually-pasted job submission — ManualConnector's `query` type.

    Attributes:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text, stored verbatim.
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


def _hash_payload(source_payload: dict[str, object]) -> str:
    """Hash the dedup-relevant payload, independent of extraction results.

    Args:
        source_payload: The pre-extraction record content.

    Returns:
        The SHA-256 hex digest of the payload's canonical JSON form.
    """
    canonical = json.dumps(source_payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ManualConnector:
    """Fetches exactly one RawJob from a manually-pasted job submission."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        llm_adapters: dict[str, LLMAdapter],
        extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields,
    ) -> None:
        """Initialise the connector.

        Args:
            http_client: Used for the canonical URL's redirect resolution.
            llm_adapters: Every available LLM adapter, keyed by provider.
            extract_fn: Injectable extraction function — defaults to the
                real `extract_job_fields`.
        """
        self._http_client = http_client
        self._llm_adapters = llm_adapters
        self._extract_fn = extract_fn

    def fetch(
        self, query: ManualJobQuery, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield the one RawJob this manual submission produces.

        Args:
            query: The `ManualJobQuery` describing what was pasted.
            since: Unused — manual entry has no incremental-fetch concept.
            run_id: The ULID assigned by the runner for this run.

        Yields:
            Exactly one `RawJob`.
        """
        canonical_url = canonicalise_url(query.job_url, http_client=self._http_client)
        source_job_id = extract_source_job_id(canonical_url)
        fetched_at = datetime.datetime.now(datetime.UTC)

        source_payload: dict[str, object] = {
            "raw_text": query.job_spec,
            "posted_date": (
                query.posted_date.isoformat() if query.posted_date else None
            ),
            "notes": query.notes,
            "overrides": {
                "company": query.company,
                "title": query.title,
                "location": query.location,
            },
        }
        payload_sha256 = _hash_payload(source_payload)

        try:
            extracted = self._extract_fn(query.job_spec, adapters=self._llm_adapters)
        except Exception:  # noqa: BLE001 — extraction is best-effort by design
            _logger.warning(
                "LLM extraction failed for source_name=%s job_url=%s; "
                "proceeding with an unextracted record (re-runnable from landing)",
                query.source_name,
                query.job_url,
                exc_info=True,
            )
            extracted = ExtractedJobFields()

        merged, field_source = apply_user_overrides(
            extracted,
            {"company": query.company, "title": query.title, "location": query.location},
        )

        payload = {
            **source_payload,
            "parsed": merged.model_dump(),
            "field_source": field_source,
        }

        yield RawJob(
            source_name=query.source_name,
            source_job_id=source_job_id,
            job_url=query.job_url,
            job_url_canonical=canonical_url,
            payload=payload,
            fetched_at=fetched_at,
            run_id=run_id,
            request_params={},
            payload_sha256=payload_sha256,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_manual_connector -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/manual_connector.py packages/core/tests/test_manual_connector.py
git commit -m "feat(job_search): add ManualConnector implementing the Connector protocol"
```

---

## Task 8: Refactor `ingest_manual_job` to route through the runner

**Files:**
- Modify: `job_search/packages/core/core/ingestion/manual.py`
- Modify: `job_search/packages/core/tests/test_manual_ingest.py`

**Interfaces:**
- Consumes: `run_connector`, `RunResult` (Task 6); `ManualConnector`, `ManualJobQuery` (Task 7).
- Produces: `ingest_manual_job(...)` and `ManualIngestResult` — **same external signature and return shape as Step 2**, so `apps/api/app/routers/ingest.py` (Step 2) needs zero changes.

- [ ] **Step 1: Read the current file before editing**

Read `job_search/packages/core/core/ingestion/manual.py` in full — you're refactoring, not rewriting from scratch, and need to see exactly what's there.

- [ ] **Step 2: Rewrite `job_search/packages/core/core/ingestion/manual.py`**

```python
"""Orchestrates the manual job-entry pipeline (PLAN.md Step 2), routed
through the shared runner and ManualConnector (PLAN.md Step 3) — one code
path into landing and bronze, same as every future connector.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from core.ingestion.extraction import ExtractedJobFields, extract_job_fields
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery
from core.ingestion.runner import RunResult, run_connector
from core.llm.types import LLMAdapter


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
    load_to_bronze_fn: Callable[..., None] | None = None,
    extract_fn: Callable[..., ExtractedJobFields] = extract_job_fields,
    run_connector_fn: Callable[..., RunResult] = run_connector,
) -> ManualIngestResult:
    """Run the manual job-entry pipeline end to end.

    Args:
        source_name: Where this posting came from, e.g. "linkedin_manual".
        job_url: The raw job URL, as pasted.
        job_spec: The full posting text, stored verbatim.
        posted_date: When the job was posted, if known.
        company: User-supplied company override.
        title: User-supplied title override.
        location: User-supplied location override.
        notes: Free-text notes travelling with the job.
        landing_uri: Root URI of the landing zone.
        database_url: The migration/owner Postgres DSN for the bronze load.
        http_client: Used for the canonical URL's redirect resolution.
        llm_adapters: Every available LLM adapter, keyed by provider.
        load_to_bronze_fn: Injectable bronze loader, threaded through to
            `run_connector_fn`. `None` (the default) lets `run_connector`
            use its own real default rather than this function importing
            `load_to_bronze` itself just to pass it along.
        extract_fn: Injectable extraction function, threaded through to
            `ManualConnector`.
        run_connector_fn: Injectable runner entrypoint — defaults to the
            real `run_connector`.

    Returns:
        The `ManualIngestResult` describing what was ingested.
    """
    query = ManualJobQuery(
        source_name=source_name,
        job_url=job_url,
        job_spec=job_spec,
        posted_date=posted_date,
        company=company,
        title=title,
        location=location,
        notes=notes,
    )
    connector = ManualConnector(
        http_client=http_client, llm_adapters=llm_adapters, extract_fn=extract_fn
    )

    kwargs: dict[str, object] = {
        "connector_key": "manual",
        "connector": connector,
        "query": query,
        "since": None,
        "entry_method": "manual",
        "landing_uri": landing_uri,
        "database_url": database_url,
    }
    if load_to_bronze_fn is not None:
        kwargs["load_to_bronze_fn"] = load_to_bronze_fn

    result = run_connector_fn(**kwargs)
    raw_job = result.raw_jobs[0]
    landing_path = result.landing_paths[0]

    extracted = ExtractedJobFields(**raw_job.payload["parsed"])
    field_source = raw_job.payload["field_source"]

    return ManualIngestResult(
        source_name=raw_job.source_name,
        job_url=raw_job.job_url,
        job_url_canonical=raw_job.job_url_canonical,
        source_job_id=raw_job.source_job_id,
        run_id=raw_job.run_id,
        landing_path=landing_path,
        payload_sha256=raw_job.payload_sha256,
        extracted=extracted,
        field_source=field_source,
    )
```

- [ ] **Step 3: Run the EXISTING Step 2 tests unchanged first**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_manual_ingest -v`
Expected: this is the key check for this task. All 5 of Step 2's original tests should PASS with **no changes to the test file** — the refactor is designed to be externally invisible. If any fail, read the failure carefully before touching the test file; the most likely cause is a subtle mismatch between what the old `ingest_manual_job` did directly and what `ManualConnector.fetch()` now does — fix `manual.py` or `manual_connector.py` to match the ORIGINAL Step 2 behavior (the tests are the source of truth for that behavior), not the other way around.

- [ ] **Step 4: If all 5 pass unchanged, add one new test proving the runner is genuinely used**

Append to `job_search/packages/core/tests/test_manual_ingest.py` (read the file first to match its existing style/imports):

```python
    def test_routes_through_run_connector_with_the_manual_connector_key(self) -> None:
        """ingest_manual_job genuinely calls run_connector_fn, not ad-hoc logic."""
        from core.ingestion.runner import run_connector as _real_run_connector

        calls: list[dict[str, object]] = []

        def _capturing_run_connector(**kwargs: object):
            calls.append(kwargs)
            return _real_run_connector(**kwargs)

        ingest_manual_job(
            source_name="linkedin_manual",
            job_url="https://example.com/job",
            job_spec="text",
            landing_uri=self.landing_uri,
            database_url="unused-in-this-test",
            http_client=self.http_client,
            llm_adapters={},
            load_to_bronze_fn=self._fake_load_to_bronze,
            extract_fn=self._fake_extract,
            run_connector_fn=_capturing_run_connector,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["connector_key"], "manual")
        self.assertEqual(calls[0]["entry_method"], "manual")
```

- [ ] **Step 5: Run the updated test file**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_manual_ingest -v`
Expected: PASS (6 tests: the original 5 unchanged, plus the new one).

- [ ] **Step 6: Verify the FastAPI layer needs no changes**

Run: `grep -n "ingest_manual_job\|ManualIngestResult" job_search/apps/api/app/routers/ingest.py`
Expected: the calls there use only `source_name, job_url, job_spec, posted_date, company, title, location, notes, landing_uri, database_url, http_client, llm_adapters` — none of the new/changed parameters (`load_to_bronze_fn` defaults to `None`, `run_connector_fn` defaults to the real one) — so this file needs zero edits. If it DOES need a change, something in this task's design diverged from Task 8's promise; stop and report NEEDS_CONTEXT rather than guessing a fix.

- [ ] **Step 7: Run the API integration test to prove the whole chain still works live**

Run: `docker compose up -d postgres` (from `job_search/`, if not already up)
Run: `cd job_search/packages/core && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search LANDING_URI=file:///path/to/job_search/data/landing python3.11 -m unittest tests.test_api_ingest -v`

(Substitute the real absolute path to `job_search/data/landing` on your machine for `LANDING_URI` — the default `/data/landing` is only writable inside a container.)

Expected: PASS (3 tests, from Step 2 — genuinely exercising the new runner-routed path against live Postgres).

- [ ] **Step 8: Commit**

```bash
cd job_search
git add packages/core/core/ingestion/manual.py packages/core/tests/test_manual_ingest.py
git commit -m "refactor(job_search): route ingest_manual_job through the shared runner"
```

---

## Task 9: CLI `ingest` subcommand

**Files:**
- Modify: `job_search/apps/pipeline/app/cli.py`
- Modify: `job_search/packages/core/tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: `run_connector` (Task 6); `ManualConnector`, `ManualJobQuery` (Task 7); `load_sources_config` (Task 4); `TokenBucket` (Task 2).
- Produces: `ingest --source SOURCE --query QUERY [--since ISO_DATETIME] [--region REGION]` on the existing `pipeline` CLI.

- [ ] **Step 1: Read the current file before editing**

Read `job_search/apps/pipeline/app/cli.py` in full (it's small — a placeholder `main()` from Step 1) and `job_search/packages/core/tests/test_pipeline_cli.py` (has the sys.modules snapshot/restore pattern from Step 1/1a — preserve it exactly, you're adding to this file, not replacing its careful handling).

- [ ] **Step 2: Write the failing test — append to `job_search/packages/core/tests/test_pipeline_cli.py`**

Keep the existing `test_runs_with_no_arguments_and_exits_zero` test and its sys.modules snapshot/restore setup exactly as-is. Add:

```python
    def test_ingest_subcommand_unknown_source_reports_an_error_and_exits_nonzero(
        self,
    ) -> None:
        """An unregistered --source name fails clearly, not with a traceback."""
        with mock.patch("sys.stdout", new_callable=StringIO), mock.patch(
            "sys.stderr", new_callable=StringIO
        ) as stderr:
            exit_code = main(["ingest", "--source", "nonexistent", "--query", "{}"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("nonexistent", stderr.getvalue())

    def test_ingest_subcommand_manual_source_requires_valid_json_query(self) -> None:
        """A malformed --query for the manual source fails clearly."""
        with mock.patch("sys.stdout", new_callable=StringIO), mock.patch(
            "sys.stderr", new_callable=StringIO
        ) as stderr:
            exit_code = main(["ingest", "--source", "manual", "--query", "not json"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("query", stderr.getvalue().lower())
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_pipeline_cli -v`
Expected: the two new tests FAIL (the original one still passes) — `main` doesn't accept an `ingest` subcommand yet, so argparse rejects it with a usage error, not the specific "unknown source" / "query" messages these tests look for.

- [ ] **Step 4: Rewrite `job_search/apps/pipeline/app/cli.py`**

```python
"""Pipeline batch entrypoint (PLAN.md Steps 1 and 3).

The `ingest` subcommand runs one connector through the shared runner —
adding a new connector means adding one entry to `_KNOWN_SOURCES` and
`_build_connector_factories()` below plus one config/sources.yml block,
never touching run_connector itself.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Callable

import httpx

from core.ingestion.connector import Connector
from core.ingestion.manual_connector import ManualConnector, ManualJobQuery
from core.ingestion.rate_limiter import TokenBucket
from core.ingestion.runner import run_connector
from core.ingestion.sources_config import load_sources_config
from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.types import LLMAdapter
from core.settings import get_settings


def _build_llm_adapters(http_client: httpx.Client) -> dict[str, LLMAdapter]:
    """Build the LLM adapter registry for CLI-driven connectors.

    Args:
        http_client: The shared HTTP client, reused for the Ollama adapter.

    Returns:
        A dict keyed by provider name, matching `apps/api/app/dependencies.
        get_llm_adapters`'s shape (duplicated rather than shared, since
        `apps/api/app` and `apps/pipeline/app` are separate top-level
        packages both named `app` — see PLAN.md Step 1's PYTHONPATH note).
    """
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


_KNOWN_SOURCES = frozenset({"manual"})
"""Every `--source` name the CLI recognises. Checked before touching
Settings or building any connector, so an unknown/malformed request fails
with a clean message even with no DSN configured — see
`_build_connector_factories` for the actual (Settings-dependent)
construction, which only runs once `args.source` is already known-good.
Adding a real API connector here (Step 4+) is the "one new file" half of
the acceptance bar — a one-line addition, not a runner change.
"""


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
        `args.source in _KNOWN_SOURCES` — it constructs Settings-dependent
        LLM adapters eagerly and shouldn't run for an unknown source.
    """
    llm_adapters = _build_llm_adapters(http_client)
    return {
        "manual": lambda: ManualConnector(
            http_client=http_client, llm_adapters=llm_adapters
        ),
    }


def _build_manual_query(raw_query: str) -> ManualJobQuery:
    """Parse `--query`'s JSON string into a ManualJobQuery.

    Args:
        raw_query: The `--query` argument's raw string value.

    Returns:
        The parsed `ManualJobQuery`.

    Raises:
        ValueError: If `raw_query` isn't valid JSON, or is missing a
            required field.
    """
    try:
        data = json.loads(raw_query)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--query must be valid JSON for source=manual: {exc}") from exc
    posted_date = data.get("posted_date")
    return ManualJobQuery(
        source_name=data["source_name"],
        job_url=data["job_url"],
        job_spec=data["job_spec"],
        posted_date=datetime.date.fromisoformat(posted_date) if posted_date else None,
        company=data.get("company"),
        title=data.get("title"),
        location=data.get("location"),
        notes=data.get("notes"),
    )


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Run the `ingest` subcommand.

    Args:
        args: Parsed CLI arguments — `source`, `query`, `since`, `region`.

    Returns:
        0 on success, 1 on a reported error.
    """
    if args.source not in _KNOWN_SOURCES:
        print(
            f"Unknown --source {args.source!r}. Known sources: "
            f"{sorted(_KNOWN_SOURCES)}",
            file=sys.stderr,
        )
        return 1

    if args.source == "manual":
        try:
            query: object = _build_manual_query(args.query)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        query = args.query

    since = datetime.datetime.fromisoformat(args.since) if args.since else None

    http_client = httpx.Client(timeout=10.0)
    try:
        factories = _build_connector_factories(http_client)
        sources_config = load_sources_config()
        source_config = sources_config.get(args.source)
        rate_limiter = None
        if source_config is not None and source_config.calls_per_hour:
            rate_limiter = TokenBucket(
                capacity=source_config.calls_per_hour, refill_period_seconds=3600.0
            )

        settings = get_settings()
        result = run_connector(
            connector_key=args.source,
            connector=factories[args.source](),
            query=query,
            since=since,
            entry_method="manual" if args.source == "manual" else "api",
            landing_uri=settings.landing_uri,
            database_url=settings.database_url,
            rate_limiter=rate_limiter,
        )
        print(
            f"ingest complete: source={args.source} records={result.run_metadata.records} "
            f"run_id={result.run_metadata.run_id}"
        )
        return 0
    finally:
        http_client.close()


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline CLI.

    Args:
        argv: Command-line arguments, excluding the program name. Defaults
            to `sys.argv[1:]` when `None`.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(prog="pipeline")
    subparsers = parser.add_subparsers(dest="command")

    ingest_parser = subparsers.add_parser(
        "ingest", help="Run one connector through the shared runner"
    )
    ingest_parser.add_argument("--source", required=True)
    ingest_parser.add_argument("--query", required=True)
    ingest_parser.add_argument("--since", default=None)
    ingest_parser.add_argument("--region", default=None)

    args = parser.parse_args(argv)

    if args.command == "ingest":
        return _cmd_ingest(args)

    print("pipeline scaffold ready — run with `ingest --source X --query Y`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_pipeline_cli -v`
Expected: PASS (3 tests: the original no-args test, plus the two new ones).

- [ ] **Step 6: Commit**

```bash
cd job_search
git add apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli.py
git commit -m "feat(job_search): add the ingest CLI subcommand"
```

---

## Task 10: Full-stack verification and acceptance sign-off

**Files:** none created — this task proves Step 3's literal acceptance criterion and that nothing regressed.

**Interfaces:** none — verification only.

- [ ] **Step 1: Bring Postgres up, migrations current (no new migration this step)**

Run: `cd job_search && docker compose up -d postgres` (if not already up)
Run: `cd job_search/db && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search alembic upgrade head`
Expected: no new migrations to apply — already at `0004` from Step 2.

- [ ] **Step 2: Prove the acceptance criterion directly — add a throwaway connector without touching the runner**

This is the literal test of "adding a connector requires one new file plus one sources.yml block — no changes to the runner." Do this as a scratch exercise (do not commit it):

Create a temporary file `job_search/packages/core/core/ingestion/_scratch_fake_connector.py`:

```python
"""Scratch file proving Step 3's acceptance criterion — DELETE after use."""

from __future__ import annotations

import datetime
from collections.abc import Iterator

from core.ingestion.raw_job import RawJob


class ScratchFakeConnector:
    """A throwaway connector, added without touching runner.py."""

    def fetch(
        self, query: object, since: datetime.datetime | None, *, run_id: str
    ) -> Iterator[RawJob]:
        """Yield one hardcoded job."""
        yield RawJob(
            source_name="scratch",
            source_job_id="1",
            job_url="https://example.com/scratch",
            job_url_canonical="https://example.com/scratch",
            payload={"raw_text": "acceptance-criterion proof"},
            fetched_at=datetime.datetime.now(datetime.UTC),
            run_id=run_id,
            request_params={},
            payload_sha256="scratch-proof",
        )
```

Run this from `job_search/packages/core` with the DSN env vars set:

```bash
python3.11 -c "
from core.ingestion._scratch_fake_connector import ScratchFakeConnector
from core.ingestion.runner import run_connector
from core.settings import get_settings

settings = get_settings()
result = run_connector(
    connector_key='scratch',
    connector=ScratchFakeConnector(),
    query='n/a',
    since=None,
    entry_method='api',
    landing_uri=settings.landing_uri,
    database_url=settings.database_url,
)
print('records:', result.run_metadata.records, 'status:', result.run_metadata.status)
"
```

(Set `LANDING_URI` to a host-writable absolute path first, same as earlier tasks.)

Expected: `records: 1 status: success` — printed with **zero edits to `runner.py`**, proving the acceptance criterion for real. Then verify the row landed:

Run: `docker compose exec postgres psql -U job_search_owner -d job_search -c "SELECT source_name, payload->>'raw_text' FROM bronze.raw_jobs WHERE source_name = 'scratch';"`
Expected: one row.

Clean up: `rm job_search/packages/core/core/ingestion/_scratch_fake_connector.py` and `docker compose exec postgres psql -U job_search_owner -d job_search -c "DELETE FROM bronze.raw_jobs WHERE source_name = 'scratch';"` — this file and row are proof artifacts, not part of the plan's deliverable; do not commit the scratch file.

- [ ] **Step 3: Run the full test suite and coverage**

Run: `cd job_search/packages/core && DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search APP_DATABASE_URL=postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search LANDING_URI=file:///path/to/job_search/data/landing coverage run -m unittest discover && DATABASE_URL=... APP_DATABASE_URL=... coverage report -m`

(Substitute the real absolute path for `LANDING_URI`; reuse the same DSN values on the `coverage report -m` line too, though that command doesn't need Postgres — kept for consistency with how this repo invokes it.)

Expected: every test passes, including the new integration tests (not skipped).

- [ ] **Step 4: Run the full project quality gate**

Run (from `job_search/`, with `pwd` verified first per this plan's Global Constraints note):
```bash
python3.11 -m black --check . && python3.11 -m isort --check-only . && python3.11 -m ruff check .
python3.11 -m mypy packages/core/core
python3.11 -m mypy apps/api/app
python3.11 -m mypy apps/pipeline/app
```
Expected: all clean. Fix and re-run if anything fails — do not skip this step, and do not trust a prior "clean" result without re-running it with an explicitly verified `pwd`.

- [ ] **Step 5: Tear down**

Run: `cd job_search && docker compose down`

- [ ] **Step 6: Open the PR**

Use the `commit-push-pr` skill on branch `feat/JOB-58-connector-runner`. Reference JOB-58 and its subtasks in the PR description. Paste the Step 2 scratch-connector proof (records/status output + the psql row) and the coverage percentage from Step 3 into the PR body.
