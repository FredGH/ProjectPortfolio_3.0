# Step 9 — Calibration and Review Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the infrastructure Step 9 needs — a labels table, a
precision-recall computation, a FastAPI router, and two Streamlit pages
(bootstrap-mode calibration sampling and ongoing middle-band review) —
so the user can hand-label real candidate pairs and set real,
measured thresholds. This plan does **not** perform the labeling
itself (see the scope note below).

**Architecture:** A new `dedup.pair_labels` table (every human label,
shared-zone) and `dedup.calibration_thresholds` table (an append-only
history of calibration runs — the most recent row is "current"). A pure
`core.dedup.calibration` module computes precision/recall at each
distinct blended_score threshold from a list of labeled pairs — no DB
dependency, fully unit-testable. A new FastAPI router exposes both as
CRUD/compute endpoints, following the existing `apps/api/app/routers/
ingest.py` pattern exactly. Two new Streamlit pages, following the
existing `apps/ui/app/pages/1_Manual_Job_Entry.py` pattern exactly (UI
calls the API via `httpx`, never touches Postgres directly).

**Tech Stack:** FastAPI + Pydantic (existing), Streamlit (existing, no
new dependency — precision/recall is simple enough to compute in plain
Python, and `st.line_chart` needs no plotting library).

**Spec:** `PLAN.md`'s "Step 9 — Calibration and review queue" section
and `plan/backlog.yml`'s `STEP-09` entry (`jira_key: JOB-134`).

## Scope note — this plan builds the tool, not the calibration itself

`STEP-09`'s first subtask is "Hand-label 50 candidate pairs as match /
not-match" — real human judgment against real data, which this plan
cannot perform on the user's behalf without defeating the entire point
of calibration (a precision figure computed from invented labels would
be meaningless). Decided with the user before writing this plan: build
the Streamlit review queue and calibration tooling first; the user then
labels 50 real pairs through it; a final, separate, interactive/
controller-run step (not in this plan's task list) reads those real
labels, records the measured precision/recall, and writes it to the
repo — that step cannot happen until real labels exist.

**Bootstrapping problem, solved by one dual-mode endpoint:** the
"review queue for the middle band" (production mode) can't exist before
thresholds are set, and thresholds can't be set before labels exist. So
`GET /dedup/pairs-to-label` runs in one of two modes depending on
whether `dedup.calibration_thresholds` has any rows yet:
- **Bootstrap mode** (no thresholds yet): a stratified sample across the
  *actual population deciles* of `blended_score` (via `NTILE(10)`, not a
  fixed 0-1 range split — real data is heavily concentrated between 0.5
  and 0.8, confirmed live in this session: deciles 1-4 are empty,
  decile 7 alone holds 45,986 of ~72,775 non-veto pairs). This is what
  the initial 50-pair calibration exercise uses.
- **Production mode** (thresholds exist): pairs with `blended_score`
  strictly between the two thresholds — the actual "middle band" review
  queue PLAN.md describes.

## Scope note — thresholds live in Postgres, not a YAML config file

`STEP-09`'s subtask says "Set auto-match and auto-reject cutoffs in
config." This plan stores them in a new Postgres table
(`dedup.calibration_thresholds`) instead of a `config/*.yml` file.
Reasoning: `docker-compose.yml`'s `api` service bind-mounts `./config`
locally, so a runtime file write would work in this dev environment —
but PLAN.md's own Step 1 rule ("every environment difference is an
environment variable... one settings.py") and Step 12's future GCP
Cloud Run deployment make a runtime-writable local file a dead end
(Cloud Run's filesystem is ephemeral and not shared across replicas).
Postgres is already provisioned identically in both environments. This
also gives calibration history for free (Step 16 recalibrates later;
an append-only table shows every past run, a single file would not).

## Global Constraints

- Python style (Google docstrings, type hints, black/isort/ruff) per
  `.claude/rules/python-style.md`; tests per
  `.claude/rules/python-testing.md` (`unittest`, no DB mocking in
  integration tests).
- `dedup.pair_labels`/`dedup.calibration_thresholds` are SHARED-zone —
  a duplicate-pair judgment or a calibration threshold is the same fact
  for both of this project's two users, not scoped per-user. No RLS, no
  `user_id`. `job_search_app` needs `SELECT, INSERT, UPDATE` on both
  (matching `target_company`'s migration `0005` precedent — the API
  writes to these directly).
- Next migration is `0010`, `down_revision = "0009"` — confirm via `ls
  db/migrations/versions/` at execution time (there's a separate,
  disposable, untracked `0006_add_collection_channel.py` file from an
  unrelated unmerged branch that may or may not be present in a given
  worktree — ignore it either way, it's never part of this chain).
- New API code follows `apps/api/app/routers/ingest.py`'s exact
  conventions: `APIRouter()`, Pydantic request/response models,
  `Depends(get_app_db_engine)` for DB access. New UI code follows
  `apps/ui/app/pages/1_Manual_Job_Entry.py`'s exact conventions:
  `httpx` calls to `settings.api_base_url`, no direct Postgres access.
- `docker compose up -d postgres` must be running for every DB-touching
  task's verification.
- This project has no existing test coverage for Streamlit pages
  (`Home.py`/`1_Manual_Job_Entry.py` have none) — this plan doesn't
  introduce that precedent either. Streamlit pages are verified by
  starting the app and checking it loads without error, not by
  automated test.

---

### Task 1: Migration — `dedup.pair_labels` and `dedup.calibration_thresholds`

**Files:**
- Create: `db/migrations/versions/0010_create_dedup_pair_labels_and_calibration.py`

**Interfaces:**
- Produces: tables `dedup.pair_labels` (composite PK `job_key_a,
  job_key_b`) and `dedup.calibration_thresholds` (serial PK, append-only)
  — consumed by Task 3 (API router).

- [ ] **Step 1: Confirm the migration head**

```bash
ls db/migrations/versions/
```

Expected latest: `0009_create_dedup_similarity_tables.py`. If different,
stop and re-check before renumbering.

- [ ] **Step 2: Write the migration**

`db/migrations/versions/0010_create_dedup_pair_labels_and_calibration.py`:

```python
"""create dedup.pair_labels and dedup.calibration_thresholds

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-08

Both tables are SHARED job-pair data (PLAN.md's two-zone rule, same
pattern as dedup.job_blocking_keys (0008)): whether two postings are
the same job, and where the auto-match/auto-reject thresholds sit, are
the same facts for every user of this project — no user_id, no RLS.

Unlike earlier dedup.* tables (written only by pipeline CLI subcommands
via the owner role), these two are written directly by the FastAPI
request-serving layer (PLAN.md Step 9's review-queue and calibration
endpoints) — so, following target_company's precedent (migration
0005), job_search_app gets SELECT/INSERT/UPDATE, not just SELECT.

calibration_thresholds is append-only (no UPDATE path) — every
calibration run adds a row; the most recently calibrated_at row is
"current." This gives free history when Step 16 recalibrates later.

This migration ALSO grants job_search_app schema-level access to
`silver` and `dedup` for the first time — every prior table in both
schemas (silver.silver__job_posting, silver.job_engagement_terms,
dedup.job_blocking_keys, dedup.dedup__candidate_pairs, dedup.
dedup__similarity_scores, dedup.job_similarity_features, dedup.
pair_title_scores) has only ever been read by the owner role (dbt,
pipeline CLI subcommands) — job_search_app has never had USAGE on
either schema. Step 9's endpoints are the first request-serving code to
read silver__job_posting/dedup__similarity_scores directly, so this
migration grants USAGE on both schemas and SELECT on every table
currently in them.

Critically, this uses `ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner`,
not just one-time GRANTs on today's tables: dbt's `table` materialization
does DROP+CREATE on every `dbt run`, which silently wipes a one-time
GRANT on that exact table (Postgres does not carry privileges across a
DROP), while ALTER DEFAULT PRIVILEGES makes every *future* table
job_search_owner creates in these schemas automatically grant SELECT
to job_search_app — verified live in this session against this exact
Postgres instance (dropping and recreating a table after a one-time
GRANT does lose it; after ALTER DEFAULT PRIVILEGES, a freshly-created
table in the same schema has the grant already).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # job_search_app has never had access to either schema before this
    # migration — grant USAGE plus SELECT on every existing table, AND
    # a default-privileges rule so every FUTURE table job_search_owner
    # creates here (dbt rebuilds every table on every `dbt run`, which
    # would otherwise silently drop a one-time GRANT) keeps the grant.
    # Verified live against this exact Postgres instance: a table
    # dropped and recreated after a one-time GRANT loses it; after
    # ALTER DEFAULT PRIVILEGES, a freshly (re)created table has it
    # already.
    op.execute("GRANT USAGE ON SCHEMA silver TO job_search_app")
    op.execute("GRANT USAGE ON SCHEMA dedup TO job_search_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA silver TO job_search_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA dedup TO job_search_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver "
        "GRANT SELECT ON TABLES TO job_search_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA dedup "
        "GRANT SELECT ON TABLES TO job_search_app"
    )

    op.create_table(
        "pair_labels",
        sa.Column("job_key_a", sa.Text(), primary_key=True),
        sa.Column("job_key_b", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "is_manual_override", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("labeled_by", sa.Text(), nullable=True),
        sa.Column(
            "labeled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "label IN ('match', 'not_match')", name="ck_pair_labels_label"
        ),
        schema="dedup",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON dedup.pair_labels TO job_search_app"
    )

    op.create_table(
        "calibration_thresholds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("auto_match_threshold", sa.Numeric(), nullable=False),
        sa.Column("auto_reject_threshold", sa.Numeric(), nullable=False),
        sa.Column("measured_precision", sa.Numeric(), nullable=False),
        sa.Column("measured_recall", sa.Numeric(), nullable=False),
        sa.Column("labeled_pair_count", sa.Integer(), nullable=False),
        sa.Column("calibrated_by", sa.Text(), nullable=True),
        sa.Column(
            "calibrated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )
    op.execute(
        "GRANT SELECT, INSERT ON dedup.calibration_thresholds TO job_search_app"
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON dedup.calibration_thresholds FROM job_search_app")
    op.drop_table("calibration_thresholds", schema="dedup")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON dedup.pair_labels FROM job_search_app")
    op.drop_table("pair_labels", schema="dedup")

    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA dedup "
        "REVOKE SELECT ON TABLES FROM job_search_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver "
        "REVOKE SELECT ON TABLES FROM job_search_app"
    )
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA dedup FROM job_search_app")
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA silver FROM job_search_app")
    op.execute("REVOKE USAGE ON SCHEMA dedup FROM job_search_app")
    op.execute("REVOKE USAGE ON SCHEMA silver FROM job_search_app")
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
  -c "\d dedup.pair_labels" -c "\d dedup.calibration_thresholds" \
  -c "\dp dedup.pair_labels" -c "\dp dedup.calibration_thresholds"
```

Expected: both tables exist with the columns above; `\dp` (privileges)
shows `job_search_app` with the grants specified.

- [ ] **Step 4: Confirm `job_search_app` can actually read the pre-existing dedup/silver tables**

This is the schema-level access grant, not just the two new tables —
confirm it works, since Task 3/4's API endpoints depend on it and
nothing before this migration ever exercised it:

```bash
docker compose exec -T postgres psql -U job_search_app -d job_search \
  -c "SELECT count(*) FROM silver.silver__job_posting;" \
  -c "SELECT count(*) FROM dedup.dedup__similarity_scores;"
```

Expected: both return real row counts (no `permission denied` error).
If either fails, the migration's grants are wrong — fix it before
moving on, since every later task in this plan depends on this working.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/versions/0010_create_dedup_pair_labels_and_calibration.py
git commit -m "feat(job_search): add dedup.pair_labels and dedup.calibration_thresholds"
```

---

### Task 2: `core.dedup.calibration` — precision/recall computation

**Files:**
- Create: `packages/core/core/dedup/calibration.py`
- Test: `packages/core/tests/test_calibration.py`

**Interfaces:**
- Produces: `LabeledPair` (dataclass: `job_key_a: str`, `job_key_b:
  str`, `blended_score: float`, `label: str`), `ThresholdMetrics`
  (dataclass: `threshold: float`, `precision: float | None`, `recall:
  float | None`, `predicted_match_count: int`), and
  `compute_precision_recall_curve(labeled_pairs: list[LabeledPair]) ->
  list[ThresholdMetrics]` — consumed by Task 4 (the `/dedup/
  calibration` endpoint).

## Hand-verified synthetic test data — flagged, not real

Unlike this project's other plans, no real labeled pairs exist yet
(that's what this whole plan exists to enable) — there is nothing real
to test against. The 5-pair fixture below is invented specifically to
make the precision/recall arithmetic hand-verifiable, and is labeled as
such rather than presented as real data:

| blended_score | label | predicted @ threshold=score | running TP/FP among 3 true matches |
|---|---|---|---|
| 0.95 | match | {0.95} | TP=1, FP=0 → P=1.0, R=1/3 |
| 0.90 | match | {0.95, 0.90} | TP=2, FP=0 → P=1.0, R=2/3 |
| 0.80 | not_match | {..., 0.80} | TP=2, FP=1 → P=2/3, R=2/3 |
| 0.70 | match | {..., 0.70} | TP=3, FP=1 → P=3/4, R=3/3=1.0 |
| 0.60 | not_match | {..., 0.60} (all 5) | TP=3, FP=2 → P=3/5=0.6, R=1.0 |

(3 total true matches: 0.95, 0.90, 0.70.)

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_calibration.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.calibration import LabeledPair, compute_precision_recall_curve

# Synthetic, hand-verified fixture — NOT real data. No real labels exist
# yet; this plan builds the tool that will produce them. See the plan's
# own hand-verification table for the arithmetic behind every assertion
# below.
_SYNTHETIC_PAIRS = [
    LabeledPair("job-1", "job-2", blended_score=0.95, label="match"),
    LabeledPair("job-3", "job-4", blended_score=0.90, label="match"),
    LabeledPair("job-5", "job-6", blended_score=0.80, label="not_match"),
    LabeledPair("job-7", "job-8", blended_score=0.70, label="match"),
    LabeledPair("job-9", "job-10", blended_score=0.60, label="not_match"),
]


class TestComputePrecisionRecallCurve(unittest.TestCase):
    """Tests against a small, hand-verified synthetic fixture."""

    def test_curve_has_one_point_per_distinct_score(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        self.assertEqual(len(curve), 5)

    def test_curve_is_sorted_by_descending_threshold(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        thresholds = [point.threshold for point in curve]
        self.assertEqual(thresholds, sorted(thresholds, reverse=True))

    def test_highest_threshold_point(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = curve[0]
        self.assertEqual(point.threshold, 0.95)
        self.assertEqual(point.predicted_match_count, 1)
        self.assertAlmostEqual(point.precision, 1.0)
        self.assertAlmostEqual(point.recall, 1 / 3)

    def test_middle_threshold_point_with_one_false_positive(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = next(p for p in curve if p.threshold == 0.80)
        self.assertEqual(point.predicted_match_count, 3)
        self.assertAlmostEqual(point.precision, 2 / 3)
        self.assertAlmostEqual(point.recall, 2 / 3)

    def test_threshold_where_recall_reaches_one(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = next(p for p in curve if p.threshold == 0.70)
        self.assertEqual(point.predicted_match_count, 4)
        self.assertAlmostEqual(point.precision, 3 / 4)
        self.assertAlmostEqual(point.recall, 1.0)

    def test_lowest_threshold_predicts_everything_as_a_match(self) -> None:
        curve = compute_precision_recall_curve(_SYNTHETIC_PAIRS)
        point = curve[-1]
        self.assertEqual(point.threshold, 0.60)
        self.assertEqual(point.predicted_match_count, 5)
        self.assertAlmostEqual(point.precision, 3 / 5)
        self.assertAlmostEqual(point.recall, 1.0)

    def test_empty_input_returns_empty_curve(self) -> None:
        self.assertEqual(compute_precision_recall_curve([]), [])

    def test_no_true_matches_gives_none_recall_everywhere(self) -> None:
        """Recall is undefined (0 true matches to find) — None, not a
        divide-by-zero crash or a misleading 0.0/1.0."""
        pairs = [
            LabeledPair("job-1", "job-2", blended_score=0.9, label="not_match"),
            LabeledPair("job-3", "job-4", blended_score=0.5, label="not_match"),
        ]
        curve = compute_precision_recall_curve(pairs)
        self.assertTrue(all(point.recall is None for point in curve))
        # Precision is still well-defined here (0 TP / N predicted = 0.0).
        self.assertAlmostEqual(curve[0].precision, 0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_calibration -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.calibration'`.

- [ ] **Step 3: Implement `compute_precision_recall_curve`**

`packages/core/core/dedup/calibration.py`:

```python
"""Precision/recall computation for dedup match-threshold calibration
(PLAN.md Step 9).

Pure computation, no database dependency — takes whatever labeled pairs
its caller already fetched (real ones, from core.enrichment... no, from
dedup.pair_labels joined against dedup__similarity_scores) and returns a
curve. PLAN.md Step 9 is explicit that thresholds get chosen by eye from
this curve ("the curve will tell you the knee is at 0.87"), not by an
automated cutoff-selection algorithm — this module stops at producing
the curve.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabeledPair:
    """One hand-labeled candidate pair.

    Attributes:
        job_key_a: The pair's first job.
        job_key_b: The pair's second job.
        blended_score: The pair's dedup__similarity_scores.blended_score
            at the time it was labeled.
        label: "match" or "not_match".
    """

    job_key_a: str
    job_key_b: str
    blended_score: float
    label: str


@dataclass(frozen=True)
class ThresholdMetrics:
    """Precision/recall at one candidate auto-match threshold.

    Attributes:
        threshold: A candidate auto-match cutoff — pairs with
            blended_score >= this value would be auto-matched.
        precision: Of the pairs that would be auto-matched at this
            threshold, the fraction actually labeled "match". None only
            when predicted_match_count is 0 (no pairs to compute over).
        recall: Of every pair labeled "match" in the input, the fraction
            that would be auto-matched at this threshold. None when the
            input has zero "match"-labeled pairs (recall is undefined,
            not zero — there is nothing to find).
        predicted_match_count: How many pairs would be auto-matched at
            this threshold.
    """

    threshold: float
    precision: float | None
    recall: float | None
    predicted_match_count: int


def compute_precision_recall_curve(
    labeled_pairs: list[LabeledPair],
) -> list[ThresholdMetrics]:
    """Compute precision/recall at every distinct blended_score in the input.

    Args:
        labeled_pairs: Every hand-labeled pair to calibrate against.

    Returns:
        One `ThresholdMetrics` per distinct `blended_score` present in
        `labeled_pairs`, sorted by descending threshold. Empty list if
        `labeled_pairs` is empty.
    """
    total_matches = sum(1 for pair in labeled_pairs if pair.label == "match")
    thresholds = sorted({pair.blended_score for pair in labeled_pairs}, reverse=True)

    curve = []
    for threshold in thresholds:
        predicted = [p for p in labeled_pairs if p.blended_score >= threshold]
        true_positives = sum(1 for p in predicted if p.label == "match")
        precision = true_positives / len(predicted) if predicted else None
        recall = true_positives / total_matches if total_matches else None
        curve.append(
            ThresholdMetrics(
                threshold=threshold,
                precision=precision,
                recall=recall,
                predicted_match_count=len(predicted),
            )
        )
    return curve
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_calibration -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Quality gate**

```bash
cd job_search
python3.11 -m black packages/core/core/dedup/calibration.py packages/core/tests/test_calibration.py
python3.11 -m isort packages/core/core/dedup/calibration.py packages/core/tests/test_calibration.py
python3.11 -m ruff check packages/core/core/dedup/calibration.py packages/core/tests/test_calibration.py
python3.11 -m mypy packages/core/core/dedup/calibration.py
```

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/dedup/calibration.py packages/core/tests/test_calibration.py
git commit -m "feat(job_search): add compute_precision_recall_curve"
```

---

### Task 3: `GET /dedup/pairs-to-label` and `POST /dedup/labels`

**Files:**
- Create: `apps/api/app/routers/dedup.py`
- Modify: `apps/api/app/main.py`
- Test: `packages/core/tests/integration/test_dedup_router.py`

**Interfaces:**
- Consumes: `dedup.dedup__similarity_scores`, `dedup.job_blocking_keys`
  (via `matching_title`... no — the review queue needs the actual
  postings' display fields, from `silver.silver__job_posting`),
  `dedup.pair_labels`, `dedup.calibration_thresholds` (all via raw SQL
  through `Depends(get_app_db_engine)`).
- Produces: `router` (an `APIRouter`), registered in `main.py` —
  `GET /dedup/pairs-to-label?limit=int`, `POST /dedup/labels` —
  consumed by Task 5 (the review-queue Streamlit page).

- [ ] **Step 1: Write the failing integration test**

Per `.claude/rules/python-testing.md`, no DB mocking — this hits real
Postgres through a real `TestClient`, following `test_api_ingest.py`'s
`sys.path` pattern for importing `app.main` (this project's `apps/api/
app` and `apps/ui/app` are both top-level packages named `app`).

`packages/core/tests/integration/test_dedup_router.py`:

```python
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db.session import build_engine  # noqa: E402

_OWNER_DSN = (
    "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
)
_APP_DSN = (
    "postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search"
)


class TestPairsToLabelAndLabels(unittest.TestCase):
    """Integration tests against real Postgres — no mocking the database."""

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{suffix}"
        self.job_key_b = f"test-b-{suffix}"
        with self.owner_engine.begin() as conn:
            for job_key in (self.job_key_a, self.job_key_b):
                conn.execute(
                    text(
                        "INSERT INTO silver.silver__job_posting "
                        "(job_key, source_name, source_job_id, job_url, "
                        "job_url_canonical, entry_method, title, company, "
                        "location, description, salary_raw, posted_at, "
                        "engagement_type, ir35_status, engagement_vehicle, "
                        "rate_basis, extension_likelihood) VALUES "
                        "(:job_key, 'test_source', :job_key, 'https://x', "
                        "'https://x', 'api', 'Data Engineer', 'Acme Ltd', "
                        "'London', 'A test description.', NULL, now(), "
                        "'unknown', 'unknown', 'unknown', 'unknown', "
                        "'unstated')"
                    ),
                    {"job_key": job_key},
                )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__candidate_pairs "
                    "(job_key_a, job_key_b, match_type) "
                    "VALUES (:a, :b, 'block')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) VALUES "
                    "(:a, :b, 'block', 1.0, 1.0, 1.0, 0.5, 0, 1.0, 0.5, "
                    "false, 0.85)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__similarity_scores "
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
                    "DELETE FROM silver.silver__job_posting "
                    "WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_pairs_to_label_includes_the_seeded_pair_in_bootstrap_mode(self) -> None:
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 500})
        self.assertEqual(response.status_code, 200)
        pairs = response.json()
        keys = {(p["scores"]["job_key_a"], p["scores"]["job_key_b"]) for p in pairs}
        self.assertIn((self.job_key_a, self.job_key_b), keys)

    def test_posting_labels_round_trip(self) -> None:
        response = self.client.post(
            "/dedup/labels",
            json={
                "job_key_a": self.job_key_a,
                "job_key_b": self.job_key_b,
                "label": "match",
                "labeled_by": "test-user",
            },
        )
        self.assertEqual(response.status_code, 200)

        with self.owner_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT label, is_manual_override, labeled_by "
                    "FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).one()
        self.assertEqual(row.label, "match")
        self.assertTrue(row.is_manual_override)
        self.assertEqual(row.labeled_by, "test-user")

    def test_labeled_pairs_are_excluded_from_pairs_to_label(self) -> None:
        self.client.post(
            "/dedup/labels",
            json={
                "job_key_a": self.job_key_a,
                "job_key_b": self.job_key_b,
                "label": "not_match",
            },
        )
        response = self.client.get("/dedup/pairs-to-label", params={"limit": 500})
        keys = {
            (p["scores"]["job_key_a"], p["scores"]["job_key_b"])
            for p in response.json()
        }
        self.assertNotIn((self.job_key_a, self.job_key_b), keys)

    def test_relabeling_upserts_rather_than_duplicating(self) -> None:
        for label in ("match", "not_match"):
            self.client.post(
                "/dedup/labels",
                json={
                    "job_key_a": self.job_key_a,
                    "job_key_b": self.job_key_b,
                    "label": label,
                },
            )
        with self.owner_engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).scalar_one()
            row = conn.execute(
                text(
                    "SELECT label FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            ).one()
        self.assertEqual(count, 1)
        self.assertEqual(row.label, "not_match")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd job_search
docker compose up -d postgres
cd packages/core
python3.11 -m unittest tests.integration.test_dedup_router -v
```

Expected: import error or 404s — the router doesn't exist yet.

- [ ] **Step 3: Implement the router**

`apps/api/app/routers/dedup.py`:

```python
"""GET /dedup/pairs-to-label, POST /dedup/labels, GET /dedup/calibration,
GET|POST /dedup/thresholds — PLAN.md Step 9.
"""

from __future__ import annotations

import datetime
from typing import Literal

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Engine, text

from core.dedup.calibration import LabeledPair, compute_precision_recall_curve

router = APIRouter()


class PostingSummary(BaseModel):
    """Enough of a posting to review it side by side with another.

    Attributes:
        job_key: The posting's identity key.
        title: The posting's title, or None.
        company: The posting's company, or None.
        location: The posting's location, or None.
        description: The posting's description, or None.
        job_url_canonical: The canonicalised job URL.
        posted_at: When the posting was posted, if known.
    """

    job_key: str
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    job_url_canonical: str
    posted_at: datetime.datetime | None


class SimilarityScores(BaseModel):
    """One candidate pair's Step 8 similarity components.

    Attributes:
        job_key_a: The pair's first job.
        job_key_b: The pair's second job.
        blended_score: The overall weighted score.
        company_similarity: Company-name trigram similarity, 0-1.
        title_similarity: Title token-set-ratio similarity, 0-1.
        description_similarity: Description SimHash-derived similarity, 0-1.
        location_similarity: Location match score, 0-1.
        date_diff_days: Absolute days between the two posted_at values.
        salary_similarity: Salary-band overlap score, 0-1.
    """

    job_key_a: str
    job_key_b: str
    blended_score: float
    company_similarity: float
    title_similarity: float
    description_similarity: float
    location_similarity: float
    date_diff_days: float
    salary_similarity: float


class PairToLabel(BaseModel):
    """One candidate pair, with both postings' details, ready to review.

    Attributes:
        scores: The pair's similarity components.
        posting_a: The first posting's details.
        posting_b: The second posting's details.
    """

    scores: SimilarityScores
    posting_a: PostingSummary
    posting_b: PostingSummary


class LabelRequest(BaseModel):
    """A human's match/not-match decision on one candidate pair.

    Attributes:
        job_key_a: The pair's first job.
        job_key_b: The pair's second job.
        label: "match" or "not_match".
        labeled_by: Free-text identifier of who labeled it, if given.
    """

    job_key_a: str
    job_key_b: str
    label: Literal["match", "not_match"]
    labeled_by: str | None = None


_POSTING_COLUMNS = (
    "job_key, title, company, location, description, "
    "job_url_canonical, posted_at"
)


def _posting_summary(row: object) -> PostingSummary:
    """Build a PostingSummary from one silver__job_posting row.

    Args:
        row: A SQLAlchemy result row with `_POSTING_COLUMNS`' fields.

    Returns:
        The `PostingSummary`.
    """
    return PostingSummary(
        job_key=row.job_key,
        title=row.title,
        company=row.company,
        location=row.location,
        description=row.description,
        job_url_canonical=row.job_url_canonical,
        posted_at=row.posted_at,
    )


@router.get("/dedup/pairs-to-label", response_model=list[PairToLabel])
def get_pairs_to_label(
    limit: int = 50,
    engine: Engine = Depends(get_app_db_engine),
) -> list[PairToLabel]:
    """Return candidate pairs needing a human label.

    Bootstrap mode (no calibration_thresholds row yet): a stratified
    sample across the actual population deciles of blended_score, since
    real data is heavily concentrated in a narrow range (confirmed live
    in this project: most non-veto pairs score between 0.5 and 0.8) —
    a fixed-range split would starve the sparse high/low deciles.
    Production mode (thresholds exist): pairs strictly between the two
    thresholds — the actual "middle band" review queue.

    Args:
        limit: Maximum number of pairs to return.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Up to `limit` unlabeled `PairToLabel` entries.
    """
    with engine.connect() as conn:
        thresholds_row = conn.execute(
            text(
                "SELECT auto_match_threshold, auto_reject_threshold "
                "FROM dedup.calibration_thresholds "
                "ORDER BY calibrated_at DESC LIMIT 1"
            )
        ).one_or_none()

        if thresholds_row is None:
            per_bucket = max(1, limit // 10)
            rows = conn.execute(
                text(
                    f"""
                    WITH unlabeled AS (
                        SELECT s.*
                        FROM dedup.dedup__similarity_scores AS s
                        LEFT JOIN dedup.pair_labels AS l
                            ON s.job_key_a = l.job_key_a
                            AND s.job_key_b = l.job_key_b
                        WHERE l.job_key_a IS NULL AND s.hard_veto = false
                    ),
                    bucketed AS (
                        SELECT
                            *,
                            NTILE(10) OVER (ORDER BY blended_score) AS score_bucket
                        FROM unlabeled
                    ),
                    ranked AS (
                        SELECT
                            *,
                            ROW_NUMBER() OVER (
                                PARTITION BY score_bucket ORDER BY random()
                            ) AS rn
                        FROM bucketed
                    )
                    SELECT
                        job_key_a, job_key_b, match_type, company_similarity,
                        title_similarity, description_similarity,
                        location_similarity, date_diff_days, salary_similarity,
                        blended_score
                    FROM ranked
                    WHERE rn <= :per_bucket
                    ORDER BY score_bucket, rn
                    LIMIT :limit
                    """
                ),
                {"per_bucket": per_bucket, "limit": limit},
            ).all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        s.job_key_a, s.job_key_b, s.match_type,
                        s.company_similarity, s.title_similarity,
                        s.description_similarity, s.location_similarity,
                        s.date_diff_days, s.salary_similarity, s.blended_score
                    FROM dedup.dedup__similarity_scores AS s
                    LEFT JOIN dedup.pair_labels AS l
                        ON s.job_key_a = l.job_key_a AND s.job_key_b = l.job_key_b
                    WHERE l.job_key_a IS NULL
                        AND s.blended_score > :auto_reject
                        AND s.blended_score < :auto_match
                    ORDER BY random()
                    LIMIT :limit
                    """
                ),
                {
                    "auto_reject": thresholds_row.auto_reject_threshold,
                    "auto_match": thresholds_row.auto_match_threshold,
                    "limit": limit,
                },
            ).all()

        if not rows:
            return []

        job_keys = {row.job_key_a for row in rows} | {row.job_key_b for row in rows}
        posting_rows = conn.execute(
            text(
                f"SELECT {_POSTING_COLUMNS} FROM silver.silver__job_posting "
                "WHERE job_key = ANY(:job_keys)"
            ),
            {"job_keys": list(job_keys)},
        ).all()
        postings_by_key = {row.job_key: _posting_summary(row) for row in posting_rows}

    return [
        PairToLabel(
            scores=SimilarityScores(
                job_key_a=row.job_key_a,
                job_key_b=row.job_key_b,
                blended_score=float(row.blended_score),
                company_similarity=float(row.company_similarity),
                title_similarity=float(row.title_similarity),
                description_similarity=float(row.description_similarity),
                location_similarity=float(row.location_similarity),
                date_diff_days=float(row.date_diff_days),
                salary_similarity=float(row.salary_similarity),
            ),
            posting_a=postings_by_key[row.job_key_a],
            posting_b=postings_by_key[row.job_key_b],
        )
        for row in rows
    ]


@router.post("/dedup/labels")
def post_label(
    request: LabelRequest,
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, str]:
    """Record (or update) a human's label for one candidate pair.

    Args:
        request: The label being recorded.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"status": "ok"}`.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dedup.pair_labels (
                    job_key_a, job_key_b, label, labeled_by
                ) VALUES (
                    :job_key_a, :job_key_b, :label, :labeled_by
                )
                ON CONFLICT (job_key_a, job_key_b) DO UPDATE SET
                    label = EXCLUDED.label,
                    labeled_by = EXCLUDED.labeled_by,
                    labeled_at = now()
                """
            ),
            {
                "job_key_a": request.job_key_a,
                "job_key_b": request.job_key_b,
                "label": request.label,
                "labeled_by": request.labeled_by,
            },
        )
    return {"status": "ok"}
```

- [ ] **Step 4: Register the router**

In `apps/api/app/main.py`, add the import and registration alongside
the existing `ingest` router:

```python
from app.routers import dedup, ingest
```

```python
app.include_router(dedup.router)
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_dedup_router -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Quality gate and commit**

```bash
cd job_search
python3.11 -m black apps/api/app/routers/dedup.py apps/api/app/main.py packages/core/tests/integration/test_dedup_router.py
python3.11 -m isort apps/api/app/routers/dedup.py apps/api/app/main.py packages/core/tests/integration/test_dedup_router.py
python3.11 -m ruff check apps/api/app/routers/dedup.py apps/api/app/main.py packages/core/tests/integration/test_dedup_router.py
python3.11 -m mypy apps/api/app
```

```bash
git add apps/api/app/routers/dedup.py apps/api/app/main.py packages/core/tests/integration/test_dedup_router.py
git commit -m "feat(job_search): add GET /dedup/pairs-to-label and POST /dedup/labels"
```

---

### Task 4: `GET /dedup/calibration` and `GET|POST /dedup/thresholds`

**Files:**
- Modify: `apps/api/app/routers/dedup.py`
- Modify: `packages/core/tests/integration/test_dedup_router.py`

**Interfaces:**
- Consumes: `compute_precision_recall_curve` (Task 2), `dedup.
  pair_labels` joined with `dedup.dedup__similarity_scores`.
- Produces: `GET /dedup/calibration` (returns the full curve from every
  current label), `GET /dedup/thresholds` (the current thresholds, or
  `null`), `POST /dedup/thresholds` (records a new calibration run) —
  consumed by Task 6 (the calibration Streamlit page).

- [ ] **Step 1: Add the failing tests**

Append to `packages/core/tests/integration/test_dedup_router.py`, inside
a new test class (same file, same `setUp`/`tearDown` fixture — add a
label to the seeded pair as part of these tests since calibration needs
at least one label to produce a non-empty curve):

```python
class TestCalibrationAndThresholds(unittest.TestCase):
    """Integration tests for /dedup/calibration and /dedup/thresholds."""

    def setUp(self) -> None:
        self.owner_engine = build_engine(_OWNER_DSN)
        self.app_engine = build_engine(_APP_DSN)
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

        suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{suffix}"
        self.job_key_b = f"test-b-{suffix}"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) VALUES "
                    "(:a, :b, 'block', 1.0, 1.0, 1.0, 0.5, 0, 1.0, 0.5, "
                    "false, 0.9)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "INSERT INTO dedup.pair_labels (job_key_a, job_key_b, label) "
                    "VALUES (:a, :b, 'match')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM dedup.calibration_thresholds "
                    "WHERE calibrated_by = 'test-runner'"
                )
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.pair_labels "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
            conn.execute(
                text(
                    "DELETE FROM dedup.dedup__similarity_scores "
                    "WHERE job_key_a = :a AND job_key_b = :b"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        del app.dependency_overrides[get_app_db_engine]
        self.owner_engine.dispose()
        self.app_engine.dispose()

    def test_calibration_curve_includes_the_seeded_labeled_pair(self) -> None:
        response = self.client.get("/dedup/calibration")
        self.assertEqual(response.status_code, 200)
        curve = response.json()
        self.assertTrue(any(point["threshold"] == 0.9 for point in curve))

    def test_thresholds_is_null_before_any_calibration_run(self) -> None:
        response = self.client.get("/dedup/thresholds")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json())

    def test_posting_thresholds_makes_them_retrievable(self) -> None:
        post_response = self.client.post(
            "/dedup/thresholds",
            json={
                "auto_match_threshold": 0.9,
                "auto_reject_threshold": 0.6,
                "measured_precision": 0.97,
                "measured_recall": 0.85,
                "labeled_pair_count": 50,
                "calibrated_by": "test-runner",
            },
        )
        self.assertEqual(post_response.status_code, 200)

        get_response = self.client.get("/dedup/thresholds")
        body = get_response.json()
        self.assertEqual(body["auto_match_threshold"], 0.9)
        self.assertEqual(body["auto_reject_threshold"], 0.6)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_dedup_router.TestCalibrationAndThresholds -v
```

- [ ] **Step 3: Implement the two new endpoints**

Append to `apps/api/app/routers/dedup.py`:

```python
class ThresholdsRequest(BaseModel):
    """A newly-calibrated pair of thresholds.

    Attributes:
        auto_match_threshold: Pairs at or above this score auto-match.
        auto_reject_threshold: Pairs at or below this score auto-reject.
        measured_precision: The precision measured at auto_match_threshold.
        measured_recall: The recall measured at auto_match_threshold.
        labeled_pair_count: How many labeled pairs this calibration used.
        calibrated_by: Free-text identifier of who ran the calibration.
    """

    auto_match_threshold: float
    auto_reject_threshold: float
    measured_precision: float
    measured_recall: float
    labeled_pair_count: int
    calibrated_by: str | None = None


class ThresholdsResponse(BaseModel):
    """The current (most recent) calibration run.

    Attributes:
        auto_match_threshold: The current auto-match cutoff.
        auto_reject_threshold: The current auto-reject cutoff.
        measured_precision: The precision measured at calibration time.
        measured_recall: The recall measured at calibration time.
        labeled_pair_count: How many labeled pairs informed this run.
        calibrated_at: When this calibration was recorded.
    """

    auto_match_threshold: float
    auto_reject_threshold: float
    measured_precision: float
    measured_recall: float
    labeled_pair_count: int
    calibrated_at: datetime.datetime


class CalibrationPoint(BaseModel):
    """One point on the precision-recall curve.

    Attributes:
        threshold: The candidate auto-match cutoff.
        precision: Precision at this threshold, or None (see
            core.dedup.calibration.ThresholdMetrics).
        recall: Recall at this threshold, or None.
        predicted_match_count: How many labeled pairs would auto-match.
    """

    threshold: float
    precision: float | None
    recall: float | None
    predicted_match_count: int


@router.get("/dedup/calibration", response_model=list[CalibrationPoint])
def get_calibration(engine: Engine = Depends(get_app_db_engine)) -> list[CalibrationPoint]:
    """Compute the precision-recall curve from every current label.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        One `CalibrationPoint` per distinct blended_score among labeled
        pairs, sorted by descending threshold. Empty if no pairs are
        labeled yet.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT l.job_key_a, l.job_key_b, l.label, s.blended_score
                FROM dedup.pair_labels AS l
                INNER JOIN dedup.dedup__similarity_scores AS s
                    ON l.job_key_a = s.job_key_a AND l.job_key_b = s.job_key_b
                """
            )
        ).all()

    labeled_pairs = [
        LabeledPair(
            job_key_a=row.job_key_a,
            job_key_b=row.job_key_b,
            blended_score=float(row.blended_score),
            label=row.label,
        )
        for row in rows
    ]
    curve = compute_precision_recall_curve(labeled_pairs)
    return [
        CalibrationPoint(
            threshold=point.threshold,
            precision=point.precision,
            recall=point.recall,
            predicted_match_count=point.predicted_match_count,
        )
        for point in curve
    ]


@router.get("/dedup/thresholds", response_model=ThresholdsResponse | None)
def get_thresholds(
    engine: Engine = Depends(get_app_db_engine),
) -> ThresholdsResponse | None:
    """Return the current (most recently calibrated) thresholds.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The current `ThresholdsResponse`, or None if no calibration run
        has ever been recorded.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT auto_match_threshold, auto_reject_threshold, "
                "measured_precision, measured_recall, labeled_pair_count, "
                "calibrated_at FROM dedup.calibration_thresholds "
                "ORDER BY calibrated_at DESC LIMIT 1"
            )
        ).one_or_none()
    if row is None:
        return None
    return ThresholdsResponse(
        auto_match_threshold=float(row.auto_match_threshold),
        auto_reject_threshold=float(row.auto_reject_threshold),
        measured_precision=float(row.measured_precision),
        measured_recall=float(row.measured_recall),
        labeled_pair_count=row.labeled_pair_count,
        calibrated_at=row.calibrated_at,
    )


@router.post("/dedup/thresholds")
def post_thresholds(
    request: ThresholdsRequest,
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, str]:
    """Record a new calibration run.

    Args:
        request: The newly-calibrated thresholds and their measured
            precision/recall.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"status": "ok"}`.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dedup.calibration_thresholds (
                    auto_match_threshold, auto_reject_threshold,
                    measured_precision, measured_recall, labeled_pair_count,
                    calibrated_by
                ) VALUES (
                    :auto_match_threshold, :auto_reject_threshold,
                    :measured_precision, :measured_recall,
                    :labeled_pair_count, :calibrated_by
                )
                """
            ),
            {
                "auto_match_threshold": request.auto_match_threshold,
                "auto_reject_threshold": request.auto_reject_threshold,
                "measured_precision": request.measured_precision,
                "measured_recall": request.measured_recall,
                "labeled_pair_count": request.labeled_pair_count,
                "calibrated_by": request.calibrated_by,
            },
        )
    return {"status": "ok"}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.integration.test_dedup_router -v
```

Expected: all 7 tests PASS (4 from Task 3 + 3 new).

- [ ] **Step 5: Quality gate and commit**

```bash
cd job_search
python3.11 -m black apps/api/app/routers/dedup.py packages/core/tests/integration/test_dedup_router.py
python3.11 -m isort apps/api/app/routers/dedup.py packages/core/tests/integration/test_dedup_router.py
python3.11 -m ruff check apps/api/app/routers/dedup.py packages/core/tests/integration/test_dedup_router.py
python3.11 -m mypy apps/api/app
```

```bash
git add apps/api/app/routers/dedup.py packages/core/tests/integration/test_dedup_router.py
git commit -m "feat(job_search): add GET /dedup/calibration and GET|POST /dedup/thresholds"
```

---

### Task 5: Streamlit review queue page

**Files:**
- Create: `apps/ui/app/pages/2_Dedup_Review_Queue.py`

**Interfaces:**
- Consumes: `GET /dedup/pairs-to-label`, `POST /dedup/labels` (Task 3).

- [ ] **Step 1: Write the page**

`apps/ui/app/pages/2_Dedup_Review_Queue.py`:

```python
"""Dedup review queue — label candidate pairs as match/not-match
(PLAN.md Step 9). Runs in bootstrap mode (broad stratified sample)
until calibration_thresholds has a row, then narrows to the
auto-reject/auto-match middle band automatically — see
GET /dedup/pairs-to-label's own docstring for the exact rule.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Dedup Review Queue", layout="wide")
st.title("Dedup Review Queue")
st.write(
    "Label each pair as the same job posting or not. Before any "
    "calibration run exists, pairs are sampled broadly across the "
    "score range for the initial 50-pair calibration exercise; "
    "afterwards, only the undecided middle band shows up here."
)

_settings = get_settings()

if "dedup_pairs" not in st.session_state:
    st.session_state.dedup_pairs = []
    st.session_state.dedup_pair_index = 0

labeled_by = st.text_input("Your name (for the audit trail)", key="labeled_by")

if st.button("Load pairs") or not st.session_state.dedup_pairs:
    try:
        response = httpx.get(
            f"{_settings.api_base_url}/dedup/pairs-to-label",
            params={"limit": 50},
            timeout=30.0,
        )
        response.raise_for_status()
        st.session_state.dedup_pairs = response.json()
        st.session_state.dedup_pair_index = 0
    except httpx.HTTPError as exc:
        st.error(f"Failed to load pairs: {exc}")

pairs = st.session_state.dedup_pairs
index = st.session_state.dedup_pair_index

if not pairs:
    st.success("No pairs need labeling right now.")
elif index >= len(pairs):
    st.success(f"Done — labeled all {len(pairs)} loaded pairs. Click 'Load pairs' for more.")
else:
    pair = pairs[index]
    st.progress((index) / len(pairs), text=f"Pair {index + 1} of {len(pairs)}")

    scores = pair["scores"]
    st.subheader(f"Blended score: {scores['blended_score']:.3f}")
    score_cols = st.columns(6)
    score_cols[0].metric("Company", f"{scores['company_similarity']:.2f}")
    score_cols[1].metric("Title", f"{scores['title_similarity']:.2f}")
    score_cols[2].metric("Description", f"{scores['description_similarity']:.2f}")
    score_cols[3].metric("Location", f"{scores['location_similarity']:.2f}")
    score_cols[4].metric("Date diff (days)", f"{scores['date_diff_days']:.1f}")
    score_cols[5].metric("Salary", f"{scores['salary_similarity']:.2f}")

    col_a, col_b = st.columns(2)
    for col, posting in ((col_a, pair["posting_a"]), (col_b, pair["posting_b"])):
        with col:
            st.markdown(f"**{posting['title'] or '(no title)'}**")
            st.write(f"Company: {posting['company'] or '(none)'}")
            st.write(f"Location: {posting['location'] or '(none)'}")
            st.write(f"Posted: {posting['posted_at'] or '(unknown)'}")
            st.write(f"URL: {posting['job_url_canonical']}")
            st.text_area(
                "Description",
                value=posting["description"] or "(none)",
                height=200,
                disabled=True,
                key=f"desc_{posting['job_key']}_{index}",
            )

    def _submit_label(label: str) -> None:
        """Post the current pair's label and advance to the next one."""
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/dedup/labels",
                json={
                    "job_key_a": scores["job_key_a"],
                    "job_key_b": scores["job_key_b"],
                    "label": label,
                    "labeled_by": labeled_by or None,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            st.session_state.dedup_pair_index += 1
        except httpx.HTTPError as exc:
            st.error(f"Failed to save label: {exc}")

    button_cols = st.columns(3)
    if button_cols[0].button("✅ Same job (match)", use_container_width=True):
        _submit_label("match")
        st.rerun()
    if button_cols[1].button("❌ Different jobs (not a match)", use_container_width=True):
        _submit_label("not_match")
        st.rerun()
    if button_cols[2].button("⏭ Skip", use_container_width=True):
        st.session_state.dedup_pair_index += 1
        st.rerun()
```

- [ ] **Step 2: Start the app and confirm the page loads**

```bash
cd job_search
docker compose up -d postgres
docker compose up -d api ui
sleep 3
curl -s http://localhost:8000/health
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501
```

Expected: `{"status": "ok"}` from the API, `200` from Streamlit. Open
`http://localhost:8501/Dedup_Review_Queue` in a browser if you have
one available and confirm pairs load without a Python traceback in the
page — this project has no automated test coverage for Streamlit pages
(see this plan's Global Constraints), so this manual check is the
verification.

- [ ] **Step 3: Commit**

```bash
git add apps/ui/app/pages/2_Dedup_Review_Queue.py
git commit -m "feat(job_search): add the dedup review queue Streamlit page"
```

---

### Task 6: Streamlit calibration page

**Files:**
- Create: `apps/ui/app/pages/3_Dedup_Calibration.py`

**Interfaces:**
- Consumes: `GET /dedup/calibration`, `GET /dedup/thresholds`, `POST
  /dedup/thresholds` (Task 4).

- [ ] **Step 1: Write the page**

`apps/ui/app/pages/3_Dedup_Calibration.py`:

```python
"""Dedup calibration — view the precision-recall curve from current
labels and record chosen auto-match/auto-reject thresholds (PLAN.md
Step 9). PLAN.md is explicit that cutoffs are chosen "from the curve,
not from intuition" — this page shows the curve; the human picks the
knee.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Dedup Calibration", layout="wide")
st.title("Dedup Calibration")

_settings = get_settings()

try:
    thresholds_response = httpx.get(
        f"{_settings.api_base_url}/dedup/thresholds", timeout=10.0
    )
    thresholds_response.raise_for_status()
    current_thresholds = thresholds_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load current thresholds: {exc}")
    current_thresholds = None

if current_thresholds:
    st.info(
        f"Current: auto-match ≥ {current_thresholds['auto_match_threshold']:.3f}, "
        f"auto-reject ≤ {current_thresholds['auto_reject_threshold']:.3f} "
        f"(measured precision {current_thresholds['measured_precision']:.3f}, "
        f"recall {current_thresholds['measured_recall']:.3f}, from "
        f"{current_thresholds['labeled_pair_count']} labeled pairs, "
        f"as of {current_thresholds['calibrated_at']})"
    )
else:
    st.warning("No calibration run recorded yet — label some pairs first.")

try:
    curve_response = httpx.get(f"{_settings.api_base_url}/dedup/calibration", timeout=10.0)
    curve_response.raise_for_status()
    curve = curve_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load the calibration curve: {exc}")
    curve = []

if not curve:
    st.write("No labeled pairs yet.")
else:
    st.subheader(f"Precision/recall across {len(curve)} distinct thresholds")
    chart_data = {
        "threshold": [point["threshold"] for point in curve],
        "precision": [point["precision"] or 0.0 for point in curve],
        "recall": [point["recall"] or 0.0 for point in curve],
    }
    st.line_chart(chart_data, x="threshold", y=["precision", "recall"])
    st.dataframe(curve, use_container_width=True)

    st.subheader("Record a new calibration run")
    with st.form("record_calibration"):
        col1, col2 = st.columns(2)
        with col1:
            auto_match_threshold = st.number_input(
                "Auto-match threshold", min_value=0.0, max_value=1.0, value=0.9
            )
            measured_precision = st.number_input(
                "Measured precision at that threshold",
                min_value=0.0,
                max_value=1.0,
                value=0.95,
            )
        with col2:
            auto_reject_threshold = st.number_input(
                "Auto-reject threshold", min_value=0.0, max_value=1.0, value=0.5
            )
            measured_recall = st.number_input(
                "Measured recall at that threshold",
                min_value=0.0,
                max_value=1.0,
                value=0.8,
            )
        calibrated_by = st.text_input("Your name")
        submitted = st.form_submit_button("Save thresholds")

    if submitted:
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/dedup/thresholds",
                json={
                    "auto_match_threshold": auto_match_threshold,
                    "auto_reject_threshold": auto_reject_threshold,
                    "measured_precision": measured_precision,
                    "measured_recall": measured_recall,
                    "labeled_pair_count": len(curve),
                    "calibrated_by": calibrated_by or None,
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            st.error(f"Failed to save thresholds: {exc}")
        else:
            st.success("Thresholds saved.")
            st.rerun()
```

- [ ] **Step 2: Start the app and confirm the page loads**

```bash
cd job_search
docker compose up -d postgres api ui
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501
```

Expected: `200`. Open `http://localhost:8501/Dedup_Calibration` in a
browser if available and confirm no traceback (an empty curve with 0
labels is expected and fine at this point — no real labels exist yet).

- [ ] **Step 3: Commit**

```bash
git add apps/ui/app/pages/3_Dedup_Calibration.py
git commit -m "feat(job_search): add the dedup calibration Streamlit page"
```

---

### Task 7: Full verification (controller-run, not dispatched)

- [ ] **Step 1: Full Python quality gate and test suite**

```bash
cd job_search
python3.11 -m black --check .
python3.11 -m isort --check-only .
python3.11 -m ruff check .
python3.11 -m mypy packages/core/core apps/pipeline/app apps/api/app
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
`collection_channel` errors (unrelated to this plan).

- [ ] **Step 2: Confirm both Streamlit pages and both new API routers together**

```bash
cd job_search
docker compose up -d postgres api ui
curl -s http://localhost:8000/dedup/pairs-to-label?limit=5 | python3.11 -m json.tool | head -20
curl -s http://localhost:8000/dedup/thresholds
curl -s http://localhost:8000/dedup/calibration
```

Expected: the first call returns a JSON array (possibly empty if all
candidate pairs happen to already be excluded — check `dedup.
dedup__similarity_scores` has non-veto rows if so); the second returns
`null` (no calibration run yet); the third returns `[]` (no labels
yet).

- [ ] **Step 3: Tell the user the tool is ready**

Report to the user: the review queue and calibration tooling are live
at `http://localhost:8501` (pages "Dedup Review Queue" and "Dedup
Calibration"). Ask them to label at least 50 real pairs (sampled
broadly across the score range in bootstrap mode), then use the
Calibration page to pick thresholds from the real curve and save them.
Once that's done, a short follow-up task — not part of this plan, since
it needs real data this plan cannot produce — will read the actual
measured precision/recall from `dedup.calibration_thresholds` and
record it in the repo (`PLAN.md`'s Step 9 "Done when" requires this be
documented, per the backlog's own "Document the measured precision
figure in the repo" subtask).

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage:** `STEP-09`'s 6 subtasks: hand-labeling itself is
  explicitly out of this plan's scope (see the scope note) but the tool
  to do it is Task 5; the precision-recall plot is Task 6; setting
  cutoffs "in config" is Tasks 1+4+6 (stored in Postgres, scope-noted
  why); the review queue for the middle band is Task 5 (dual-mode, same
  page); recording `is_manual_override` is Task 1's migration column,
  always `true` for any row in this human-only-write table; documenting
  the measured precision is explicitly deferred to the post-labeling
  follow-up task described in Task 7 Step 3, since no real number
  exists until the user labels real pairs.
- **Placeholder scan:** none found — every migration, module, endpoint,
  and Streamlit page has complete, real code. The one flagged, disclosed
  exception is Task 2's synthetic (not real) test fixture, clearly
  labeled as such with its own hand-verification table, for the same
  reason Step 8's SimHash tests couldn't be hand-verified to real
  numbers: no real labeled data exists yet for either.
- **Genuine, flagged uncertainty:** the bootstrap-mode NTILE query's
  exact bucket population will shift as more postings are ingested —
  this plan's own live-queried decile numbers (from this session) are
  illustrative of *why* stratified-by-population beats a fixed-range
  split, not a guarantee that stayed frozen.
- **Type consistency:** `LabeledPair`/`ThresholdMetrics` (Task 2) field
  names match exactly how Task 4's `/dedup/calibration` endpoint
  constructs and reads them; `SimilarityScores`/`PostingSummary`'s field
  names (Task 3) match exactly what Task 5's Streamlit page reads from
  the JSON response (`scores['blended_score']`, `posting['title']`,
  etc.), checked side by side before finalising this plan.
