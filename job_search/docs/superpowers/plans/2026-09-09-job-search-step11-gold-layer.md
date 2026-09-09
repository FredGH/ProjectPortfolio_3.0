# Step 11 — Gold Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first gold-layer dbt models — `dim_job` (one row per
deduplicated job), `dim_company` (one row per normalised company), and
`fct_market_demand` (the base market-facing demand fact, excluding
manual entries) — finally giving Step 10's `silver.job_identity_map`
and `core.dedup.survivorship` a real consumer.

**Architecture:** One new Python write path,
`core/dedup/write_job_survivorship.py`, resolves Step 10's
already-tested survivorship rules (which source's `description` wins,
which source's `apply_url`/`title_for_display` wins) once per
`job_group_id` and persists the decision in a new `silver.
job_survivorship` table — reusing `core.dedup.survivorship`'s functions
directly rather than re-implementing the same rules in SQL, so Step
10's test suite remains the single source of correctness for those
rules. Everything else — picking the other fields from the same winning
posting, aggregating `sources[]`, deriving `first_seen_at`, building
`dim_company`, and building `fct_market_demand` — is pure dbt SQL, since
none of it needs anything SQL can't already express (`ROW_NUMBER()`,
`jsonb_agg`, `MIN()`, `GROUP BY`). This is the first `gold` schema in
the project; `dbt_project.yml` gains a `gold:` config block alongside
the existing `staging`/`intermediate`/`silver`/`dedup` ones.

**Tech Stack:** No new dependency. Reuses `core.dedup.survivorship`
(Step 10) and `core.normalisation.title.title_for_display` (Step 6,
written but never wired into a persisted column until now). dbt-core
1.11.11 / dbt-postgres 1.8.1, `dbt_utils.generate_surrogate_key` for
`dim_company`'s key, native `jsonb_agg`/`jsonb_build_object` for
`sources[]`.

**Spec:** `PLAN.md`'s "Step 11 — Gold layer" section (lines 802–827),
`plan/backlog.yml`'s `STEP-11` entry (`jira_key: JOB-152`), and
`DECISIONS.md` §2.7/§5 for the survivorship rules this model consumes.

## Scope note — three gaps this plan resolves, and one it can't yet

Step 10 shipped `core.dedup.survivorship` with two fields
(`ClusterMember.title_for_display`, `.first_seen_at`) that nothing in
the pipeline actually produced yet — documented at the time as a known
gap for this step to close. This plan closes both, differently:

1. **`title_for_display`** has real, tested logic sitting unused:
   `core.normalisation.title.title_for_display(raw)` (Step 6) exists,
   is tested, and is called by nothing. Task 2 wires it in — computed
   per posting inside the new `write_job_survivorship.py` write path,
   not persisted as its own column anywhere else, since the only thing
   that ever needs it is the survivorship winner's value.
2. **`first_seen_at`** does not need a Python computation at all: bronze
   is append-only (`bronze.raw_jobs`, one row per fetch, per PLAN.md
   Step 2/3/4), so `MIN(fetched_at)` grouped by `(source_name,
   source_job_id)` is the true first-seen timestamp — it was never
   genuinely missing data, just dropped by `silver__job_posting`'s
   contract. Task 3 computes this directly in `dim_job.sql` as a CTE.
3. **Every other field has no stated survivorship rule.** PLAN.md and
   DECISIONS.md only specify rules for `description` (longest wins)
   and `apply_url`/`title_for_display` (source rank, same winner for
   both). Nothing says what happens to `company`, `location`, salary/
   engagement fields, or `posted_at` when postings disagree. This
   plan's documented default: they come from the **same posting that
   won `apply_url`** — that posting is already established as this
   job's canonical, applied-through record, and Step 10's own
   `title_for_display` rule already applies exactly this reasoning to
   one field. `dim_job.sql`'s header comment states this explicitly so
   it reads as a decision, not an oversight.
4. **`category` cannot exist yet, and `fct_market_demand` is scoped
   accordingly.** `plan/backlog.yml`'s subtask text says "Build
   fct_market_demand (category x region x week)" — but `plan/
   backlog.yml` itself lists `STEP-11 blocks: [STEP-11A]`, and Step 11a
   (job categorisation) is the step that will *create* `category`.
   PLAN.md's own Step 11 section never mentions category for `fct_
   market_demand` at all — the category-grouped rollup it describes
   elsewhere is a **different, later** fact, `fct_market_volume`
   (`category x region x week x seniority x work_model`, PLAN.md line
   1484), built once Step 11a supplies `category`/`seniority_band`.
   Treating `backlog.yml`'s subtask wording as an error (it reads as
   copy-paste from `fct_market_volume`'s later spec) rather than a
   requirement, this plan builds `fct_market_demand` at `job_group_id`
   grain — PLAN.md's own words, "the base demand fact" — ready for
   `fct_market_volume` to aggregate from once category exists.

## Global Constraints

- SQL style per `.claude/rules/sql-style.md`; Python style per
  `.claude/rules/python-style.md`; tests per `.claude/rules/python-
  testing.md` (`unittest`, no DB mocking) and `.claude/rules/sql-
  testing.md` (dimensions: PK + FK relationships + accepted_values on
  status columns; facts: PK + FK relationships + business-rule singular
  tests).
- Next migration is `0012`, `down_revision = "0011"` — confirm via
  `ls db/migrations/versions/` at execution time.
- `silver.job_survivorship` is SHARED-zone (PLAN.md's two-zone rule):
  no `user_id`, no RLS, written by the migration/owner role only.
  `job_search_app` inherits SELECT automatically via migration 0010's
  `ALTER DEFAULT PRIVILEGES` rule — no explicit grant needed.
- `docker compose up -d postgres` must be running for every task's
  verification. dbt commands run from the `dbt/` directory with
  `DBT_PROFILES_DIR=.` and `POSTGRES_USER=job_search_owner
  POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search` set (dbt's
  `env_var()` reads real shell env vars, not `.env` directly). Python
  CLI commands run from the `job_search` root with `PYTHONPATH=
  packages/core:apps/pipeline` and `DATABASE_URL`/`APP_DATABASE_URL`
  overridden to `localhost` (`.env`'s values point at the Docker-
  internal `postgres` hostname, unreachable from a host-side process).
- Exact schema for the new Python-written table (do not deviate without
  updating this plan):

  ```sql
  silver.job_survivorship (
    job_group_id, winning_description, apply_source_name,
    apply_source_job_id, apply_job_url, apply_title_for_display,
    computed_at
  )
  ```

---

### Task 1: Migration and gold schema config

**Files:**
- Create: `db/migrations/versions/0012_create_silver_job_survivorship.py`
- Modify: `dbt/dbt_project.yml`

**Interfaces:**
- Produces: table `silver.job_survivorship` — PK `job_group_id`,
  columns `winning_description text nullable`, `apply_source_name text
  not null`, `apply_source_job_id text not null`, `apply_job_url text
  not null`, `apply_title_for_display text nullable`, `computed_at
  timestamptz not null default now()`. A new `gold` dbt schema/
  materialization config. Consumed by Task 2's write path and Task 3's
  `dim_job.sql`.

- [ ] **Step 1: Confirm the migration head**

```bash
cd job_search
ls db/migrations/versions/
```

Expected: `0011_create_silver_job_identity_map.py` is the latest. If
not, stop and re-check before renumbering.

- [ ] **Step 2: Write the migration**

`db/migrations/versions/0012_create_silver_job_survivorship.py`:

```python
"""create silver.job_survivorship

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-09

silver.job_survivorship is SHARED per-cluster survivorship data
(PLAN.md Step 11, DECISIONS.md §2.7/§5): which source's description/
apply-link/display-title wins for a job_group_id is the same for every
user, so no user_id, no RLS — same two-zone reasoning as job_identity_
map (0011).

Unlike job_identity_map, this table is UPSERTed, not insert-only:
job_group_id itself is the only value PLAN.md requires to be immutable
once assigned (DECISIONS.md §2.6) — which source currently "wins"
survivorship is free to change as new source data arrives for an
existing cluster.

Written only by the migration/owner role, via the `compute-
survivorship` pipeline CLI subcommand (core.dedup.
write_job_survivorship) — never by a live per-user request. Read by
dbt's gold.dim_job model as a plain source().

job_search_app inherits SELECT here automatically via migration 0010's
`ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver`
rule (see 0010's and 0011's docstrings) — no explicit grant needed in
this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_survivorship",
        sa.Column("job_group_id", sa.Text(), primary_key=True),
        sa.Column("winning_description", sa.Text(), nullable=True),
        sa.Column("apply_source_name", sa.Text(), nullable=False),
        sa.Column("apply_source_job_id", sa.Text(), nullable=False),
        sa.Column("apply_job_url", sa.Text(), nullable=False),
        sa.Column("apply_title_for_display", sa.Text(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="silver",
    )


def downgrade() -> None:
    op.drop_table("job_survivorship", schema="silver")
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
  -c "\d silver.job_survivorship"
```

Expected: PK on `job_group_id`, all 7 columns present with the right
nullability.

- [ ] **Step 4: Add the `gold` dbt config block**

In `dbt/dbt_project.yml`, add a `gold:` entry to the `models: job_search:`
block, alongside the existing `staging`/`intermediate`/`silver`/`dedup`
entries:

```yaml
    gold:
      +materialized: table
      +schema: gold
```

- [ ] **Step 5: Commit**

```bash
git add db/migrations/versions/0012_create_silver_job_survivorship.py dbt/dbt_project.yml
git commit -m "feat(job_search): add silver.job_survivorship and the gold dbt layer config"
```

---

### Task 2: Survivorship write path and `compute-survivorship` CLI subcommand

**Files:**
- Create: `packages/core/core/dedup/write_job_survivorship.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/integration/test_write_job_survivorship.py`

**Interfaces:**
- Consumes: `core.dedup.survivorship.{ClusterMember, resolve_description,
  resolve_apply_source}` (Step 10, already shipped);
  `core.normalisation.title.title_for_display` (Step 6, already
  shipped); `core.db.session.build_engine`; `core.settings.get_settings`.
- Produces: `write_job_survivorship(engine: Engine) -> int`; CLI
  subcommand `compute-survivorship`.

- [ ] **Step 1: Write the failing integration test**

`packages/core/tests/integration/test_write_job_survivorship.py`:

```python
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.dedup.write_job_survivorship import write_job_survivorship

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


class TestWriteJobSurvivorship(unittest.TestCase):
    """Integration test against a real Postgres instance.

    Inserts two fixture postings in the same job_group_id directly into
    silver.silver__job_posting (a dbt table) and silver.job_identity_map
    (safe here since nothing runs `dbt run` mid-test, matching the
    established test_write_job_identity_map.py pattern), then exercises
    the real write path.
    """

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.suffix = uuid.uuid4().hex
        self.job_group_id = f"group-{self.suffix}"
        self.job_key_greenhouse = f"gh-{self.suffix}"
        self.job_key_reed = f"reed-{self.suffix}"
        with self.engine.begin() as conn:
            # Greenhouse: shorter description, but wins apply_url via
            # source rank (greenhouse=0 beats reed=1).
            conn.execute(
                text(
                    "INSERT INTO silver.silver__job_posting "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at) "
                    "VALUES (:job_key, 'greenhouse', :source_job_id, "
                    "'https://greenhouse.example/job', "
                    "'https://greenhouse.example/job', 'api', "
                    "'Senior Data Engineer (m/f/d)', 'Test Co', 'London', "
                    "'short desc', NULL, now())"
                ),
                {"job_key": self.job_key_greenhouse, "source_job_id": f"gh-src-{self.suffix}"},
            )
            # Reed: longer description, but loses apply_url via source
            # rank — proves the two rules pick independently.
            conn.execute(
                text(
                    "INSERT INTO silver.silver__job_posting "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at) "
                    "VALUES (:job_key, 'reed', :source_job_id, "
                    "'https://reed.example/job', "
                    "'https://reed.example/job', 'api', "
                    "'Senior Data Engineer', 'Test Co', 'London', "
                    "'a much longer description than the other one', "
                    "NULL, now())"
                ),
                {"job_key": self.job_key_reed, "source_job_id": f"reed-src-{self.suffix}"},
            )
            for job_key, source_name, source_job_id in (
                (self.job_key_greenhouse, "greenhouse", f"gh-src-{self.suffix}"),
                (self.job_key_reed, "reed", f"reed-src-{self.suffix}"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO silver.job_identity_map "
                        "(source_name, source_job_id, job_group_id, "
                        "match_method, confidence) "
                        "VALUES (:source_name, :source_job_id, :job_group_id, "
                        "'fuzzy', 0.9)"
                    ),
                    {
                        "source_name": source_name,
                        "source_job_id": source_job_id,
                        "job_group_id": self.job_group_id,
                    },
                )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM silver.job_survivorship WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.job_identity_map WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.silver__job_posting WHERE job_key IN (:a, :b)"),
                {"a": self.job_key_greenhouse, "b": self.job_key_reed},
            )
        self.engine.dispose()

    def test_resolves_description_and_apply_url_independently(self) -> None:
        write_job_survivorship(self.engine)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT winning_description, apply_source_name, "
                    "apply_job_url, apply_title_for_display "
                    "FROM silver.job_survivorship WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).one()

        self.assertEqual(
            row.winning_description, "a much longer description than the other one"
        )
        self.assertEqual(row.apply_source_name, "greenhouse")
        self.assertEqual(row.apply_job_url, "https://greenhouse.example/job")
        # title_for_display strips "(m/f/d)" but keeps "Senior" —
        # DECISIONS.md §5, computed here via core.normalisation.title.
        self.assertEqual(row.apply_title_for_display, "Senior Data Engineer")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        write_job_survivorship(self.engine)
        write_job_survivorship(self.engine)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM silver.job_survivorship "
                    "WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).scalar_one()

        self.assertEqual(count, 1)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd packages/core
../../venv/bin/python -m coverage run -m unittest tests.integration.test_write_job_survivorship -v
```

Expected: `ModuleNotFoundError: No module named 'core.dedup.write_job_survivorship'`.

- [ ] **Step 3: Implement the write path**

`packages/core/core/dedup/write_job_survivorship.py`:

```python
"""Batch write path for silver.job_survivorship (PLAN.md Step 11).

For every job_group_id in silver.job_identity_map, resolves the
field-level survivorship winners Step 10's core.dedup.survivorship
module decided how to compute but never had a caller for: which
source's description wins (longest non-empty), which source's
apply_url/title_for_display wins (source rank), per DECISIONS.md §2.7
and §5. Runs outside dbt for the same reason every earlier "Python
computes, dbt joins" step did — but the deeper reason here is reuse,
not SQL's inability: source-rank tie-breaking and the longest-string
rule ARE expressible in SQL, but reusing the exact, already-tested
survivorship.py functions here means Step 10's test suite IS this
step's correctness guarantee. A second SQL implementation of the same
rules would need its own tests and could silently drift from the
first.

Also computes title_for_display here, per posting, using Step 6's
core.normalisation.title.title_for_display — a pure function that
existed since Step 6 but was never wired into a persisted column until
now (see this plan's "Scope note").

Unlike silver.job_identity_map, this table is a plain UPSERT: a
survivorship winner is not an identity decision (job_group_id is the
only value PLAN.md requires to be immutable) — if new source data
arrives for an existing group, its winner should be free to change.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import Engine, text

from core.dedup.survivorship import (
    ClusterMember,
    resolve_apply_source,
    resolve_description,
)
from core.normalisation.title import title_for_display

_SELECT_GROUP_MEMBERS = text(
    """
    SELECT
        im.job_group_id,
        sp.source_name,
        sp.source_job_id,
        sp.job_url,
        sp.title,
        sp.description
    FROM silver.job_identity_map AS im
    INNER JOIN silver.silver__job_posting AS sp
        ON im.source_name = sp.source_name AND im.source_job_id = sp.source_job_id
    """
)

_UPSERT = text(
    """
    INSERT INTO silver.job_survivorship (
        job_group_id, winning_description, apply_source_name,
        apply_source_job_id, apply_job_url, apply_title_for_display
    ) VALUES (
        :job_group_id, :winning_description, :apply_source_name,
        :apply_source_job_id, :apply_job_url, :apply_title_for_display
    )
    ON CONFLICT (job_group_id) DO UPDATE SET
        winning_description = EXCLUDED.winning_description,
        apply_source_name = EXCLUDED.apply_source_name,
        apply_source_job_id = EXCLUDED.apply_source_job_id,
        apply_job_url = EXCLUDED.apply_job_url,
        apply_title_for_display = EXCLUDED.apply_title_for_display,
        computed_at = now()
    """
)


def write_job_survivorship(engine: Engine) -> int:
    """Resolve and upsert one survivorship row per job_group_id.

    Args:
        engine: The migration/owner engine — SHARED-zone, like every
            other dedup/silver Python-written table.

    Returns:
        The number of job_group_id groups written (inserted or
        updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_GROUP_MEMBERS).all()

        members_by_group: dict[str, list[ClusterMember]] = defaultdict(list)
        # ClusterMember has no source_job_id field (Step 10 never
        # needed one for pure resolution) — track it separately, keyed
        # by (source_name, job_url), to recover the winner's natural
        # key afterwards. Assumes job_url is unique per source within a
        # group, true for any real posting.
        source_job_id_by_group: dict[str, dict[tuple[str, str], str]] = defaultdict(
            dict
        )
        for row in rows:
            display_title = title_for_display(row.title)
            members_by_group[row.job_group_id].append(
                ClusterMember(
                    source_name=row.source_name,
                    job_url=row.job_url,
                    title_for_display=display_title or "",
                    description=row.description,
                    first_seen_at=None,
                )
            )
            source_job_id_by_group[row.job_group_id][
                (row.source_name, row.job_url)
            ] = row.source_job_id

        written = 0
        for job_group_id, members in members_by_group.items():
            winning_description = resolve_description(members)
            winner = resolve_apply_source(members)
            winner_source_job_id = source_job_id_by_group[job_group_id][
                (winner.source_name, winner.job_url)
            ]
            conn.execute(
                _UPSERT,
                {
                    "job_group_id": job_group_id,
                    "winning_description": winning_description,
                    "apply_source_name": winner.source_name,
                    "apply_source_job_id": winner_source_job_id,
                    "apply_job_url": winner.job_url,
                    "apply_title_for_display": winner.title_for_display or None,
                },
            )
            written += 1
    return written
```

- [ ] **Step 4: Wire the `compute-survivorship` CLI subcommand**

In `apps/pipeline/app/cli.py`, add the import alongside the other
`core.dedup.write_*` imports:

```python
from core.dedup.write_job_survivorship import write_job_survivorship
```

Add the command function, next to `_cmd_cluster_jobs`:

```python
def _cmd_compute_survivorship(args: argparse.Namespace) -> int:
    """Run the `compute-survivorship` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    written = write_job_survivorship(engine)
    print(f"compute-survivorship complete: rows_written={written}")
    return 0
```

Register it in `main()`, next to the `cluster-jobs` subparser:

```python
    subparsers.add_parser(
        "compute-survivorship",
        help="Resolve field-level survivorship for every job_group_id",
    )
```

And dispatch it alongside the other `args.command ==` checks:

```python
    if args.command == "compute-survivorship":
        return _cmd_compute_survivorship(args)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd packages/core
../../venv/bin/python -m coverage run -m unittest tests.integration.test_write_job_survivorship -v
```

Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/dedup/write_job_survivorship.py \
        packages/core/tests/integration/test_write_job_survivorship.py \
        apps/pipeline/app/cli.py
git commit -m "feat(job_search): add compute-survivorship pipeline subcommand"
```

---

### Task 3: `dim_job` dbt model

**Files:**
- Create: `dbt/models/gold/dim_job.sql`
- Create: `dbt/models/gold/_gold.yml`

**Interfaces:**
- Consumes: `silver.job_survivorship` (Task 2, via `source()`),
  `silver.job_identity_map` (Step 10, via `source()`),
  `{{ ref('silver__job_posting') }}`, `dedup.job_blocking_keys` (Step
  7, via `source()`), `bronze.raw_jobs` (via `source()`, already
  declared in `_sources.yml`).
- Produces: model `gold.dim_job`, grain `job_group_id` (unique). Columns
  include `apply_source_name`/`apply_source_job_id` (not part of the
  original spec, added so Task 4/5 can join back without a fragile URL
  match) and `normalised_company`/`country_iso`/`region` (so Task 4/5
  don't need to re-derive the join chain to `job_blocking_keys`).
  Consumed by Task 4's `dim_company.sql` and Task 5's `fct_market_
  demand.sql`.

- [ ] **Step 1: Write `_gold.yml`'s sources block**

`dbt/models/gold/_gold.yml` (model entries added in later steps of this
task and Tasks 4-5 — start with just the sources block and this task's
model):

```yaml
version: 2

sources:
  - name: silver_ingest
    schema: silver
    tables:
      - name: job_identity_map
        description: >
          Written by the cluster-jobs pipeline CLI subcommand
          (core.dedup.write_job_identity_map), not dbt — union-find
          clustering has no dbt equivalent (PLAN.md Step 10). Maps each
          source posting to its stable job_group_id. Insert-only:
          job_group_id never changes once assigned.
        columns:
          - name: job_group_id
            description: "Matches gold.dim_job.job_group_id."
      - name: job_survivorship
        description: >
          Written by the compute-survivorship pipeline CLI subcommand
          (core.dedup.write_job_survivorship), not dbt — reuses Step
          10's already-tested core.dedup.survivorship functions rather
          than re-implementing the same rules in SQL (PLAN.md Step 11).
          One row per job_group_id: which source's description/apply
          link/display title wins.
        columns:
          - name: job_group_id
            description: "Matches gold.dim_job.job_group_id."

models:
  - name: dim_job
    description: >
      One row per deduplicated job (PLAN.md Step 11). job_group_id is
      the PK; description/apply_url/title_for_display are resolved by
      silver.job_survivorship (Step 10's rules, reused verbatim); every
      other field mirrors the same posting that won apply_url — see
      this model's header comment for why.
    columns:
      - name: job_group_id
        data_type: text
        tests: [unique, not_null]
      - name: apply_url
        data_type: text
        tests: [not_null]
      - name: title_for_display
        data_type: text
      - name: description
        data_type: text
      - name: title_raw
        data_type: text
      - name: company
        data_type: text
      - name: normalised_company
        data_type: text
        tests: [not_null]
      - name: location
        data_type: text
      - name: country_iso
        data_type: text
      - name: region
        data_type: text
      - name: salary_raw
        data_type: text
      - name: rate_currency
        data_type: text
      - name: rate_annualised
        data_type: numeric
      - name: rate_daily_equivalent
        data_type: numeric
      - name: contract_length_months
        data_type: integer
      - name: engagement_type
        data_type: text
        tests:
          - accepted_values:
              values: ['permanent', 'contract', 'ftc', 'interim', 'unknown']
      - name: ir35_status
        data_type: text
        tests:
          - accepted_values:
              values:
                ['inside', 'outside', 'not_applicable', 'undetermined', 'unknown']
      - name: engagement_vehicle
        data_type: text
        tests:
          - accepted_values:
              values: ['umbrella', 'limited', 'paye', 'agency_paye', 'unknown']
      - name: rate_basis
        data_type: text
        tests:
          - accepted_values:
              values: ['annual', 'daily', 'hourly', 'unknown']
      - name: extension_likelihood
        data_type: text
        tests:
          - accepted_values:
              values: ['likely', 'possible', 'unlikely', 'unstated']
      - name: posted_at
        data_type: timestamptz
      - name: entry_method
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: ['api', 'manual', 'scraped']
      - name: apply_source_name
        data_type: text
        tests: [not_null]
      - name: apply_source_job_id
        data_type: text
        tests: [not_null]
      - name: sources
        data_type: jsonb
        tests: [not_null]
```

- [ ] **Step 2: Write `dim_job.sql`**

`dbt/models/gold/dim_job.sql`:

```sql
-- dim_job: one row per deduplicated job (PLAN.md Step 11).
-- Grain: job_group_id (unique).
--
-- Field-level survivorship (DECISIONS.md §2.7, §5): description and
-- apply_url/title_for_display are resolved separately by Step 10's
-- core.dedup.survivorship (see silver.job_survivorship, written by the
-- compute-survivorship pipeline CLI subcommand — not dbt, since the
-- source-rank/longest-string rules are already tested there and this
-- avoids a second implementation of the same rules drifting from the
-- first). Every OTHER field (company, location, salary, engagement
-- terms, posted_at) has no survivorship rule stated anywhere in
-- PLAN.md/DECISIONS.md — this model's documented default is that they
-- come from the SAME posting that won apply_url, on the reasoning that
-- it is already established as this job's canonical record (the one a
-- user actually applies through), and Step 10's own title_for_display
-- rule already applies this exact reasoning to one field.

WITH survivorship AS (

    SELECT * FROM {{ source('silver_ingest', 'job_survivorship') }}

),

identity_map AS (

    SELECT * FROM {{ source('silver_ingest', 'job_identity_map') }}

),

-- The posting whose apply_url won survivorship — every field not
-- covered by its own explicit rule is taken from here.
apply_posting AS (

    SELECT sp.*
    FROM {{ ref('silver__job_posting') }} AS sp
    INNER JOIN survivorship AS s
        ON sp.source_name = s.apply_source_name
        AND sp.source_job_id = s.apply_source_job_id

),

-- normalised_company/country_iso/region, from the same apply-winning
-- posting's blocking key (Step 7) — exposed here so dim_company and
-- fct_market_demand don't need to re-derive this join chain.
apply_blocking_keys AS (

    SELECT
        s.job_group_id,
        bk.normalised_company,
        bk.country_iso,
        bk.region
    FROM survivorship AS s
    INNER JOIN {{ ref('silver__job_posting') }} AS sp
        ON s.apply_source_name = sp.source_name
        AND s.apply_source_job_id = sp.source_job_id
    INNER JOIN {{ source('dedup_ingest', 'job_blocking_keys') }} AS bk
        ON sp.job_key = bk.job_key

),

-- First-seen timestamp per source posting, from bronze's full
-- (uncollapsed) append-only history — int_jobs__unioned/silver__job_
-- posting only carry the current version, not the first-fetched one.
first_seen AS (

    SELECT
        source_name,
        source_job_id,
        MIN(fetched_at) AS first_seen_at
    FROM {{ source('bronze', 'raw_jobs') }}
    GROUP BY source_name, source_job_id

),

-- Every source URL for a job_group_id, regardless of which one won
-- survivorship — mirrors core.dedup.survivorship.build_sources_array's
-- shape exactly, computed here in SQL since it is pure aggregation
-- with no decision-logic (unlike description/apply_url).
sources AS (

    SELECT
        im.job_group_id,
        jsonb_agg(
            jsonb_build_object(
                'source_name', sp.source_name,
                'job_url', sp.job_url,
                'first_seen_at', fs.first_seen_at
            )
            ORDER BY sp.source_name
        ) AS sources
    FROM identity_map AS im
    INNER JOIN {{ ref('silver__job_posting') }} AS sp
        ON im.source_name = sp.source_name AND im.source_job_id = sp.source_job_id
    LEFT JOIN first_seen AS fs
        ON sp.source_name = fs.source_name AND sp.source_job_id = fs.source_job_id
    GROUP BY im.job_group_id

)

SELECT
    s.job_group_id,
    s.apply_job_url AS apply_url,
    s.apply_title_for_display AS title_for_display,
    s.winning_description AS description,
    ap.title AS title_raw,
    ap.company,
    abk.normalised_company,
    ap.location,
    abk.country_iso,
    abk.region,
    ap.salary_raw,
    ap.rate_currency,
    ap.rate_annualised,
    ap.rate_daily_equivalent,
    ap.contract_length_months,
    ap.engagement_type,
    ap.ir35_status,
    ap.engagement_vehicle,
    ap.rate_basis,
    ap.extension_likelihood,
    ap.posted_at,
    ap.entry_method,
    s.apply_source_name,
    s.apply_source_job_id,
    src.sources
FROM survivorship AS s
INNER JOIN apply_posting AS ap
    ON s.apply_source_name = ap.source_name AND s.apply_source_job_id = ap.source_job_id
INNER JOIN apply_blocking_keys AS abk
    ON s.job_group_id = abk.job_group_id
INNER JOIN sources AS src
    ON s.job_group_id = src.job_group_id
```

- [ ] **Step 3: Run and verify**

```bash
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search \
  DBT_PROFILES_DIR=. dbt build --select dim_job
```

Expected: model builds, all schema tests PASS. If a join fails to
resolve (e.g. a `job_blocking_keys` row missing for some apply-winning
posting because `compute-blocking-keys` hasn't been rerun since new
postings landed), re-run the out-of-band CLI steps documented in `dbt/
README.md`'s dedup section first, then retry.

- [ ] **Step 4: Spot-check one real multi-source job**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
  SELECT job_group_id, apply_url, title_for_display, jsonb_array_length(sources) AS source_count
  FROM gold.dim_job
  WHERE jsonb_array_length(sources) > 1
  LIMIT 3;
"
```

Expected: at least one row with `source_count > 1` and a non-null
`title_for_display` (e.g. seniority kept, `(m/f/d)` stripped).

- [ ] **Step 5: Commit**

```bash
git add dbt/models/gold/dim_job.sql dbt/models/gold/_gold.yml
git commit -m "feat(job_search): add gold.dim_job"
```

---

### Task 4: `dim_company` dbt model

**Files:**
- Create: `dbt/models/gold/dim_company.sql`
- Modify: `dbt/models/gold/_gold.yml`

**Interfaces:**
- Consumes: `{{ ref('dim_job') }}` (Task 3).
- Produces: model `gold.dim_company`, grain `company_key` (unique).

- [ ] **Step 1: Add `dim_company`'s entry to `_gold.yml`**

Append to the `models:` list in `dbt/models/gold/_gold.yml`:

```yaml
  - name: dim_company
    description: >
      One row per normalised company (PLAN.md Step 11). Greenfield — no
      prior dim_company convention exists; see this model's header
      comment for the display-name resolution rule.
    columns:
      - name: company_key
        data_type: text
        tests: [unique, not_null]
      - name: normalised_company
        data_type: text
        tests: [unique, not_null]
      - name: company_name
        data_type: text
        tests: [not_null]
      - name: job_count
        data_type: bigint
        tests:
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 1
```

- [ ] **Step 2: Write `dim_company.sql`**

`dbt/models/gold/dim_company.sql`:

```sql
-- dim_company: one row per normalised company (PLAN.md Step 11).
-- Grain: company_key (unique). Greenfield — no prior dim_company or
-- company-identity convention exists in this codebase; this model
-- introduces one, reusing Step 7's normalise_company output (already
-- computed for dedup blocking and exposed on dim_job as
-- normalised_company) as the grouping key. normalise_company is
-- explicitly documented as matching-only, never a display string
-- (core/normalisation/company.py) — so a separate display name is
-- resolved here as the most frequent raw company spelling within the
-- group (ties broken alphabetically for determinism).

WITH company_spellings AS (

    SELECT
        job_group_id,
        normalised_company,
        company AS raw_company
    FROM {{ ref('dim_job') }}
    WHERE normalised_company != ''

),

spelling_counts AS (

    SELECT
        normalised_company,
        raw_company,
        COUNT(*) AS spelling_count
    FROM company_spellings
    GROUP BY normalised_company, raw_company

),

ranked_spellings AS (

    SELECT
        normalised_company,
        raw_company,
        ROW_NUMBER() OVER (
            PARTITION BY normalised_company
            ORDER BY spelling_count DESC, raw_company
        ) AS spelling_rank
    FROM spelling_counts

),

job_counts AS (

    SELECT
        normalised_company,
        COUNT(DISTINCT job_group_id) AS job_count
    FROM company_spellings
    GROUP BY normalised_company

)

SELECT
    {{ dbt_utils.generate_surrogate_key(['ranked_spellings.normalised_company']) }}
        AS company_key,
    ranked_spellings.normalised_company,
    ranked_spellings.raw_company AS company_name,
    job_counts.job_count
FROM ranked_spellings
INNER JOIN job_counts USING (normalised_company)
WHERE ranked_spellings.spelling_rank = 1
```

- [ ] **Step 3: Run and verify**

```bash
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search \
  DBT_PROFILES_DIR=. dbt build --select dim_company
```

Expected: model builds, all schema tests PASS.

- [ ] **Step 4: Commit**

```bash
git add dbt/models/gold/dim_company.sql dbt/models/gold/_gold.yml
git commit -m "feat(job_search): add gold.dim_company"
```

---

### Task 5: `fct_market_demand` dbt model

**Files:**
- Create: `dbt/models/gold/fct_market_demand.sql`
- Create: `dbt/tests/assert_fct_market_demand_excludes_manual_entries.sql`
- Modify: `dbt/models/gold/_gold.yml`

**Interfaces:**
- Consumes: `{{ ref('dim_job') }}` (Task 3), `{{ ref('dim_company') }}`
  (Task 4).
- Produces: model `gold.fct_market_demand`, grain `job_group_id`
  (unique, FK to `dim_job`).

- [ ] **Step 1: Add `fct_market_demand`'s entry to `_gold.yml`**

Append to the `models:` list in `dbt/models/gold/_gold.yml`:

```yaml
  - name: fct_market_demand
    description: >
      The base demand fact (PLAN.md Step 11), filtered to entry_method
      = 'api' — see this model's header comment for the selection-bias
      rationale and why no category column exists yet.
    columns:
      - name: job_group_id
        data_type: text
        tests:
          - unique
          - not_null
          - relationships:
              to: ref('dim_job')
              field: job_group_id
      - name: company_key
        data_type: text
        tests:
          - relationships:
              to: ref('dim_company')
              field: company_key
      - name: country_iso
        data_type: text
      - name: region
        data_type: text
      - name: posted_at
        data_type: timestamptz
      - name: posted_week
        data_type: timestamptz
      - name: entry_method
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: ['api']
```

- [ ] **Step 2: Write `fct_market_demand.sql`**

`dbt/models/gold/fct_market_demand.sql`:

```sql
-- fct_market_demand: the base demand fact (PLAN.md Step 11), one row
-- per dim_job, filtered to entry_method = 'api'.
-- Grain: job_group_id (unique, FK to dim_job).
--
-- Selection-bias filter: manual entries are jobs a user chose to paste
-- in themselves, not postings the pipeline discovered independently.
-- If they flowed into a market-facing fact, the resulting chart would
-- measure the user's own browsing habits, not the market (PLAN.md
-- Step 11). Manual entries still flow freely into dim_job/scoring/the
-- application pipeline, where this bias is harmless — this filter
-- exists ONLY here, not upstream, so a future model built on dim_job
-- doesn't inherit it by accident.
--
-- No category column yet: Step 11a (job categorisation) is the next
-- step and is explicitly blocked BY this one finishing first
-- (plan/backlog.yml STEP-11 blocks STEP-11A) — category cannot exist
-- before it runs. This model is deliberately grained at job_group_id
-- rather than pre-aggregated to (category x region x week): that
-- coarser aggregation is PLAN.md's separately-specified fct_market_
-- volume (category x region x week x seniority x work_model, PLAN.md
-- line 1484), built once Step 11a supplies category/seniority_band.

SELECT
    dj.job_group_id,
    dc.company_key,
    dj.country_iso,
    dj.region,
    dj.posted_at,
    DATE_TRUNC('week', dj.posted_at) AS posted_week,
    dj.entry_method
FROM {{ ref('dim_job') }} AS dj
LEFT JOIN {{ ref('dim_company') }} AS dc
    ON dj.normalised_company = dc.normalised_company
WHERE dj.entry_method = 'api'
```

`LEFT JOIN` to `dim_company`, not `INNER`: `dim_company` excludes rows
where `normalised_company = ''` (an unparseable/missing company name),
and this fact should still count a job with no resolved company rather
than silently dropping it — `company_key` is nullable here by design.

- [ ] **Step 3: Write the singular acceptance test**

`dbt/tests/assert_fct_market_demand_excludes_manual_entries.sql`:

```sql
-- assert_fct_market_demand_excludes_manual_entries: PLAN.md Step 11's
-- explicit "Done when" criterion. A test passes on zero rows returned
-- — this must never find a manual entry, even if a future edit
-- accidentally loosens the model's WHERE clause.

SELECT job_group_id
FROM {{ ref('fct_market_demand') }}
WHERE entry_method != 'api'
```

- [ ] **Step 4: Run and verify**

```bash
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search \
  DBT_PROFILES_DIR=. dbt build --select fct_market_demand
```

Expected: model builds, all schema tests and the singular test PASS.

- [ ] **Step 5: Confirm the selection-bias filter against real data**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
  SELECT dj.entry_method, COUNT(*) FROM gold.dim_job dj GROUP BY 1;
" -c "
  SELECT COUNT(*) FROM gold.fct_market_demand;
"
```

Expected: `gold.dim_job`'s `entry_method` breakdown includes at least
one non-`'api'` row (this project has manual entries per earlier
steps), and `gold.fct_market_demand`'s total row count is strictly less
than `gold.dim_job`'s total row count.

- [ ] **Step 6: Commit**

```bash
git add dbt/models/gold/fct_market_demand.sql \
        dbt/tests/assert_fct_market_demand_excludes_manual_entries.sql \
        dbt/models/gold/_gold.yml
git commit -m "feat(job_search): add gold.fct_market_demand"
```

---

### Task 6: Docs — gold layer run order

**Files:**
- Modify: `dbt/README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Add a gold layer section**

In `dbt/README.md`, after the existing dedup section (including its
`cluster-jobs` addition from Step 10), add a new bullet following the
same format as the `dedup` bullet:

```markdown
- **gold** (`models/gold/`, tables, schema `gold`) — `dim_job`,
  `dim_company`, and `fct_market_demand` (PLAN.md Step 11). Depends on
  `silver.job_survivorship`, written **outside dbt** by the
  `compute-survivorship` pipeline CLI subcommand, which itself depends
  on `silver.job_identity_map` (Step 10) already being populated.

  After `cluster-jobs` has run (see the dedup section above), build the
  gold layer:

  ```bash
  python3.11 -m apps.pipeline.app.cli compute-survivorship
  dbt build --select dim_job dim_company fct_market_demand
  ```

  `compute-survivorship` is a plain UPSERT (unlike `cluster-jobs`'s
  insert-only design) — safe to re-run any time source data changes for
  an existing cluster, since which source "wins" survivorship is never
  required to stay fixed, only `job_group_id` itself.
```

- [ ] **Step 2: Commit**

```bash
git add dbt/README.md
git commit -m "docs(job_search): document the gold layer run order"
```

---

## Final verification

- [ ] **Full Python quality gate and test suite**

```bash
cd job_search
ruff check . && isort --check-only . && black --check .
cd packages/core
../../venv/bin/python -m coverage run -m unittest discover
coverage report -m
```

Expected: no new failures beyond any documented, pre-existing ones from
earlier steps.

- [ ] **Full gold-layer dbt build against real data**

```bash
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search \
  DBT_PROFILES_DIR=. dbt build --select dim_job dim_company fct_market_demand
```

Expected: `dim_job` has a unique `job_group_id` (PLAN.md's own "Done
when" criterion), and `fct_market_demand` excludes every manual entry —
both already asserted by dbt tests above, confirmed clean here as one
final end-to-end run.

- [ ] **Surface open items to the user**

After this plan is fully executed, explicitly tell the user:
1. `fct_market_demand` is built at `job_group_id` grain, not `category
   x region x week` — `backlog.yml`'s subtask wording for this appears
   to be a copy-paste error from the later `fct_market_volume` spec;
   flagged rather than silently followed. Confirm this reading is
   correct before Step 11a assumes otherwise.
2. Every `dim_job` field without an explicit survivorship rule
   (company, location, salary/engagement terms, posted_at) defaults to
   the same posting that won `apply_url` — a documented default, not
   something PLAN.md/DECISIONS.md states outright.
3. `dim_company` is entirely new ground — no prior convention existed
   for company identity in this codebase. Its display-name rule (most
   frequent raw spelling) is this plan's own reasonable default, not a
   specified requirement.
