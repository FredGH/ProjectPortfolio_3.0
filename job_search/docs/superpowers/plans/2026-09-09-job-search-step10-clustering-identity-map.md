# Step 10 — Clustering, Identity Map and Survivorship Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assign a stable `job_group_id` to every `silver__job_posting` row
by union-find over Step 7/8's exact-duplicate and similarity-score
signals plus Step 9's human pair labels, persist it in a new
`silver.job_identity_map` table that never changes a row once written,
and implement the field-level survivorship rules (`description`,
`apply_url`, `title_for_display`, `sources[]`) as pure functions ready
for Step 11's `dim_job` build to call.

**Architecture:** Union-find has no dbt equivalent (no transitive-closure
join), so this step is pure Python, orchestrated as `dbt run --select
staging → cluster-jobs (this step) → dbt run --select silver+` per
PLAN.md. Every clustering decision lives in DB-free, pure functions
(`core/dedup/clustering.py`, `core/dedup/identity_assignment.py`,
`core/dedup/survivorship.py`) so the "run twice, get byte-identical
`job_group_id`" acceptance test needs no live database. Exactly one
module (`core/dedup/write_job_identity_map.py`) touches Postgres: it
reads `dedup.dedup__exact_duplicates`, `dedup.dedup__similarity_scores`,
Step 9's `dedup.pair_labels` and `dedup.calibration_thresholds`, and
`silver.silver__job_posting`, and only ever **inserts** new rows into
`silver.job_identity_map` — never updates one already there. That
INSERT-only asymmetry (unlike every other dedup write path's UPSERT) is
the entire mechanism behind `job_group_id` immutability and manual
-override stickiness: a row already present is simply never revisited.

**Tech Stack:** Standard library only (`uuid`, `dataclasses`) for
union-find and clustering — no new dependency. SQLAlchemy `Engine`/`text`
for the one write path, matching every existing `core/dedup/write_*.py`
module.

**Spec:** `PLAN.md`'s "Step 10 — Clustering, identity map and
survivorship" section (lines 733–789), `plan/backlog.yml`'s `STEP-10`
entry (`jira_key: JOB-141`), `DECISIONS.md` §2.6, §2.7, and §5
(`title_for_display` mirroring), and Step 9's shipped implementation
(`docs/superpowers/plans/2026-09-08-job-search-step9-calibration-review-queue.md`,
merged into `main` immediately before this plan was written).

## Scope note — Step 9 is done and merged; use its real calibration, not a guess

An earlier draft of this plan assumed Step 9 (calibration) hadn't been
built and used a hardcoded placeholder threshold. That assumption was
wrong: Step 9 was already fully built on a separate branch
(`worktree-job-search-step9-calibration`) with a working Streamlit
review queue, `/dedup/calibration` and `/dedup/thresholds` API
endpoints, and — critically — **real measured calibration data already
sitting in the shared dev Postgres database**: `dedup.
calibration_thresholds` has a row with `auto_match_threshold = 0.81`
(53 hand-labeled pairs, precision 1.0, recall 0.125), and `dedup.
pair_labels` has 53 real human match/not_match decisions. That branch
has since been merged into `main` (and this plan rebased onto the
result) specifically so Step 10 can use the real thing instead of a
guess.

Two consequences for this plan:

1. **No hardcoded threshold.** `write_job_identity_map` reads the
   current threshold live: `SELECT auto_match_threshold FROM dedup.
   calibration_thresholds ORDER BY calibrated_at DESC LIMIT 1` — the
   exact query `apps/api/app/routers/dedup.py`'s `get_pairs_to_label`
   already uses for the same "current threshold" concept (Task 4). A
   documented, conservative bootstrap constant (`_BOOTSTRAP_AUTO_MATCH_
   THRESHOLD = 0.9`, erring toward under-merging per DECISIONS.md
   §2.5's precision-over-recall rule) is used only on a fresh database
   with zero calibration rows — which is not this database's current
   state, but must not crash a from-scratch environment.
2. **`dedup.pair_labels` feeds the union-find graph directly.** A pair
   a human labeled `'match'` becomes a `ClusterEdge` with
   `match_method='manual'`, `confidence=1.0`, regardless of its
   `blended_score` — this is the actual, concrete form Step 10's
   "manual overrides sticky" requirement takes for pair-level evidence
   (Task 3). A pair labeled `'not_match'` is excluded from ever forming
   a *fuzzy* (score-based) edge, even if its `blended_score` clears the
   threshold — a human's explicit rejection overrides the automatic
   signal. Labeled pairs never come from `dedup__exact_duplicates`
   (Step 9's review queue only ever surfaces `dedup__similarity_scores`
   rows for labeling — confirmed in `get_pairs_to_label`), so this
   exclusion applies only to the fuzzy edge set, never to exact-duplicate
   edges.

`silver.job_identity_map.match_method` therefore has **four** values,
not three: `'exact'`, `'fuzzy'`, `'manual'`, `'singleton'`. A row whose
winning edge was a human `'match'` label is written with
`is_manual_override = true`; every other row keeps the column's default
`false`. This is a per-job-key flag, not a per-cluster one: other
members of the same resulting group that joined via an ordinary
exact/fuzzy edge are not marked, only the specific job_key(s) a human
label actually vouches for.

## Scope note — `apply_url` does not exist as a column; it maps onto `job_url`

PLAN.md and DECISIONS.md both name a survivorship field `apply_url`.
`silver__job_posting` has never had a column by that name — only
`job_url` and `job_url_canonical` (PLAN.md Steps 5a/6 never built a
separate apply-vs-listing URL distinction). This plan's survivorship
module treats `job_url` as the intended target of every "apply_url" rule
in the spec, documented with a comment at the point of use. This is
noted here so it doesn't read as a silent scope gap later.

## Scope note — `sources[]` is a function here, a column in Step 11

`plan/backlog.yml`'s `STEP-10` subtasks list "preserve every source URL
in a `sources[]` array," but PLAN.md's own `silver.job_identity_map`
schema (reproduced below) has no such column — `dim_job.sources[]` is
explicitly a **Step 11** deliverable. This plan satisfies the Step 10
subtask by building `build_sources_array` as a tested, working pure
function (Task 5) that Step 11 calls when materialising `dim_job`;
Step 10 does not itself persist a `sources[]` array anywhere, since
nothing downstream reads `job_identity_map` for it yet.

## Global Constraints

- SQL style per `.claude/rules/sql-style.md`; Python style per
  `.claude/rules/python-style.md`; tests per
  `.claude/rules/python-testing.md` (`unittest`, no DB mocking).
- Next migration is `0011`, `down_revision = "0010"` (Step 9's `dedup.
  pair_labels`/`calibration_thresholds` migration) — confirm via
  `ls db/migrations/versions/` at execution time.
- `silver.job_identity_map` is SHARED-zone (PLAN.md's two-zone rule): no
  `user_id`, no RLS, written by the migration/owner role only.
- `docker compose up -d postgres` must be running for every task's
  verification.
- PLAN.md's target schema (do not deviate without updating this plan),
  extended with a fourth `match_method` value per the scope note above:

  ```sql
  silver.job_identity_map (
    source_name, source_job_id, job_group_id,
    match_method, confidence, matched_at, is_manual_override
  )
  -- match_method IN ('exact', 'fuzzy', 'manual', 'singleton')
  ```

---

### Task 1: Migration — `silver.job_identity_map`

**Files:**
- Create: `db/migrations/versions/0011_create_silver_job_identity_map.py`

**Interfaces:**
- Produces: table `silver.job_identity_map` — PK `(source_name,
  source_job_id)`, columns `job_group_id text not null`, `match_method
  text not null check in ('exact','fuzzy','manual','singleton')`,
  `confidence numeric not null`, `matched_at timestamptz not null
  default now()`, `is_manual_override boolean not null default false`;
  index on `job_group_id`. Consumed by Task 4's write path and every
  later Step 11+ model.

- [ ] **Step 1: Confirm the migration head**

```bash
cd job_search
ls db/migrations/versions/
```

Expected: `0010_create_dedup_pair_labels_and_calibration.py` is the
latest (Step 9's migration). If not, stop and re-check before
renumbering.

- [ ] **Step 2: Write the migration**

`db/migrations/versions/0011_create_silver_job_identity_map.py`:

```python
"""create silver.job_identity_map

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-09

silver.job_identity_map is SHARED dedup-identity data (PLAN.md's
two-zone rule, same pattern as dedup.job_blocking_keys (0008)): which
cluster a posting belongs to is the same for every user, so it carries
no user_id and has no row-level security.

Written only by the migration/owner role, via the `cluster-jobs`
pipeline CLI subcommand (core.dedup.write_job_identity_map) — never by
a live per-user request, and (unlike every other dedup write path)
never updated after insert: PLAN.md Step 10 requires job_group_id to
never change once assigned, so this table is insert-only by design (see
write_job_identity_map's ON CONFLICT DO NOTHING).

Keyed by (source_name, source_job_id) rather than job_key because that
pair is exactly silver__job_posting's own natural key (job_key is its
surrogate hash of the same two columns) — this is PLAN.md's own named
schema for this table, and keeping the natural key avoids depending on
dbt's surrogate-key macro from a migration-owned table.

match_method has a fourth value, 'manual', beyond PLAN.md's original
three ('exact', 'fuzzy', 'singleton') — Step 9's dedup.pair_labels
(migration 0010) lets a human directly confirm a match independent of
blended_score, and that evidence needs its own match_method so it's
distinguishable from an ordinary threshold-cleared fuzzy match. See the
Step 10 plan's "Step 9 is done and merged" scope note.

job_search_app is not granted any access here (unlike pair_labels/
calibration_thresholds in migration 0010): this table is written only
by the pipeline CLI via the owner role, and nothing in the FastAPI layer
reads it yet.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS silver")

    op.create_table(
        "job_identity_map",
        sa.Column("source_name", sa.Text(), primary_key=True),
        sa.Column("source_job_id", sa.Text(), primary_key=True),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column(
            "matched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_manual_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.CheckConstraint(
            "match_method IN ('exact', 'fuzzy', 'manual', 'singleton')",
            name="ck_job_identity_map_match_method",
        ),
        schema="silver",
    )
    op.create_index(
        "ix_job_identity_map_job_group_id",
        "job_identity_map",
        ["job_group_id"],
        schema="silver",
    )


def downgrade() -> None:
    op.drop_table("job_identity_map", schema="silver")
    # Never drop the `silver` schema — dbt also owns objects in it
    # (silver__job_posting, job_engagement_terms). See migration 0007's
    # downgrade for the same lesson.
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
  -c "\d silver.job_identity_map"
```

Expected: PK on `(source_name, source_job_id)`, the `match_method`
check constraint listing all four values, and
`ix_job_identity_map_job_group_id` all present.

- [ ] **Step 4: Commit**

```bash
git add db/migrations/versions/0011_create_silver_job_identity_map.py
git commit -m "feat(job_search): add silver.job_identity_map"
```

---

### Task 2: Generic union-find

**Files:**
- Create: `packages/core/core/dedup/clustering.py`
- Test: `packages/core/tests/test_clustering.py`

**Interfaces:**
- Produces: `UnionFind` class with `find(key) -> str`, `union(a, b) ->
  None`, `components() -> dict[str, list[str]]`. Consumed by Task 3's
  `assign_new_job_groups`.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_clustering.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.clustering import UnionFind


class TestUnionFind(unittest.TestCase):
    def test_unseen_key_is_its_own_representative(self) -> None:
        uf = UnionFind()
        self.assertEqual(uf.find("a"), "a")

    def test_union_makes_two_keys_share_a_representative(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        self.assertEqual(uf.find("a"), uf.find("b"))

    def test_transitive_union_forms_one_component(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        uf.union("b", "c")
        self.assertEqual(uf.find("a"), uf.find("c"))

    def test_union_is_deterministic_regardless_of_argument_order(self) -> None:
        uf_ab = UnionFind()
        uf_ab.union("a", "b")
        uf_ba = UnionFind()
        uf_ba.union("b", "a")
        self.assertEqual(uf_ab.find("a"), uf_ba.find("a"))

    def test_components_groups_every_seen_key(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        uf.find("c")  # seen but never unioned — its own singleton
        components = uf.components()
        self.assertEqual(len(components), 2)
        group_sizes = sorted(len(members) for members in components.values())
        self.assertEqual(group_sizes, [1, 2])

    def test_union_of_already_joined_keys_is_a_no_op(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        uf.union("a", "b")
        self.assertEqual(len(uf.components()), 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd packages/core
coverage run -m unittest tests.test_clustering -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.clustering'`.

- [ ] **Step 3: Implement `UnionFind`**

`packages/core/core/dedup/clustering.py`:

```python
"""Generic union-find (disjoint-set) over string keys — the connected
-components primitive PLAN.md Step 10 needs for transitive dedup
grouping (A~B, B~C => one cluster), which dbt cannot express directly.
"""

from __future__ import annotations


class UnionFind:
    """Disjoint-set over arbitrary string keys, with path compression."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        """Return `key`'s current set representative, seeding it if new.

        Args:
            key: Any string identifier.

        Returns:
            The representative (root) of `key`'s set. A key seen for the
            first time is its own representative.
        """
        self._parent.setdefault(key, key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        """Merge `a` and `b`'s sets, if not already the same set.

        Args:
            a: One key.
            b: The other key.
        """
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        # Deterministic merge direction (lexicographically smaller root
        # wins) so `components()` never depends on call order — required
        # for the "run twice, byte-identical job_group_id" guarantee.
        if root_b < root_a:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a

    def components(self) -> dict[str, list[str]]:
        """Group every key seen so far (via `find` or `union`) by root.

        Returns:
            representative -> every key in its set, including
            singletons.
        """
        groups: dict[str, list[str]] = {}
        for key in self._parent:
            groups.setdefault(self.find(key), []).append(key)
        return groups
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
coverage run -m unittest tests.test_clustering -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/dedup/clustering.py packages/core/tests/test_clustering.py
git commit -m "feat(job_search): add generic union-find for dedup clustering"
```

---

### Task 3: Incremental identity assignment

**Files:**
- Create: `packages/core/core/dedup/identity_assignment.py`
- Test: `packages/core/tests/test_identity_assignment.py`

**Interfaces:**
- Consumes: `core.dedup.clustering.UnionFind` (Task 2).
- Produces: `ClusterEdge` and `IdentityAssignment` dataclasses;
  `build_edges_from_exact_duplicates(rows) -> list[ClusterEdge]`;
  `build_edges_from_similarity_scores(rows, threshold) ->
  list[ClusterEdge]`; `build_manual_match_edges(rows) ->
  list[ClusterEdge]`; `exclude_labeled_pairs(edges, labeled_pairs) ->
  list[ClusterEdge]`; `compute_representatives(assigned: dict[str, str])
  -> dict[str, str]`; `assign_new_job_groups(all_job_keys, edges,
  assigned, new_group_id=None) -> list[IdentityAssignment]`. Consumed
  by Task 4's write path.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_identity_assignment.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.identity_assignment import (
    ClusterEdge,
    assign_new_job_groups,
    build_edges_from_exact_duplicates,
    build_edges_from_similarity_scores,
    build_manual_match_edges,
    compute_representatives,
    exclude_labeled_pairs,
)


class TestBuildEdgesFromExactDuplicates(unittest.TestCase):
    def test_chains_a_three_way_group_into_two_edges(self) -> None:
        rows = [
            ("a", "url_canonical", "same-url"),
            ("b", "url_canonical", "same-url"),
            ("c", "url_canonical", "same-url"),
        ]
        edges = build_edges_from_exact_duplicates(rows)
        self.assertEqual(len(edges), 2)
        self.assertTrue(all(e.match_method == "exact" for e in edges))
        self.assertTrue(all(e.confidence == 1.0 for e in edges))

    def test_singleton_group_produces_no_edge(self) -> None:
        rows = [("a", "url_canonical", "unique-url")]
        self.assertEqual(build_edges_from_exact_duplicates(rows), [])

    def test_different_duplicate_types_do_not_cross_link(self) -> None:
        rows = [
            ("a", "url_canonical", "x"),
            ("b", "content_hash", "x"),
        ]
        self.assertEqual(build_edges_from_exact_duplicates(rows), [])


class TestBuildEdgesFromSimilarityScores(unittest.TestCase):
    def test_row_at_or_above_threshold_becomes_an_edge(self) -> None:
        rows = [("a", "b", 0.9, False)]
        edges = build_edges_from_similarity_scores(rows, threshold=0.8)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].match_method, "fuzzy")
        self.assertEqual(edges[0].confidence, 0.9)

    def test_row_below_threshold_is_dropped(self) -> None:
        rows = [("a", "b", 0.5, False)]
        self.assertEqual(build_edges_from_similarity_scores(rows, threshold=0.8), [])

    def test_hard_veto_row_is_dropped_even_above_threshold(self) -> None:
        rows = [("a", "b", 0.95, True)]
        self.assertEqual(build_edges_from_similarity_scores(rows, threshold=0.8), [])


class TestBuildManualMatchEdges(unittest.TestCase):
    def test_match_label_becomes_a_manual_edge(self) -> None:
        rows = [("a", "b", "match")]
        edges = build_manual_match_edges(rows)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].match_method, "manual")
        self.assertEqual(edges[0].confidence, 1.0)

    def test_not_match_label_produces_no_edge(self) -> None:
        rows = [("a", "b", "not_match")]
        self.assertEqual(build_manual_match_edges(rows), [])


class TestExcludeLabeledPairs(unittest.TestCase):
    def test_labeled_pair_is_removed_regardless_of_score(self) -> None:
        edges = [
            ClusterEdge("a", "b", 0.95, "fuzzy"),
            ClusterEdge("c", "d", 0.95, "fuzzy"),
        ]
        # A human said "not_match" for a/b despite its high score — Step
        # 9's review queue exists precisely to catch cases like this.
        result = exclude_labeled_pairs(edges, labeled_pairs={("a", "b")})
        self.assertEqual(result, [edges[1]])

    def test_pair_order_does_not_matter(self) -> None:
        edges = [ClusterEdge("a", "b", 0.95, "fuzzy")]
        result = exclude_labeled_pairs(edges, labeled_pairs={("b", "a")})
        self.assertEqual(result, [])

    def test_unlabeled_pairs_pass_through_unchanged(self) -> None:
        edges = [ClusterEdge("a", "b", 0.95, "fuzzy")]
        self.assertEqual(exclude_labeled_pairs(edges, labeled_pairs=set()), edges)


class TestComputeRepresentatives(unittest.TestCase):
    def test_picks_the_lexicographically_smallest_member(self) -> None:
        assigned = {"bravo": "group-1", "alpha": "group-1", "zulu": "group-2"}
        self.assertEqual(
            compute_representatives(assigned),
            {"group-1": "alpha", "group-2": "zulu"},
        )


class TestAssignNewJobGroups(unittest.TestCase):
    def _ids(self) -> object:
        counter = iter(f"new-group-{i}" for i in range(100))
        return lambda: next(counter)

    def test_singleton_with_no_edges_gets_its_own_group(self) -> None:
        result = assign_new_job_groups(
            all_job_keys=["a"], edges=[], assigned={}, new_group_id=self._ids()
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].job_key, "a")
        self.assertEqual(result[0].match_method, "singleton")
        self.assertEqual(result[0].confidence, 1.0)
        self.assertFalse(result[0].is_manual_override)

    def test_two_new_jobs_matching_each_other_share_a_fresh_group(self) -> None:
        edges = [ClusterEdge("a", "b", 0.9, "fuzzy")]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned={}, new_group_id=self._ids()
        )
        group_ids = {r.job_key: r.job_group_id for r in result}
        self.assertEqual(group_ids["a"], group_ids["b"])

    def test_new_job_matching_an_existing_representative_joins_that_group(self) -> None:
        assigned = {"rep": "existing-group"}
        edges = [ClusterEdge("new", "rep", 0.85, "fuzzy")]
        result = assign_new_job_groups(
            all_job_keys=["new"], edges=edges, assigned=assigned, new_group_id=self._ids()
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].job_group_id, "existing-group")
        self.assertEqual(result[0].match_method, "fuzzy")
        self.assertEqual(result[0].confidence, 0.85)

    def test_manual_edge_wins_even_below_typical_thresholds_and_flags_override(
        self,
    ) -> None:
        # A human confirmed this pair matches even though its own
        # blended_score (not modeled here) may sit under the auto-match
        # threshold — the manual edge is the only signal this function
        # ever sees for this pair, at confidence 1.0.
        edges = [ClusterEdge("a", "b", 1.0, "manual")]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned={}, new_group_id=self._ids()
        )
        self.assertTrue(all(r.match_method == "manual" for r in result))
        self.assertTrue(all(r.is_manual_override for r in result))

    def test_already_assigned_job_is_never_reassigned(self) -> None:
        assigned = {"old": "existing-group"}
        result = assign_new_job_groups(
            all_job_keys=["old"], edges=[], assigned=assigned, new_group_id=self._ids()
        )
        self.assertEqual(result, [])

    def test_new_component_bridging_to_one_existing_group_all_join_it(self) -> None:
        # a-b are new and match each other; a also matches an existing
        # representative. Both a and b must land in the existing group.
        assigned = {"rep": "existing-group"}
        edges = [
            ClusterEdge("a", "b", 0.9, "fuzzy"),
            ClusterEdge("a", "rep", 0.85, "fuzzy"),
        ]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned=assigned, new_group_id=self._ids()
        )
        group_ids = {r.job_key: r.job_group_id for r in result}
        self.assertEqual(group_ids["a"], "existing-group")
        self.assertEqual(group_ids["b"], "existing-group")

    def test_rerun_with_everything_already_assigned_is_a_no_op(self) -> None:
        assigned = {"a": "group-1", "b": "group-1"}
        edges = [ClusterEdge("a", "b", 0.9, "fuzzy")]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned=assigned, new_group_id=self._ids()
        )
        self.assertEqual(result, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
coverage run -m unittest tests.test_identity_assignment -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.identity_assignment'`.

- [ ] **Step 3: Implement `identity_assignment.py`**

`packages/core/core/dedup/identity_assignment.py`:

```python
"""Incremental clustering logic for Step 10 (PLAN.md): builds match
edges from dedup's exact-duplicate, similarity-score, and (Step 9)
human-labeled pair tables, then assigns job_group_id to any job_key not
yet present in silver.job_identity_map.

Kept dbt-free and DB-free by design (PLAN.md's "why this is Python, not
dbt" — union-find needs transitive closure dbt can't express) — every
function here takes plain data in and returns plain data out, so the
"run the pipeline twice, get byte-identical job_group_id" acceptance
criterion (PLAN.md Step 10) can be tested without Postgres at all. Only
core.dedup.write_job_identity_map talks to the database, including
resolving the real auto-match threshold from dedup.
calibration_thresholds — this module never hardcodes one.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from core.dedup.clustering import UnionFind


@dataclass(frozen=True)
class ClusterEdge:
    """One match signal between two job_keys, feeding union-find.

    Attributes:
        job_key_a: One side of the match. No ordering requirement.
        job_key_b: The other side.
        confidence: 1.0 for an exact or manually-confirmed match, else
            the similarity blend.
        match_method: 'exact', 'fuzzy', or 'manual' — never 'singleton'
            (that is assigned later, only to jobs with no edge at all).
    """

    job_key_a: str
    job_key_b: str
    confidence: float
    match_method: str


@dataclass(frozen=True)
class IdentityAssignment:
    """One new silver.job_identity_map row this run should insert.

    Attributes:
        job_key: The newly assigned job.
        job_group_id: The cluster it was assigned to — either an
            existing group's id (representative match) or a freshly
            generated one.
        match_method: 'exact', 'fuzzy', 'manual', or 'singleton'.
        confidence: The strongest edge confidence involved in this
            assignment, or 1.0 for a singleton.
        is_manual_override: True only when `match_method == 'manual'`
            — this specific job_key's assignment is backed by a human
            pair label (Step 9's dedup.pair_labels), not an automatic
            signal, and per PLAN.md must never be recomputed.
    """

    job_key: str
    job_group_id: str
    match_method: str
    confidence: float
    is_manual_override: bool = False


def build_edges_from_exact_duplicates(
    rows: Iterable[tuple[str, str, str]],
) -> list[ClusterEdge]:
    """Convert dedup__exact_duplicates rows into pairwise edges.

    Args:
        rows: (job_key, duplicate_type, duplicate_group_key) tuples, one
            per dedup__exact_duplicates row (PLAN.md Step 7).

    Returns:
        One edge per non-first member of each (duplicate_type,
        duplicate_group_key) group, chained to the group's first member
        — sufficient for union-find's transitive closure without
        building the full O(n^2) pairwise set.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for job_key, duplicate_type, duplicate_group_key in rows:
        groups.setdefault((duplicate_type, duplicate_group_key), []).append(job_key)

    edges: list[ClusterEdge] = []
    for members in groups.values():
        first = members[0]
        for other in members[1:]:
            edges.append(
                ClusterEdge(
                    job_key_a=first,
                    job_key_b=other,
                    confidence=1.0,
                    match_method="exact",
                )
            )
    return edges


def build_edges_from_similarity_scores(
    rows: Iterable[tuple[str, str, float, bool]],
    threshold: float,
) -> list[ClusterEdge]:
    """Convert dedup__similarity_scores rows into pairwise edges.

    Args:
        rows: (job_key_a, job_key_b, blended_score, hard_veto) tuples.
        threshold: Minimum blended_score to treat a pair as a match —
            the caller (core.dedup.write_job_identity_map) resolves
            this from dedup.calibration_thresholds; this function never
            picks its own default so the real number is never hidden
            behind an implicit fallback.

    Returns:
        One edge per row clearing the threshold. hard_veto rows are
        excluded even though dedup__similarity_scores already forces
        their blended_score to 0.0 — belt-and-braces, matching the SQL
        model's own veto logic explicitly rather than relying on it.
    """
    return [
        ClusterEdge(
            job_key_a=job_key_a,
            job_key_b=job_key_b,
            confidence=blended_score,
            match_method="fuzzy",
        )
        for job_key_a, job_key_b, blended_score, hard_veto in rows
        if not hard_veto and blended_score >= threshold
    ]


def build_manual_match_edges(
    rows: Iterable[tuple[str, str, str]],
) -> list[ClusterEdge]:
    """Convert Step 9's dedup.pair_labels rows into forced-match edges.

    Args:
        rows: (job_key_a, job_key_b, label) tuples, `label` being
            'match' or 'not_match'.

    Returns:
        One confidence-1.0 'manual' edge per row labeled 'match'.
        'not_match' rows produce no edge here — they instead feed
        `exclude_labeled_pairs`, which blocks them from ever becoming a
        fuzzy edge regardless of score.
    """
    return [
        ClusterEdge(job_key_a=a, job_key_b=b, confidence=1.0, match_method="manual")
        for a, b, label in rows
        if label == "match"
    ]


def exclude_labeled_pairs(
    edges: list[ClusterEdge],
    labeled_pairs: set[tuple[str, str]],
) -> list[ClusterEdge]:
    """Drop any fuzzy edge whose pair a human has already labeled.

    Applies to BOTH labels, not just 'not_match': a pair labeled
    'match' already gets its own edge from `build_manual_match_edges`,
    so leaving its fuzzy edge in too would just be a redundant,
    lower-confidence duplicate of the same pair. A pair labeled
    'not_match' must never contribute a fuzzy edge at all, no matter how
    high its blended_score — a human's explicit rejection overrides the
    automatic signal.

    Args:
        edges: Candidate fuzzy edges (from
            `build_edges_from_similarity_scores`).
        labeled_pairs: Every `(job_key_a, job_key_b)` pair present in
            dedup.pair_labels, in either order.

    Returns:
        `edges` with any labeled pair removed, order preserved.
    """
    normalized = {frozenset(pair) for pair in labeled_pairs}
    return [e for e in edges if frozenset((e.job_key_a, e.job_key_b)) not in normalized]


def compute_representatives(assigned: dict[str, str]) -> dict[str, str]:
    """Pick one representative job_key per existing job_group_id.

    Recomputed fresh every run from whichever job_keys currently belong
    to each group — used only to bound *this run's* comparisons against
    representatives (PLAN.md's "never re-cluster the whole table"),
    never to decide identity, so a representative changing between runs
    (e.g. a smaller job_key joins later) is harmless: job_group_id
    itself never moves once assigned.

    Args:
        assigned: job_key -> job_group_id, for every job already in
            silver.job_identity_map.

    Returns:
        job_group_id -> the lexicographically smallest job_key currently
        in that group.
    """
    by_group: dict[str, list[str]] = {}
    for job_key, job_group_id in assigned.items():
        by_group.setdefault(job_group_id, []).append(job_key)
    return {group_id: min(members) for group_id, members in by_group.items()}


def assign_new_job_groups(
    all_job_keys: Iterable[str],
    edges: list[ClusterEdge],
    assigned: dict[str, str],
    new_group_id: Callable[[], str] | None = None,
) -> list[IdentityAssignment]:
    """Assign job_group_id to every job_key not already in `assigned`.

    Two-phase, so a brand-new job that matches both an existing cluster
    AND another brand-new job ends up consistent with both matches
    (PLAN.md only forbids re-clustering *existing* groups into each
    other — it says nothing about two new postings that turn out to be
    duplicates of each other, which must still land in one group):

    1. Union-find over edges where *neither* side is already assigned —
       the only clustering that happens among brand-new jobs; existing
       groups are never touched by it. This includes 'manual' edges
       between two new job_keys just like 'exact'/'fuzzy' ones.
    2. For each resulting new component, look for any edge connecting it
       to an existing group's representative (`compute_representatives`).
       If found, the whole component joins that job_group_id (the
       strongest such edge wins if more than one existing group is
       touched — a component is never split across two existing
       groups, and two existing groups are never merged into each
       other). Otherwise the component gets a freshly generated
       job_group_id.

    Args:
        all_job_keys: Every job_key in silver__job_posting this run —
            needed so a job_key with zero edges (a true singleton) still
            gets assigned, not just jobs that appear in `edges`.
        edges: Every edge from `build_edges_from_exact_duplicates`,
            `build_edges_from_similarity_scores` (after
            `exclude_labeled_pairs`), and `build_manual_match_edges`,
            combined.
        assigned: job_key -> job_group_id, for every job already in
            silver.job_identity_map. Never mutated or reassigned here —
            this is what guarantees job_group_id immutability and
            manual-override stickiness (an overridden job_key is simply
            already in `assigned`, so it is never revisited).
        new_group_id: Zero-argument factory for a fresh job_group_id,
            injected for deterministic tests. Defaults to
            `lambda: uuid.uuid4().hex`.

    Returns:
        One `IdentityAssignment` per job_key not already in `assigned`,
        empty if every job_key is already assigned (the "run twice"
        case — this is what makes a second run a no-op).
    """
    if new_group_id is None:
        new_group_id = lambda: uuid.uuid4().hex  # noqa: E731

    representatives = compute_representatives(assigned)
    representative_to_group = {rep: gid for gid, rep in representatives.items()}

    new_keys = {k for k in all_job_keys if k not in assigned}

    uf = UnionFind()
    for key in new_keys:
        uf.find(key)  # seed every new key, even ones with no edges

    new_new_edges = [
        e for e in edges if e.job_key_a in new_keys and e.job_key_b in new_keys
    ]
    for e in new_new_edges:
        uf.union(e.job_key_a, e.job_key_b)

    bridge_edges = [
        e for e in edges if (e.job_key_a in new_keys) != (e.job_key_b in new_keys)
    ]

    # Strongest edge touching each individual new job_key, from either
    # edge set — used only to set each assigned row's own confidence/
    # match_method, never to decide grouping (grouping is decided below,
    # per-component). 'manual' and 'exact' edges both carry confidence
    # 1.0; ties are broken by whichever was appended first, which is
    # deterministic given a deterministic input ordering upstream.
    best_edge_for_key: dict[str, ClusterEdge] = {}
    for e in new_new_edges + bridge_edges:
        for key in (e.job_key_a, e.job_key_b):
            if key in new_keys:
                current = best_edge_for_key.get(key)
                if current is None or e.confidence > current.confidence:
                    best_edge_for_key[key] = e

    assignments: list[IdentityAssignment] = []
    for members in uf.components().values():
        bridge_matches = [
            e
            for e in bridge_edges
            if (e.job_key_a in members and e.job_key_b in representative_to_group)
            or (e.job_key_b in members and e.job_key_a in representative_to_group)
        ]
        if bridge_matches:
            best_bridge = max(bridge_matches, key=lambda e: e.confidence)
            rep = (
                best_bridge.job_key_b
                if best_bridge.job_key_a in members
                else best_bridge.job_key_a
            )
            group_id = representative_to_group[rep]
        else:
            group_id = new_group_id()

        for key in members:
            best_edge = best_edge_for_key.get(key)
            if best_edge is not None:
                assignments.append(
                    IdentityAssignment(
                        job_key=key,
                        job_group_id=group_id,
                        match_method=best_edge.match_method,
                        confidence=best_edge.confidence,
                        is_manual_override=best_edge.match_method == "manual",
                    )
                )
            else:
                assignments.append(
                    IdentityAssignment(
                        job_key=key,
                        job_group_id=group_id,
                        match_method="singleton",
                        confidence=1.0,
                    )
                )

    return assignments
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
coverage run -m unittest tests.test_identity_assignment -v
```

Expected: all 19 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/dedup/identity_assignment.py packages/core/tests/test_identity_assignment.py
git commit -m "feat(job_search): add incremental job_group_id assignment"
```

---

### Task 4: Write path and `cluster-jobs` CLI subcommand

**Files:**
- Create: `packages/core/core/dedup/write_job_identity_map.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/integration/test_write_job_identity_map.py`

**Interfaces:**
- Consumes: `core.dedup.identity_assignment.{assign_new_job_groups,
  build_edges_from_exact_duplicates, build_edges_from_similarity_scores,
  build_manual_match_edges, exclude_labeled_pairs}` (Task 3);
  `core.db.session.build_engine`; `core.settings.get_settings`.
- Produces: `write_job_identity_map(engine: Engine) -> int`; CLI
  subcommand `cluster-jobs`.

- [ ] **Step 1: Write the failing integration test**

`packages/core/tests/integration/test_write_job_identity_map.py`:

```python
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_job_identity_map import write_job_identity_map

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteJobIdentityMap(unittest.TestCase):
    """Integration test against a real Postgres instance.

    Inserts fixture rows directly into silver.silver__job_posting and
    dedup.dedup__similarity_scores (both dbt tables — safe here since
    nothing runs `dbt run` mid-test, matching the existing
    test_write_title_similarity_scores.py pattern), then exercises the
    real write path.
    """

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.suffix = uuid.uuid4().hex
        self.job_key_a = f"test-a-{self.suffix}"
        self.job_key_b = f"test-b-{self.suffix}"
        self.source_a = f"src-a-{self.suffix}"
        self.source_b = f"src-b-{self.suffix}"
        with self.engine.begin() as conn:
            for job_key, source_job_id in (
                (self.job_key_a, self.source_a),
                (self.job_key_b, self.source_b),
            ):
                conn.execute(
                    text(
                        "INSERT INTO silver.silver__job_posting "
                        "(job_key, source_name, source_job_id, job_url, "
                        "job_url_canonical, entry_method, title, company, "
                        "location, description, salary_raw, posted_at) "
                        "VALUES (:job_key, 'manual', :source_job_id, "
                        "'https://example.com/' || :source_job_id, "
                        "'https://example.com/' || :source_job_id, "
                        "'manual', 'Data Engineer', 'Test Co', 'London', "
                        "'A description', NULL, now())"
                    ),
                    {"job_key": job_key, "source_job_id": source_job_id},
                )
            conn.execute(
                text(
                    "INSERT INTO dedup.dedup__similarity_scores "
                    "(job_key_a, job_key_b, match_type, company_similarity, "
                    "title_similarity, description_similarity, "
                    "location_similarity, date_diff_days, date_similarity, "
                    "salary_similarity, hard_veto, blended_score) "
                    "VALUES (:a, :b, 'block', 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, "
                    "1.0, false, 0.95)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM silver.job_identity_map "
                    "WHERE source_job_id IN (:a, :b)"
                ),
                {"a": self.source_a, "b": self.source_b},
            )
            conn.execute(
                text("DELETE FROM dedup.pair_labels WHERE job_key_a = :a AND job_key_b = :b"),
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
                    "DELETE FROM silver.silver__job_posting "
                    "WHERE job_key IN (:a, :b)"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )
        self.engine.dispose()

    def test_writes_both_jobs_into_the_same_group(self) -> None:
        # 0.95 clears the real calibrated threshold (0.81 as of Step 9)
        # comfortably, so this exercises the ordinary fuzzy-match path
        # without needing to know the exact live threshold value.
        write_job_identity_map(self.engine)

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source_job_id, job_group_id, match_method, "
                    "is_manual_override "
                    "FROM silver.job_identity_map "
                    "WHERE source_job_id IN (:a, :b)"
                ),
                {"a": self.source_a, "b": self.source_b},
            ).all()
        self.assertEqual(len(rows), 2)
        group_ids = {row.job_group_id for row in rows}
        self.assertEqual(len(group_ids), 1)
        self.assertTrue(all(row.match_method == "fuzzy" for row in rows))
        self.assertTrue(all(row.is_manual_override is False for row in rows))

    def test_manual_not_match_label_blocks_the_merge_despite_high_score(self) -> None:
        # Step 9's whole point: a human can override a high automatic
        # score. Without this label the fixture pair (blended_score
        # 0.95) would merge, per the test above.
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dedup.pair_labels (job_key_a, job_key_b, label) "
                    "VALUES (:a, :b, 'not_match')"
                ),
                {"a": self.job_key_a, "b": self.job_key_b},
            )

        write_job_identity_map(self.engine)

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source_job_id, job_group_id "
                    "FROM silver.job_identity_map "
                    "WHERE source_job_id IN (:a, :b)"
                ),
                {"a": self.source_a, "b": self.source_b},
            ).all()
        self.assertEqual(len(rows), 2)
        group_ids = {row.job_group_id for row in rows}
        self.assertEqual(len(group_ids), 2, "not_match must prevent the merge")

    def test_rerun_produces_byte_identical_job_group_id(self) -> None:
        write_job_identity_map(self.engine)
        with self.engine.connect() as conn:
            first_run = dict(
                conn.execute(
                    text(
                        "SELECT source_job_id, job_group_id "
                        "FROM silver.job_identity_map "
                        "WHERE source_job_id IN (:a, :b)"
                    ),
                    {"a": self.source_a, "b": self.source_b},
                ).all()
            )

        second_written = write_job_identity_map(self.engine)

        with self.engine.connect() as conn:
            second_run = dict(
                conn.execute(
                    text(
                        "SELECT source_job_id, job_group_id "
                        "FROM silver.job_identity_map "
                        "WHERE source_job_id IN (:a, :b)"
                    ),
                    {"a": self.source_a, "b": self.source_b},
                ).all()
            )

        self.assertEqual(second_written, 0)
        self.assertEqual(first_run, second_run)
```

Note: like every existing `write_*` path in this codebase (`write_
blocking_keys`, `write_similarity_features`, `write_title_similarity_
scores`), this write path processes the *entire* `silver__job_posting`
table on every call, not just the two fixture rows — that's an
established, accepted pattern here, not new to this test. Since
`silver.job_identity_map` starts empty (migration 0011 is new), the
first time this test suite runs it will also perform the real dataset's
initial full clustering pass — against the real, already-calibrated
threshold and the real 53 `dedup.pair_labels` rows — as a side effect.
Expect the first run to take noticeably longer than a typical unit
test; this is not a bug.

- [ ] **Step 2: Run the test to verify it fails**

```bash
coverage run -m unittest tests.integration.test_write_job_identity_map -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.write_job_identity_map'`.

- [ ] **Step 3: Implement the write path**

`packages/core/core/dedup/write_job_identity_map.py`:

```python
"""Batch write path for silver.job_identity_map (PLAN.md Step 10).

Runs outside dbt for the same reason Step 10 as a whole does (see
core.dedup.identity_assignment's module docstring): union-find's
transitive closure has no dbt equivalent. This module is the only place
in Step 10 that touches Postgres — every clustering decision lives in
pure, DB-free functions so the "run twice, byte-identical job_group_id"
acceptance test never needs a live database to prove out.

This is also the only place that resolves the auto-match threshold:
Step 9's dedup.calibration_thresholds is queried for the most recently
calibrated row (the exact query apps/api/app/routers/dedup.py's
get_pairs_to_label already uses for the same concept). A fresh database
with no calibration row yet falls back to a conservative, documented
constant that errs toward under-merging (DECISIONS.md §2.5: precision
over recall) rather than guessing a number that might over-merge.

Unlike every other dedup write path (write_blocking_keys, write_
similarity_features, write_title_similarity_scores), this one never
UPSERTs — it only INSERTs job_keys not already in silver.
job_identity_map. That asymmetry is the mechanism behind PLAN.md's
"job_group_id never changes once assigned" and "manual overrides are
never recomputed": a job_key already present is simply never revisited.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.dedup.identity_assignment import (
    assign_new_job_groups,
    build_edges_from_exact_duplicates,
    build_edges_from_similarity_scores,
    build_manual_match_edges,
    exclude_labeled_pairs,
)

_BOOTSTRAP_AUTO_MATCH_THRESHOLD = 0.9
"""Used only when dedup.calibration_thresholds has zero rows (a fresh
database that has never run Step 9's calibration flow) — not this
project's current database, which already has a real measured value
(0.81 as of Step 9). Deliberately conservative/high so an uncalibrated
environment under-merges rather than over-merges, per DECISIONS.md
§2.5's precision-over-recall rule."""

_SELECT_ALL_JOB_KEYS = text(
    "SELECT job_key, source_name, source_job_id FROM silver.silver__job_posting"
)

_SELECT_ASSIGNED = text(
    """
    SELECT sp.job_key, im.job_group_id
    FROM silver.job_identity_map AS im
    INNER JOIN silver.silver__job_posting AS sp
        ON im.source_name = sp.source_name AND im.source_job_id = sp.source_job_id
    """
)

_SELECT_EXACT_DUPLICATE_EDGES = text(
    "SELECT job_key, duplicate_type, duplicate_group_key "
    "FROM dedup.dedup__exact_duplicates"
)

_SELECT_SIMILARITY_EDGES = text(
    "SELECT job_key_a, job_key_b, blended_score, hard_veto "
    "FROM dedup.dedup__similarity_scores"
)

_SELECT_PAIR_LABELS = text("SELECT job_key_a, job_key_b, label FROM dedup.pair_labels")

_SELECT_CURRENT_THRESHOLD = text(
    "SELECT auto_match_threshold FROM dedup.calibration_thresholds "
    "ORDER BY calibrated_at DESC LIMIT 1"
)

_INSERT = text(
    """
    INSERT INTO silver.job_identity_map (
        source_name, source_job_id, job_group_id, match_method,
        confidence, is_manual_override
    ) VALUES (
        :source_name, :source_job_id, :job_group_id, :match_method,
        :confidence, :is_manual_override
    )
    ON CONFLICT (source_name, source_job_id) DO NOTHING
    """
)


def write_job_identity_map(engine: Engine) -> int:
    """Assign job_group_id to every silver posting not yet clustered.

    Args:
        engine: The migration/owner engine — SHARED-zone, like every
            other dedup table.

    Returns:
        The number of new rows inserted (0 on a re-run once every
        posting is already assigned — the "run twice" acceptance case).
    """
    with engine.begin() as conn:
        universe_rows = conn.execute(_SELECT_ALL_JOB_KEYS).all()
        job_key_to_source = {
            row.job_key: (row.source_name, row.source_job_id) for row in universe_rows
        }
        assigned = {
            row.job_key: row.job_group_id
            for row in conn.execute(_SELECT_ASSIGNED).all()
        }

        threshold_row = conn.execute(_SELECT_CURRENT_THRESHOLD).one_or_none()
        threshold = (
            float(threshold_row.auto_match_threshold)
            if threshold_row is not None
            else _BOOTSTRAP_AUTO_MATCH_THRESHOLD
        )

        label_rows = conn.execute(_SELECT_PAIR_LABELS).all()
        labeled_pairs = {(row.job_key_a, row.job_key_b) for row in label_rows}

        fuzzy_edges = exclude_labeled_pairs(
            build_edges_from_similarity_scores(
                (
                    (row.job_key_a, row.job_key_b, float(row.blended_score), row.hard_veto)
                    for row in conn.execute(_SELECT_SIMILARITY_EDGES).all()
                ),
                threshold=threshold,
            ),
            labeled_pairs=labeled_pairs,
        )

        edges = (
            build_edges_from_exact_duplicates(
                (row.job_key, row.duplicate_type, row.duplicate_group_key)
                for row in conn.execute(_SELECT_EXACT_DUPLICATE_EDGES).all()
            )
            + fuzzy_edges
            + build_manual_match_edges(
                (row.job_key_a, row.job_key_b, row.label) for row in label_rows
            )
        )

        assignments = assign_new_job_groups(
            all_job_keys=job_key_to_source.keys(),
            edges=edges,
            assigned=assigned,
        )

        for assignment in assignments:
            source_name, source_job_id = job_key_to_source[assignment.job_key]
            conn.execute(
                _INSERT,
                {
                    "source_name": source_name,
                    "source_job_id": source_job_id,
                    "job_group_id": assignment.job_group_id,
                    "match_method": assignment.match_method,
                    "confidence": assignment.confidence,
                    "is_manual_override": assignment.is_manual_override,
                },
            )
    return len(assignments)
```

- [ ] **Step 4: Wire the `cluster-jobs` CLI subcommand**

In `apps/pipeline/app/cli.py`, add the import alongside the other
`core.dedup.write_*` imports:

```python
from core.dedup.write_job_identity_map import write_job_identity_map
```

Add the command function, next to `_cmd_compute_title_similarity_scores`:

```python
def _cmd_cluster_jobs(args: argparse.Namespace) -> int:
    """Run the `cluster-jobs` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_job_identity_map(engine)
    print(f"cluster-jobs complete: rows_written={written}")
    return 0
```

Register it in `main()`, next to the `compute-title-similarity-scores`
subparser:

```python
    subparsers.add_parser(
        "cluster-jobs",
        help="Assign job_group_id to every unclustered silver posting",
    )
```

And dispatch it alongside the other `args.command ==` checks:

```python
    if args.command == "cluster-jobs":
        return _cmd_cluster_jobs(args)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
coverage run -m unittest tests.integration.test_write_job_identity_map -v
```

Expected: all three tests PASS.

- [ ] **Step 6: Run it live and confirm real clusters form**

```bash
cd job_search
python3.11 -m apps.pipeline.app.cli cluster-jobs
```

Expected: `cluster-jobs complete: rows_written=<N>` where N is close to
the total row count of `silver.silver__job_posting` (every posting gets
*some* assignment — a group of its own if nothing else). Then:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
  SELECT job_group_id, COUNT(*) FROM silver.job_identity_map
  GROUP BY job_group_id HAVING COUNT(*) > 1 ORDER BY 2 DESC LIMIT 5;
" -c "
  SELECT match_method, COUNT(*) FROM silver.job_identity_map GROUP BY 1;
"
```

Expected: at least one multi-row group — the real Adzuna/Reed "Sopra
Steria" duplicate pair used throughout Steps 7/8's plans should appear
here — and at least one `match_method = 'manual'` row, since the real
`dedup.pair_labels` table already has 53 human-labeled pairs including
some 'match' labels. Cross-check one specific pair you know was labeled
'match':

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
  SELECT job_key_a, job_key_b FROM dedup.pair_labels WHERE label = 'match' LIMIT 1;
"
```

then look up both job_keys' `source_name`/`source_job_id` in
`silver.silver__job_posting` and confirm they landed in
`silver.job_identity_map` with the same `job_group_id` and
`is_manual_override = true`. Flag anything surprising to the user
rather than silently changing the clustering logic.

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/dedup/write_job_identity_map.py \
        packages/core/tests/integration/test_write_job_identity_map.py \
        apps/pipeline/app/cli.py
git commit -m "feat(job_search): add cluster-jobs pipeline subcommand"
```

---

### Task 5: Survivorship pure functions

**Files:**
- Create: `packages/core/core/dedup/survivorship.py`
- Test: `packages/core/tests/test_survivorship.py`

**Interfaces:**
- Produces: `ClusterMember` dataclass; `resolve_description(members) ->
  str | None`; `resolve_apply_source(members) -> ClusterMember`;
  `build_sources_array(members) -> list[dict]`. Consumed by Step 11's
  `dim_job` build (not built in this plan — see the scope note above).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_survivorship.py`:

```python
from __future__ import annotations

import unittest

from core.dedup.survivorship import (
    ClusterMember,
    build_sources_array,
    resolve_apply_source,
    resolve_description,
)


def _member(
    source_name: str,
    job_url: str = "https://example.com",
    title_for_display: str = "Data Engineer",
    description: str | None = "desc",
    first_seen_at: str = "2026-09-01",
) -> ClusterMember:
    return ClusterMember(
        source_name=source_name,
        job_url=job_url,
        title_for_display=title_for_display,
        description=description,
        first_seen_at=first_seen_at,
    )


class TestResolveDescription(unittest.TestCase):
    def test_longest_non_empty_description_wins(self) -> None:
        members = [
            _member("reed", description="short"),
            _member("manual", description="a much longer description"),
        ]
        self.assertEqual(
            resolve_description(members), "a much longer description"
        )

    def test_empty_and_none_descriptions_are_ignored(self) -> None:
        members = [
            _member("reed", description=""),
            _member("adzuna", description=None),
            _member("manual", description="the only real one"),
        ]
        self.assertEqual(resolve_description(members), "the only real one")

    def test_all_empty_returns_none(self) -> None:
        members = [_member("reed", description=None), _member("adzuna", description="")]
        self.assertIsNone(resolve_description(members))


class TestResolveApplySource(unittest.TestCase):
    def test_ats_source_wins_even_with_shorter_description(self) -> None:
        members = [
            _member("reed", job_url="https://reed.example", description="x" * 500),
            _member("greenhouse", job_url="https://greenhouse.example", description="x"),
        ]
        winner = resolve_apply_source(members)
        self.assertEqual(winner.source_name, "greenhouse")
        self.assertEqual(winner.job_url, "https://greenhouse.example")

    def test_title_for_display_comes_from_the_same_winning_source(self) -> None:
        members = [
            _member("reed", title_for_display="Data Engineer (Reed phrasing)"),
            _member("greenhouse", title_for_display="Senior Data Engineer"),
        ]
        winner = resolve_apply_source(members)
        self.assertEqual(winner.title_for_display, "Senior Data Engineer")

    def test_unknown_source_name_never_wins_over_a_known_one(self) -> None:
        members = [
            _member("some-new-scraper"),
            _member("jooble"),
        ]
        self.assertEqual(resolve_apply_source(members).source_name, "jooble")


class TestBuildSourcesArray(unittest.TestCase):
    def test_keeps_every_member_regardless_of_survivorship_winner(self) -> None:
        members = [
            _member("greenhouse", job_url="https://gh.example"),
            _member("reed", job_url="https://reed.example"),
        ]
        sources = build_sources_array(members)
        self.assertEqual(len(sources), 2)
        self.assertEqual(
            {s["source_name"] for s in sources}, {"greenhouse", "reed"}
        )

    def test_ordered_deterministically_by_source_name(self) -> None:
        members = [_member("reed"), _member("adzuna"), _member("greenhouse")]
        sources = build_sources_array(members)
        self.assertEqual(
            [s["source_name"] for s in sources],
            ["adzuna", "greenhouse", "reed"],
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
coverage run -m unittest tests.test_survivorship -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.survivorship'`.

- [ ] **Step 3: Implement `survivorship.py`**

`packages/core/core/dedup/survivorship.py`:

```python
"""Field-level survivorship rules for a dedup cluster (PLAN.md Step 10,
DECISIONS.md §2.7 and §5). Consumed by Step 11's dim_job build — kept
here, DB-free and dbt-free, so Step 11 only needs to call these against
whatever rows it has already fetched for one job_group_id.

Note on `apply_url`: PLAN.md and DECISIONS.md name this field
`apply_url`, but no column by that name exists anywhere in the pipeline
(silver__job_posting has `job_url` and `job_url_canonical` only — PLAN.md
Steps 5a/6 never built a separate apply-vs-listing URL distinction).
This module treats `job_url` as the intended target of every
"apply_url" survivorship rule in the spec.
"""

from __future__ import annotations

from dataclasses import dataclass

_SOURCE_RANK: dict[str, int] = {
    "greenhouse": 0,  # direct ATS
    "reed": 1,
    "adzuna": 1,
    "jooble": 2,  # aggregator
    "manual": 3,  # manual/scraped entry — least trusted apply link
}
"""PLAN.md Step 10: 'direct ATS (Greenhouse/Lever/Ashby) > Reed/Adzuna >
other aggregator > scraped/manual'. Lever/Ashby and a genuine scraped
source don't exist yet in this pipeline (only the 5 sources in
core.ingestion) — extend this table, never change its ordering
semantics, when one is added."""

_UNKNOWN_SOURCE_RANK = max(_SOURCE_RANK.values()) + 1


@dataclass(frozen=True)
class ClusterMember:
    """One source posting within a resolved dedup cluster.

    Attributes:
        source_name: e.g. 'greenhouse', 'reed'.
        job_url: The posting's listing/apply URL.
        title_for_display: Step 6's decoration-stripped, seniority-kept
            title (DECISIONS.md §5) — never strip_title's output.
        description: The posting's full description text.
        first_seen_at: When this source's copy was first ingested.
    """

    source_name: str
    job_url: str
    title_for_display: str
    description: str | None
    first_seen_at: object


def _source_rank(source_name: str) -> int:
    return _SOURCE_RANK.get(source_name, _UNKNOWN_SOURCE_RANK)


def resolve_description(members: list[ClusterMember]) -> str | None:
    """Longest non-empty description wins (DECISIONS.md §2.7).

    Args:
        members: Every source posting in one dedup cluster.

    Returns:
        The longest non-empty `description` among `members`, or None if
        every member's description is None/empty.
    """
    candidates = [m.description for m in members if m.description]
    if not candidates:
        return None
    return max(candidates, key=len)


def resolve_apply_source(members: list[ClusterMember]) -> ClusterMember:
    """Pick the member whose `job_url` wins the apply-link survivorship
    rule (DECISIONS.md §2.7: source rank, not description length).

    Args:
        members: Every source posting in one dedup cluster. Must be
            non-empty.

    Returns:
        The winning `ClusterMember` — its `job_url` is the surviving
        apply_url, and (DECISIONS.md §5) its `title_for_display` is the
        one that must be used too, since both rules pick the same
        source by construction.
    """
    return min(members, key=lambda m: (_source_rank(m.source_name), m.source_name))


def build_sources_array(members: list[ClusterMember]) -> list[dict[str, object]]:
    """Build dim_job.sources[] (PLAN.md Step 11): every source URL kept,
    regardless of which one wins survivorship.

    Args:
        members: Every source posting in one dedup cluster.

    Returns:
        One `{source_name, job_url, first_seen_at}` dict per member,
        ordered by `source_name` for a deterministic array.
    """
    return [
        {
            "source_name": m.source_name,
            "job_url": m.job_url,
            "first_seen_at": m.first_seen_at,
        }
        for m in sorted(members, key=lambda m: m.source_name)
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
coverage run -m unittest tests.test_survivorship -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/dedup/survivorship.py packages/core/tests/test_survivorship.py
git commit -m "feat(job_search): add field-level survivorship rules"
```

---

### Task 6: Docs — run-order

**Files:**
- Modify: `dbt/README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Extend the dedup run-order section**

In `dbt/README.md`, immediately after the existing dedup run-order code
block (the one ending `dbt build --select dedup__similarity_scores`),
add:

```markdown
  Once `dedup__similarity_scores` is built, cluster:

  ```bash
  python3.11 -m apps.pipeline.app.cli cluster-jobs
  ```

  `cluster-jobs` reads `dedup__exact_duplicates`,
  `dedup__similarity_scores`, and Step 9's `dedup.pair_labels`/`dedup.
  calibration_thresholds`, and writes `silver.job_identity_map`
  (PLAN.md Step 10) — insert-only, so re-running it after new postings
  land only assigns the new ones; existing `job_group_id`s never
  change. The auto-match threshold is read live from the most recent
  `dedup.calibration_thresholds` row (Step 9's calibration flow), not
  hardcoded; a human `dedup.pair_labels` decision always overrides the
  automatic score for that specific pair.
```

- [ ] **Step 2: Commit**

```bash
git add dbt/README.md
git commit -m "docs(job_search): document the cluster-jobs run-order step"
```

---

## Final verification

- [ ] **Full Python quality gate and test suite**

```bash
cd job_search
ruff check . && isort --check-only . && black --check .
cd packages/core
coverage run -m unittest discover
coverage report -m
```

Expected: no new failures beyond any documented, pre-existing ones from
earlier steps.

- [ ] **End-to-end run against real data**

```bash
cd job_search
python3.11 -m apps.pipeline.app.cli compute-blocking-keys
cd dbt && dbt build --select dedup__exact_duplicates dedup__candidate_pairs && cd ..
python3.11 -m apps.pipeline.app.cli compute-similarity-features
python3.11 -m apps.pipeline.app.cli compute-title-similarity-scores
cd dbt && dbt build --select dedup__similarity_scores && cd ..
python3.11 -m apps.pipeline.app.cli cluster-jobs
python3.11 -m apps.pipeline.app.cli cluster-jobs
```

Expected: the second `cluster-jobs` call reports `rows_written=0` —
this is the "Done when" acceptance criterion from PLAN.md Step 10,
confirmed live, not just in the unit-level regression test from Task 4.

- [ ] **Surface open items to the user**

After this plan is fully executed, explicitly tell the user (don't bury
it in a commit message):
1. `apply_url` throughout PLAN.md/DECISIONS.md maps onto `job_url` — no
   separate column exists.
2. `sources[]` is implemented as a tested pure function
   (`build_sources_array`) but not yet persisted anywhere — Step 11's
   `dim_job` is what will call it.
3. The real calibration threshold (0.81 as of Step 9) and the 53 real
   `dedup.pair_labels` rows now directly drive production clustering —
   any future re-calibration in Step 9's UI takes effect on the very
   next `cluster-jobs` run for new postings (existing `job_group_id`s
   never get reassigned retroactively, by design).
