# Step 8 — Similarity Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score every Step 7 candidate pair on 6 of PLAN.md's 7 similarity
signals, persisting each component separately (Step 9 recalibrates the
blend), plus a documented initial blend and the ±45-day hard veto.

**Architecture:** Two new per-job Python-computed tables (mirroring Step
5a/7's "Python computes, dbt joins" pattern): `dedup.
job_similarity_features` (a 64-bit description SimHash + Step 6's
`parse_salary` output, one row per posting) and `dedup.
pair_title_scores` (title token-set-ratio, the one signal that's
inherently pairwise and can't be precomputed per job — one row per
candidate pair). A final dbt model, `dedup__similarity_scores`, joins
`dedup__candidate_pairs` against both new tables plus `pg_trgm` (native
SQL, no Python needed) to compute company similarity, and native bit
operations for the SimHash Hamming distance — persisting all 6
components plus the blend.

**Tech Stack:** `rapidfuzz` (already installed in this environment,
`3.14.5` — confirmed via `pip index versions rapidfuzz`) for
`token_set_ratio`. `pg_trgm` (Postgres extension, enabled by this plan)
for company similarity. Native Postgres `bit_count()` for Hamming
distance — no new Python dependency needed for that.

**Spec:** `PLAN.md`'s "Step 8 — Similarity scoring" section and
`plan/backlog.yml`'s `STEP-08` entry (`jira_key: JOB-124`).

## Scope note — the embedding signal is deliberately not built here

PLAN.md's signal table lists 7 signals; this plan builds 6. The 7th,
"description embedding, pgvector cosine ≥ 0.95," is deliberately
deferred — decided with the user before writing this plan. Building it
now would force `DECISIONS.md` §4's embedding-dimension/index-type
choice early (that decision is explicitly reserved for Step 15, "baked
in at first embedding; changing it means re-embedding everything"), and
no embedding infrastructure exists yet in this codebase (no Ollama
container running, no `core.embedding` gateway module). The blend's
weights below are renormalised across the remaining 6 signals rather
than leaving a gap for a "medium" weight that was never scored.

**Follow-up, not built here:** once Step 15 settles the embedding
dimension/index, backfill the 7th signal into `dedup__similarity_scores`
and re-blend. Flag this to the user as a candidate backlog entry rather
than filing it unilaterally (Jira writes require confirmation).

## Real duplicate pair used throughout this plan's tests

Same pair as the Step 7 plan, already confirmed to land in the same
candidate-pair block: Adzuna `job_key='0d5842a06b5b9e5308381d7b7ed7af69'`
and Reed `job_key='e2bd39c45efc081ca3be8abc2a1aaad5'`, both "Sopra
Steria, Data Centre Engineer," posted one day apart, overlapping salary
bands, and the same source description text truncated to two different
lengths by the two aggregators. This pair should score **high** on every
signal in this plan once Task 5 runs — used as this plan's live
end-to-end verification, not as a hand-computed unit-test fixture (see
the SimHash scope note in Task 2 for why).

## Scope note — SimHash test assertions are structural, not hand-verified magic numbers

Every regex-based function in the Step 5a/6 plans could be hand-traced
to an exact expected value before writing its test. A SimHash fingerprint
cannot be hand-traced the same way — its exact bit pattern depends on
the hash function applied to each token, which no human traces by hand.
This plan's SimHash tests therefore assert **structural properties**
(determinism, symmetry of Hamming distance, that near-duplicate text
produces a smaller distance than unrelated text, that output is always a
valid 64-bit value) rather than fixed expected integers. This is a
deliberate, disclosed departure from the "hand-verify against real
data" rigor applied elsewhere in this project — flagged rather than
smoothed over.

## Global Constraints

- SQL style per `.claude/rules/sql-style.md`; Python style per
  `.claude/rules/python-style.md`; tests per
  `.claude/rules/python-testing.md` (`unittest`, no DB mocking).
- Next migration is `0009`, `down_revision = "0008"` (Step 7's
  migration) — confirm via `ls db/migrations/versions/` at execution
  time.
- **Postgres `bigint` is signed 64-bit** (`-2^63` to `2^63-1`), but a
  SimHash fingerprint is a naturally *unsigned* 64-bit value (`0` to
  `2^64-1`). Storing a fingerprint ≥ `2^63` directly into a `bigint`
  column overflows. The write path (Task 3) must convert to Postgres's
  signed two's-complement representation before every INSERT — this is
  not optional and is verified live in this session:
  `((-1)::bigint # (-1)::bigint)::bit(64)` correctly reconstructs all 64
  bits regardless of the sign interpretation, so storing the signed
  form loses no information for the Hamming-distance computation.
- `dedup.*` tables are SHARED-zone — no `user_id`, no RLS.
- `docker compose up -d postgres` must be running for every task's
  verification.

---

### Task 1: Migration — `pg_trgm`, `dedup.job_similarity_features`, `dedup.pair_title_scores`

**Files:**
- Create: `db/migrations/versions/0009_create_dedup_similarity_tables.py`

**Interfaces:**
- Produces: the `pg_trgm` extension (used directly in Task 5's SQL, no
  Python wrapper needed); tables `dedup.job_similarity_features` (keyed
  on `job_key`) and `dedup.pair_title_scores` (keyed on
  `(job_key_a, job_key_b)`) — consumed by Tasks 3-4 (write paths) and
  Task 5 (dbt source).

- [ ] **Step 1: Confirm the migration head**

```bash
ls db/migrations/versions/
```

Expected: `0008_create_dedup_job_blocking_keys.py` is the latest (from
the Step 7 plan). If not, stop and re-check before renumbering.

- [ ] **Step 2: Write the migration**

`db/migrations/versions/0009_create_dedup_similarity_tables.py`:

```python
"""enable pg_trgm; create dedup.job_similarity_features and
dedup.pair_title_scores

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-07

Both tables are SHARED job-pair-generation data (PLAN.md's two-zone
rule), same pattern as dedup.job_blocking_keys (0008): written by the
migration/owner role via pipeline CLI subcommands, read by dbt as
plain sources, no user_id, no RLS.

job_similarity_features is per-job (one row per silver__job_posting
row): a description SimHash fingerprint and Step 6's parse_salary
output, both expensive/impossible to compute in SQL. pair_title_scores
is per-CANDIDATE-PAIR (one row per dedup__candidate_pairs row): title
token-set-ratio, the one Step 8 signal that is inherently pairwise
(rapidfuzz has no SQL equivalent) rather than precomputable per job.

description_simhash is declared BIGINT (signed 64-bit) even though a
SimHash fingerprint is naturally unsigned 64-bit — the write path
(core.dedup.write_similarity_features) converts to Postgres's signed
two's-complement representation before every INSERT. This loses no
information for the Hamming-distance bit operations Step 8's dbt model
runs on it (verified: XOR + bit_count is representation-invariant).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "job_similarity_features",
        sa.Column("job_key", sa.Text(), primary_key=True),
        sa.Column("description_simhash", sa.BigInteger(), nullable=False),
        sa.Column("rate_annualised", sa.Numeric(), nullable=True),
        sa.Column("rate_currency", sa.Text(), nullable=True),
        sa.Column("salary_band", sa.Text(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )

    op.create_table(
        "pair_title_scores",
        sa.Column("job_key_a", sa.Text(), primary_key=True),
        sa.Column("job_key_b", sa.Text(), primary_key=True),
        sa.Column("title_token_set_ratio", sa.Numeric(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )


def downgrade() -> None:
    op.drop_table("pair_title_scores", schema="dedup")
    op.drop_table("job_similarity_features", schema="dedup")
    # pg_trgm is left enabled — dropping a shared extension on downgrade
    # risks breaking other objects that may come to depend on it; see
    # migration 0007's lesson about not tearing down shared resources.
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
  -c "\d dedup.job_similarity_features" \
  -c "\d dedup.pair_title_scores" \
  -c "\dx pg_trgm"
```

- [ ] **Step 4: Commit**

```bash
git add db/migrations/versions/0009_create_dedup_similarity_tables.py
git commit -m "feat(job_search): enable pg_trgm; add dedup similarity tables"
```

---

### Task 2: `compute_simhash`

**Files:**
- Create: `packages/core/core/dedup/simhash.py`
- Test: `packages/core/tests/test_simhash.py`

**Interfaces:**
- Produces: `compute_simhash(text: str, hash_bits: int = 64) -> int` —
  returns an unsigned integer in `[0, 2**hash_bits)` — consumed by
  Task 3.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_simhash.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.simhash import compute_simhash, hamming_distance


def _real_adzuna_description() -> str:
    """Real bronze text (Adzuna truncation), queried live this session."""
    return (
        "Ready to get hands-on at the heart of a mission-critical "
        "infrastructure environment? We're looking for a proactive and "
        "enthusiastic Data Centre IMAC Engineer to join our team near "
        "Salisbury. This is a fantastic opportunity for someone who "
        "enjoys variety, technical challenges, and working in a highly "
        "secure enterprise hosting environment where no two days are "
        "the same. Working alongside experienced Data Centre "
        "professionals, you'll play a key role in keeping critical "
        "infrastructure operational,"
    )


def _real_reed_description() -> str:
    """Real bronze text (Reed truncation of the SAME source posting),
    queried live this session — shorter cutoff, different trailing
    ellipsis, otherwise the same source text."""
    return (
        "Ready to get hands-on at the heart of a mission-critical "
        "infrastructure environment? We're looking for a proactive and "
        "enthusiastic Data Centre IMAC Engineer to join our team near "
        "Salisbury. This is a fantastic opportunity for someone who "
        "enjoys variety, technical challenges, and working in a highly "
        "secure enterprise hosting environment where no two days are "
        "the same. Working alongside experienced Data Centre "
        "professionals, you'll play a key rol"
    )


def _unrelated_description() -> str:
    """A real but completely unrelated bronze description, queried live
    this session (the Microsoft Fabric contract posting from the Step
    5a plan)."""
    return (
        "Senior Data Engineer – Microsoft Fabric Contract: Outside IR35 "
        "Rate : £450 - £500 per day Start date: Expected 14 September "
        "2026 Duration: To 18 December 2026 Location : Onsite in London "
        "4 days/week We are looking for a Senior Data Engineer to "
        "support a major financial-services data transformation "
        "programme."
    )


class TestComputeSimhash(unittest.TestCase):
    """Structural tests — see this plan's scope note on why SimHash
    output can't be hand-verified to an exact integer the way Step 5a/6's
    regex-based functions could."""

    def test_output_is_a_valid_64_bit_unsigned_value(self) -> None:
        result = compute_simhash(_real_adzuna_description())
        self.assertGreaterEqual(result, 0)
        self.assertLess(result, 2**64)

    def test_deterministic_for_identical_input(self) -> None:
        text = _real_adzuna_description()
        self.assertEqual(compute_simhash(text), compute_simhash(text))

    def test_empty_string_is_zero(self) -> None:
        self.assertEqual(compute_simhash(""), 0)

    def test_near_duplicate_real_descriptions_are_closer_than_unrelated(
        self,
    ) -> None:
        """The real cross-source Sopra Steria/Data Centre Engineer pair
        (Step 7 plan) must Hamming-distance closer to each other than
        either does to a real, unrelated posting."""
        adzuna_hash = compute_simhash(_real_adzuna_description())
        reed_hash = compute_simhash(_real_reed_description())
        unrelated_hash = compute_simhash(_unrelated_description())

        near_duplicate_distance = hamming_distance(adzuna_hash, reed_hash)
        unrelated_distance = hamming_distance(adzuna_hash, unrelated_hash)

        self.assertLess(near_duplicate_distance, unrelated_distance)
        # PLAN.md's own threshold for "this counts as a match."
        self.assertLessEqual(near_duplicate_distance, 3)


class TestHammingDistance(unittest.TestCase):
    def test_distance_to_self_is_zero(self) -> None:
        value = compute_simhash("some text")
        self.assertEqual(hamming_distance(value, value), 0)

    def test_symmetric(self) -> None:
        a = compute_simhash("some text")
        b = compute_simhash("other text")
        self.assertEqual(hamming_distance(a, b), hamming_distance(b, a))

    def test_maximally_different_64_bit_values(self) -> None:
        self.assertEqual(hamming_distance(0, 2**64 - 1), 64)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_simhash -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.simhash'`.

- [ ] **Step 3: Implement `compute_simhash` and `hamming_distance`**

`packages/core/core/dedup/simhash.py`:

```python
"""64-bit SimHash fingerprinting for near-duplicate description
detection (PLAN.md Step 8).

Standard word-level SimHash (Charikar's algorithm): each token votes on
every bit of its own hash, weighted +1/-1, and the final fingerprint bit
is 1 wherever the votes sum positive. Similar texts (sharing most
tokens) produce fingerprints with a small Hamming distance; unrelated
texts produce fingerprints close to random (~32 bits different, on
average, for two 64-bit fingerprints).
"""

from __future__ import annotations

import hashlib
import re

_TOKEN_RE = re.compile(r"\w+")


def compute_simhash(text: str, hash_bits: int = 64) -> int:
    """Compute a SimHash fingerprint over a text's word tokens.

    Args:
        text: The text to fingerprint (e.g. a posting's description).
        hash_bits: The fingerprint width. Defaults to 64, matching
            PLAN.md Step 8's "SimHash 64-bit."

    Returns:
        An unsigned integer in [0, 2**hash_bits) — 0 for empty or
        all-non-word-character input.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return 0

    votes = [0] * hash_bits
    for token in tokens:
        token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for bit_position in range(hash_bits):
            bit = (token_hash >> bit_position) & 1
            votes[bit_position] += 1 if bit else -1

    fingerprint = 0
    for bit_position in range(hash_bits):
        if votes[bit_position] > 0:
            fingerprint |= 1 << bit_position
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Count the differing bits between two fingerprints.

    Args:
        a: The first fingerprint.
        b: The second fingerprint.

    Returns:
        The number of bit positions where `a` and `b` differ.
    """
    return bin(a ^ b).count("1")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_simhash -v
```

Expected: all 7 tests PASS. If
`test_near_duplicate_real_descriptions_are_closer_than_unrelated`'s
`assertLessEqual(near_duplicate_distance, 3)` fails (the ≤3 threshold is
PLAN.md's own target, not a mathematical guarantee for this specific
tokenisation choice), do NOT weaken the assertion silently — first
check whether increasing shingle context (e.g. word bigrams instead of
unigrams) brings real near-duplicates closer, since unigram SimHash on
short/generic vocabulary can occasionally land above 3 even for genuine
near-duplicates. If bigrams don't help either, relax only this one
assertion to `assertLess(near_duplicate_distance, 10)` and document why
in a comment — report this as a concern either way, since Step 9's
future calibration will want to know the achievable threshold on real
data, not an assumed one.

- [ ] **Step 5: Quality gate**

```bash
cd job_search
python3.11 -m black packages/core/core/dedup/simhash.py packages/core/tests/test_simhash.py
python3.11 -m isort packages/core/core/dedup/simhash.py packages/core/tests/test_simhash.py
python3.11 -m ruff check packages/core/core/dedup/simhash.py packages/core/tests/test_simhash.py
python3.11 -m mypy packages/core/core/dedup/simhash.py
```

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/dedup/simhash.py packages/core/tests/test_simhash.py
git commit -m "feat(job_search): add compute_simhash and hamming_distance"
```

---

### Task 3: Per-job similarity features (`compute-similarity-features` CLI)

**Files:**
- Create: `packages/core/core/dedup/similarity_features.py`
- Create: `packages/core/core/dedup/write_similarity_features.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/test_similarity_features.py`
- Test: `packages/core/tests/integration/test_write_similarity_features.py`

**Interfaces:**
- Consumes: `compute_simhash` (Task 2),
  `core.normalisation.salary.parse_salary` (Step 6).
- Produces: `SimilarityFeatures` (dataclass: `description_simhash: int`,
  `rate_annualised: float | None`, `rate_currency: str | None`,
  `salary_band: str | None`) and `compute_similarity_features(
  description: str | None, salary_raw: str | None) ->
  SimilarityFeatures`; `write_similarity_features(engine: Engine) ->
  int` — invoked by the new `compute-similarity-features` CLI
  subcommand.

- [ ] **Step 1: Write the failing unit test**

`packages/core/tests/test_similarity_features.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.similarity_features import compute_similarity_features
from core.dedup.simhash import compute_simhash


class TestComputeSimilarityFeatures(unittest.TestCase):
    def test_combines_simhash_and_parsed_salary(self) -> None:
        description = "Data Engineer role, permanent, London."
        result = compute_similarity_features(
            description, salary_raw="£80k - £95k per year"
        )
        self.assertEqual(
            result.description_simhash, compute_simhash(description)
        )
        self.assertEqual(result.rate_annualised, 87500.0)
        self.assertEqual(result.rate_currency, "GBP")
        self.assertEqual(result.salary_band, "80000-90000")

    def test_none_description_hashes_to_zero(self) -> None:
        result = compute_similarity_features(None, salary_raw=None)
        self.assertEqual(result.description_simhash, 0)
        self.assertIsNone(result.rate_annualised)
        self.assertIsNone(result.salary_band)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_similarity_features -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `compute_similarity_features`**

`packages/core/core/dedup/similarity_features.py`:

```python
"""Per-job similarity features for Step 8's candidate-pair scoring
(PLAN.md Step 8): a description SimHash fingerprint and Step 6's
parsed-salary output. Computed once per job (core.dedup.
write_similarity_features), never per pair — the pairwise Hamming
distance and salary-overlap comparison happen in dbt (dedup__
similarity_scores), reading both sides' precomputed features.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.dedup.simhash import compute_simhash
from core.normalisation.salary import parse_salary


@dataclass(frozen=True)
class SimilarityFeatures:
    """One job's precomputed Step 8 similarity inputs.

    Attributes:
        description_simhash: 64-bit SimHash fingerprint of the
            description (0 if description is None/empty).
        rate_annualised: Step 6's parse_salary annualised GBP figure, or
            None if unstated.
        rate_currency: The originally-stated currency, or None.
        salary_band: Step 6's 10k-wide GBP band string, or None.
    """

    description_simhash: int
    rate_annualised: float | None
    rate_currency: str | None
    salary_band: str | None


def compute_similarity_features(
    description: str | None, salary_raw: str | None
) -> SimilarityFeatures:
    """Compute one job's Step 8 similarity features.

    Args:
        description: The posting's free text, or None.
        salary_raw: The staging-layer salary_raw column, or None.

    Returns:
        The `SimilarityFeatures`.
    """
    fingerprint = compute_simhash(description or "")
    salary = parse_salary(description, salary_raw)
    return SimilarityFeatures(
        description_simhash=fingerprint,
        rate_annualised=salary.annualised_gbp,
        rate_currency=salary.original_currency,
        salary_band=salary.band,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_similarity_features -v
```

- [ ] **Step 5: Write the failing integration test**

`packages/core/tests/integration/test_write_similarity_features.py`:

```python
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_similarity_features import write_similarity_features

_OWNER_DSN = (
    "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
)


class TestWriteSimilarityFeatures(unittest.TestCase):
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
                    "'https://x', 'api', 'Data Engineer', 'Acme Ltd', "
                    "'London', 'A test description.', "
                    "'£80k - £95k per year', now(), now(), 'run-1', "
                    "'sha-1')"
                ),
                {"job_key": self.job_key},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.job_similarity_features "
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

    def test_writes_one_row_with_a_signed_bigint_simhash(self) -> None:
        written = write_similarity_features(self.engine)
        self.assertGreaterEqual(written, 1)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT description_simhash, rate_annualised, "
                    "salary_band FROM dedup.job_similarity_features "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).one()
        # Postgres bigint round-trips as a Python int within its signed
        # range — if the write path failed to convert an unsigned
        # fingerprint >= 2**63, this INSERT would have raised instead of
        # reaching this assertion.
        self.assertIsInstance(row.description_simhash, int)
        self.assertEqual(row.salary_band, "80000-90000")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        write_similarity_features(self.engine)
        write_similarity_features(self.engine)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM dedup.job_similarity_features "
                    "WHERE job_key = :job_key"
                ),
                {"job_key": self.job_key},
            ).scalar_one()
        self.assertEqual(count, 1)
```

- [ ] **Step 6: Run the test to verify it fails**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_write_similarity_features -v
```

- [ ] **Step 7: Implement `write_similarity_features`**

`packages/core/core/dedup/write_similarity_features.py`:

```python
"""Batch write path for dedup.job_similarity_features (PLAN.md Step 8).

Converts each SimHash fingerprint from Python's natural unsigned
representation to Postgres bigint's signed two's-complement range
before every INSERT — see this plan's Global Constraints for why this
conversion is required, not optional.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.dedup.similarity_features import compute_similarity_features

_SIGNED_BIGINT_OFFSET = 2**64
_SIGNED_BIGINT_MAX = 2**63 - 1

_SELECT_POSTINGS = text(
    "SELECT job_key, description, salary_raw FROM silver.silver__job_posting"
)

_UPSERT = text(
    """
    INSERT INTO dedup.job_similarity_features (
        job_key, description_simhash, rate_annualised, rate_currency,
        salary_band
    ) VALUES (
        :job_key, :description_simhash, :rate_annualised, :rate_currency,
        :salary_band
    )
    ON CONFLICT (job_key) DO UPDATE SET
        description_simhash = EXCLUDED.description_simhash,
        rate_annualised = EXCLUDED.rate_annualised,
        rate_currency = EXCLUDED.rate_currency,
        salary_band = EXCLUDED.salary_band,
        computed_at = now()
    """
)


def _to_signed_bigint(unsigned_value: int) -> int:
    """Convert an unsigned 64-bit value to Postgres bigint's signed range.

    Args:
        unsigned_value: A value in [0, 2**64).

    Returns:
        The same 64-bit pattern, reinterpreted as a signed integer in
        [-2**63, 2**63 - 1] — the range Postgres bigint accepts.
    """
    if unsigned_value > _SIGNED_BIGINT_MAX:
        return unsigned_value - _SIGNED_BIGINT_OFFSET
    return unsigned_value


def write_similarity_features(engine: Engine) -> int:
    """Compute and upsert similarity features for every posting.

    Args:
        engine: The migration/owner engine — this table is SHARED-zone.

    Returns:
        The number of rows written (inserted or updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_POSTINGS).all()
        for row in rows:
            features = compute_similarity_features(
                row.description, row.salary_raw
            )
            conn.execute(
                _UPSERT,
                {
                    "job_key": row.job_key,
                    "description_simhash": _to_signed_bigint(
                        features.description_simhash
                    ),
                    "rate_annualised": features.rate_annualised,
                    "rate_currency": features.rate_currency,
                    "salary_band": features.salary_band,
                },
            )
    return len(rows)
```

- [ ] **Step 8: Run the test to verify it passes**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_write_similarity_features -v
```

- [ ] **Step 9: Wire the CLI subcommand**

In `apps/pipeline/app/cli.py`, add the import (alongside the existing
`core.dedup.write_blocking_keys` import from the Step 7 plan):

```python
from core.dedup.write_similarity_features import write_similarity_features
```

Add the command function:

```python
def _cmd_compute_similarity_features(args: argparse.Namespace) -> int:
    """Run the `compute-similarity-features` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_similarity_features(engine)
    print(f"compute-similarity-features complete: rows_written={written}")
    return 0
```

Add the subparser and dispatch branch, mirroring the existing pattern:

```python
    subparsers.add_parser(
        "compute-similarity-features",
        help="Compute and write per-job similarity features (SimHash, salary)",
    )
```

```python
    if args.command == "compute-similarity-features":
        return _cmd_compute_similarity_features(args)
```

- [ ] **Step 10: Run it live**

```bash
cd job_search
python3.11 -m apps.pipeline.app.cli compute-similarity-features
```

Expected: `compute-similarity-features complete: rows_written=<N>`
matching `silver.silver__job_posting`'s row count.

- [ ] **Step 11: Quality gate and commit**

```bash
cd job_search
python3.11 -m black packages/core/core/dedup packages/core/tests
python3.11 -m isort packages/core/core/dedup packages/core/tests
python3.11 -m ruff check packages/core/core/dedup packages/core/tests
python3.11 -m mypy packages/core/core/dedup
```

```bash
git add packages/core/core/dedup/similarity_features.py \
  packages/core/core/dedup/write_similarity_features.py \
  packages/core/tests/test_similarity_features.py \
  packages/core/tests/integration/test_write_similarity_features.py \
  apps/pipeline/app/cli.py
git commit -m "feat(job_search): add compute-similarity-features pipeline subcommand"
```

---

### Task 4: Pair-level title similarity (`compute-title-similarity-scores` CLI)

**Files:**
- Create: `packages/core/core/dedup/title_similarity.py`
- Create: `packages/core/core/dedup/write_title_similarity_scores.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/test_title_similarity.py`
- Test: `packages/core/tests/integration/test_write_title_similarity_scores.py`

**Interfaces:**
- Consumes: `rapidfuzz.fuzz.token_set_ratio`, `ref('dedup__
  candidate_pairs')` (Step 7's dbt model, read directly via SQL — this
  is the one place this plan's Python reads a dbt-built table rather
  than a plain source table, since candidate-pair generation genuinely
  needs the self-join dbt already did; recomputing it in Python would
  duplicate Step 7's logic).
- Produces: `title_token_set_ratio(title_a: str, title_b: str) -> float`
  (0.0-1.0) and `write_title_similarity_scores(engine: Engine) -> int` —
  invoked by the new `compute-title-similarity-scores` CLI subcommand.

- [ ] **Step 1: Write the failing unit test**

`packages/core/tests/test_title_similarity.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.title_similarity import title_token_set_ratio


class TestTitleTokenSetRatio(unittest.TestCase):
    def test_identical_titles_score_one(self) -> None:
        self.assertEqual(
            title_token_set_ratio("Data Centre Engineer", "Data Centre Engineer"),
            1.0,
        )

    def test_seniority_variant_scores_highly(self) -> None:
        """PLAN.md's own example: 'Senior Data Engineer' vs 'Data
        Platform Engineer' at the same company — token-set ratio should
        still register meaningful overlap even though these are two
        DIFFERENT roles (this is why Step 8 also weighs company/
        description, not title alone)."""
        score = title_token_set_ratio(
            "Senior Data Engineer", "Data Platform Engineer"
        )
        self.assertGreater(score, 0.5)

    def test_completely_different_titles_score_low(self) -> None:
        score = title_token_set_ratio("Data Engineer", "Payroll Administrator")
        self.assertLess(score, 0.5)

    def test_empty_titles_do_not_raise(self) -> None:
        result = title_token_set_ratio("", "")
        self.assertIsInstance(result, float)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_title_similarity -v
```

- [ ] **Step 3: Implement `title_token_set_ratio`**

`packages/core/core/dedup/title_similarity.py`:

```python
"""Pairwise title similarity via token-set ratio (PLAN.md Step 8).

The one Step 8 signal that's inherently pairwise, not precomputable per
job — rapidfuzz's token_set_ratio compares two strings' word sets
directly and has no per-job "feature" representation to store ahead of
time the way a SimHash fingerprint does.
"""

from __future__ import annotations

from rapidfuzz import fuzz


def title_token_set_ratio(title_a: str, title_b: str) -> float:
    """Compute token-set ratio similarity between two (matching) titles.

    Args:
        title_a: The first title — expected to already be
            core.normalisation.title.strip_title's output (matching
            form), not the raw display title.
        title_b: The second title, same expectation.

    Returns:
        A similarity score in [0.0, 1.0] (rapidfuzz's native 0-100
        scale, divided by 100).
    """
    return fuzz.token_set_ratio(title_a, title_b) / 100.0
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_title_similarity -v
```

- [ ] **Step 5: Build the candidate pairs' titles are available live**

This task's write path reads `dedup.job_blocking_keys` (for each pair's
`matching_title`, already computed by the Step 7 plan) joined against
`dedup__candidate_pairs` (Step 7's dbt model, already built). Confirm
both exist before writing the integration test:

```bash
cd job_search/dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt run --select dedup__candidate_pairs
```

- [ ] **Step 6: Write the failing integration test**

`packages/core/tests/integration/test_write_title_similarity_scores.py`:

```python
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_title_similarity_scores import (
    write_title_similarity_scores,
)

_OWNER_DSN = (
    "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
)


class TestWriteTitleSimilarityScores(unittest.TestCase):
    """Integration test against a real Postgres instance."""

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{suffix}"
        self.job_key_b = f"test-b-{suffix}"
        with self.engine.begin() as conn:
            for job_key, title in (
                (self.job_key_a, "Data Engineer"),
                (self.job_key_b, "Data Engineer"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO dedup.job_blocking_keys "
                        "(job_key, normalised_company, matching_title, "
                        "country_iso, region, block_key, content_sha256) "
                        "VALUES (:job_key, 'Test Co', :title, NULL, NULL, "
                        "'Test Co|Data Engine|', 'sha-' || :job_key)"
                    ),
                    {"job_key": job_key, "title": title},
                )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__candidate_pairs "
                    "(job_key_a, job_key_b, match_type) "
                    "VALUES (:a, :b, 'block')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.pair_title_scores "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__candidate_pairs "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.job_blocking_keys "
                    "WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        self.engine.dispose()

    def test_writes_a_perfect_score_for_identical_titles(self) -> None:
        write_title_similarity_scores(self.engine)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT title_token_set_ratio FROM dedup.pair_title_scores "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).one()
        self.assertEqual(float(row.title_token_set_ratio), 1.0)
```

Note: `dedup__candidate_pairs` is a dbt-materialized TABLE, not a
migration-owned table — this test inserts a row directly into it for
the duration of the test, which is safe because dbt only ever
truncate-and-rebuilds it on `dbt run`, never reads back what was there
before. Nothing in this test suite runs `dbt run` mid-test, so this row
persists for exactly this test's lifetime.

- [ ] **Step 7: Run the test to verify it fails**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_write_title_similarity_scores -v
```

- [ ] **Step 8: Implement `write_title_similarity_scores`**

`packages/core/core/dedup/write_title_similarity_scores.py`:

```python
"""Batch write path for dedup.pair_title_scores (PLAN.md Step 8).

Runs outside dbt because rapidfuzz's token_set_ratio has no SQL
equivalent — the one Step 8 signal that must be computed in Python per
PAIR rather than per job. Reads dedup__candidate_pairs (a dbt-built
table, not a plain migration-owned source) directly via SQL, since
recomputing the self-join in Python would duplicate Step 7's logic.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.dedup.title_similarity import title_token_set_ratio

_SELECT_PAIRS_WITH_TITLES = text(
    """
    SELECT
        p.job_key_a,
        p.job_key_b,
        a.matching_title AS title_a,
        b.matching_title AS title_b
    FROM dedup.dedup__candidate_pairs AS p
    INNER JOIN dedup.job_blocking_keys AS a ON p.job_key_a = a.job_key
    INNER JOIN dedup.job_blocking_keys AS b ON p.job_key_b = b.job_key
    """
)

_UPSERT = text(
    """
    INSERT INTO dedup.pair_title_scores (
        job_key_a, job_key_b, title_token_set_ratio
    ) VALUES (
        :job_key_a, :job_key_b, :title_token_set_ratio
    )
    ON CONFLICT (job_key_a, job_key_b) DO UPDATE SET
        title_token_set_ratio = EXCLUDED.title_token_set_ratio,
        computed_at = now()
    """
)


def write_title_similarity_scores(engine: Engine) -> int:
    """Compute and upsert title similarity for every candidate pair.

    Args:
        engine: The migration/owner engine — this table is SHARED-zone.

    Returns:
        The number of rows written (inserted or updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_PAIRS_WITH_TITLES).all()
        for row in rows:
            ratio = title_token_set_ratio(row.title_a, row.title_b)
            conn.execute(
                _UPSERT,
                {
                    "job_key_a": row.job_key_a,
                    "job_key_b": row.job_key_b,
                    "title_token_set_ratio": ratio,
                },
            )
    return len(rows)
```

- [ ] **Step 9: Run the test to verify it passes**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_write_title_similarity_scores -v
```

- [ ] **Step 10: Wire the CLI subcommand**

Same pattern as Task 3 Step 9 — import
`write_title_similarity_scores`, add
`_cmd_compute_title_similarity_scores`, register the
`compute-title-similarity-scores` subparser and dispatch branch.

- [ ] **Step 11: Add `rapidfuzz` to `requirements.txt`**

Add alongside the other pinned dependencies:

```
rapidfuzz==3.14.5
```

(Already installed in this environment at this exact version — confirm
with `python3.11 -c "import rapidfuzz; print(rapidfuzz.__version__)"`
rather than assuming; if the installed version has since moved on,
pin to whatever `pip show rapidfuzz` reports instead of forcing a
downgrade.)

- [ ] **Step 12: Run it live**

```bash
cd job_search
python3.11 -m apps.pipeline.app.cli compute-title-similarity-scores
```

Expected: `compute-title-similarity-scores complete: rows_written=<N>`
matching `dedup__candidate_pairs`'s row count.

- [ ] **Step 13: Quality gate and commit**

```bash
cd job_search
python3.11 -m black packages/core/core/dedup packages/core/tests
python3.11 -m isort packages/core/core/dedup packages/core/tests
python3.11 -m ruff check packages/core/core/dedup packages/core/tests
python3.11 -m mypy packages/core/core/dedup
```

```bash
git add packages/core/core/dedup/title_similarity.py \
  packages/core/core/dedup/write_title_similarity_scores.py \
  packages/core/tests/test_title_similarity.py \
  packages/core/tests/integration/test_write_title_similarity_scores.py \
  apps/pipeline/app/cli.py requirements.txt
git commit -m "feat(job_search): add compute-title-similarity-scores pipeline subcommand"
```

---

### Task 5: `dedup__similarity_scores` dbt model

**Files:**
- Modify: `dbt/models/dedup/_dedup.yml`
- Create: `dbt/models/dedup/dedup__similarity_scores.sql`

**Interfaces:**
- Consumes: `ref('dedup__candidate_pairs')` (Step 7),
  `source('dedup_ingest', 'job_blocking_keys')` (Step 7),
  `source('dedup_ingest', 'job_similarity_features')` (Task 1/3),
  `source('dedup_ingest', 'pair_title_scores')` (Task 1/4),
  `ref('silver__job_posting')` (Step 5a, for `posted_at`).
- Produces: `ref('dedup__similarity_scores')` — one row per candidate
  pair, every component signal persisted separately plus the blend.

## The blend — documented, not hidden in the SQL

Renormalised across 6 signals (the 7th, embedding, is deferred — see
this plan's scope note). PLAN.md's qualitative weights (high/medium/low)
become explicit integers so the formula is inspectable and Step 9 can
retune from a known starting point, not an implicit one:

| Signal | PLAN.md weight | This plan's integer weight |
|---|---|---|
| Company (trigram) | high | 3 |
| Title (token-set ratio) | high | 3 |
| Description (SimHash) | high | 3 |
| Location (post geo-normalisation) | medium | 2 |
| Posted date (±14 soft, ±45 hard veto) | low | 1 |
| Salary band | low | 1 |

Total weight = 13. `blended_score = weighted sum / 13`, forced to `0`
whenever the ±45-day hard veto trips, regardless of every other signal
— PLAN.md is explicit that a repost months later is a genuinely
different opportunity, not a near-miss on scoring.

Missing information (unresolved location, absent salary on either side)
scores **0.5** (neutral) for that one component, not `0` — an unknown
signal is not evidence of a mismatch, and scoring it as a confident "no"
would bias every pair with a manual/failed-extraction entry toward
looking like a non-duplicate for reasons that have nothing to do with
whether it actually is one.

- [ ] **Step 1: Write `dedup__similarity_scores.sql`**

`dbt/models/dedup/dedup__similarity_scores.sql`:

```sql
-- dedup__similarity_scores: one row per Step 7 candidate pair, every
-- component signal persisted separately (PLAN.md Step 8's "you will
-- retune the weights in Step 9" requirement) plus an initial blend.
-- Grain: (job_key_a, job_key_b).

WITH pairs AS (

    SELECT * FROM {{ ref('dedup__candidate_pairs') }}

),

-- Per-job fields needed on both sides of every pair.
keys AS (

    SELECT * FROM {{ source('dedup_ingest', 'job_blocking_keys') }}

),

features AS (

    SELECT * FROM {{ source('dedup_ingest', 'job_similarity_features') }}

),

title_scores AS (

    SELECT * FROM {{ source('dedup_ingest', 'pair_title_scores') }}

),

postings AS (

    SELECT job_key, posted_at FROM {{ ref('silver__job_posting') }}

),

-- Every raw component, computed pairwise from the two sides' precomputed
-- per-job fields (or, for title, read directly from Task 4's Python
-- output — token-set ratio has no SQL equivalent).
components AS (

    SELECT
        p.job_key_a,
        p.job_key_b,
        p.match_type,
        SIMILARITY(ka.normalised_company, kb.normalised_company)
            AS company_similarity,
        ts.title_token_set_ratio AS title_similarity,
        1.0 - (
            bit_count((fa.description_simhash # fb.description_simhash)::bit(64))
            / 64.0
        ) AS description_similarity,
        CASE
            WHEN ka.country_iso IS NULL OR kb.country_iso IS NULL THEN 0.5
            WHEN ka.country_iso != kb.country_iso THEN 0.0
            WHEN ka.region IS NULL OR kb.region IS NULL THEN 0.75
            WHEN ka.region = kb.region THEN 1.0
            ELSE 0.25
        END AS location_similarity,
        ABS(EXTRACT(DAY FROM (pa.posted_at - pb.posted_at))) AS date_diff_days,
        CASE
            WHEN fa.rate_annualised IS NULL OR fb.rate_annualised IS NULL THEN 0.5
            WHEN ABS(fa.rate_annualised - fb.rate_annualised)
                / GREATEST(fa.rate_annualised, fb.rate_annualised) <= 0.15
                THEN 1.0
            ELSE 0.0
        END AS salary_similarity
    FROM pairs AS p
    INNER JOIN keys AS ka ON p.job_key_a = ka.job_key
    INNER JOIN keys AS kb ON p.job_key_b = kb.job_key
    INNER JOIN features AS fa ON p.job_key_a = fa.job_key
    INNER JOIN features AS fb ON p.job_key_b = fb.job_key
    INNER JOIN title_scores AS ts
        ON p.job_key_a = ts.job_key_a AND p.job_key_b = ts.job_key_b
    INNER JOIN postings AS pa ON p.job_key_a = pa.job_key
    INNER JOIN postings AS pb ON p.job_key_b = pb.job_key

)

SELECT
    job_key_a,
    job_key_b,
    match_type,
    company_similarity,
    title_similarity,
    description_similarity,
    location_similarity,
    date_diff_days,
    GREATEST(0.0, 1.0 - (date_diff_days / 45.0)) AS date_similarity,
    salary_similarity,
    (date_diff_days > 45) AS hard_veto,
    CASE
        WHEN date_diff_days > 45 THEN 0.0
        ELSE (
            3 * company_similarity
            + 3 * title_similarity
            + 3 * description_similarity
            + 2 * location_similarity
            + 1 * GREATEST(0.0, 1.0 - (date_diff_days / 45.0))
            + 1 * salary_similarity
        ) / 13.0
    END AS blended_score
FROM components
```

- [ ] **Step 2: Extend `_dedup.yml`**

Append to `dbt/models/dedup/_dedup.yml`'s `sources:` list (two new
tables under the same `dedup_ingest` source already defined for
`job_blocking_keys`):

```yaml
      - name: job_similarity_features
        description: >
          Written by the compute-similarity-features pipeline CLI
          subcommand (core.dedup.write_similarity_features). One row per
          silver__job_posting job: a description SimHash fingerprint and
          Step 6's parsed-salary output.
        columns:
          - name: job_key
            description: "Matches silver.silver__job_posting.job_key."
      - name: pair_title_scores
        description: >
          Written by the compute-title-similarity-scores pipeline CLI
          subcommand (core.dedup.write_title_similarity_scores). One row
          per dedup__candidate_pairs row — the one Step 8 signal
          computed per PAIR rather than per job, since rapidfuzz's
          token_set_ratio has no SQL equivalent.
```

And append to the `models:` list:

```yaml
  - name: dedup__similarity_scores
    description: >
      One row per Step 7 candidate pair, every Step 8 component signal
      persisted separately plus an initial blend (see this plan's
      documented weight table) — Step 9 recalibrates the blend from
      real labelled pairs, reading these components directly rather
      than recomputing anything.
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
      - name: company_similarity
        data_type: "double precision"
        tests:
          - not_null
      - name: title_similarity
        data_type: numeric
        tests:
          - not_null
      - name: description_similarity
        data_type: "double precision"
        tests:
          - not_null
      - name: location_similarity
        data_type: "double precision"
        tests:
          - not_null
      - name: date_diff_days
        data_type: "double precision"
        tests:
          - not_null
      - name: date_similarity
        data_type: "double precision"
        tests:
          - not_null
      - name: salary_similarity
        data_type: "double precision"
        tests:
          - not_null
      - name: hard_veto
        data_type: boolean
        tests:
          - not_null
      - name: blended_score
        data_type: "double precision"
        tests:
          - not_null
```

Note: this model does NOT declare `config: {contract: {enforced: true}}`
— unlike the staging/silver layers, this model's column set is expected
to grow when Step 9 adds calibration fields and Step 15's deferred
embedding signal eventually lands, and a contract would force a
migration-style change for every such addition. This mirrors
`int_jobs__unioned`'s own precedent (also uncontracted).

- [ ] **Step 3: Run and test live**

```bash
cd job_search/dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt run --select dedup__similarity_scores
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt test --select dedup__similarity_scores
```

Expected: model builds, all tests PASS.

- [ ] **Step 4: Confirm the real duplicate pair scores highly**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT * FROM dedup.dedup__similarity_scores
WHERE (job_key_a = '0d5842a06b5b9e5308381d7b7ed7af69' AND job_key_b = 'e2bd39c45efc081ca3be8abc2a1aaad5')
   OR (job_key_a = 'e2bd39c45efc081ca3be8abc2a1aaad5' AND job_key_b = '0d5842a06b5b9e5308381d7b7ed7af69');
"
```

Expected: `company_similarity = 1.0` (identical strings), `title_similarity
= 1.0`, `description_similarity` high (small Hamming distance — see
Task 2's SimHash note on why this isn't a fixed hand-verified number),
`date_diff_days = 1`, `hard_veto = false`, `blended_score` well above
the pairs this session's other, unrelated postings score. If
`blended_score` is surprisingly low, investigate before moving on —
this pair is this plan's own designated proof that the pipeline works
end to end, not a nice-to-have check.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/dedup/dedup__similarity_scores.sql dbt/models/dedup/_dedup.yml
git commit -m "feat(job_search): add dedup__similarity_scores"
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

Expected: every model builds, every test passes.

- [ ] **Step 2: Full pipeline run order, from scratch, as a real operator would run it**

```bash
cd job_search
python3.11 -m apps.pipeline.app.cli compute-blocking-keys
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt run --select dedup__exact_duplicates dedup__candidate_pairs
cd ..
python3.11 -m apps.pipeline.app.cli compute-similarity-features
python3.11 -m apps.pipeline.app.cli compute-title-similarity-scores
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt run --select dedup__similarity_scores
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search DBT_PROFILES_DIR=. dbt test
```

Expected: clean run end to end, confirming the operational order
(blocking keys → candidate pairs → similarity features/title scores →
final scores) actually works when run in sequence, not just when each
piece was tested in isolation during its own task.

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
`collection_channel` errors (see the Step 5a plan's notes — unrelated
to this plan, caused by a sibling unmerged branch's migration already
applied to the shared dev database).

- [ ] **Step 4: Surface the deferred embedding signal to the user**

Before merging, tell the user: this plan deliberately deferred the
description-embedding/pgvector signal (Step 8's 7th, per the scope note
above). Ask whether they want it logged as a new backlog/Jira entry now
(via the `jira-log` skill, which requires their confirmation) or left as
a known gap until Step 15 settles the embedding dimension/index
decision.

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage:** 6 of `STEP-08`'s subtasks map to a task above
  (`pg_trgm`/`pgvector` extensions → Task 1, only `pg_trgm` since
  embedding is deferred; company similarity → Task 5; title similarity
  → Task 4; description SimHash → Task 2-3; description embedding →
  deliberately deferred, see scope note; location match → Task 5, reuses
  Step 7's already-resolved country/region; date proximity + veto →
  Task 5; salary band overlap → Task 3/5; persist every component → the
  entire point of Task 5's column list).
- **Placeholder scan:** none found — every function, migration, and dbt
  model is complete. The one deliberately-flagged exception is the
  SimHash test's exact-value assertions, which are structural/
  comparative rather than hand-verified magic numbers, per this plan's
  own scope note (not hidden, called out twice: in the scope note and
  again inline at Task 2 Step 4).
- **Genuine, flagged uncertainty:** whether unigram-tokenised SimHash
  actually achieves Hamming distance ≤3 on the real duplicate pair is
  not provable by inspection the way Step 5a/6's regex work was — Task
  2 Step 4 gives the executor an explicit, honest escalation path
  (try bigrams, then relax the threshold with a comment, then report as
  a concern) rather than a false assurance either way.
- **Type consistency:** `SimilarityFeatures` and `BlockingKeyResult`'s
  field names were checked against Task 1's migration column names and
  Task 5's dbt model's column references side by side before finalising
  this plan. `title_token_set_ratio`'s 0.0-1.0 scale (divided from
  rapidfuzz's native 0-100) is used consistently everywhere it's
  referenced, including in the blend formula's weighting.
