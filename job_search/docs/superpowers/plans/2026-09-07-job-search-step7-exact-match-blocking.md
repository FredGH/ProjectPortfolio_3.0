# Step 7 — Exact Match and Blocking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate deduplication candidate pairs across the full `silver__job_posting` table without comparing every pair (O(n²)) — cheap exact matches first (`job_url_canonical`, `content_sha256`), then a blocking key so only postings sharing a block get compared in Step 8.

**Architecture:** A new Python module computes each job's blocking-relevant fields (normalised company, matching title, resolved country/region, block key, content hash) by reusing Step 6's `core.normalisation` functions — the same "Python enrichment writes a table, dbt reads it as a source" pattern Step 5a established, because these functions are pure Python, not SQL. A new `compute-blocking-keys` pipeline CLI subcommand populates `dedup.job_blocking_keys` from `silver__job_posting`. Two new dbt models read that table: `dedup__exact_duplicates` (URL/hash equality groups) and `dedup__candidate_pairs` (a self-join on the block key, plus a soft block on company alone).

**Tech Stack:** Reuses `core.normalisation.{company,title,location}` (Step 6, already null-safe). New `dedup` Postgres schema. dbt for the pair-generation self-joins (Postgres, not Python — a straightforward SQL self-join is the natural tool here, unlike Step 10's future clustering which genuinely needs Python).

**Spec:** `PLAN.md`'s "Step 7 — Exact match and blocking" section and `plan/backlog.yml`'s `STEP-07` entry (`jira_key: JOB-117`).

## Real duplicate pair used throughout this plan's tests

Live-queried from this session's own bronze/silver data — a genuine cross-source duplicate, not invented:

- `job_key='0d5842a06b5b9e5308381d7b7ed7af69'` (Adzuna): company `"Sopra Steria"`, title `"Data Centre Engineer"`, location `"Salisbury, Wiltshire"`, posted `2026-08-15`, description starting `"Ready to get hands-on at the heart of a mission-critical infrastructure environment? We're looking for a proactive and enthusiastic Data Centre IMAC Engineer..."`, truncated to 500 chars with a trailing `…`.
- `job_key='e2bd39c45efc081ca3be8abc2a1aaad5'` (Reed): same company, same title, location `"Salisbury"` (no county), posted `2026-08-14`, the **same source description**, truncated to ~450 chars with a trailing `...`.

Two things this pair proves about the design, verified in this session:

1. **Neither cheap exact match catches it** — different `job_url_canonical`s, and the two truncation lengths/suffixes mean `content_sha256` differs too (confirmed: the two description prefixes diverge before either truncation point ends, so a byte-level hash over the first 1000 normalised chars does not match). This is realistic, not a plan flaw — exact hashing is deliberately fragile, which is exactly why blocking + Step 8's fuzzy signals exist.
2. **The block key does catch it.** `normalise_company("Sopra Steria") == normalise_company("Sopra Steria")`, `strip_title("Data Centre Engineer")` is identical on both sides (no seniority prefix to strip), and `normalise_location` resolves neither `"Salisbury, Wiltshire"` nor `"Salisbury"` to a known region (Wiltshire isn't in Step 6's small UK lookup table) — so both sides get `country_iso=None`, and **both sides land in the same block anyway**, because the block key is computed identically on unresolved input. This plan's tests assert this pair collides into one block.

## Global Constraints

- SQL style per `.claude/rules/sql-style.md`; Python style (Google docstrings, type hints, `black`/`isort`/`ruff`) per `.claude/rules/python-style.md`.
- Tests per `.claude/rules/python-testing.md`: `unittest` + `coverage`, no DB mocking in integration tests.
- `dedup.*` tables are SHARED-zone (job-pair data, no `user_id`) — same convention as `bronze.raw_jobs`/`silver.job_engagement_terms`. No RLS.
- Next migration is `0008`, `down_revision = "0007"` — confirmed via `ls db/migrations/versions/` at plan-authoring time; `0007` is the real, merged, `main`-committed migration from the Step 5a plan (not the still-unmerged `0006` from `feat/JOB-76-discovery-corpus`, which remains a separate concern outside this plan's scope).
- `docker compose up -d postgres` must be running for every task's verification.
- `pg_trgm` is NOT enabled by this plan — Step 8 enables it (this plan's blocking is pure equality/grouping, no fuzzy matching yet).

---

### Task 1: `dedup.job_blocking_keys` migration

**Files:**
- Create: `db/migrations/versions/0008_create_dedup_job_blocking_keys.py`

**Interfaces:**
- Produces: table `dedup.job_blocking_keys`, keyed on `job_key` (matches `silver.silver__job_posting.job_key`) — consumed by Task 3 (write path) and Task 4 (dbt source).

- [ ] **Step 1: Confirm the migration head**

```bash
ls db/migrations/versions/
```

Expected: `0001` through `0005`, `0007` (the real, merged migration — `0006` stays absent from `main` until `feat/JOB-76-discovery-corpus` merges, a separate unmerged branch, not this plan's concern). If a `0006` or `0008` file already exists that this plan doesn't know about, stop and re-check with the user before renumbering.

- [ ] **Step 2: Write the migration**

`db/migrations/versions/0008_create_dedup_job_blocking_keys.py`:

```python
"""create dedup.job_blocking_keys

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-07

dedup.job_blocking_keys is SHARED job-pair-generation data (PLAN.md's
two-zone rule, same pattern as silver.job_engagement_terms (0007)): the
blocking key for a posting is the same for every user, so it carries no
user_id and has no row-level security.

Written only by the migration/owner role, via the
`compute-blocking-keys` pipeline CLI subcommand (core.dedup.
write_blocking_keys) — never by a live per-user request. Read only by
dbt (dedup__candidate_pairs, dedup__exact_duplicates, PLAN.md Step 7),
which also connects as owner (dbt/profiles.yml).

normalised_company/matching_title/country_iso/region are stored
separately from block_key (not just the concatenated key) so Step 8 can
reuse them directly (company trigram similarity, location match) without
re-deriving them from a packed string.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS dedup")

    op.create_table(
        "job_blocking_keys",
        sa.Column("job_key", sa.Text(), primary_key=True),
        sa.Column("normalised_company", sa.Text(), nullable=False),
        sa.Column("matching_title", sa.Text(), nullable=False),
        sa.Column("country_iso", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("block_key", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )
    op.create_index(
        "ix_job_blocking_keys_block_key",
        "job_blocking_keys",
        ["block_key"],
        schema="dedup",
    )
    op.create_index(
        "ix_job_blocking_keys_normalised_company",
        "job_blocking_keys",
        ["normalised_company"],
        schema="dedup",
    )
    op.create_index(
        "ix_job_blocking_keys_content_sha256",
        "job_blocking_keys",
        ["content_sha256"],
        schema="dedup",
    )


def downgrade() -> None:
    op.drop_table("job_blocking_keys", schema="dedup")
    # Never drop the `dedup` schema here — a later step's dbt models (or
    # a sibling migration) may also own objects in it. See Step 5a's
    # migration 0007 for the same lesson learned the hard way.
```

- [ ] **Step 3: Run the migration**

```bash
cd job_search
docker compose up -d postgres
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
  python3.11 -m alembic -c db/alembic.ini upgrade head
```

Verify:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "\d dedup.job_blocking_keys"
```

- [ ] **Step 4: Commit**

```bash
git add db/migrations/versions/0008_create_dedup_job_blocking_keys.py
git commit -m "feat(job_search): add dedup.job_blocking_keys"
```

---

### Task 2: `compute_blocking_key` function

**Files:**
- Create: `packages/core/core/dedup/__init__.py`
- Create: `packages/core/core/dedup/blocking_keys.py`
- Test: `packages/core/tests/test_blocking_keys.py`

**Interfaces:**
- Consumes: `core.normalisation.company.normalise_company`,
  `core.normalisation.title.strip_title`,
  `core.normalisation.location.normalise_location` (Step 6 — all
  null-safe as of the Step 5a/6 final-review fix wave).
- Produces: `BlockingKeyResult` (dataclass: `normalised_company: str`,
  `matching_title: str`, `country_iso: str | None`, `region: str |
  None`, `block_key: str`, `content_sha256: str`) and
  `compute_blocking_key(company: str | None, title: str | None,
  location: str | None, description: str | None) -> BlockingKeyResult`
  — consumed by Task 3 (write path).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_blocking_keys.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.blocking_keys import compute_blocking_key


class TestComputeBlockingKey(unittest.TestCase):
    """Tests against the real cross-source duplicate pair found live in
    this project's own silver__job_posting data (Adzuna job_key
    0d5842a06b5b9e5308381d7b7ed7af69 / Reed job_key
    e2bd39c45efc081ca3be8abc2a1aaad5 — both "Sopra Steria, Data Centre
    Engineer")."""

    def test_real_cross_source_duplicate_lands_in_the_same_block(self) -> None:
        adzuna = compute_blocking_key(
            company="Sopra Steria",
            title="Data Centre Engineer",
            location="Salisbury, Wiltshire",
            description=(
                "Ready to get hands-on at the heart of a mission-critical "
                "infrastructure environment? We're looking for a proactive "
                "and enthusiastic Data Centre IMAC Engineer to join our "
                "team near Salisbury."
            ),
        )
        reed = compute_blocking_key(
            company="Sopra Steria",
            title="Data Centre Engineer",
            location="Salisbury",
            description=(
                "Ready to get hands-on at the heart of a mission-critical "
                "infrastructure environment? We're looking for a proactive "
                "and enthusiastic Data Centre IMAC Engineer to join our "
                "team near Salisbury, but the wording trails off "
                "differently here because Reed truncates shorter."
            ),
        )
        self.assertEqual(adzuna.block_key, reed.block_key)
        self.assertEqual(adzuna.normalised_company, "Sopra Steria")
        self.assertEqual(adzuna.matching_title, "Data Centre Engineer")
        # Neither "Salisbury, Wiltshire" nor "Salisbury" resolves in Step
        # 6's small UK lookup table — both fall back to unresolved, which
        # is WHY they still collide into the same block.
        self.assertIsNone(adzuna.country_iso)
        self.assertIsNone(reed.country_iso)

    def test_real_duplicate_pair_does_not_share_content_sha256(self) -> None:
        """The two real descriptions diverge before their respective
        truncation points, so the exact hash correctly does NOT catch
        this pair — that's Step 8's job, not Step 7's cheap check."""
        adzuna = compute_blocking_key(
            "Sopra Steria",
            "Data Centre Engineer",
            "Salisbury, Wiltshire",
            "Ready to get hands-on... near Salisbury. This is a fantastic "
            "opportunity for someone who enjoys variety…",
        )
        reed = compute_blocking_key(
            "Sopra Steria",
            "Data Centre Engineer",
            "Salisbury",
            "Ready to get hands-on... near Salisbury. This is a fantastic "
            "opportunity for someone who enjoys variety, technical "
            "challenges, and working in a highly secure...",
        )
        self.assertNotEqual(adzuna.content_sha256, reed.content_sha256)

    def test_identical_inputs_produce_identical_hash(self) -> None:
        result_a = compute_blocking_key(
            "Acme Ltd", "Data Engineer", "London", "Some description text."
        )
        result_b = compute_blocking_key(
            "Acme Ltd", "Data Engineer", "London", "Some description text."
        )
        self.assertEqual(result_a.content_sha256, result_b.content_sha256)

    def test_different_company_produces_different_block(self) -> None:
        acme = compute_blocking_key("Acme Ltd", "Data Engineer", "London", "x")
        other = compute_blocking_key("Other Co", "Data Engineer", "London", "x")
        self.assertNotEqual(acme.block_key, other.block_key)

    def test_seniority_stripped_before_blocking(self) -> None:
        """'Senior Data Engineer' and 'Data Engineer' at the same company
        must block together — this is the entire reason strip_title
        exists (DECISIONS.md §5)."""
        senior = compute_blocking_key(
            "Acme Ltd", "Senior Data Engineer", "London", "x"
        )
        plain = compute_blocking_key("Acme Ltd", "Data Engineer", "London", "x")
        self.assertEqual(senior.block_key, plain.block_key)

    def test_resolved_country_included_in_block_key(self) -> None:
        """Two identical company/title pairs in different resolved
        countries must NOT block together."""
        uk = compute_blocking_key(
            "Acme Ltd", "Data Engineer", "Central London, London", "x"
        )
        de = compute_blocking_key("Acme Ltd", "Data Engineer", "Berlin, DE", "x")
        self.assertNotEqual(uk.block_key, de.block_key)

    def test_none_fields_do_not_raise(self) -> None:
        """Manual entries can have every one of these genuinely null."""
        result = compute_blocking_key(None, None, None, None)
        self.assertIsInstance(result.block_key, str)
        self.assertIsInstance(result.content_sha256, str)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_blocking_keys -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup'`.

- [ ] **Step 3: Implement `compute_blocking_key`**

`packages/core/core/dedup/__init__.py`: empty file.

`packages/core/core/dedup/blocking_keys.py`:

```python
"""Blocking-key and content-hash computation for dedup candidate
generation (PLAN.md Step 7).

Reuses Step 6's core.normalisation functions rather than re-deriving
company/title/location normalisation — the entire point of Step 6
existing first. Every field here is computed once per job and stored
(core.dedup.write_blocking_keys), then read by dbt's self-join models —
the same "Python computes, dbt joins" split Step 5a established, since
these normalisation functions are pure Python, not SQL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from core.normalisation.company import normalise_company
from core.normalisation.location import normalise_location
from core.normalisation.title import strip_title

_WHITESPACE_RE = re.compile(r"\s+")

_BLOCK_KEY_TITLE_PREFIX_LEN = 12
"""PLAN.md Step 7: 'normalise_company || left(strip_title, 12) || country'."""

_CONTENT_HASH_DESCRIPTION_LEN = 1000
"""PLAN.md Step 7: 'left(normalise(description), 1000)'."""


@dataclass(frozen=True)
class BlockingKeyResult:
    """One job's blocking-relevant fields (PLAN.md Step 7).

    Attributes:
        normalised_company: Step 6's normalise_company output (empty
            string if company was None).
        matching_title: Step 6's strip_title output, full length (not
            truncated) — Step 8 reuses this for title similarity, so the
            truncation to 12 chars happens only when building block_key,
            not here.
        country_iso: Step 6's normalise_location country, or None when
            unresolved.
        region: Step 6's normalise_location region, or None.
        block_key: normalised_company + left(matching_title, 12) +
            country_iso, concatenated with '|' separators so that e.g.
            company "AB" + title "C..." can never collide with company
            "A" + title "BC..." the way bare concatenation could.
        content_sha256: SHA-256 hex digest over normalised
            company|title|location|description(left 1000 chars) — an
            exact-duplicate check, deliberately fragile to any real
            difference (including different truncation points between
            sources), unlike the fuzzy signals Step 8 adds.
    """

    normalised_company: str
    matching_title: str
    country_iso: str | None
    region: str | None
    block_key: str
    content_sha256: str


def _normalise_text(raw: str | None) -> str:
    """Lowercase and collapse whitespace — the minimal text
    normalisation content_sha256 needs. Not one of Step 6's named
    functions (Step 6 never built a normalise_description — there's no
    dedup-matching structure to a free-text description beyond casing/
    whitespace), so this stays local to Step 7.

    Args:
        raw: Any free text, or None.

    Returns:
        The lowercased, whitespace-collapsed text, or "" if raw is None.
    """
    if raw is None:
        return ""
    return _WHITESPACE_RE.sub(" ", raw.strip().lower())


def compute_blocking_key(
    company: str | None,
    title: str | None,
    location: str | None,
    description: str | None,
) -> BlockingKeyResult:
    """Compute one job's blocking key and content hash.

    Args:
        company: The posting's company name, or None.
        title: The posting's title, or None.
        location: The posting's location string, or None.
        description: The posting's description text, or None.

    Returns:
        The `BlockingKeyResult`.
    """
    normalised_company = normalise_company(company) or ""
    matching_title = strip_title(title) or ""
    normalised_location = normalise_location(location)
    country_iso = normalised_location.country_iso
    region = normalised_location.region

    block_key = "|".join(
        [
            normalised_company,
            matching_title[:_BLOCK_KEY_TITLE_PREFIX_LEN],
            country_iso or "",
        ]
    )

    content_parts = "|".join(
        [
            _normalise_text(normalised_company),
            _normalise_text(matching_title),
            _normalise_text(f"{country_iso}:{region}"),
            _normalise_text(description)[:_CONTENT_HASH_DESCRIPTION_LEN],
        ]
    )
    content_sha256 = hashlib.sha256(content_parts.encode("utf-8")).hexdigest()

    return BlockingKeyResult(
        normalised_company=normalised_company,
        matching_title=matching_title,
        country_iso=country_iso,
        region=region,
        block_key=block_key,
        content_sha256=content_sha256,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_blocking_keys -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Quality gate**

```bash
cd job_search
python3.11 -m black packages/core/core/dedup packages/core/tests/test_blocking_keys.py
python3.11 -m isort packages/core/core/dedup packages/core/tests/test_blocking_keys.py
python3.11 -m ruff check packages/core/core/dedup packages/core/tests/test_blocking_keys.py
python3.11 -m mypy packages/core/core/dedup
```

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/dedup/__init__.py \
  packages/core/core/dedup/blocking_keys.py \
  packages/core/tests/test_blocking_keys.py
git commit -m "feat(job_search): add compute_blocking_key"
```

---

### Task 3: `compute-blocking-keys` pipeline CLI subcommand

**Files:**
- Create: `packages/core/core/dedup/write_blocking_keys.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/integration/test_write_blocking_keys.py`

**Interfaces:**
- Consumes: `compute_blocking_key` (Task 2), `core.db.session.build_engine`.
- Produces: `write_blocking_keys(engine: Engine) -> int` — invoked by the
  new `compute-blocking-keys` CLI subcommand.

- [ ] **Step 1: Write the failing integration test**

`packages/core/tests/integration/test_write_blocking_keys.py`:

```python
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_blocking_keys import write_blocking_keys

_OWNER_DSN = (
    "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
)


class TestWriteBlockingKeys(unittest.TestCase):
    """Integration test against a real Postgres instance."""

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.job_key = f"test-{uuid.uuid4().hex}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO intermediate.int_jobs__unioned "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at, "
                    "fetched_at, run_id, payload_sha256) VALUES "
                    "(:job_key, 'test_source', :job_key, 'https://x', "
                    "'https://x', 'api', 'Senior Data Engineer', "
                    "'Acme Ltd', 'London', 'A test description.', "
                    "NULL, now(), now(), 'run-1', 'sha-1')"
                ),
                {"job_key": self.job_key},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.job_blocking_keys "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            )
            conn.execute(
                text(
                    "DELETE FROM intermediate.int_jobs__unioned "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            )
        self.engine.dispose()

    def test_writes_one_row_per_posting(self) -> None:
        written = write_blocking_keys(self.engine)
        self.assertGreaterEqual(written, 1)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT normalised_company, matching_title, block_key "
                    "FROM dedup.job_blocking_keys WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).one()
        self.assertEqual(row.normalised_company, "Acme Ltd")
        self.assertEqual(row.matching_title, "Data Engineer")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        write_blocking_keys(self.engine)
        write_blocking_keys(self.engine)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM dedup.job_blocking_keys "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).scalar_one()
        self.assertEqual(count, 1)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd job_search
docker compose up -d postgres
cd packages/core
python3.11 -m unittest tests.integration.test_write_blocking_keys -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.write_blocking_keys'`.

- [ ] **Step 3: Implement `write_blocking_keys`**

`packages/core/core/dedup/write_blocking_keys.py`:

```python
"""Batch write path for dedup.job_blocking_keys (PLAN.md Step 7).

Runs outside dbt for the same reason core.enrichment.
write_engagement_terms does (PLAN.md Step 5a) — the normalisation logic
is pure Python, so it has to execute once per row in application code.
dbt's dedup__candidate_pairs and dedup__exact_duplicates models
(dbt/models/dedup/) read this table's output via a source(), never
recomputing it.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.dedup.blocking_keys import compute_blocking_key

_SELECT_POSTINGS = text(
    "SELECT job_key, company, title, location, description "
    "FROM silver.silver__job_posting"
)

_UPSERT = text(
    """
    INSERT INTO dedup.job_blocking_keys (
        job_key, normalised_company, matching_title, country_iso,
        region, block_key, content_sha256
    ) VALUES (
        :job_key, :normalised_company, :matching_title, :country_iso,
        :region, :block_key, :content_sha256
    )
    ON CONFLICT (job_key) DO UPDATE SET
        normalised_company = EXCLUDED.normalised_company,
        matching_title = EXCLUDED.matching_title,
        country_iso = EXCLUDED.country_iso,
        region = EXCLUDED.region,
        block_key = EXCLUDED.block_key,
        content_sha256 = EXCLUDED.content_sha256,
        computed_at = now()
    """
)


def write_blocking_keys(engine: Engine) -> int:
    """Compute and upsert blocking keys for every posting.

    Args:
        engine: The migration/owner engine — this table is SHARED-zone
            (no user_id, no RLS), same as silver.job_engagement_terms.

    Returns:
        The number of rows written (inserted or updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_POSTINGS).all()
        for row in rows:
            result = compute_blocking_key(
                row.company, row.title, row.location, row.description
            )
            conn.execute(
                _UPSERT,
                {
                    "job_key": row.job_key,
                    "normalised_company": result.normalised_company,
                    "matching_title": result.matching_title,
                    "country_iso": result.country_iso,
                    "region": result.region,
                    "block_key": result.block_key,
                    "content_sha256": result.content_sha256,
                },
            )
    return len(rows)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_write_blocking_keys -v
```

Expected: both tests PASS.

- [ ] **Step 5: Wire the CLI subcommand**

In `apps/pipeline/app/cli.py`, add the import:

```python
from core.dedup.write_blocking_keys import write_blocking_keys
```

(place alongside the existing `from core.enrichment.write_engagement_terms import write_engagement_terms` import — read the current file first to match its exact existing import ordering).

Add a new command function, mirroring `_cmd_enrich_engagement_terms` exactly:

```python
def _cmd_compute_blocking_keys(args: argparse.Namespace) -> int:
    """Run the `compute-blocking-keys` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_blocking_keys(engine)
    print(f"compute-blocking-keys complete: rows_written={written}")
    return 0
```

In `main()`, add the subparser and dispatch branch, mirroring
`enrich-engagement-terms`'s exact pattern:

```python
    subparsers.add_parser(
        "compute-blocking-keys",
        help="Compute and write blocking keys for every silver posting",
    )
```

```python
    if args.command == "compute-blocking-keys":
        return _cmd_compute_blocking_keys(args)
```

- [ ] **Step 6: Run it live**

```bash
cd job_search
python3.11 -m apps.pipeline.app.cli compute-blocking-keys
```

Expected: `compute-blocking-keys complete: rows_written=<N>` matching
`silver.silver__job_posting`'s row count (3751 in this environment as of
this plan's authoring — dbt build order matters: `silver__job_posting`
must already be built, which it is, from the Step 5a plan).

Confirm the real duplicate pair actually lands in the same block:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT job_key, normalised_company, matching_title, country_iso, block_key
FROM dedup.job_blocking_keys
WHERE job_key IN ('0d5842a06b5b9e5308381d7b7ed7af69', 'e2bd39c45efc081ca3be8abc2a1aaad5');
"
```

Expected: both rows show the same `block_key`.

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/dedup/write_blocking_keys.py \
  packages/core/tests/integration/test_write_blocking_keys.py \
  apps/pipeline/app/cli.py
git commit -m "feat(job_search): add compute-blocking-keys pipeline subcommand"
```

---

### Task 4: `dedup__exact_duplicates` dbt model

**Files:**
- Create: `dbt/models/dedup/_dedup.yml`
- Create: `dbt/models/dedup/dedup__exact_duplicates.sql`
- Modify: `dbt/dbt_project.yml`

**Interfaces:**
- Consumes: `source('dedup_ingest', 'job_blocking_keys')` (Task 3's
  write target), `ref('silver__job_posting')`.
- Produces: `ref('dedup__exact_duplicates')` — one row per job that is
  part of an exact-duplicate group (shares `job_url_canonical` or
  `content_sha256` with at least one other job), with a `duplicate_type`
  and `duplicate_group_key` so Step 10's future identity-map step can
  read groups directly instead of re-deriving them.

- [ ] **Step 1: Add the `dedup` layer to `dbt_project.yml`**

```yaml
models:
  job_search:
    staging:
      +materialized: view
      +schema: staging
    intermediate:
      +materialized: table
      +schema: intermediate
    silver:
      +materialized: table
      +schema: silver
    dedup:
      +materialized: table
      +schema: dedup
```

- [ ] **Step 2: Write `_dedup.yml`**

`dbt/models/dedup/_dedup.yml`:

```yaml
version: 2

sources:
  - name: dedup_ingest
    schema: dedup
    tables:
      - name: job_blocking_keys
        description: >
          Written by the compute-blocking-keys pipeline CLI subcommand
          (core.dedup.write_blocking_keys), not dbt. One row per
          silver__job_posting job, carrying its normalised company,
          matching (seniority-stripped) title, resolved country/region,
          concatenated block key, and exact-duplicate content hash.
        columns:
          - name: job_key
            description: "Matches silver.silver__job_posting.job_key."

models:
  - name: dedup__exact_duplicates
    description: >
      One row per job that shares job_url_canonical OR content_sha256
      with at least one other job — PLAN.md Step 7's "cheap matches
      first" check. duplicate_group_key identifies which group a row
      belongs to; two jobs with the same duplicate_group_key AND the
      same duplicate_type are the same exact-match group.
    columns:
      - name: job_key
        data_type: text
        tests:
          - not_null
      - name: duplicate_type
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: ['url_canonical', 'content_hash']
      - name: duplicate_group_key
        description: "The shared job_url_canonical or content_sha256 value."
        data_type: text
        tests:
          - not_null
      - name: group_size
        description: "How many jobs share this duplicate_group_key."
        data_type: bigint
        tests:
          - not_null
```

- [ ] **Step 3: Write `dedup__exact_duplicates.sql`**

`dbt/models/dedup/dedup__exact_duplicates.sql`:

```sql
-- dedup__exact_duplicates: one row per job that shares job_url_canonical
-- or content_sha256 with at least one other job (PLAN.md Step 7's cheap
-- exact-match check). Grain: (job_key, duplicate_type).

WITH url_groups AS (

    SELECT
        job_key,
        'url_canonical' AS duplicate_type,
        job_url_canonical AS duplicate_group_key,
        COUNT(*) OVER (PARTITION BY job_url_canonical) AS group_size
    FROM {{ ref('silver__job_posting') }}

),

-- Content-hash groups, joined in from the Python-computed blocking-keys
-- table (dbt never recomputes the hash, only groups by it).
hash_groups AS (

    SELECT
        job_key,
        'content_hash' AS duplicate_type,
        content_sha256 AS duplicate_group_key,
        COUNT(*) OVER (PARTITION BY content_sha256) AS group_size
    FROM {{ source('dedup_ingest', 'job_blocking_keys') }}

)

SELECT * FROM url_groups WHERE group_size > 1
UNION ALL
SELECT * FROM hash_groups WHERE group_size > 1
```

- [ ] **Step 4: Run and test live**

```bash
cd job_search/dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt run --select dedup__exact_duplicates
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt test --select dedup__exact_duplicates
```

Expected: model builds, tests PASS. Inspect real groups:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT duplicate_type, duplicate_group_key, group_size
FROM dedup.dedup__exact_duplicates
ORDER BY group_size DESC LIMIT 10;
"
```

The real Sopra Steria/Data Centre Engineer pair should NOT appear here
(different URLs, different content hashes, per this plan's own opening
note) — that's expected, not a bug.

- [ ] **Step 5: Commit**

```bash
git add dbt/dbt_project.yml dbt/models/dedup/_dedup.yml dbt/models/dedup/dedup__exact_duplicates.sql
git commit -m "feat(job_search): add dedup__exact_duplicates"
```

---

### Task 5: `dedup__candidate_pairs` dbt model, with block-size monitoring

**Files:**
- Modify: `dbt/models/dedup/_dedup.yml`
- Create: `dbt/models/dedup/dedup__candidate_pairs.sql`
- Create: `dbt/tests/assert_no_block_exceeds_five_hundred.sql`

**Interfaces:**
- Consumes: `source('dedup_ingest', 'job_blocking_keys')` (Task 3).
- Produces: `ref('dedup__candidate_pairs')` — one row per candidate pair
  `(job_key_a, job_key_b)` with `job_key_a < job_key_b` (so each pair
  appears once, not twice), and a `match_type` of `'block'` or
  `'soft_block'` — consumed by Step 8's similarity-scoring model (not
  built here).

- [ ] **Step 1: Write `dedup__candidate_pairs.sql`**

`dbt/models/dedup/dedup__candidate_pairs.sql`:

```sql
-- dedup__candidate_pairs: one row per candidate pair sharing a block
-- key (hard block) or company alone (soft block) — PLAN.md Step 7's
-- pair-generation step, feeding Step 8's similarity scoring. Grain:
-- (job_key_a, job_key_b, match_type), job_key_a < job_key_b so each
-- unordered pair appears once per match_type it qualifies under.

WITH keys AS (

    SELECT * FROM {{ source('dedup_ingest', 'job_blocking_keys') }}

),

-- Hard block: same company, same truncated title, same resolved
-- country. The primary blocking mechanism.
block_pairs AS (

    SELECT
        a.job_key AS job_key_a,
        b.job_key AS job_key_b,
        'block' AS match_type
    FROM keys AS a
    INNER JOIN keys AS b
        ON a.block_key = b.block_key
        AND a.job_key < b.job_key

),

-- Soft block: same company only, titles diverging too badly to share a
-- 12-character prefix (PLAN.md's "Senior Data Engineer" vs "Data
-- Platform Engineer" example). Excludes pairs already caught by the
-- hard block, so a pair never appears under both match_types.
soft_block_pairs AS (

    SELECT
        a.job_key AS job_key_a,
        b.job_key AS job_key_b,
        'soft_block' AS match_type
    FROM keys AS a
    INNER JOIN keys AS b
        ON a.normalised_company = b.normalised_company
        AND a.job_key < b.job_key
    WHERE a.normalised_company != ''

)

SELECT * FROM block_pairs
UNION ALL
SELECT sb.*
FROM soft_block_pairs AS sb
LEFT JOIN block_pairs AS bp
    ON sb.job_key_a = bp.job_key_a AND sb.job_key_b = bp.job_key_b
WHERE bp.job_key_a IS NULL
```

- [ ] **Step 2: Extend `_dedup.yml`**

Append to `dbt/models/dedup/_dedup.yml`'s `models:` list:

```yaml
  - name: dedup__candidate_pairs
    description: >
      One row per candidate pair sharing a blocking key or company
      alone (soft block). Feeds Step 8's similarity scoring — nothing
      downstream should regenerate candidate pairs independently.
    columns:
      - name: job_key_a
        data_type: text
        tests:
          - not_null
      - name: job_key_b
        data_type: text
        tests:
          - not_null
      - name: match_type
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: ['block', 'soft_block']
```

- [ ] **Step 3: Write the block-size monitoring test**

`dbt/tests/assert_no_block_exceeds_five_hundred.sql`:

```sql
-- PLAN.md Step 7: "measure block size distribution... if any block
-- exceeds a few hundred records, your key is too coarse." Runs at warn
-- severity (see the config below) — this is a monitoring signal, not a
-- hard build-blocking assertion, since a single large employer having a
-- genuinely big block isn't wrong, just worth knowing about.

SELECT
    block_key,
    COUNT(*) AS block_size
FROM {{ source('dedup_ingest', 'job_blocking_keys') }}
WHERE block_key != ''
GROUP BY block_key
HAVING COUNT(*) > 500
```

Add the `warn` severity via a `config()` call at the top of the same
file (dbt singular tests accept an inline config block):

```sql
{{ config(severity='warn') }}

-- PLAN.md Step 7: ...
```

(Combine both — the `config()` call goes first, then the comment, then
the `SELECT`.)

- [ ] **Step 4: Run and test live**

```bash
cd job_search/dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt run --select dedup__candidate_pairs
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt test --select dedup__candidate_pairs
```

Expected: model builds, all tests PASS (the block-size test may WARN if
a real block exceeds 500 — inspect it if so, don't treat a WARN as a
failure to fix blindly):

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT block_key, COUNT(*) FROM dedup.job_blocking_keys
WHERE block_key != '' GROUP BY block_key ORDER BY 2 DESC LIMIT 10;
"
```

Confirm the real duplicate pair is present:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT * FROM dedup.dedup__candidate_pairs
WHERE (job_key_a = '0d5842a06b5b9e5308381d7b7ed7af69' AND job_key_b = 'e2bd39c45efc081ca3be8abc2a1aaad5')
   OR (job_key_a = 'e2bd39c45efc081ca3be8abc2a1aaad5' AND job_key_b = '0d5842a06b5b9e5308381d7b7ed7af69');
"
```

Expected: exactly one row (since `job_key_a < job_key_b` string-orders
one specific way), `match_type = 'block'`.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/dedup/dedup__candidate_pairs.sql dbt/models/dedup/_dedup.yml dbt/tests/assert_no_block_exceeds_five_hundred.sql
git commit -m "feat(job_search): add dedup__candidate_pairs"
```

---

### Task 6: Full verification (controller-run, not dispatched)

- [ ] **Step 1: Full `dbt build`**

```bash
cd job_search
docker compose up -d postgres
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt build
```

Expected: every model builds, every test passes (WARN on the block-size
test is acceptable, ERROR is not).

- [ ] **Step 2: Confirm the "Done when" criterion directly**

```bash
time (POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt run --select dedup__candidate_pairs)
```

Expected: single-digit seconds at this data volume (~3,751 postings) —
confirms "candidate pair generation... completes in seconds."

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT MAX(block_size) FROM (
  SELECT COUNT(*) AS block_size FROM dedup.job_blocking_keys
  WHERE block_key != '' GROUP BY block_key
) t;
"
```

Expected: consistent with the block-size test's threshold (≤ 500, or a
noted, understood exception if not).

- [ ] **Step 3: Python quality gate and full test suite**

```bash
cd job_search
python3.11 -m black --check .
python3.11 -m isort --check-only .
python3.11 -m ruff check .
python3.11 -m mypy packages/core/core apps/pipeline/app
```

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_verify" \
python3.11 -m coverage run -m unittest discover
python3.11 -m coverage report -m
```

Expected: no new failures beyond the documented, pre-existing
`collection_channel` errors (unrelated to this plan — see the Step 5a
plan's own notes on this if the executor hasn't seen it before: the
shared dev Postgres has an unmerged sibling branch's migration applied
without its code, causing 6 unrelated `dlt`/bronze test failures that
predate this plan).

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage:** `STEP-07`'s 6 subtasks map cleanly: exact match on
  `job_url_canonical` + content hash → Task 4; block key definition →
  Tasks 2-3; soft block on company → Task 5; Postgres index on the block
  key → Task 1's migration; block-size measurement → Task 5's singular
  test + Task 6's direct query.
- **Placeholder scan:** none found — every function, migration, and dbt
  model is complete; the real duplicate pair's exact field values were
  queried live in this session, not invented.
- **Genuine, flagged uncertainty:** `dedup__candidate_pairs`'s soft-block
  exclusion of already-hard-blocked pairs assumes `LEFT JOIN ... WHERE
  ... IS NULL` is the right anti-join idiom for this project's SQL style
  (no existing model in this codebase does an anti-join yet, so there's
  no established precedent to match — flagged as a fresh pattern, not a
  copied one).
- **Type consistency:** `BlockingKeyResult`'s field names match the
  migration's column names (Task 1) and every dbt model's column
  references exactly, checked side by side before finalising this plan.
