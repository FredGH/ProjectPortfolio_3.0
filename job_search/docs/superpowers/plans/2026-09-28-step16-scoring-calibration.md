# Step 16 — Calibrate the Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human hand-label 30 jobs as strong/maybe/no, fit the four
scoring-component weights against 20 of them, validate on the other 10,
and save the result to `scoring.weight` — replacing the equal-weight
placeholder `blend.py` uses today.

**Architecture:** One new migration (two tables), one new core module
(`calibration.py`) with pure fitting functions plus DB-backed CRUD/fit/save
functions, six new FastAPI endpoints on the existing `scoring` router, and
one new Streamlit page mirroring Step 9's dedup-calibration page structure.

**Tech Stack:** Python 3.11, SQLAlchemy Core (raw `text()` SQL, no ORM —
matches every other `core.scoring.*` module), `scipy.stats.spearmanr` for
rank correlation, FastAPI, Streamlit, `unittest` + `coverage`.

**Spec:** `docs/superpowers/specs/2026-09-28-step16-scoring-calibration-design.md`

## Global Constraints

- Per-user tables get RLS with the standard policy: `USING (user_id =
  current_setting('app.current_user_id', true)::uuid)` — see
  `db/migrations/versions/0028_create_scoring_schema.py`'s `_rls` helper,
  reused verbatim.
- `job_search_app` needs an explicit `GRANT` on every new table (and its
  `SERIAL` sequence, for `calibration_run`) — RLS alone does not grant
  access.
- Label scale is the text values `'strong'`, `'maybe'`, `'no'` — never an
  integer enum. Numeric encoding for fitting only:
  `strong=1.0, maybe=0.5, no=0.0`.
- The eligible pool for labeling/fitting is `scoring.job_score` rows with
  `hard_filter_passed = true` AND all four of `vector_similarity_score`,
  `reranker_score`, `skill_coverage_score`, `llm_fit_score` non-null.
- `split_and_fit` requires ≥30 labels (raises `ValueError` otherwise),
  samples exactly 30 via `random.Random(seed).sample(...)` (default
  `seed=0`), and splits `sample[:20]` (fit) / `sample[20:]` (holdout) —
  this exact slicing, not a second shuffle.
- Every DB access goes through `core.db.session.session_scope(engine,
  user_id=...)` — never a bare `engine.connect()`.
- All new SQL uses SQLAlchemy `text()` with named bind parameters — never
  an f-string interpolating a value (only `session_scope`'s own internal
  UUID-validated GUC line does that, and only because `SET LOCAL` can't
  bind parameters — not a pattern to repeat here).
- Add `scipy==1.17.1` to `/requirements.txt` (already an installed
  transitive dependency of `sentence-transformers`; this makes it a
  declared, not accidental, dependency). Alphabetical position: after
  `scikit-learn` if present, else by the file's existing ordering
  convention — check the file before inserting.

## Review Focus

- **Fewer than 30 eligible jobs exist to label.** `pick_labeling_candidate`
  must return `None`, not raise, once nothing eligible/unlabeled remains —
  covered in Task 4.
- **`POST /scoring/calibrate` called with fewer than 30 labels.** Must
  return `400` with a clear message, not a 500 from an uncaught
  `ValueError` — covered in Task 6.
- **Every label has the same value** (e.g. all `'strong'`). Spearman
  correlation against a constant array is mathematically undefined
  (`scipy` returns `nan`) — `_spearman_agreement` must not crash, and the
  caller must be able to detect and report this rather than silently
  showing a bogus number — covered in Task 2.
- **A labeled job's `scoring.job_score` row is gone by fit time** (e.g. a
  dbt rebuild changed job identity, or the job was deleted from the pool).
  `split_and_fit` must skip that label rather than crash — covered in
  Task 5.
- **Re-labeling a job that was already labeled.** `write_label` must
  upsert (overwrite), never create a second row or raise a PK-violation —
  covered in Task 3.

---

## File Structure

- `db/migrations/versions/0029_create_scoring_job_label_and_calibration_run.py`
  — new migration, the two tables from the spec.
- `packages/core/core/scoring/calibration.py` — new module: pure fitting
  functions, label CRUD, candidate picking, fit/save orchestration.
- `apps/api/app/routers/scoring.py` — extended with six new endpoints.
- `apps/ui/app/pages/9_Scoring_Calibration.py` — new Streamlit page.
- `requirements.txt` — add `scipy==1.17.1`.
- `packages/core/tests/integration/test_scoring_job_label.py` — new.
- `packages/core/tests/integration/test_scoring_calibration.py` — new
  (pure-function unit tests + DB-backed fit/save tests, split by
  `TestCase` class within the one file since both share the module).
- `packages/core/tests/integration/test_scoring_router_calibration.py` —
  new, router-level tests for the six new endpoints.
- `PLAN.md` — Task 8 appends the real "Measured (date): ..." note under
  Step 16, once a genuine calibration exists (see Task 8's own caveat:
  the actual 30-job hand-labeling is a human judgment call this plan
  cannot script — Task 8 proves the mechanism end-to-end against real
  data with disposable synthetic labels, then hands the real exercise to
  the project owner).

---

### Task 1: Migration — `scoring.job_label` and `scoring.calibration_run`

**Files:**
- Create: `db/migrations/versions/0029_create_scoring_job_label_and_calibration_run.py`
- Test: `packages/core/tests/integration/test_scoring_job_label_schema.py`

**Interfaces:**
- Consumes: `db.migrations.versions.0028_create_scoring_schema`'s `_rls`
  pattern (re-implement it locally in 0029 — migrations don't import each
  other in this codebase; check `0028`'s own file to confirm, it defines
  `_rls` as a private module-level function, not exported).
- Produces: tables `scoring.job_label(user_id, job_group_id, label,
  labeled_at)` and `scoring.calibration_run(id, user_id, fit_count,
  holdout_count, vector_similarity_weight, reranker_weight,
  skill_coverage_weight, llm_fit_weight, holdout_agreement,
  embedding_model, calibrated_by, calibrated_at)` — later tasks read/write
  these by exactly these column names.

- [ ] **Step 1: Find the current migration head**

```bash
cd job_search
ls db/migrations/versions/ | sort | tail -5
```

Confirm `0028_create_scoring_schema.py` is the newest (its `revision =
"0028"`). This new migration's `down_revision = "0028"`.

- [ ] **Step 2: Write the migration**

```python
"""create scoring.job_label and scoring.calibration_run (PLAN.md Step 16)

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-28

Two tables for hand-labeling and recording calibration runs:

- job_label: per-user hand labels (strong/maybe/no) on individual jobs,
  used to fit scoring.weight (RLS, since a label is a personal judgment).
- calibration_run: append-only history of every fit run's inputs and
  result (RLS, per-user — unlike dedup.calibration_thresholds, which is
  shared, scoring weights are a personal preference, not a global fact).

Both tables were deliberately NOT created in migration 0028 (see its own
docstring) — reserved for this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `scoring` schema.
    """
    op.execute(f"ALTER TABLE scoring.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON scoring.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def upgrade() -> None:
    """Create scoring.job_label and scoring.calibration_run."""
    op.create_table(
        "job_label",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "labeled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "job_group_id"),
        sa.CheckConstraint(
            "label IN ('strong', 'maybe', 'no')", name="ck_job_label_label"
        ),
        schema="scoring",
    )
    _rls("job_label")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON scoring.job_label TO job_search_app"
    )

    op.create_table(
        "calibration_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("fit_count", sa.Integer(), nullable=False),
        sa.Column("holdout_count", sa.Integer(), nullable=False),
        sa.Column("vector_similarity_weight", sa.Numeric(), nullable=False),
        sa.Column("reranker_weight", sa.Numeric(), nullable=False),
        sa.Column("skill_coverage_weight", sa.Numeric(), nullable=False),
        sa.Column("llm_fit_weight", sa.Numeric(), nullable=False),
        sa.Column("holdout_agreement", sa.Numeric(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("calibrated_by", sa.Text(), nullable=True),
        sa.Column(
            "calibrated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="scoring",
    )
    _rls("calibration_run")
    op.execute(
        "GRANT SELECT, INSERT ON scoring.calibration_run TO job_search_app"
    )
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE scoring.calibration_run_id_seq "
        "TO job_search_app"
    )


def downgrade() -> None:
    """Drop both tables."""
    op.execute(
        "REVOKE USAGE, SELECT ON SEQUENCE scoring.calibration_run_id_seq "
        "FROM job_search_app"
    )
    op.execute("REVOKE SELECT, INSERT ON scoring.calibration_run FROM job_search_app")
    op.drop_table("calibration_run", schema="scoring")
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON scoring.job_label "
        "FROM job_search_app"
    )
    op.drop_table("job_label", schema="scoring")
```

`holdout_agreement` is `nullable=True` because Review Focus item 3 (all
labels the same value) means a real run can produce an undefined
(`nan`→`None`) agreement figure — the schema must accept that, not force a
fabricated number.

- [ ] **Step 3: Run the migration**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "\dt scoring.*"
docker compose run --rm api alembic upgrade head
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "\dt scoring.*"
```

Expected: the second `\dt` output now lists `scoring.job_label` and
`scoring.calibration_run` alongside the five Step 15 tables.

- [ ] **Step 4: Write the schema test**

```python
"""Schema tests for scoring.job_label and scoring.calibration_run
(PLAN.md Step 16) — confirms RLS, grants, and constraints exist, the
same style as test_scoring_schema.py verifies migration 0028.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine, session_scope
from core.settings import get_settings


class TestJobLabelAndCalibrationRunSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture calibration schema user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.calibration_run WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_job_label_rejects_an_invalid_label_value(self) -> None:
        with self.assertRaises(Exception):
            with self.owner_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO scoring.job_label (user_id, job_group_id, label) "
                        "VALUES (:u, 'zzfixture-job-1', 'excellent')"
                    ),
                    {"u": self.user_id},
                )

    def test_job_label_is_isolated_by_rls(self) -> None:
        other_user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture other user')"
                ),
                {"id": other_user_id, "email": f"zzfixture-{other_user_id}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_label (user_id, job_group_id, label) "
                    "VALUES (:u, 'zzfixture-job-1', 'strong')"
                ),
                {"u": self.user_id},
            )
        try:
            with session_scope(self.app_engine, user_id=other_user_id) as conn:
                rows = conn.execute(
                    text("SELECT * FROM scoring.job_label")
                ).fetchall()
            self.assertEqual(rows, [])
        finally:
            with self.owner_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM app_user WHERE id = :id"), {"id": other_user_id}
                )

    def test_calibration_run_accepts_a_null_holdout_agreement(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.calibration_run "
                    "(user_id, fit_count, holdout_count, vector_similarity_weight, "
                    "reranker_weight, skill_coverage_weight, llm_fit_weight, "
                    "holdout_agreement, embedding_model) "
                    "VALUES (:u, 20, 10, 0.25, 0.25, 0.25, 0.25, NULL, "
                    "'nomic-embed-text')"
                ),
                {"u": self.user_id},
            )
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.calibration_run "
                    "WHERE user_id = :u AND holdout_agreement IS NULL"
                ),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Run the test**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_job_label_schema -v
```

Expected: 3/3 PASS.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/versions/0029_create_scoring_job_label_and_calibration_run.py \
        packages/core/tests/integration/test_scoring_job_label_schema.py
git commit -m "feat(job_search): Step 16 migration — scoring.job_label + calibration_run"
```

---

### Task 2: `calibration.py` — pure fitting functions

**Files:**
- Create: `packages/core/core/scoring/calibration.py` (this task adds only
  the pure-function section; Tasks 3-5 extend the same file)
- Test: `packages/core/tests/integration/test_scoring_calibration.py`
  (this task adds only `TestGridSearchWeights` and `TestSpearmanAgreement`;
  despite living under `tests/integration/`, these two classes make no DB
  connection — the directory is a project convention, not a signal every
  test in it needs a DB)
- Modify: `requirements.txt` — add `scipy==1.17.1`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `_COMPONENTS = ("vector_similarity", "reranker",
  "skill_coverage", "llm_fit")` (module constant, reused by Tasks 4-5);
  `_grid_search_weights(fit_rows: list[dict[str, float]], fit_labels:
  list[float]) -> dict[str, float]`; `_spearman_agreement(rows:
  list[dict[str, float]], labels: list[float], weights: dict[str, float])
  -> float | None` (returns `None`, not `nan`, when correlation is
  undefined — callers must never receive a NaN float).
  `_blend(row: dict[str, float], weights: dict[str, float]) -> float` (the
  weighted-mean helper both of the above call — same formula as
  `blend.py`'s inline computation, factored out here since this module
  needs it twice and `blend.py`'s version is inline, not importable).

- [ ] **Step 1: Add the dependency**

Open `requirements.txt`, find where `sentence-transformers==6.1.0` and
`llama-index-core==0.14.25` were added for Step 15, and add a line:

```
scipy==1.17.1
```

- [ ] **Step 2: Write the failing tests**

```python
"""Tests for core.scoring.calibration (PLAN.md Step 16).

TestGridSearchWeights and TestSpearmanAgreement exercise pure functions
with hand-built fixtures — no database connection, despite living under
tests/integration/ (a directory convention in this repo, not a promise
every test here touches a DB).
"""

from __future__ import annotations

import math
import unittest

from core.scoring.calibration import (
    _COMPONENTS,
    _blend,
    _grid_search_weights,
    _spearman_agreement,
)


class TestBlend(unittest.TestCase):
    def test_blend_is_a_weighted_mean_of_present_components(self) -> None:
        row = {
            "vector_similarity": 0.8,
            "reranker": 0.6,
            "skill_coverage": 0.4,
            "llm_fit": 0.2,
        }
        weights = {
            "vector_similarity": 0.4,
            "reranker": 0.3,
            "skill_coverage": 0.2,
            "llm_fit": 0.1,
        }
        expected = 0.8 * 0.4 + 0.6 * 0.3 + 0.4 * 0.2 + 0.2 * 0.1
        self.assertAlmostEqual(_blend(row, weights), expected)


class TestGridSearchWeights(unittest.TestCase):
    def test_finds_the_single_component_that_perfectly_predicts_the_label(
        self,
    ) -> None:
        # skill_coverage matches the label exactly; every other component
        # is constant (uninformative) across all four fixture rows — the
        # winning weight vector must put (near-)all weight on
        # skill_coverage, since only it can produce perfect rank agreement.
        fit_rows = [
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.9,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.7,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.3,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.5,
                "reranker": 0.5,
                "skill_coverage": 0.1,
                "llm_fit": 0.5,
            },
        ]
        fit_labels = [1.0, 1.0, 0.5, 0.0]
        weights = _grid_search_weights(fit_rows, fit_labels)
        self.assertEqual(set(weights), set(_COMPONENTS))
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)
        self.assertGreaterEqual(weights["skill_coverage"], 0.9)

    def test_every_returned_weight_is_a_multiple_of_0_05_and_non_negative(
        self,
    ) -> None:
        fit_rows = [
            {
                "vector_similarity": 0.9,
                "reranker": 0.1,
                "skill_coverage": 0.5,
                "llm_fit": 0.5,
            },
            {
                "vector_similarity": 0.1,
                "reranker": 0.9,
                "skill_coverage": 0.5,
                "llm_fit": 0.5,
            },
        ]
        fit_labels = [1.0, 0.0]
        weights = _grid_search_weights(fit_rows, fit_labels)
        for component in _COMPONENTS:
            w = weights[component]
            self.assertGreaterEqual(w, 0.0)
            self.assertAlmostEqual(round(w / 0.05) * 0.05, w, places=6)


class TestSpearmanAgreement(unittest.TestCase):
    def test_perfect_rank_agreement_scores_close_to_one(self) -> None:
        rows = [
            {
                "vector_similarity": 0.9,
                "reranker": 0.9,
                "skill_coverage": 0.9,
                "llm_fit": 0.9,
            },
            {
                "vector_similarity": 0.1,
                "reranker": 0.1,
                "skill_coverage": 0.1,
                "llm_fit": 0.1,
            },
        ]
        labels = [1.0, 0.0]
        weights = {c: 0.25 for c in _COMPONENTS}
        agreement = _spearman_agreement(rows, labels, weights)
        self.assertIsNotNone(agreement)
        self.assertGreater(agreement, 0.99)

    def test_a_constant_label_array_returns_none_not_nan(self) -> None:
        rows = [
            {
                "vector_similarity": 0.9,
                "reranker": 0.9,
                "skill_coverage": 0.9,
                "llm_fit": 0.9,
            },
            {
                "vector_similarity": 0.1,
                "reranker": 0.1,
                "skill_coverage": 0.1,
                "llm_fit": 0.1,
            },
        ]
        labels = [1.0, 1.0]
        weights = {c: 0.25 for c in _COMPONENTS}
        agreement = _spearman_agreement(rows, labels, weights)
        self.assertIsNone(agreement)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_calibration -v
```

Expected: FAIL / ERROR — `core.scoring.calibration` doesn't exist yet.

- [ ] **Step 4: Write the implementation**

```python
"""Weight fitting and calibration for the scoring funnel (PLAN.md Step
16). Rank correlation, not least-squares regression, is the fitting
objective: PLAN.md's own "Done when" criterion is about ranking
agreement ("top 10 by computed score substantially matches top 10 by
hand ranking"), not about predicting a label's exact numeric value.
"""

from __future__ import annotations

from itertools import product

from scipy.stats import spearmanr

_COMPONENTS = ("vector_similarity", "reranker", "skill_coverage", "llm_fit")

# Every multiple of 0.05 from 0.0 to 1.0 — the grid search's per-component
# candidate values. 1,771 four-tuples sum to 1.0 out of this grid; trivial
# to evaluate all of them (no need for a smarter optimizer at this scale).
_STEP = 0.05
_LEVELS = tuple(round(i * _STEP, 2) for i in range(int(1 / _STEP) + 1))


def _blend(row: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean of a row's components — same formula as blend.py's
    inline computation, factored out here since this module calls it
    from both the grid search and the agreement check.

    Args:
        row: Component name -> score, e.g. {"vector_similarity": 0.8, ...}.
        weights: Component name -> weight. Assumed to sum to 1.0 (the
            grid search only ever produces such vectors).

    Returns:
        The weighted-mean blended score.
    """
    return sum(row[c] * weights[c] for c in _COMPONENTS)


def _spearman_agreement(
    rows: list[dict[str, float]],
    labels: list[float],
    weights: dict[str, float],
) -> float | None:
    """Spearman rank correlation between blended scores and labels.

    Args:
        rows: One dict of component scores per job, same order as `labels`.
        labels: The numeric label (1.0/0.5/0.0) for each row, same order.
        weights: The weight vector to blend `rows` with.

    Returns:
        The Spearman correlation, or None when it is mathematically
        undefined (e.g. every label is identical, so there is no rank
        variation to correlate against) — scipy returns `nan` in that
        case; this function converts `nan` to `None` so no caller ever
        has to special-case a NaN float.
    """
    blended = [_blend(row, weights) for row in rows]
    correlation, _p_value = spearmanr(blended, labels)
    if correlation != correlation:  # NaN != NaN is the classic NaN check
        return None
    return float(correlation)


def _grid_search_weights(
    fit_rows: list[dict[str, float]], fit_labels: list[float]
) -> dict[str, float]:
    """Find the weight vector maximizing Spearman agreement on the fit set.

    Searches every 4-tuple of multiples of 0.05 in [0, 1] that sums to
    1.0. Ties are broken toward the smoothest distribution (lowest max
    single weight), then by iteration order, for full determinism.

    Args:
        fit_rows: One dict of component scores per fit-set job.
        fit_labels: The numeric label for each row, same order.

    Returns:
        The winning weight vector, one entry per component in
        `_COMPONENTS`, summing to 1.0.
    """
    best_weights: dict[str, float] | None = None
    best_score = float("-inf")
    best_max_weight = float("inf")
    for combo in product(_LEVELS, repeat=len(_COMPONENTS)):
        if abs(sum(combo) - 1.0) > 1e-9:
            continue
        weights = dict(zip(_COMPONENTS, combo))
        agreement = _spearman_agreement(fit_rows, fit_labels, weights)
        if agreement is None:
            continue
        max_weight = max(combo)
        better = agreement > best_score + 1e-9
        tied_but_smoother = (
            abs(agreement - best_score) <= 1e-9 and max_weight < best_max_weight
        )
        if better or tied_but_smoother:
            best_weights = weights
            best_score = agreement
            best_max_weight = max_weight
    if best_weights is None:
        # Every candidate produced an undefined agreement (e.g. every
        # fit_label is identical) — fall back to equal weights, the same
        # placeholder blend.py itself uses pre-calibration.
        return {c: 1.0 / len(_COMPONENTS) for c in _COMPONENTS}
    return best_weights
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_calibration -v
```

Expected: 4/4 PASS (`TestBlend`, `TestGridSearchWeights` x2,
`TestSpearmanAgreement` x2 — 5 total test methods across 3 classes).

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/scoring/calibration.py \
        packages/core/tests/integration/test_scoring_calibration.py \
        requirements.txt
git commit -m "feat(job_search): Step 16 — weight-fitting grid search + Spearman agreement"
```

---

### Task 3: `calibration.py` — label CRUD

**Files:**
- Modify: `packages/core/core/scoring/calibration.py` — add `JobLabel`
  dataclass, `read_labels`, `write_label`, `delete_label`.
- Test: `packages/core/tests/integration/test_scoring_job_label.py`

**Interfaces:**
- Consumes: `core.db.session.session_scope` (existing).
- Produces: `@dataclass(frozen=True) class JobLabel: job_group_id: str;
  label: str; labeled_at: datetime` (import `datetime` from
  `datetime`); `read_labels(engine: Engine, user_id: uuid.UUID) ->
  list[JobLabel]`; `write_label(engine: Engine, user_id: uuid.UUID,
  job_group_id: str, label: str) -> None`; `delete_label(engine: Engine,
  user_id: uuid.UUID, job_group_id: str) -> None`. Tasks 4-6 call all
  three.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for core.scoring.calibration's label CRUD (PLAN.md Step 16).
Uses live Postgres via zzfixture-scoped rows, same pattern as
test_scoring_hard_filters.py.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.calibration import delete_label, read_labels, write_label
from core.settings import get_settings


class TestJobLabelCrud(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture label crud user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_write_then_read_round_trips_the_label(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "strong")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].job_group_id, "zzfixture-job-1")
        self.assertEqual(labels[0].label, "strong")

    def test_writing_the_same_job_again_overwrites_not_duplicates(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "strong")
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "no")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].label, "no")

    def test_delete_removes_the_label(self) -> None:
        write_label(self.app_engine, self.user_id, "zzfixture-job-1", "maybe")
        delete_label(self.app_engine, self.user_id, "zzfixture-job-1")
        labels = read_labels(self.app_engine, self.user_id)
        self.assertEqual(labels, [])

    def test_read_labels_returns_empty_list_when_none_exist(self) -> None:
        self.assertEqual(read_labels(self.app_engine, self.user_id), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_job_label -v
```

Expected: FAIL/ERROR — `read_labels`/`write_label`/`delete_label` don't
exist yet.

- [ ] **Step 3: Add the implementation to `calibration.py`**

Append to `packages/core/core/scoring/calibration.py` (after the pure
functions from Task 2):

```python
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from core.db.session import session_scope


@dataclass(frozen=True)
class JobLabel:
    """One hand-labeled job.

    Attributes:
        job_group_id: The labeled job's identity.
        label: "strong" | "maybe" | "no".
        labeled_at: When this label was last written.
    """

    job_group_id: str
    label: str
    labeled_at: datetime


def read_labels(engine: Engine, user_id: uuid.UUID) -> list[JobLabel]:
    """Read every label this user has recorded, newest first.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose labels to read.

    Returns:
        Every `JobLabel`, ordered by `labeled_at` descending. Empty list
        if this user has never labeled anything.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT job_group_id, label, labeled_at FROM scoring.job_label "
                "WHERE user_id = :user_id ORDER BY labeled_at DESC"
            ),
            {"user_id": user_id},
        ).all()
    return [JobLabel(row.job_group_id, row.label, row.labeled_at) for row in rows]


_UPSERT_LABEL = text(
    "INSERT INTO scoring.job_label (user_id, job_group_id, label, labeled_at) "
    "VALUES (:user_id, :job_group_id, :label, now()) "
    "ON CONFLICT (user_id, job_group_id) DO UPDATE SET "
    "label = EXCLUDED.label, labeled_at = now()"
)


def write_label(
    engine: Engine, user_id: uuid.UUID, job_group_id: str, label: str
) -> None:
    """Create or overwrite one job's label.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose label this is.
        job_group_id: The job being labeled.
        label: "strong" | "maybe" | "no".
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            _UPSERT_LABEL,
            {"user_id": user_id, "job_group_id": job_group_id, "label": label},
        )


def delete_label(engine: Engine, user_id: uuid.UUID, job_group_id: str) -> None:
    """Remove one job's label, if it exists.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose label to remove.
        job_group_id: The job to un-label.
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "DELETE FROM scoring.job_label "
                "WHERE user_id = :user_id AND job_group_id = :job_group_id"
            ),
            {"user_id": user_id, "job_group_id": job_group_id},
        )
```

Note: move the `import uuid`, `from dataclasses import dataclass`, `from
datetime import datetime`, `from sqlalchemy import Engine, text`, and
`from core.db.session import session_scope` lines to the top of the file
(with the other imports from Task 2's `from itertools import product` and
`from scipy.stats import spearmanr`), not repeated inline — Python only
needs one copy of each import per file. Follow the project's existing
import-order convention (standard library, then third-party, then local —
see `.claude/rules/python-style.md`).

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_job_label -v
```

Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/scoring/calibration.py \
        packages/core/tests/integration/test_scoring_job_label.py
git commit -m "feat(job_search): Step 16 — job_label read/write/delete"
```

---

### Task 4: `calibration.py` — `pick_labeling_candidate`

**Files:**
- Modify: `packages/core/core/scoring/calibration.py` — add
  `LabelCandidate` dataclass and `pick_labeling_candidate`.
- Test: `packages/core/tests/integration/test_scoring_job_label.py` —
  add a new `TestPickLabelingCandidate` class to the same file (it needs
  `gold.dim_job`/`scoring.job_score` fixtures, the same insert/cleanup
  style `test_scoring_hard_filters.py` uses).

**Interfaces:**
- Consumes: `_COMPONENTS` (Task 2), `write_label`/`read_labels` (Task 3).
- Produces: `@dataclass(frozen=True) class LabelCandidate: job_group_id:
  str; title: str; company: str; location: str; engagement_type: str;
  description: str; vector_similarity_score: float; reranker_score:
  float; skill_coverage_score: float; llm_fit_score: float;
  llm_rationale: str | None; llm_missing_skills: list[str] | None;
  llm_stretch_flag: bool | None`; `pick_labeling_candidate(engine: Engine,
  user_id: uuid.UUID) -> LabelCandidate | None`. Task 7 (API) calls this.

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/integration/test_scoring_job_label.py`:

```python
import random

from core.scoring.calibration import pick_labeling_candidate


class TestPickLabelingCandidate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture candidate user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'zzfixture-cand-%'")
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _insert_job(self, job_group_id: str) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )

    def _insert_score(
        self, job_group_id: str, *, all_four_present: bool = True
    ) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score) "
                    "VALUES (:u, :j, true, 0.5, 0.5, 0.5, :llm, 0.5)"
                ),
                {"u": self.user_id, "j": job_group_id, "llm": 70 if all_four_present else None},
            )

    def test_never_returns_a_job_missing_any_of_the_four_components(self) -> None:
        self._insert_job("zzfixture-cand-incomplete")
        self._insert_score("zzfixture-cand-incomplete", all_four_present=False)
        candidate = pick_labeling_candidate(self.app_engine, self.user_id)
        self.assertIsNone(candidate)

    def test_never_returns_an_already_labeled_job(self) -> None:
        self._insert_job("zzfixture-cand-labeled")
        self._insert_score("zzfixture-cand-labeled")
        write_label(self.app_engine, self.user_id, "zzfixture-cand-labeled", "strong")
        candidate = pick_labeling_candidate(self.app_engine, self.user_id)
        self.assertIsNone(candidate)

    def test_returns_an_eligible_unlabeled_job_with_full_context(self) -> None:
        self._insert_job("zzfixture-cand-eligible")
        self._insert_score("zzfixture-cand-eligible")
        candidate = pick_labeling_candidate(self.app_engine, self.user_id)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.job_group_id, "zzfixture-cand-eligible")
        self.assertEqual(candidate.title, "zzfixture role")
        self.assertEqual(candidate.vector_similarity_score, 0.5)
        self.assertEqual(candidate.llm_fit_score, 70)


if __name__ == "__main__":
    unittest.main()
```

Note: `write_label` is already imported at the top of this test file from
Task 3 — do not add a duplicate import.

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_job_label -v
```

Expected: FAIL/ERROR on the three new tests — `pick_labeling_candidate`
doesn't exist yet.

- [ ] **Step 3: Add the implementation**

Append to `packages/core/core/scoring/calibration.py`:

```python
@dataclass(frozen=True)
class LabelCandidate:
    """One job to show a human for hand-labeling, with enough context to
    judge fit confidently.

    Attributes:
        job_group_id: The candidate job's identity.
        title: `gold.dim_job.title_for_display`.
        company: The employer name.
        location: The posting's location string.
        engagement_type: permanent/contract/ftc/interim.
        description: The full job description.
        vector_similarity_score: Stage-2 component score.
        reranker_score: Stage-2 (cross-encoder) component score.
        skill_coverage_score: Stage-3 component score.
        llm_fit_score: Stage-4 component score, 0-100.
        llm_rationale: The LLM re-rank's free-text rationale, if present.
        llm_missing_skills: Skills the LLM flagged as missing, if present.
        llm_stretch_flag: Whether the LLM flagged this as a stretch role.
    """

    job_group_id: str
    title: str
    company: str
    location: str
    engagement_type: str
    description: str
    vector_similarity_score: float
    reranker_score: float
    skill_coverage_score: float
    llm_fit_score: float
    llm_rationale: str | None
    llm_missing_skills: list[str] | None
    llm_stretch_flag: bool | None


_SELECT_ELIGIBLE_CANDIDATES = text(
    "SELECT s.job_group_id, j.title_for_display, j.company, j.location, "
    "j.engagement_type, j.description, s.vector_similarity_score, "
    "s.reranker_score, s.skill_coverage_score, s.llm_fit_score, "
    "s.llm_rationale, s.llm_missing_skills, s.llm_stretch_flag "
    "FROM scoring.job_score s "
    "JOIN gold.dim_job j ON j.job_group_id = s.job_group_id "
    "WHERE s.user_id = :user_id AND s.hard_filter_passed = true "
    "AND s.vector_similarity_score IS NOT NULL "
    "AND s.reranker_score IS NOT NULL "
    "AND s.skill_coverage_score IS NOT NULL "
    "AND s.llm_fit_score IS NOT NULL "
    "AND s.job_group_id NOT IN ("
    "SELECT job_group_id FROM scoring.job_label WHERE user_id = :user_id)"
)


def pick_labeling_candidate(
    engine: Engine, user_id: uuid.UUID
) -> LabelCandidate | None:
    """Pick a random eligible, not-yet-labeled job for hand-labeling.

    Eligible means every one of the four scoring components is present
    (a job missing one can't inform that component's weight) and
    `hard_filter_passed`. Picks uniformly at random among every eligible,
    unlabeled candidate — with at most 50 jobs ever reaching all-four-
    present (the LLM-rerank stage's own cap), stratifying further into
    score bands is unnecessary complexity for a pool this small.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose candidate pool to pick from.

    Returns:
        One `LabelCandidate`, or None if nothing eligible remains unlabeled.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            _SELECT_ELIGIBLE_CANDIDATES, {"user_id": user_id}
        ).all()
    if not rows:
        return None
    row = random.choice(rows)
    return LabelCandidate(
        job_group_id=row.job_group_id,
        title=row.title_for_display,
        company=row.company,
        location=row.location,
        engagement_type=row.engagement_type,
        description=row.description,
        vector_similarity_score=float(row.vector_similarity_score),
        reranker_score=float(row.reranker_score),
        skill_coverage_score=float(row.skill_coverage_score),
        llm_fit_score=float(row.llm_fit_score),
        llm_rationale=row.llm_rationale,
        llm_missing_skills=(
            list(row.llm_missing_skills) if row.llm_missing_skills else None
        ),
        llm_stretch_flag=row.llm_stretch_flag,
    )
```

Add `import random` to the top-of-file standard-library imports.

The spec's stratified-by-decile sampling is simplified here to uniform
random choice among all eligible candidates: with the LLM-rerank stage
capping the whole pool at 50 jobs (and hard-filter/all-four-present
narrowing it further), decile stratification over what's typically a
pool of a few dozen rows adds real complexity for no measurable diversity
benefit at this scale. Ruling made during planning, not left to the
implementer to discover mid-task.

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_job_label -v
```

Expected: 7/7 PASS (4 from Task 3 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/scoring/calibration.py \
        packages/core/tests/integration/test_scoring_job_label.py
git commit -m "feat(job_search): Step 16 — pick_labeling_candidate"
```

---

### Task 5: `calibration.py` — `split_and_fit` and `save_calibration`

**Files:**
- Modify: `packages/core/core/scoring/calibration.py` — add
  `CalibrationPreview` dataclass, `split_and_fit`, `save_calibration`.
- Modify: `packages/core/tests/integration/test_scoring_calibration.py` —
  add `TestSplitAndFit` and `TestSaveCalibration` classes (DB-backed,
  unlike Task 2's two classes in the same file).

**Interfaces:**
- Consumes: `_COMPONENTS`, `_grid_search_weights`, `_spearman_agreement`
  (Task 2); `read_labels` (Task 3); `session_scope` (existing).
- Produces: `@dataclass(frozen=True) class CalibrationPreview:
  fit_count: int; holdout_count: int; weights: dict[str, float];
  holdout_agreement: float | None; embedding_model: str`;
  `split_and_fit(engine: Engine, user_id: uuid.UUID, seed: int = 0) ->
  CalibrationPreview` (raises `ValueError` if fewer than 30 labels exist);
  `save_calibration(engine: Engine, user_id: uuid.UUID, preview:
  CalibrationPreview, calibrated_by: str | None = None) -> None`. Task 6
  (API) calls both.

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/integration/test_scoring_calibration.py`:

```python
from tests.integration.skills_fixtures import live_owner_engine

from core.db.session import build_engine
from core.scoring.calibration import (
    save_calibration,
    split_and_fit,
    write_label,
)
from core.settings import get_settings


class TestSplitAndFit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture split_and_fit user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.calibration_run WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'zzfixture-fit-%'")
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _insert_labeled_job(self, index: int, label: str) -> None:
        job_group_id = f"zzfixture-fit-{index}"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score, "
                    "embedding_model) "
                    "VALUES (:u, :j, true, :s, :s, :s, :llm, :s, 'nomic-embed-text')"
                ),
                {
                    "u": self.user_id,
                    "j": job_group_id,
                    # Score rises with index so labels correlate with score
                    # — a fit run against this fixture set should find a
                    # sane, non-degenerate weight vector.
                    "s": round(0.1 + 0.02 * index, 4),
                    "llm": round((0.1 + 0.02 * index) * 100, 2),
                },
            )
        write_label(self.app_engine, self.user_id, job_group_id, label)

    def test_raises_below_30_labels(self) -> None:
        for i in range(29):
            self._insert_labeled_job(i, "maybe")
        with self.assertRaises(ValueError):
            split_and_fit(self.app_engine, self.user_id)

    def test_splits_30_labels_into_20_fit_and_10_holdout_with_no_overlap(
        self,
    ) -> None:
        for i in range(30):
            label = "strong" if i >= 20 else ("maybe" if i >= 10 else "no")
            self._insert_labeled_job(i, label)
        preview = split_and_fit(self.app_engine, self.user_id)
        self.assertEqual(preview.fit_count, 20)
        self.assertEqual(preview.holdout_count, 10)
        self.assertEqual(set(preview.weights), {
            "vector_similarity", "reranker", "skill_coverage", "llm_fit"
        })
        self.assertAlmostEqual(sum(preview.weights.values()), 1.0, places=6)
        self.assertEqual(preview.embedding_model, "nomic-embed-text")

    def test_same_seed_produces_the_same_split_every_time(self) -> None:
        for i in range(30):
            label = "strong" if i >= 20 else ("maybe" if i >= 10 else "no")
            self._insert_labeled_job(i, label)
        preview_a = split_and_fit(self.app_engine, self.user_id, seed=0)
        preview_b = split_and_fit(self.app_engine, self.user_id, seed=0)
        self.assertEqual(preview_a.weights, preview_b.weights)
        self.assertEqual(preview_a.holdout_agreement, preview_b.holdout_agreement)


class TestSaveCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture save_calibration user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.calibration_run WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _preview(self) -> "CalibrationPreview":
        from core.scoring.calibration import CalibrationPreview

        return CalibrationPreview(
            fit_count=20,
            holdout_count=10,
            weights={
                "vector_similarity": 0.4,
                "reranker": 0.3,
                "skill_coverage": 0.2,
                "llm_fit": 0.1,
            },
            holdout_agreement=0.8,
            embedding_model="nomic-embed-text",
        )

    def test_writes_one_weight_row_per_component(self) -> None:
        save_calibration(self.app_engine, self.user_id, self._preview())
        with self.app_engine.connect() as conn:
            from core.db.session import session_scope

        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            rows = conn.execute(
                text(
                    "SELECT component, weight FROM scoring.weight "
                    "WHERE user_id = :u"
                ),
                {"u": self.user_id},
            ).all()
        self.assertEqual(len(rows), 4)
        by_component = {r.component: float(r.weight) for r in rows}
        self.assertAlmostEqual(by_component["vector_similarity"], 0.4)

    def test_re_saving_upserts_weights_rather_than_duplicating(self) -> None:
        save_calibration(self.app_engine, self.user_id, self._preview())
        save_calibration(self.app_engine, self.user_id, self._preview())
        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            count = conn.execute(
                text("SELECT count(*) FROM scoring.weight WHERE user_id = :u"),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 4)

    def test_appends_one_calibration_run_row_per_save(self) -> None:
        save_calibration(self.app_engine, self.user_id, self._preview())
        save_calibration(self.app_engine, self.user_id, self._preview())
        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM scoring.calibration_run WHERE user_id = :u"
                ),
                {"u": self.user_id},
            ).scalar_one()
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
```

Remove the stray `with self.app_engine.connect() as conn:` line inside
`test_writes_one_weight_row_per_component` before running — it was left
in mid-edit; the `session_scope` block right below it is the real query.
Also move `from core.db.session import session_scope` and `from
core.scoring.calibration import CalibrationPreview` to the file's top-level
imports rather than importing inside methods, for consistency with the
rest of the file.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_calibration -v
```

Expected: FAIL/ERROR on the new tests — `split_and_fit`/`save_calibration`
don't exist yet.

- [ ] **Step 3: Add the implementation**

Append to `packages/core/core/scoring/calibration.py` (`random` is
already imported at the top of the file, added in Task 4):

```python
@dataclass(frozen=True)
class CalibrationPreview:
    """The result of a fit run, not yet persisted.

    Attributes:
        fit_count: How many labeled jobs were used to fit the weights
            (always 20 for a normal run, but see the Review Focus note on
            labels whose job_score row has since disappeared).
        holdout_count: How many labeled jobs were held out and never used
            for fitting (always 10 for a normal run, same caveat).
        weights: Component name -> fitted weight, summing to 1.0.
        holdout_agreement: Spearman correlation between the fitted-weight
            blended score and the numeric label, computed on the holdout
            set only. None when undefined (e.g. every holdout label is
            identical).
        embedding_model: The embedding model in use for the fit-set jobs
            at fit time — recorded so a later embedding-model change can
            be detected as making this calibration stale.
    """

    fit_count: int
    holdout_count: int
    weights: dict[str, float]
    holdout_agreement: float | None
    embedding_model: str


_SELECT_LABELED_JOB_SCORES = text(
    "SELECT l.job_group_id, l.label, s.vector_similarity_score, "
    "s.reranker_score, s.skill_coverage_score, s.llm_fit_score, "
    "s.embedding_model "
    "FROM scoring.job_label l "
    "JOIN scoring.job_score s "
    "ON s.user_id = l.user_id AND s.job_group_id = l.job_group_id "
    "WHERE l.user_id = :user_id"
)

_LABEL_TO_NUMERIC = {"strong": 1.0, "maybe": 0.5, "no": 0.0}


def split_and_fit(
    engine: Engine, user_id: uuid.UUID, seed: int = 0
) -> CalibrationPreview:
    """Sample 30 labeled jobs, split 20 fit / 10 holdout, fit weights,
    and measure holdout agreement. Writes nothing.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose labels/scores to fit against.
        seed: Random seed for the 30-of-N sample and the fit/holdout
            split — the same seed always produces the same split for the
            same label set, so a re-run is reproducible.

    Returns:
        The fit result, ready either to inspect (a preview) or persist
        via `save_calibration`.

    Raises:
        ValueError: If fewer than 30 labels have a matching
            `scoring.job_score` row (a label whose job has since dropped
            out of the scored pool doesn't count — see the module's
            Review Focus note on a vanished job_score row).
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(_SELECT_LABELED_JOB_SCORES, {"user_id": user_id}).all()
    if len(rows) < 30:
        raise ValueError(
            f"split_and_fit needs at least 30 labeled jobs with a current "
            f"score, have {len(rows)}"
        )
    sample = random.Random(seed).sample(rows, 30)
    fit_rows_raw, holdout_rows_raw = sample[:20], sample[20:]

    def _to_component_row(row) -> dict[str, float]:
        return {
            "vector_similarity": float(row.vector_similarity_score),
            "reranker": float(row.reranker_score),
            "skill_coverage": float(row.skill_coverage_score),
            "llm_fit": float(row.llm_fit_score) / 100.0,
        }

    fit_rows = [_to_component_row(r) for r in fit_rows_raw]
    fit_labels = [_LABEL_TO_NUMERIC[r.label] for r in fit_rows_raw]
    holdout_rows = [_to_component_row(r) for r in holdout_rows_raw]
    holdout_labels = [_LABEL_TO_NUMERIC[r.label] for r in holdout_rows_raw]

    weights = _grid_search_weights(fit_rows, fit_labels)
    holdout_agreement = _spearman_agreement(holdout_rows, holdout_labels, weights)

    return CalibrationPreview(
        fit_count=len(fit_rows),
        holdout_count=len(holdout_rows),
        weights=weights,
        holdout_agreement=holdout_agreement,
        embedding_model=sample[0].embedding_model,
    )


_UPSERT_WEIGHT = text(
    "INSERT INTO scoring.weight (user_id, component, weight, fitted_at) "
    "VALUES (:user_id, :component, :weight, now()) "
    "ON CONFLICT (user_id, component) DO UPDATE SET "
    "weight = EXCLUDED.weight, fitted_at = now()"
)
_INSERT_CALIBRATION_RUN = text(
    "INSERT INTO scoring.calibration_run (user_id, fit_count, holdout_count, "
    "vector_similarity_weight, reranker_weight, skill_coverage_weight, "
    "llm_fit_weight, holdout_agreement, embedding_model, calibrated_by) "
    "VALUES (:user_id, :fit_count, :holdout_count, :vector_similarity_weight, "
    ":reranker_weight, :skill_coverage_weight, :llm_fit_weight, "
    ":holdout_agreement, :embedding_model, :calibrated_by)"
)


def save_calibration(
    engine: Engine,
    user_id: uuid.UUID,
    preview: CalibrationPreview,
    calibrated_by: str | None = None,
) -> None:
    """Persist a previewed calibration: upsert scoring.weight and append
    a scoring.calibration_run history row, in one transaction.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose weights to save.
        preview: The result of a prior `split_and_fit` call.
        calibrated_by: Optional free-text attribution (mirrors
            `dedup.calibration_thresholds.calibrated_by`).
    """
    with session_scope(engine, user_id=user_id) as conn:
        for component, weight in preview.weights.items():
            conn.execute(
                _UPSERT_WEIGHT,
                {"user_id": user_id, "component": component, "weight": weight},
            )
        conn.execute(
            _INSERT_CALIBRATION_RUN,
            {
                "user_id": user_id,
                "fit_count": preview.fit_count,
                "holdout_count": preview.holdout_count,
                "vector_similarity_weight": preview.weights["vector_similarity"],
                "reranker_weight": preview.weights["reranker"],
                "skill_coverage_weight": preview.weights["skill_coverage"],
                "llm_fit_weight": preview.weights["llm_fit"],
                "holdout_agreement": preview.holdout_agreement,
                "embedding_model": preview.embedding_model,
                "calibrated_by": calibrated_by,
            },
        )
```

`random` is already imported at the top of the file (added in Task 4) —
no new import needed for `random.Random(seed).sample(...)`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_calibration -v
```

Expected: all tests in the file PASS (Task 2's 5 + this task's 6 = 11).

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/scoring/calibration.py \
        packages/core/tests/integration/test_scoring_calibration.py
git commit -m "feat(job_search): Step 16 — split_and_fit + save_calibration"
```

---

### Task 6: API endpoints

**Files:**
- Modify: `apps/api/app/routers/scoring.py` — add six endpoints.
- Test: `packages/core/tests/integration/test_scoring_router_calibration.py`

**Interfaces:**
- Consumes: everything from `core.scoring.calibration` (Tasks 2-5);
  `get_current_user_id`, `get_app_db_engine` (existing, same as the rest
  of `scoring.py`).
- Produces: the six routes below. Task 7 (UI) calls all of them by URL.

- [ ] **Step 1: Write the failing tests**

```python
"""Router tests for the Step 16 calibration endpoints. Uses the real
app with Step 22a's DEV_USER_ID-free override bypassed via
dependency_overrides, the same pattern test_api_whoami.py's second test
uses — a real ASGI request, not a mocked router.
"""

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

from core.db.session import build_engine, get_current_user_id  # noqa: E402
from core.scoring.calibration import write_label  # noqa: E402
from core.settings import get_settings  # noqa: E402


class TestCalibrationRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture router user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        self.client = TestClient(app)

    def tearDown(self) -> None:
        del app.dependency_overrides[get_current_user_id]
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.calibration_run WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id LIKE 'zzfixture-router-%'")
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _insert_labeled_job(self, index: int, label: str) -> None:
        job_group_id = f"zzfixture-router-{index}"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score, "
                    "embedding_model) "
                    "VALUES (:u, :j, true, :s, :s, :s, :llm, :s, 'nomic-embed-text')"
                ),
                {
                    "u": self.user_id,
                    "j": job_group_id,
                    "s": round(0.1 + 0.02 * index, 4),
                    "llm": round((0.1 + 0.02 * index) * 100, 2),
                },
            )
        write_label(self.app_engine, self.user_id, job_group_id, label)

    def test_labeling_candidate_returns_204_when_none_eligible(self) -> None:
        response = self.client.get("/scoring/labeling-candidate")
        self.assertEqual(response.status_code, 204)

    def test_labeling_candidate_returns_an_eligible_job(self) -> None:
        self._insert_labeled_job(0, "strong")  # already labeled — excluded
        job_group_id = "zzfixture-router-unlabeled"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score) "
                    "VALUES (:u, :j, true, 0.5, 0.5, 0.5, 60, 0.5)"
                ),
                {"u": self.user_id, "j": job_group_id},
            )
        response = self.client.get("/scoring/labeling-candidate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_group_id"], job_group_id)

    def test_put_label_then_get_labels_round_trips(self) -> None:
        response = self.client.put(
            "/scoring/labels/zzfixture-router-put", json={"label": "maybe"}
        )
        self.assertEqual(response.status_code, 200)
        labels = self.client.get("/scoring/labels").json()
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["label"], "maybe")

    def test_delete_label_removes_it(self) -> None:
        self.client.put(
            "/scoring/labels/zzfixture-router-del", json={"label": "no"}
        )
        response = self.client.delete("/scoring/labels/zzfixture-router-del")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/scoring/labels").json(), [])

    def test_calibrate_returns_400_below_30_labels(self) -> None:
        self._insert_labeled_job(0, "strong")
        response = self.client.post("/scoring/calibrate")
        self.assertEqual(response.status_code, 400)

    def test_calibrate_then_save_then_history(self) -> None:
        for i in range(30):
            label = "strong" if i >= 20 else ("maybe" if i >= 10 else "no")
            self._insert_labeled_job(i, label)
        preview_response = self.client.post("/scoring/calibrate")
        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.json()
        self.assertEqual(preview["fit_count"], 20)
        self.assertEqual(preview["holdout_count"], 10)

        save_response = self.client.post(
            "/scoring/calibration-runs",
            json={"preview": preview, "calibrated_by": "zzfixture tester"},
        )
        self.assertEqual(save_response.status_code, 200)

        history = self.client.get("/scoring/calibration-runs").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["calibrated_by"], "zzfixture tester")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_router_calibration -v
```

Expected: FAIL/ERROR — the new routes (404) don't exist yet.

- [ ] **Step 3: Add the endpoints**

Append to `apps/api/app/routers/scoring.py` (add these imports to the
existing import block at the top, alongside the existing
`UserPreference`/`read_preference`/`write_preference` import):

```python
from fastapi import HTTPException, Response

from core.scoring.calibration import (
    CalibrationPreview,
    JobLabel,
    LabelCandidate,
    delete_label,
    read_labels,
    save_calibration,
    split_and_fit,
    write_label,
)
```

Then add:

```python
class LabelCandidateModel(BaseModel):
    """Response body for GET /scoring/labeling-candidate."""

    job_group_id: str
    title: str
    company: str
    location: str
    engagement_type: str
    description: str
    vector_similarity_score: float
    reranker_score: float
    skill_coverage_score: float
    llm_fit_score: float
    llm_rationale: str | None
    llm_missing_skills: list[str] | None
    llm_stretch_flag: bool | None


class JobLabelModel(BaseModel):
    """Response body for one recorded label."""

    job_group_id: str
    label: str
    labeled_at: str


class LabelBody(BaseModel):
    """Request body for PUT /scoring/labels/{job_group_id}."""

    label: str


class CalibrationPreviewModel(BaseModel):
    """Request/response body for a fit preview."""

    fit_count: int
    holdout_count: int
    weights: dict[str, float]
    holdout_agreement: float | None
    embedding_model: str


class SaveCalibrationBody(BaseModel):
    """Request body for POST /scoring/calibration-runs."""

    preview: CalibrationPreviewModel
    calibrated_by: str | None = None


class CalibrationRunModel(BaseModel):
    """Response body for one saved calibration run."""

    fit_count: int
    holdout_count: int
    weights: dict[str, float]
    holdout_agreement: float | None
    embedding_model: str
    calibrated_by: str | None
    calibrated_at: str


@router.get("/scoring/labeling-candidate")
def get_labeling_candidate(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> LabelCandidateModel | Response:
    """Return one eligible, unlabeled job to hand-label, or 204 if none.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The candidate, or an empty 204 response when nothing eligible
        remains unlabeled.
    """
    from core.scoring.calibration import pick_labeling_candidate

    candidate = pick_labeling_candidate(engine, user_id)
    if candidate is None:
        return Response(status_code=204)
    return LabelCandidateModel(**candidate.__dict__)


@router.get("/scoring/labels", response_model=list[JobLabelModel])
def get_labels(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[JobLabelModel]:
    """Return every label this user has recorded, newest first.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every recorded label.
    """
    labels = read_labels(engine, user_id)
    return [
        JobLabelModel(
            job_group_id=label.job_group_id,
            label=label.label,
            labeled_at=label.labeled_at.isoformat(),
        )
        for label in labels
    ]


@router.put("/scoring/labels/{job_group_id}", response_model=JobLabelModel)
def put_label(
    job_group_id: str,
    body: LabelBody,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> JobLabelModel:
    """Create or overwrite one job's label.

    Args:
        job_group_id: The job being labeled.
        body: The label to store.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The label as stored.
    """
    write_label(engine, user_id, job_group_id, body.label)
    [saved] = [
        label for label in read_labels(engine, user_id)
        if label.job_group_id == job_group_id
    ]
    return JobLabelModel(
        job_group_id=saved.job_group_id,
        label=saved.label,
        labeled_at=saved.labeled_at.isoformat(),
    )


@router.delete("/scoring/labels/{job_group_id}")
def delete_label_endpoint(
    job_group_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> Response:
    """Remove one job's label.

    Args:
        job_group_id: The job to un-label.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        An empty 204 response.
    """
    delete_label(engine, user_id, job_group_id)
    return Response(status_code=204)


@router.post("/scoring/calibrate", response_model=CalibrationPreviewModel)
def calibrate(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> CalibrationPreviewModel:
    """Preview a fit run against this user's current labels. Writes nothing.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The fit preview.

    Raises:
        fastapi.HTTPException: 400, when fewer than 30 labels exist.
    """
    try:
        preview = split_and_fit(engine, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CalibrationPreviewModel(
        fit_count=preview.fit_count,
        holdout_count=preview.holdout_count,
        weights=preview.weights,
        holdout_agreement=preview.holdout_agreement,
        embedding_model=preview.embedding_model,
    )


@router.post("/scoring/calibration-runs", response_model=CalibrationRunModel)
def save_calibration_run(
    body: SaveCalibrationBody,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> CalibrationRunModel:
    """Persist a previewed calibration.

    Args:
        body: The preview to save (as returned by POST /scoring/calibrate)
            plus optional attribution.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The saved run, read back from history.
    """
    preview = CalibrationPreview(
        fit_count=body.preview.fit_count,
        holdout_count=body.preview.holdout_count,
        weights=body.preview.weights,
        holdout_agreement=body.preview.holdout_agreement,
        embedding_model=body.preview.embedding_model,
    )
    save_calibration(engine, user_id, preview, calibrated_by=body.calibrated_by)
    [latest] = _read_calibration_history(engine, user_id)[:1]
    return latest


def _read_calibration_history(
    engine: Engine, user_id: uuid.UUID
) -> list[CalibrationRunModel]:
    """Read this user's calibration-run history, newest first.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose history to read.

    Returns:
        Every saved run, newest first.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT fit_count, holdout_count, vector_similarity_weight, "
                "reranker_weight, skill_coverage_weight, llm_fit_weight, "
                "holdout_agreement, embedding_model, calibrated_by, calibrated_at "
                "FROM scoring.calibration_run WHERE user_id = :user_id "
                "ORDER BY calibrated_at DESC"
            ),
            {"user_id": user_id},
        ).all()
    return [
        CalibrationRunModel(
            fit_count=row.fit_count,
            holdout_count=row.holdout_count,
            weights={
                "vector_similarity": float(row.vector_similarity_weight),
                "reranker": float(row.reranker_weight),
                "skill_coverage": float(row.skill_coverage_weight),
                "llm_fit": float(row.llm_fit_weight),
            },
            holdout_agreement=(
                float(row.holdout_agreement)
                if row.holdout_agreement is not None
                else None
            ),
            embedding_model=row.embedding_model,
            calibrated_by=row.calibrated_by,
            calibrated_at=row.calibrated_at.isoformat(),
        )
        for row in rows
    ]


@router.get("/scoring/calibration-runs", response_model=list[CalibrationRunModel])
def get_calibration_runs(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[CalibrationRunModel]:
    """Return this user's calibration-run history, newest first.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every saved run, newest first.
    """
    return _read_calibration_history(engine, user_id)
```

`_read_calibration_history` needs `session_scope` and `text` imported —
both are already available via the existing `from sqlalchemy import
Engine, text` import at the top of `scoring.py` (add `text` to it if not
already there) and a new `from core.db.session import
get_current_user_id, session_scope` (extend the existing
`get_current_user_id`-only import).

Move the local `from core.scoring.calibration import
pick_labeling_candidate` import inside `get_labeling_candidate` up to the
top-level import block added at the start of this step, alongside the
other `core.scoring.calibration` imports — a function-local import was
shown above only to keep the diff readable line-by-line; the shipped code
imports everything once, at the top of the file.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose exec -T api python -m unittest tests.integration.test_scoring_router_calibration -v
```

Expected: 6/6 PASS.

- [ ] **Step 5: Run the full existing scoring test suite for regressions**

```bash
docker compose exec -T api python -m unittest discover -s tests -p "test_scoring_*.py" -v
```

Expected: every existing scoring test still passes (no regression from
the router file's new imports/endpoints).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/scoring.py \
        packages/core/tests/integration/test_scoring_router_calibration.py
git commit -m "feat(job_search): Step 16 — calibration API endpoints"
```

---

### Task 7: UI page

**Files:**
- Create: `apps/ui/app/pages/9_Scoring_Calibration.py`

**Interfaces:**
- Consumes: every endpoint from Task 6, via `httpx` + `core.settings.get_settings().api_base_url` — same pattern as `8_Scoring_Preferences.py` and `3_Dedup_Calibration.py`.
- Produces: nothing further tasks depend on (this is the plan's last
  UI-facing task; Task 8 exercises the API directly, not this page).

This repo has no automated tests for Streamlit pages (`3_Dedup_Calibration.py`
and `8_Scoring_Preferences.py` both ship without a dedicated test file) —
this task is verified manually against the live stack, matching that
existing convention.

- [ ] **Step 1: Write the page**

```python
"""Scoring calibration — hand-label jobs, fit component weights, and
save the result (PLAN.md Step 16). Mirrors 3_Dedup_Calibration.py's
structure: a labeling flow, then a preview-before-save fit step.
"""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings

st.set_page_config(page_title="Scoring Calibration", layout="wide")
st.title("Scoring Calibration")

with st.expander("User manual"):
    st.markdown(
        """
This page calibrates how the four scoring components — vector
similarity, cross-encoder rerank, skill coverage, and LLM fit — are
weighted when blended into each job's final score. Until you calibrate,
every present component is weighted equally, which is a placeholder,
not a real preference.

**How it works:**

1. **Label 30 jobs** as `strong` / `maybe` / `no` — your honest judgment
   of how well each one fits you. Only jobs that made it all the way
   through the scoring funnel (all four components present) are shown.
2. Click **Preview calibration** once you've labeled at least 30. This
   fits weights against 20 of your labels and checks the result against
   the other 10 — labels the fitting process never saw — so the
   agreement figure reflects genuine generalization, not memorisation.
3. If the preview looks reasonable, click **Save weights** to apply it.
   Nothing is written until you do.

Saved weights take effect the next time the pipeline's `score-blend`
step runs for you.
"""
    )

_settings = get_settings()
_base = _settings.api_base_url


def _get(path: str) -> httpx.Response:
    return httpx.get(f"{_base}{path}", timeout=10.0)


def _put(path: str, json: dict) -> httpx.Response:
    return httpx.put(f"{_base}{path}", json=json, timeout=10.0)


def _delete(path: str) -> httpx.Response:
    return httpx.delete(f"{_base}{path}", timeout=10.0)


def _post(path: str, json: dict | None = None) -> httpx.Response:
    return httpx.post(f"{_base}{path}", json=json, timeout=10.0)


st.subheader("1. Hand-label jobs")

try:
    labels_response = _get("/scoring/labels")
    labels_response.raise_for_status()
    labels = labels_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load existing labels: {exc}")
    labels = []

st.progress(min(len(labels) / 30, 1.0), text=f"{len(labels)} / 30 labeled")

try:
    candidate_response = _get("/scoring/labeling-candidate")
    candidate_response.raise_for_status()
except httpx.HTTPError as exc:
    st.error(f"Failed to load a candidate: {exc}")
    candidate_response = None

if candidate_response is not None and candidate_response.status_code == 204:
    st.info("No more eligible jobs to label right now.")
elif candidate_response is not None:
    candidate = candidate_response.json()
    st.markdown(f"### {candidate['title']} — {candidate['company']}")
    st.caption(f"{candidate['location']} · {candidate['engagement_type']}")
    with st.expander("Job description"):
        st.write(candidate["description"])
    with st.expander("Current scoring signals"):
        st.write(
            {
                "vector_similarity_score": candidate["vector_similarity_score"],
                "reranker_score": candidate["reranker_score"],
                "skill_coverage_score": candidate["skill_coverage_score"],
                "llm_fit_score": candidate["llm_fit_score"],
                "llm_rationale": candidate["llm_rationale"],
                "llm_missing_skills": candidate["llm_missing_skills"],
                "llm_stretch_flag": candidate["llm_stretch_flag"],
            }
        )
    col1, col2, col3 = st.columns(3)
    for col, label in ((col1, "strong"), (col2, "maybe"), (col3, "no")):
        with col:
            if st.button(label.capitalize(), key=f"label-{label}", use_container_width=True):
                response = _put(
                    f"/scoring/labels/{candidate['job_group_id']}", {"label": label}
                )
                if response.status_code != 200:
                    st.error(f"Failed to save label: {response.text}")
                else:
                    st.rerun()

if labels:
    with st.expander(f"Already labeled ({len(labels)})"):
        for label in labels:
            col1, col2 = st.columns([4, 1])
            col1.write(f"{label['job_group_id']} — **{label['label']}**")
            if col2.button("Un-label", key=f"unlabel-{label['job_group_id']}"):
                _delete(f"/scoring/labels/{label['job_group_id']}")
                st.rerun()

st.divider()
st.subheader("2. Fit & validate")

try:
    history_response = _get("/scoring/calibration-runs")
    history_response.raise_for_status()
    history = history_response.json()
except httpx.HTTPError as exc:
    st.error(f"Failed to load calibration history: {exc}")
    history = []

if history:
    latest_embedding_model = history[0]["embedding_model"]
    st.caption(
        f"Last saved: {history[0]['calibrated_at']} "
        f"(embedding model: {latest_embedding_model})"
    )

if len(labels) < 30:
    st.info(f"Label {30 - len(labels)} more job(s) to enable fitting.")
else:
    if st.button("Preview calibration"):
        response = _post("/scoring/calibrate")
        if response.status_code != 200:
            st.error(f"Failed to preview calibration: {response.text}")
        else:
            st.session_state["calibration_preview"] = response.json()

    preview = st.session_state.get("calibration_preview")
    if preview:
        st.write("**Fitted weights:**")
        st.write(preview["weights"])
        st.write(f"Fit set: {preview['fit_count']} jobs")
        st.write(f"Holdout set: {preview['holdout_count']} jobs")
        if preview["holdout_agreement"] is None:
            st.warning(
                "Holdout agreement is undefined — every holdout label was "
                "identical, so there's no rank variation to check against. "
                "Label a wider spread of strong/maybe/no before relying on "
                "this calibration."
            )
        else:
            st.metric("Holdout agreement (Spearman)", f"{preview['holdout_agreement']:.3f}")

        if history and preview["embedding_model"] != history[0]["embedding_model"]:
            st.warning(
                f"The embedding model has changed since your last saved "
                f"calibration ({history[0]['embedding_model']} → "
                f"{preview['embedding_model']}) — PLAN.md Step 16 calls "
                f"this out explicitly: different vectors, different "
                f"distances, invalid weights. Recalibrating now is "
                f"recommended."
            )

        calibrated_by = st.text_input("Your name")
        if st.button("Save weights"):
            response = _post(
                "/scoring/calibration-runs",
                {"preview": preview, "calibrated_by": calibrated_by or None},
            )
            if response.status_code != 200:
                st.error(f"Failed to save calibration: {response.text}")
            else:
                st.success(
                    "Weights saved. They take effect next time score-blend "
                    "runs for you."
                )
                del st.session_state["calibration_preview"]
                st.rerun()

if history:
    st.write("**Calibration history:**")
    st.dataframe(history, use_container_width=True)
```

- [ ] **Step 2: Verify manually against the live stack**

```bash
docker compose up -d ui
```

Open `http://localhost:8501`, navigate to "Scoring Calibration" in the
sidebar, and confirm the page loads without error (with zero labeled
jobs so far, expect "0 / 30 labeled" and a real candidate job or "no more
eligible jobs" depending on what Task 8 has left in the DB at this point
in the run).

- [ ] **Step 3: Commit**

```bash
git add apps/ui/app/pages/9_Scoring_Calibration.py
git commit -m "feat(job_search): Step 16 — Scoring Calibration UI page"
```

---

### Task 8: End-to-end smoke test against live data, README update

**Files:**
- Modify: `README.md` — add a "Calibrating the scoring (Step 16)" section.
- No new test file — this task's verification IS the smoke test, run
  directly against the live dev stack and cleaned up afterward.

**Interfaces:**
- Consumes: everything from Tasks 1-6 (the full API surface).
- Produces: nothing further tasks depend on (final task).

This task proves the whole mechanism works end-to-end against real
`gold.dim_job`/`scoring.job_score` data at realistic volume — using
**disposable, clearly-synthetic labels that are deleted afterward**, not
a real calibration. Genuine hand-labeling requires an actual human
judgment about job fit, which is precisely what Step 16 exists to
capture; fabricating 30 "opinions" here would defeat its purpose and
produce a saved calibration that means nothing. This task therefore
smoke-tests the mechanism and explicitly stops short of calling `POST
/scoring/calibration-runs` (the persisting endpoint) — no `scoring.weight`
row is touched by this task.

Prerequisite: the real Step 15 pipeline must already have been run for
the real user (`00000000-0000-0000-0000-000000000099` in this project's
dev environment) so `scoring.job_score` has real, all-four-present rows
to label against. If it hasn't, run:

```bash
USER_ID=00000000-0000-0000-0000-000000000099
docker compose run --rm pipeline python -m app.cli score-filter-jobs --user-id "$USER_ID"
docker compose run --rm pipeline python -m app.cli chunk-embed-jobs
docker compose run --rm pipeline python -m app.cli chunk-embed-cv --user-id "$USER_ID"
docker compose run --rm pipeline python -m app.cli score-similarity --user-id "$USER_ID"
docker compose run --rm pipeline python -m app.cli score-skill-coverage --user-id "$USER_ID"
docker compose run --rm pipeline python -m app.cli score-llm-rerank --user-id "$USER_ID"
docker compose run --rm pipeline python -m app.cli score-blend --user-id "$USER_ID"
```

- [ ] **Step 1: Confirm at least 30 real all-four-present jobs exist**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT count(*) FROM scoring.job_score
WHERE user_id = '00000000-0000-0000-0000-000000000099'
AND hard_filter_passed = true
AND vector_similarity_score IS NOT NULL
AND reranker_score IS NOT NULL
AND skill_coverage_score IS NOT NULL
AND llm_fit_score IS NOT NULL;
"
```

Expected: a count ≥ 30. If lower, this smoke test cannot proceed — note
the actual count and continue to Step 5 with the mechanism unverified
end-to-end against real volume (Tasks 1-6's integration tests already
cover correctness against fixtures; this task is a volume/integration
smoke check, not the only correctness gate).

- [ ] **Step 2: Label 30 real jobs via the API with a disposable rule**

This is a mechanical smoke test, not real labeling — it exists to
exercise `pick_labeling_candidate`, `split_and_fit`, and the full API
path against real row volumes, then gets deleted:

Driven through `core.scoring.calibration` directly inside the `api`
container, not through unauthenticated `curl` (the real API requires
Step 22a's identity resolution, which a bare HTTP client outside a
browser session doesn't carry):

```bash
docker compose exec -T api python -c "
from core.db.session import build_engine
from core.scoring.calibration import pick_labeling_candidate, write_label
from core.settings import get_settings
import uuid

engine = build_engine(get_settings().app_database_url)
user_id = uuid.UUID('00000000-0000-0000-0000-000000000099')
for _ in range(30):
    candidate = pick_labeling_candidate(engine, user_id)
    if candidate is None:
        print('NONE — fewer than 30 eligible jobs remain')
        break
    write_label(engine, user_id, candidate.job_group_id, 'maybe')
    print(candidate.job_group_id)
"
```

- [ ] **Step 3: Run a preview fit and confirm it returns sane output**

```bash
docker compose exec -T api python -c "
from core.db.session import build_engine
from core.scoring.calibration import split_and_fit
from core.settings import get_settings
import uuid
engine = build_engine(get_settings().app_database_url)
user_id = uuid.UUID('00000000-0000-0000-0000-000000000099')
preview = split_and_fit(engine, user_id)
print('fit_count', preview.fit_count)
print('holdout_count', preview.holdout_count)
print('weights', preview.weights)
print('holdout_agreement', preview.holdout_agreement)
"
```

Expected: `fit_count` 20, `holdout_count` 10, `weights` summing to 1.0
(all-`maybe` labels make `holdout_agreement` likely `None` — expected and
correct given Review Focus's constant-label case, not a bug).

- [ ] **Step 4: Delete the disposable labels — leave no smoke-test residue**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
DELETE FROM scoring.job_label
WHERE user_id = '00000000-0000-0000-0000-000000000099';
"
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT count(*) FROM scoring.job_label
WHERE user_id = '00000000-0000-0000-0000-000000000099';
"
```

Expected: final count 0. Confirm also that `scoring.weight` and
`scoring.calibration_run` are untouched (this smoke test never called
`save_calibration`):

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
SELECT count(*) FROM scoring.weight
WHERE user_id = '00000000-0000-0000-0000-000000000099';
SELECT count(*) FROM scoring.calibration_run
WHERE user_id = '00000000-0000-0000-0000-000000000099';
"
```

Expected: both 0.

- [ ] **Step 5: Add the README section**

Open `README.md`, find the "Scoring the job pool (Step 15)" section added
during Step 15, and add immediately after it:

```markdown
### Calibrating the scoring (Step 16)

Until calibrated, every present scoring component is weighted equally —
a placeholder, not a real preference. To calibrate:

1. Open the **Scoring Calibration** page in the UI.
2. Label at least 30 jobs as strong/maybe/no — only jobs that made it
   through the full funnel (all four components present) are shown.
3. Click **Preview calibration**, review the fitted weights and the
   holdout agreement figure (a Spearman correlation computed only on 10
   labels never used for fitting), then **Save weights**.
4. Re-run `score-blend` for the new weights to take effect:
   `docker compose run --rm pipeline python -m app.cli score-blend --user-id <your-user-id>`

Re-calibrate after any embedding-model change — the UI warns when the
current embedding model differs from your last saved calibration's.
```

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "feat(job_search): Step 16 — README calibration section"
```

**Note for the project owner (not a plan step — this cannot be
automated):** the genuine 30-job hand-labeling PLAN.md Step 16 calls for
is a real judgment call only you can make. Once this plan lands, open the
Scoring Calibration page, label 30 real jobs honestly, save the weights,
and record the resulting holdout agreement figure as a "**Measured
(date):** ..." note under PLAN.md's Step 16 section — mirroring Step 9's
own recorded measurement. That note is Step 16's actual "Done when"
criterion; this plan only builds the tool that makes it possible.
