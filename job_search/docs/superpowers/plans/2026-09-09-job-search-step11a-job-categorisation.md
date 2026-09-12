# Step 11a — Job Categorisation and Seniority Banding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify every `gold.dim_job` row into one of 7 categories (plus
a separate 4-value `qa_category` for the question bank) and a 5-value
seniority band, via a hybrid cascade — cheap rules first, then embedding
nearest-centroid, then LLM only for the residual — persisting
`category`, `category_confidence`, `category_method`, `qa_category`, and
`seniority_band`.

**Architecture:** A new top-level Python package, `core/classification/`,
implements the 3-stage cascade as pure, independently-testable functions
(`classify_by_rules`, `classify_by_embedding`, `classify_by_llm`), each
only invoked for titles the previous stage declined to classify. A new
`core/embedding/` package adds the one capability this project's existing
`core.llm` adapters don't have — Ollama's `/api/embeddings` endpoint
(the existing `OllamaAdapter` only calls `/api/generate`, i.e.
completions). One write path, `core/classification/write_job_category.py`,
orchestrates the cascade over every unclassified `job_group_id` and
upserts into a new `silver.job_category` table; `dbt/models/gold/
dim_job.sql` joins it in, following the exact `job_survivorship`
precedent from Step 11.

**Tech Stack:** Ollama (`nomic-embed-text`, already the `Settings.
embedding_model` default) for embeddings, over the existing `docker-
compose.yml` `ollama` service (defined, not yet started). Anthropic
Claude via the existing `core.llm` gateway for the LLM residual stage —
no new provider code needed there, only a new `config/llm_tasks.yml`
entry. No new Python dependency — `httpx` (already a dependency) is
sufficient for both the embeddings HTTP call and reusing the existing
LLM adapters.

**Spec:** `PLAN.md`'s "Step 11a — Job categorisation and seniority
banding" section (lines 830–890), `plan/backlog.yml`'s `STEP-11A` entry
(`jira_key: JOB-158`), and `DECISIONS.md` §1 (LLM gateway) and §2.8
(embed locally with Ollama).

## Scope note — this plan's central finding: the embedding-infrastructure blocker from Step 8 does not apply here

Step 8's plan deferred a dedup embedding signal because "no embedding
infrastructure exists yet... PLAN.md defers [embedding dimension/index
type] to Step 15." Investigated fresh for this plan, since Step 11a
also wants embeddings:

- The `ollama` service is now defined in `docker-compose.yml` (added
  since Step 8) but not running; no `core.embedding` module exists yet;
  the existing `OllamaAdapter` only implements `/api/generate`
  (completions), not `/api/embeddings`. Confirmed live: starting the
  container and pulling `nomic-embed-text` works, and `POST /api/
  embeddings` returns a 768-dimensional vector.
- **The critical distinction Step 8 didn't need to make**: PLAN.md's
  Step 15 section (lines 1175–1183) defers a decision about
  **persisted, indexed pgvector storage** — "embedding dimension is
  baked in at first embedding... pgvector index type... should be
  chosen at the same time" — for a durable CV/JD similarity-search
  corpus (`embed CV sections and JD chunks... into pgvector... store
  embedding_model with every vector`, PLAN.md line 1138). An index type
  is meaningless for a value that is computed and discarded.
- Step 11a's embedding use is exactly that: compute a title's vector,
  compare it to a handful of category centroids by cosine distance,
  keep only the resulting `(category, confidence)` decision. The vector
  itself is never written to a database column, never indexed, never
  reused across rows. Nothing in Step 11a's, Step 15's, or DECISIONS.md
  §2.8's text extends the Step 15 deferral to this ephemeral case.

This plan therefore builds and starts real embedding infrastructure now
— it is not blocked, and waiting for Step 15 would block Step 11a for
no real reason.

## Scope note — no existing labelled examples; this plan hand-curates a starting seed set

Grepped the whole repo: no `category_map.yml`, no centroid data, no
labelled category examples exist anywhere — this is genuinely
greenfield (Step 9/10's "labelled pairs" are dedup pair-labels, an
unrelated mechanism). Building a full human-review labelling UI (Step
9's own review-queue pattern) is out of proportion for a 3-point
backlog item. This plan instead hand-curates a small, disclosed seed
set (`config/category_seed_examples.yml`, ~6 example titles per
category) used to build embedding centroids fresh on every classifier
run. This is a judgment call, not a specified requirement — flagged
here so it reads as a decision. PLAN.md's own acceptance activity
("hand-check 100 classifications, record the agreement figure") is
exactly the mechanism for discovering whether this starting seed set
needs more examples or reweighting; this plan does not attempt to
automate that check away.

## Scope note — "route low-confidence classifications to a review list" is satisfied by a queryable column, not a new review UI

`backlog.yml`'s STEP-11A subtasks include "Route low-confidence
classifications to a review list rather than guessing." Step 9 already
established what a real review-list feature looks like in this
codebase (a dedicated Streamlit page, its own API endpoints, a
labels table) — building a second one here would be disproportionate
to a 3-point backlog item, and PLAN.md's own Step 11a text never
mentions a review UI. This plan satisfies the underlying need —
low-confidence classifications must be findable, not silently
accepted as if certain — by persisting `category_confidence` on every
row (Task 1/6): `SELECT * FROM gold.dim_job WHERE category_confidence
< 0.7 ORDER BY category_confidence` already **is** a review list, just
without a dedicated UI. Building a Streamlit page for it is real,
separate follow-on work if the hand-check activity (Task 7) shows it's
needed — flagged to the user rather than built unprompted.

## Scope note — `qa_category`'s "AI engineer" is a different string from the classification taxonomy's "ai_ml_engineer"

PLAN.md is explicit that these are two separate taxonomies for two
different purposes (analytical grain vs. question-bank grain) with a
stated mapping (analytics engineer → data engineer; platform/DevOps →
software engineer; AI/ML engineer → AI engineer). This plan uses
`snake_case` identifiers matching this project's existing DB-value
convention (`engagement_type='permanent'`, etc.): the classification
taxonomy's `ai_ml_engineer` maps to the qa_category taxonomy's
`ai_engineer` — genuinely different strings, not a typo. `other` has no
qa_category mapping (`null`) — there is no reasonable question-bank
grain for a job the classifier couldn't place.

## Global Constraints

- SQL style per `.claude/rules/sql-style.md`; Python style per
  `.claude/rules/python-style.md`; tests per `.claude/rules/python-
  testing.md` (`unittest`, real connections — this project's existing
  `test_*_connector_live.py` files establish that live external-API
  integration tests, gated on credential presence via
  `unittest.skipUnless`, are this project's norm, not an exception) and
  `.claude/rules/sql-testing.md`.
- Next migration is `0013`, `down_revision = "0012"` — confirm via
  `ls db/migrations/versions/` at execution time.
- `silver.job_category` is SHARED-zone (PLAN.md's two-zone rule): no
  `user_id`, no RLS, written by the migration/owner role only.
  `job_search_app` inherits SELECT automatically via migration 0010's
  `ALTER DEFAULT PRIVILEGES` rule.
- `docker compose up -d postgres ollama` must be running for every
  task's verification; `ollama pull nomic-embed-text` must have been
  run once (confirmed working this session: `POST http://localhost:
  11434/api/embeddings` with `{"model": "nomic-embed-text", "prompt":
  "..."}` returns a 768-float `embedding` array).
- Python CLI commands run from the `job_search` root with
  `PYTHONPATH=packages/core:apps/pipeline` and `DATABASE_URL`/
  `APP_DATABASE_URL` overridden to `localhost`. dbt commands run from
  `dbt/` with `DBT_PROFILES_DIR=.` and the three `POSTGRES_*` env vars
  set (see Step 11's plan for the exact invocation — same pattern).
- The classification taxonomy (7 values):
  `software_engineer`, `data_engineer`, `data_scientist`,
  `ai_ml_engineer`, `analytics_engineer`, `platform_devops`, `other`.
  The `qa_category` taxonomy (4 active values, `other` maps to `null`):
  `software_engineer`, `data_engineer`, `data_scientist`, `ai_engineer`.
  The `seniority_band` taxonomy (5 values): `junior`, `mid`, `senior`,
  `lead`, `principal`.
- Exact schema for the new table (do not deviate without updating this
  plan):

  ```sql
  silver.job_category (
    job_group_id, category, category_confidence, category_method,
    qa_category, seniority_band, computed_at
  )
  ```

---

### Task 1: Migration, category map config, seed examples, LLM task config

**Files:**
- Create: `db/migrations/versions/0013_create_silver_job_category.py`
- Create: `config/category_map.yml`
- Create: `config/category_seed_examples.yml`
- Modify: `config/llm_tasks.yml`

**Interfaces:**
- Produces: table `silver.job_category` — PK `job_group_id`, columns
  `category text not null`, `category_confidence numeric not null`,
  `category_method text not null check in ('rules','embedding','llm')`,
  `qa_category text nullable`, `seniority_band text not null`,
  `computed_at timestamptz not null default now()`. `config/
  category_map.yml`'s `qa_category_map` dict. `config/
  category_seed_examples.yml`'s `categories` dict (category → example
  titles). `config/llm_tasks.yml`'s new `job_categorisation` task entry.
  Consumed by Tasks 3, 4, 5.

- [ ] **Step 1: Confirm the migration head**

```bash
cd job_search
ls db/migrations/versions/
```

Expected: `0012_create_silver_job_survivorship.py` is the latest. If
not, stop and re-check before renumbering.

- [ ] **Step 2: Write the migration**

`db/migrations/versions/0013_create_silver_job_category.py`:

```python
"""create silver.job_category

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-09

silver.job_category is SHARED per-job classification data (PLAN.md
Step 11a): a job's category/seniority is the same for every user, so
no user_id, no RLS — same two-zone reasoning as job_survivorship
(0012).

Like job_survivorship (UPSERT, not insert-only): a classification is
not an identity decision the way job_group_id is (DECISIONS.md §2.6)
— re-running the classifier with a better seed set or an improved
rules table should be free to change a category, not locked in
forever.

Written only by the migration/owner role, via the `classify-jobs`
pipeline CLI subcommand (core.classification.write_job_category) —
never by a live per-user request. Read by dbt's gold.dim_job model as
a plain source().

job_search_app inherits SELECT here automatically via migration 0010's
`ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver`
rule — no explicit grant needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_category",
        sa.Column("job_group_id", sa.Text(), primary_key=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("category_confidence", sa.Numeric(), nullable=False),
        sa.Column("category_method", sa.Text(), nullable=False),
        sa.Column("qa_category", sa.Text(), nullable=True),
        sa.Column("seniority_band", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "category_method IN ('rules', 'embedding', 'llm')",
            name="ck_job_category_category_method",
        ),
        schema="silver",
    )


def downgrade() -> None:
    op.drop_table("job_category", schema="silver")
```

- [ ] **Step 3: Write `config/category_map.yml`**

```yaml
# qa_category_map: PLAN.md Step 11a's second taxonomy (4 active values,
# the question-bank grain) mapped from the first taxonomy (7 values,
# the analytical grain). "other" has no reasonable question-bank
# mapping — null, not a guess.
qa_category_map:
  software_engineer: software_engineer
  data_engineer: data_engineer
  data_scientist: data_scientist
  ai_ml_engineer: ai_engineer
  analytics_engineer: data_engineer
  platform_devops: software_engineer
  other: null
```

- [ ] **Step 4: Write `config/category_seed_examples.yml`**

```yaml
# Hand-curated starting seed set for embedding centroids (PLAN.md Step
# 11a, stage 2). NOT exhaustive — a disclosed judgment call, expected
# to be refined once the "hand-check 100 classifications" acceptance
# activity (PLAN.md's own "Done when") reveals where it's weak. Titles
# chosen to be unambiguous representatives of each category, avoiding
# titles that plausibly belong to a sibling category.
categories:
  software_engineer:
    - "Software Engineer"
    - "Senior Software Engineer"
    - "Backend Developer"
    - "Full Stack Developer"
    - "Frontend Developer"
    - "iOS Engineer"
  data_engineer:
    - "Data Engineer"
    - "Senior Data Engineer"
    - "ETL Developer"
    - "Data Platform Engineer"
    - "Big Data Engineer"
    - "Data Infrastructure Engineer"
  data_scientist:
    - "Data Scientist"
    - "Senior Data Scientist"
    - "Applied Scientist"
    - "Statistical Analyst"
    - "Quantitative Analyst"
    - "Research Data Scientist"
  ai_ml_engineer:
    - "Machine Learning Engineer"
    - "AI Engineer"
    - "ML Engineer"
    - "Deep Learning Engineer"
    - "NLP Engineer"
    - "Computer Vision Engineer"
  analytics_engineer:
    - "Analytics Engineer"
    - "BI Engineer"
    - "Business Intelligence Engineer"
    - "Reporting Analyst"
    - "Analytics Developer"
    - "dbt Analytics Engineer"
  platform_devops:
    - "DevOps Engineer"
    - "Platform Engineer"
    - "Site Reliability Engineer"
    - "Infrastructure Engineer"
    - "Cloud Engineer"
    - "SRE"
  other:
    - "Product Manager"
    - "Technical Project Manager"
    - "QA Engineer"
    - "Business Analyst"
    - "Scrum Master"
    - "Technical Writer"
```

- [ ] **Step 5: Add the `job_categorisation` task to `config/llm_tasks.yml`**

Open `config/llm_tasks.yml` and add this entry under the existing
`tasks:` key (alongside `fabrication_critic` and `manual_entry_parse`
— do not remove or reformat those):

```yaml
  job_categorisation:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
```

Routed to Anthropic, not Ollama: this is the residual stage after
rules AND embedding both declined, so call volume is low — correctness
matters more than cost here, unlike `manual_entry_parse`'s local-first
routing.

- [ ] **Step 6: Run the migration**

```bash
cd job_search
docker compose up -d postgres
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
  python3.11 -m alembic -c db/alembic.ini upgrade head
```

Verify:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "\d silver.job_category"
```

Expected: PK on `job_group_id`, the `category_method` check
constraint, all 7 columns present.

- [ ] **Step 7: Commit**

```bash
git add db/migrations/versions/0013_create_silver_job_category.py \
        config/category_map.yml config/category_seed_examples.yml \
        config/llm_tasks.yml
git commit -m "feat(job_search): add silver.job_category, category_map, seed examples, and the job_categorisation LLM task"
```

---

### Task 2: Rules-based title classifier and seniority-band classifier

**Files:**
- Create: `packages/core/core/classification/__init__.py` (empty)
- Create: `packages/core/core/classification/rules.py`
- Create: `packages/core/core/classification/seniority.py`
- Test: `packages/core/tests/test_classification_rules.py`
- Test: `packages/core/tests/test_classification_seniority.py`

**Interfaces:**
- Produces: `classify_by_rules(title: str | None) -> str | None`;
  `derive_seniority_band(title: str | None) -> str`. Consumed by Task
  5's orchestration.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_classification_rules.py`:

```python
from __future__ import annotations

import unittest

from core.classification.rules import classify_by_rules


class TestClassifyByRules(unittest.TestCase):
    def test_none_title_returns_none(self) -> None:
        self.assertIsNone(classify_by_rules(None))

    def test_software_engineer_titles(self) -> None:
        for title in ("Software Engineer", "Senior Backend Developer", "Full Stack Developer"):
            self.assertEqual(classify_by_rules(title), "software_engineer")

    def test_data_engineer_titles(self) -> None:
        for title in ("Data Engineer", "ETL Developer", "Data Platform Engineer"):
            self.assertEqual(classify_by_rules(title), "data_engineer")

    def test_data_scientist_titles(self) -> None:
        for title in ("Data Scientist", "Quantitative Analyst"):
            self.assertEqual(classify_by_rules(title), "data_scientist")

    def test_ai_ml_engineer_titles(self) -> None:
        for title in ("Machine Learning Engineer", "NLP Engineer", "AI Engineer"):
            self.assertEqual(classify_by_rules(title), "ai_ml_engineer")

    def test_analytics_engineer_titles(self) -> None:
        for title in ("Analytics Engineer", "Business Intelligence Engineer"):
            self.assertEqual(classify_by_rules(title), "analytics_engineer")

    def test_platform_devops_titles(self) -> None:
        for title in ("DevOps Engineer", "Site Reliability Engineer", "SRE"):
            self.assertEqual(classify_by_rules(title), "platform_devops")

    def test_unrecognised_title_returns_none(self) -> None:
        # Must cascade to the embedding/LLM stages, not guess "other".
        self.assertIsNone(classify_by_rules("Product Manager"))

    def test_data_platform_engineer_does_not_false_positive_as_software_engineer(
        self,
    ) -> None:
        # Regression: "engineer" alone must not trigger the generic
        # software_engineer pattern before the more specific data_engineer
        # pattern gets a chance — rule order matters.
        self.assertEqual(
            classify_by_rules("Data Platform Engineer"), "data_engineer"
        )

    def test_case_insensitive(self) -> None:
        self.assertEqual(classify_by_rules("SENIOR DATA ENGINEER"), "data_engineer")
```

`packages/core/tests/test_classification_seniority.py`:

```python
from __future__ import annotations

import unittest

from core.classification.seniority import derive_seniority_band


class TestDeriveSeniorityBand(unittest.TestCase):
    def test_none_title_defaults_to_mid(self) -> None:
        self.assertEqual(derive_seniority_band(None), "mid")

    def test_no_seniority_term_defaults_to_mid(self) -> None:
        self.assertEqual(derive_seniority_band("Data Engineer"), "mid")

    def test_senior_and_sr(self) -> None:
        self.assertEqual(derive_seniority_band("Senior Data Engineer"), "senior")
        self.assertEqual(derive_seniority_band("Sr Data Engineer"), "senior")

    def test_junior_variants(self) -> None:
        for title in ("Junior Data Engineer", "Jr Data Engineer", "Graduate Data Engineer", "Associate Data Engineer"):
            self.assertEqual(derive_seniority_band(title), "junior")

    def test_lead(self) -> None:
        self.assertEqual(derive_seniority_band("Lead Data Engineer"), "lead")

    def test_principal_and_staff(self) -> None:
        # "Staff" folds into "principal" — no separate tier in PLAN.md's
        # 5-value taxonomy; industry convention treats staff+ as
        # roughly principal-equivalent. A disclosed judgment call.
        self.assertEqual(derive_seniority_band("Principal Data Engineer"), "principal")
        self.assertEqual(derive_seniority_band("Staff Data Engineer"), "principal")

    def test_case_insensitive(self) -> None:
        self.assertEqual(derive_seniority_band("SENIOR DATA ENGINEER"), "senior")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd packages/core
../../venv/bin/python -m coverage run -m unittest tests.test_classification_rules tests.test_classification_seniority -v
```

Expected: `ModuleNotFoundError: No module named 'core.classification'`.

- [ ] **Step 3: Implement `rules.py` and `seniority.py`**

`packages/core/core/classification/__init__.py`: empty file.

`packages/core/core/classification/rules.py`:

```python
"""Rules-based title classification (PLAN.md Step 11a, stage 1) —
catches the ~70% of titles a keyword match resolves deterministically
and for free, before the more expensive embedding/LLM stages run.

Rule order matters: more specific categories (ai_ml_engineer,
data_scientist, analytics_engineer, platform_devops) are checked
before the broader software_engineer/data_engineer patterns, so e.g.
"Data Platform Engineer" matches data_engineer's "data platform"
phrase rather than falling through to a generic "engineer" match.
"""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"machine learning|\bml\b|\bai\b|deep learning|\bnlp\b|computer vision",
            re.IGNORECASE,
        ),
        "ai_ml_engineer",
    ),
    (
        re.compile(
            r"data scientist|applied scientist|quantitative analyst|"
            r"statistical analyst",
            re.IGNORECASE,
        ),
        "data_scientist",
    ),
    (
        re.compile(
            r"analytics engineer|business intelligence|\bbi engineer\b|"
            r"reporting analyst|analytics developer",
            re.IGNORECASE,
        ),
        "analytics_engineer",
    ),
    (
        re.compile(
            r"data engineer|\betl\b|data platform|data infrastructure|"
            r"big data",
            re.IGNORECASE,
        ),
        "data_engineer",
    ),
    (
        re.compile(
            r"devops|site reliability|\bsre\b|platform engineer|"
            r"infrastructure engineer|cloud engineer",
            re.IGNORECASE,
        ),
        "platform_devops",
    ),
    (
        re.compile(
            r"software engineer|software developer|backend|front[\s-]?end|"
            r"full[\s-]?stack|ios engineer|android engineer",
            re.IGNORECASE,
        ),
        "software_engineer",
    ),
]


def classify_by_rules(title: str | None) -> str | None:
    """Classify a title by keyword match, if one of the rules fires.

    Args:
        title: The job title to classify (title_for_display or
            title_raw — either works, since these rules match
            substrings and don't depend on decoration being stripped).

    Returns:
        The matched category, or `None` if no rule fires — the caller
        must cascade to the embedding stage next, never guess.
    """
    if title is None:
        return None
    for pattern, category in _RULES:
        if pattern.search(title):
            return category
    return None
```

`packages/core/core/classification/seniority.py`:

```python
"""Seniority-band derivation (PLAN.md Step 11a: "junior / mid / senior
/ lead / principal — from the same pass" as categorisation). Rules-only
— no embedding/LLM stage exists for this field, since PLAN.md never
describes one; the title's own seniority prefix is a strong, cheap
signal on its own.

Uses a fresh regex rather than reusing core.normalisation.title's
private `_SENIORITY_PREFIX_RE` (that pattern strips a *prefix only* and
is private to its module) — this module needs to match the term
anywhere in the title and map it to a band, a different job entirely,
even though the underlying keyword set intentionally mirrors it for
consistency (DECISIONS.md §5's three-title-fields design already
established which seniority words this project treats as significant).
"""

from __future__ import annotations

import re

_SENIORITY_TERMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bprincipal\b|\bstaff\+?\b", re.IGNORECASE), "principal"),
    (re.compile(r"\blead\b", re.IGNORECASE), "lead"),
    (re.compile(r"\bsenior\b|\bsr\.?\b", re.IGNORECASE), "senior"),
    (
        re.compile(r"\bjunior\b|\bjr\.?\b|\bgraduate\b|\bassociate\b", re.IGNORECASE),
        "junior",
    ),
]


def derive_seniority_band(title: str | None) -> str:
    """Derive a seniority band from a job title's own wording.

    Args:
        title: The job title to inspect (title_for_display or
            title_raw — title_for_display is preferred since it keeps
            seniority terms; never strip_title's output, which removes
            them).

    Returns:
        One of 'junior', 'mid', 'senior', 'lead', 'principal'. Defaults
        to 'mid' when the title is `None` or names no seniority term —
        "no signal" means the middle of the band, not a guess at either
        extreme.
    """
    if title is not None:
        for pattern, band in _SENIORITY_TERMS:
            if pattern.search(title):
                return band
    return "mid"
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
../../venv/bin/python -m coverage run -m unittest tests.test_classification_rules tests.test_classification_seniority -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/classification/__init__.py \
        packages/core/core/classification/rules.py \
        packages/core/core/classification/seniority.py \
        packages/core/tests/test_classification_rules.py \
        packages/core/tests/test_classification_seniority.py
git commit -m "feat(job_search): add rules-based category and seniority-band classifiers"
```

---

### Task 3: Embedding client and nearest-centroid classifier

**Files:**
- Create: `packages/core/core/embedding/__init__.py` (empty)
- Create: `packages/core/core/embedding/ollama.py`
- Create: `packages/core/core/classification/embeddings.py`
- Test: `packages/core/tests/integration/test_embedding_ollama.py`
- Test: `packages/core/tests/test_classification_embeddings.py`

**Interfaces:**
- Consumes: `config/category_seed_examples.yml` (Task 1).
- Produces: `embed_text(text, *, base_url, model, client) ->
  list[float]`; `load_seed_examples(seed_path=None) -> dict[str,
  list[str]]`; `build_centroids(seed_examples, *, base_url, model,
  client) -> dict[str, list[float]]`; `classify_by_embedding(title,
  centroids, *, base_url, model, client) -> tuple[str, float] | None`.
  Consumed by Task 5's orchestration.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/integration/test_embedding_ollama.py` (a live
integration test against the real Ollama server — this project's
`test_*_connector_live.py` files establish that live external-service
tests are the norm here, gated on the service actually being available
rather than mocked):

```python
from __future__ import annotations

import unittest

import httpx

from core.embedding.ollama import embed_text

_OLLAMA_BASE_URL = "http://localhost:11434"
_MODEL = "nomic-embed-text"


def _ollama_available() -> bool:
    try:
        response = httpx.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


@unittest.skipUnless(_ollama_available(), "Ollama server not reachable")
class TestEmbedText(unittest.TestCase):
    def setUp(self) -> None:
        self.client = httpx.Client(timeout=30.0)

    def tearDown(self) -> None:
        self.client.close()

    def test_returns_a_nonempty_float_vector(self) -> None:
        vector = embed_text(
            "Senior Data Engineer",
            base_url=_OLLAMA_BASE_URL,
            model=_MODEL,
            client=self.client,
        )
        self.assertGreater(len(vector), 0)
        self.assertTrue(all(isinstance(x, float) for x in vector))

    def test_same_text_produces_the_same_vector(self) -> None:
        # Ollama embeddings are deterministic for a fixed model/input —
        # this is what makes centroid-building reproducible.
        first = embed_text(
            "Data Engineer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        second = embed_text(
            "Data Engineer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        self.assertEqual(first, second)

    def test_similar_titles_are_closer_than_dissimilar_ones(self) -> None:
        # Structural sanity check on the embedding model itself, not on
        # our code — two data-engineering titles should cosine-sim
        # closer to each other than to an unrelated title.
        import math

        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(y * y for y in b))
            return dot / (norm_a * norm_b)

        de1 = embed_text(
            "Data Engineer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        de2 = embed_text(
            "ETL Developer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        unrelated = embed_text(
            "Product Manager",
            base_url=_OLLAMA_BASE_URL,
            model=_MODEL,
            client=self.client,
        )
        self.assertGreater(cosine(de1, de2), cosine(de1, unrelated))
```

`packages/core/tests/test_classification_embeddings.py` (pure logic —
cosine similarity and the confidence-threshold cutoff — tested with
hand-built vectors, no network):

```python
from __future__ import annotations

import unittest

from core.classification.embeddings import _cosine_similarity


class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors_have_similarity_one(self) -> None:
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_orthogonal_vectors_have_similarity_zero(self) -> None:
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_opposite_vectors_have_similarity_negative_one(self) -> None:
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_zero_vector_returns_zero_rather_than_dividing_by_zero(self) -> None:
        self.assertEqual(_cosine_similarity([0.0, 0.0], [1.0, 0.0]), 0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd packages/core
../../venv/bin/python -m coverage run -m unittest tests.test_classification_embeddings -v
```

Expected: `ModuleNotFoundError: No module named 'core.embedding'` (or
`core.classification.embeddings`).

- [ ] **Step 3: Implement `embedding/ollama.py` and `classification/embeddings.py`**

`packages/core/core/embedding/__init__.py`: empty file.

`packages/core/core/embedding/ollama.py`:

```python
"""Ephemeral text embeddings via a local Ollama server (PLAN.md Step
11a, stage 2).

Unlike core.llm's adapters (which return completions), this is used to
compute a vector, compare it, and discard it — never persisted to a
database column or indexed. This is deliberately outside DECISIONS.md
§2.8 / PLAN.md Step 15's embedding-dimension-and-index-type deferral —
that deferral is specifically about Step 15's persisted, indexed
pgvector store (CV/JD chunk vectors reused across many future
queries), not a one-shot nearest-centroid comparison that touches no
database column. See the Step 11a plan's own scope note for the full
reasoning.

Separate from core.llm.adapters.ollama.OllamaAdapter deliberately:
that class implements the LLMAdapter Protocol's `.complete()` method
for Ollama's `/api/generate` endpoint — embeddings are a different
HTTP endpoint returning a different shape, not a completion, so
forcing it into the same Protocol would be a mismatch, not reuse.
"""

from __future__ import annotations

import httpx


def embed_text(
    text: str, *, base_url: str, model: str, client: httpx.Client
) -> list[float]:
    """Compute one embedding vector via Ollama's /api/embeddings endpoint.

    Args:
        text: The text to embed.
        base_url: Base URL of the Ollama server, e.g.
            "http://localhost:11434".
        model: The Ollama embedding model tag, e.g. "nomic-embed-text"
            (Settings.embedding_model's default).
        client: The HTTP client to issue the request with.

    Returns:
        The embedding vector as a list of floats.
    """
    response = client.post(
        f"{base_url}/api/embeddings",
        json={"model": model, "prompt": text},
    )
    response.raise_for_status()
    return response.json()["embedding"]
```

`packages/core/core/classification/embeddings.py`:

```python
"""Embedding nearest-centroid classification (PLAN.md Step 11a, stage
2) — the fallback for titles the rules stage (stage 1) couldn't
confidently place. Centroids are rebuilt fresh from the hand-curated
seed set (config/category_seed_examples.yml) on every run rather than
cached: a few dozen embedding calls against a local model is cheap,
and this sidesteps an entire "has the seed set changed" invalidation
problem for a 3-point backlog item.
"""

from __future__ import annotations

import math
from pathlib import Path

import httpx
import yaml

from core.embedding.ollama import embed_text

_DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "category_seed_examples.yml"
)

_CONFIDENT_COSINE_THRESHOLD = 0.75
"""Below this cosine similarity to the nearest centroid, this stage
declines to classify (returns None) and cascades to the LLM stage
rather than guess. A starting value, not empirically tuned — PLAN.md's
own "hand-check 100 classifications" acceptance activity is what
should adjust it if agreement comes in low."""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Args:
        a: The first vector.
        b: The second vector.

    Returns:
        A value in [-1, 1], or 0.0 if either vector has zero magnitude
        (avoids a division-by-zero rather than raising — a zero vector
        has no meaningful direction to compare).
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def load_seed_examples(seed_path: Path | None = None) -> dict[str, list[str]]:
    """Load the hand-curated category -> example-titles seed set.

    Args:
        seed_path: Path to the seed YAML file. Defaults to
            config/category_seed_examples.yml at the repository root.

    Returns:
        category -> list of example title strings.
    """
    path = seed_path or _DEFAULT_SEED_PATH
    raw = yaml.safe_load(path.read_text())
    return raw["categories"]


def build_centroids(
    seed_examples: dict[str, list[str]],
    *,
    base_url: str,
    model: str,
    client: httpx.Client,
) -> dict[str, list[float]]:
    """Compute one centroid embedding per category from its seed examples.

    Args:
        seed_examples: category -> list of example title strings (see
            load_seed_examples).
        base_url: Ollama server base URL.
        model: Ollama embedding model tag.
        client: The HTTP client to issue requests with.

    Returns:
        category -> centroid vector (the element-wise mean of that
        category's example embeddings).
    """
    centroids: dict[str, list[float]] = {}
    for category, examples in seed_examples.items():
        vectors = [
            embed_text(example, base_url=base_url, model=model, client=client)
            for example in examples
        ]
        dimension = len(vectors[0])
        centroids[category] = [
            sum(vector[i] for vector in vectors) / len(vectors)
            for i in range(dimension)
        ]
    return centroids


def classify_by_embedding(
    title: str,
    centroids: dict[str, list[float]],
    *,
    base_url: str,
    model: str,
    client: httpx.Client,
) -> tuple[str, float] | None:
    """Classify one title by cosine distance to the nearest centroid.

    Args:
        title: The job title to classify.
        centroids: Precomputed category -> centroid vector (see
            build_centroids — computed once per batch, not once per
            title).
        base_url: Ollama server base URL.
        model: Ollama embedding model tag.
        client: The HTTP client to issue requests with.

    Returns:
        `(category, cosine_similarity)` for the nearest centroid, or
        `None` if the best match falls below
        `_CONFIDENT_COSINE_THRESHOLD` — the caller must cascade to the
        LLM stage next, never guess.
    """
    vector = embed_text(title, base_url=base_url, model=model, client=client)
    best_category, best_score = max(
        (
            (category, _cosine_similarity(vector, centroid))
            for category, centroid in centroids.items()
        ),
        key=lambda pair: pair[1],
    )
    if best_score < _CONFIDENT_COSINE_THRESHOLD:
        return None
    return best_category, best_score
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose up -d ollama
../../venv/bin/python -m coverage run -m unittest tests.test_classification_embeddings tests.integration.test_embedding_ollama -v
```

Expected: all tests PASS (the live Ollama tests require `docker compose
up -d ollama` and `nomic-embed-text` already pulled — confirmed working
this session; if `ollama pull nomic-embed-text` hasn't been run in this
environment, run it now: `docker compose exec -T ollama ollama pull
nomic-embed-text`).

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/embedding/__init__.py \
        packages/core/core/embedding/ollama.py \
        packages/core/core/classification/embeddings.py \
        packages/core/tests/test_classification_embeddings.py \
        packages/core/tests/integration/test_embedding_ollama.py
git commit -m "feat(job_search): add Ollama embeddings and nearest-centroid classification"
```

---

### Task 4: LLM residual classifier

**Files:**
- Create: `packages/core/core/classification/llm_classifier.py`
- Test: `packages/core/tests/integration/test_classification_llm.py`

**Interfaces:**
- Consumes: `core.llm.gateway.complete` (already shipped);
  `config/llm_tasks.yml`'s `job_categorisation` entry (Task 1).
- Produces: `classify_by_llm(title, *, adapters) -> tuple[str, float]`.
  Consumed by Task 5's orchestration.

- [ ] **Step 1: Write the failing test**

`packages/core/tests/integration/test_classification_llm.py` (a live
Anthropic call, gated on the API key being configured — same pattern
as this project's other `_live` integration tests):

```python
from __future__ import annotations

import unittest

import anthropic
import httpx

from core.classification.llm_classifier import classify_by_llm
from core.llm.adapters.anthropic import AnthropicAdapter
from core.settings import get_settings

_settings = get_settings()


@unittest.skipUnless(
    _settings.anthropic_api_key, "ANTHROPIC_API_KEY not configured"
)
class TestClassifyByLlm(unittest.TestCase):
    def setUp(self) -> None:
        self.adapters = {
            "anthropic": AnthropicAdapter(
                api_key=_settings.anthropic_api_key,
                client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
            ),
        }

    def test_classifies_an_unambiguous_residual_title(self) -> None:
        # A title deliberately outside all 6 substantive categories —
        # the exact "declined by rules and embedding" case this stage
        # exists for.
        category, confidence = classify_by_llm(
            "Chief Ethics Officer", adapters=self.adapters
        )
        self.assertEqual(category, "other")
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_malformed_response_falls_back_to_other_rather_than_raising(self) -> None:
        # Can't force a real malformed response from the live API
        # deterministically, so this test exercises the parsing
        # fallback directly against a fake adapter instead of the real
        # one — still an integration-shaped test of the parsing logic,
        # not a mock of the classification decision itself.
        class _BrokenAdapter:
            def complete(self, *, model: str, prompt: str):
                from core.llm.types import LLMResponse

                return LLMResponse(
                    text="not json at all",
                    provider="anthropic",
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                )

        category, confidence = classify_by_llm(
            "Some Title", adapters={"anthropic": _BrokenAdapter()}
        )
        self.assertEqual(category, "other")
        self.assertEqual(confidence, 0.0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd packages/core
../../venv/bin/python -m coverage run -m unittest tests.integration.test_classification_llm -v
```

Expected: `ModuleNotFoundError: No module named 'core.classification.llm_classifier'`.

- [ ] **Step 3: Implement `llm_classifier.py`**

`packages/core/core/classification/llm_classifier.py`:

```python
"""LLM residual classification (PLAN.md Step 11a, stage 3) — the final
fallback for titles neither the rules nor embedding stage could
confidently classify. Routed via core.llm.gateway's per-task provider
resolution (config/llm_tasks.yml's `job_categorisation` entry, DECISIONS.md
§1) — never a hardcoded provider, and never called for the ~70%+ of
titles the cheaper stages already resolved.
"""

from __future__ import annotations

import json

from core.llm.gateway import complete
from core.llm.types import LLMAdapter

_PROMPT_VERSION = "job_categorisation-v1"

_CATEGORIES = [
    "software_engineer",
    "data_engineer",
    "data_scientist",
    "ai_ml_engineer",
    "analytics_engineer",
    "platform_devops",
    "other",
]

_PROMPT_TEMPLATE = (
    "Classify this job title into exactly one of these categories: "
    "{categories}.\n\n"
    "Job title: {title}\n\n"
    'Respond with ONLY a JSON object, no other text: '
    '{{"category": "<one of the categories above>", "confidence": <float 0-1>}}'
)


def classify_by_llm(
    title: str, *, adapters: dict[str, LLMAdapter]
) -> tuple[str, float]:
    """Classify one title via the LLM gateway's job_categorisation task.

    Args:
        title: The job title to classify.
        adapters: Every available LLM adapter, keyed by provider —
            passed straight through to core.llm.gateway.complete.

    Returns:
        `(category, confidence)`. Falls back to `("other", 0.0)` if the
        response can't be parsed as the expected JSON shape or names a
        category outside the taxonomy — one malformed response should
        not fail the whole classification batch.
    """
    prompt = _PROMPT_TEMPLATE.format(categories=", ".join(_CATEGORIES), title=title)
    response = complete(
        task="job_categorisation",
        prompt=prompt,
        prompt_version=_PROMPT_VERSION,
        adapters=adapters,
    )
    try:
        parsed = json.loads(response.text.strip())
        category = parsed["category"]
        confidence = float(parsed["confidence"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return "other", 0.0
    if category not in _CATEGORIES:
        return "other", 0.0
    return category, confidence
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
../../venv/bin/python -m coverage run -m unittest tests.integration.test_classification_llm -v
```

Expected: both tests PASS (the live test makes one real, cheap
Anthropic call — `.env`'s `ANTHROPIC_API_KEY` is already configured in
this environment).

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/classification/llm_classifier.py \
        packages/core/tests/integration/test_classification_llm.py
git commit -m "feat(job_search): add LLM residual classification"
```

---

### Task 5: Cascade orchestration, write path, and `classify-jobs` CLI subcommand

**Files:**
- Create: `packages/core/core/classification/classify.py`
- Create: `packages/core/core/classification/write_job_category.py`
- Modify: `apps/pipeline/app/cli.py`
- Test: `packages/core/tests/test_classification_classify.py`
- Test: `packages/core/tests/integration/test_write_job_category.py`

**Interfaces:**
- Consumes: `core.classification.rules.classify_by_rules`,
  `core.classification.seniority.derive_seniority_band`,
  `core.classification.embeddings.{load_seed_examples, build_centroids,
  classify_by_embedding}`, `core.classification.llm_classifier.
  classify_by_llm` (Tasks 2-4); `core.db.session.build_engine`;
  `core.settings.get_settings`.
- Produces: `Classification` dataclass; `load_qa_category_map(path=None)
  -> dict[str, str | None]`; `classify_title(title, *, centroids,
  qa_category_map, adapters, embedding_base_url, embedding_model,
  http_client) -> Classification`; `write_job_category(engine, *,
  adapters, http_client) -> int`; CLI subcommand `classify-jobs`.

- [ ] **Step 1: Write the failing unit test for the cascade**

`packages/core/tests/test_classification_classify.py` (pure — injects
fake centroids/adapters so no network call happens; only proves the
cascade calls the right stage in the right order):

```python
from __future__ import annotations

import unittest

from core.classification.classify import Classification, classify_title


class _FakeAdapter:
    """Records whether it was called — proves the LLM stage only fires
    when both earlier stages decline."""

    def __init__(self, category: str = "other", confidence: float = 0.5) -> None:
        self.called = False
        self._category = category
        self._confidence = confidence

    def complete(self, *, model: str, prompt: str):
        from core.llm.types import LLMResponse

        self.called = True
        return LLMResponse(
            text=f'{{"category": "{self._category}", "confidence": {self._confidence}}}',
            provider="anthropic",
            model=model,
            input_tokens=0,
            output_tokens=0,
        )


class TestClassifyTitle(unittest.TestCase):
    def _qa_map(self) -> dict[str, str | None]:
        return {
            "software_engineer": "software_engineer",
            "data_engineer": "data_engineer",
            "other": None,
        }

    def test_rules_stage_wins_without_touching_embedding_or_llm(self) -> None:
        adapter = _FakeAdapter()
        result = classify_title(
            "Senior Data Engineer",
            centroids={},
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertEqual(result.category, "data_engineer")
        self.assertEqual(result.category_method, "rules")
        self.assertEqual(result.seniority_band, "senior")
        self.assertEqual(result.qa_category, "data_engineer")
        self.assertFalse(adapter.called, "LLM must not be called when rules matched")

    def test_llm_stage_only_fires_when_rules_and_embedding_both_decline(
        self,
    ) -> None:
        adapter = _FakeAdapter(category="software_engineer", confidence=0.6)
        result = classify_title(
            "Chief Vibes Officer",
            centroids={},  # empty centroids -> embedding stage can never match
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertTrue(adapter.called)
        self.assertEqual(result.category, "software_engineer")
        self.assertEqual(result.category_method, "llm")
        self.assertEqual(result.category_confidence, 0.6)

    def test_none_title_classifies_as_other_via_rules_without_calling_llm(
        self,
    ) -> None:
        adapter = _FakeAdapter()
        result = classify_title(
            None,
            centroids={},
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertEqual(result.category, "other")
        self.assertEqual(result.category_method, "rules")
        self.assertEqual(result.seniority_band, "mid")
        self.assertIsNone(result.qa_category)
        self.assertFalse(adapter.called)
```

`packages/core/tests/integration/test_write_job_category.py`:

```python
from __future__ import annotations

import unittest
import uuid

import anthropic
import httpx
from sqlalchemy import text

from core.classification.embeddings import build_centroids, load_seed_examples
from core.classification.write_job_category import write_job_category
from core.db.session import build_engine
from core.llm.adapters.anthropic import AnthropicAdapter
from core.settings import get_settings

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
_OLLAMA_BASE_URL = "http://localhost:11434"
_EMBEDDING_MODEL = "nomic-embed-text"
_settings = get_settings()


def _ollama_available() -> bool:
    try:
        return httpx.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


@unittest.skipUnless(_ollama_available(), "Ollama server not reachable")
@unittest.skipUnless(_settings.anthropic_api_key, "ANTHROPIC_API_KEY not configured")
class TestWriteJobCategory(unittest.TestCase):
    """Integration test against a real Postgres instance.

    Inserts one fixture job_group_id's worth of silver.job_identity_map
    + silver.silver__job_posting rows directly (safe here since nothing
    runs `dbt run` mid-test, matching the established pattern from
    test_write_job_survivorship.py), then exercises the real write path
    end-to-end — real embeddings, real (cheap) LLM residual call.
    """

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.http_client = httpx.Client(timeout=30.0)
        self.suffix = uuid.uuid4().hex
        self.job_key = f"gh-{self.suffix}"
        self.job_group_id = f"group-{self.suffix}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.silver__job_posting "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at) "
                    "VALUES (:job_key, 'greenhouse', :source_job_id, "
                    "'https://greenhouse.example/cat-test', "
                    "'https://greenhouse.example/cat-test', 'api', "
                    "'Senior Data Engineer', 'Test Co', 'London', "
                    "'A description', NULL, now())"
                ),
                {"job_key": self.job_key, "source_job_id": f"src-{self.suffix}"},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.job_identity_map "
                    "(source_name, source_job_id, job_group_id, "
                    "match_method, confidence) "
                    "VALUES ('greenhouse', :source_job_id, :job_group_id, "
                    "'singleton', 1.0)"
                ),
                {"source_job_id": f"src-{self.suffix}", "job_group_id": self.job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship "
                    "(job_group_id, winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, apply_title_for_display) "
                    "VALUES (:job_group_id, 'A description', 'greenhouse', "
                    ":source_job_id, 'https://greenhouse.example/cat-test', "
                    "'Senior Data Engineer')"
                ),
                {"job_group_id": self.job_group_id, "source_job_id": f"src-{self.suffix}"},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM silver.job_category WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.job_survivorship WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.job_identity_map WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.silver__job_posting WHERE job_key = :k"),
                {"k": self.job_key},
            )
        self.engine.dispose()
        self.http_client.close()

    def test_classifies_and_writes_a_real_job_via_rules_stage(self) -> None:
        # "Senior Data Engineer" matches the rules stage — proves the
        # full write path end-to-end without needing the embedding/LLM
        # stages to fire for THIS specific fixture (Tasks 3/4 already
        # cover those stages directly).
        adapters = {
            "anthropic": AnthropicAdapter(
                api_key=_settings.anthropic_api_key,
                client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
            ),
        }
        written = write_job_category(
            self.engine, adapters=adapters, http_client=self.http_client
        )
        self.assertGreaterEqual(written, 1)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT category, category_method, seniority_band, qa_category "
                    "FROM silver.job_category WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).one()
        self.assertEqual(row.category, "data_engineer")
        self.assertEqual(row.category_method, "rules")
        self.assertEqual(row.seniority_band, "senior")
        self.assertEqual(row.qa_category, "data_engineer")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        adapters = {
            "anthropic": AnthropicAdapter(
                api_key=_settings.anthropic_api_key,
                client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
            ),
        }
        write_job_category(self.engine, adapters=adapters, http_client=self.http_client)
        write_job_category(self.engine, adapters=adapters, http_client=self.http_client)

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM silver.job_category WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).scalar_one()
        self.assertEqual(count, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd packages/core
../../venv/bin/python -m coverage run -m unittest tests.test_classification_classify -v
```

Expected: `ModuleNotFoundError: No module named 'core.classification.classify'`.

- [ ] **Step 3: Implement `classify.py` and `write_job_category.py`**

`packages/core/core/classification/classify.py`:

```python
"""Cascading job classification (PLAN.md Step 11a): rules -> embedding
-> LLM, cheapest and most-deterministic signal first. Each stage only
runs for a title the previous stage declined to classify — the whole
point of ordering it this way is that the expensive stages (an
embedding call, an LLM call) never run for the majority of titles the
free rules stage already resolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from core.classification.embeddings import classify_by_embedding
from core.classification.llm_classifier import classify_by_llm
from core.classification.rules import classify_by_rules
from core.classification.seniority import derive_seniority_band
from core.llm.types import LLMAdapter

_DEFAULT_CATEGORY_MAP_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "category_map.yml"
)


@dataclass(frozen=True)
class Classification:
    """One job's resolved classification.

    Attributes:
        category: One of the 7-value classification taxonomy.
        category_confidence: Confidence in `category`, 0.0-1.0.
        category_method: Which stage produced `category` — 'rules',
            'embedding', or 'llm'.
        qa_category: The mapped 4-value question-bank category, or
            `None` when `category` has no reasonable question-bank
            equivalent (currently only 'other').
        seniority_band: One of the 5-value seniority taxonomy.
    """

    category: str
    category_confidence: float
    category_method: str
    qa_category: str | None
    seniority_band: str


def load_qa_category_map(path: Path | None = None) -> dict[str, str | None]:
    """Load the classification-category -> qa_category mapping.

    Args:
        path: Path to the mapping YAML. Defaults to
            config/category_map.yml at the repository root.

    Returns:
        classification category -> qa_category (or `None`).
    """
    raw = yaml.safe_load((path or _DEFAULT_CATEGORY_MAP_PATH).read_text())
    return raw["qa_category_map"]


def classify_title(
    title: str | None,
    *,
    centroids: dict[str, list[float]],
    qa_category_map: dict[str, str | None],
    adapters: dict[str, LLMAdapter],
    embedding_base_url: str,
    embedding_model: str,
    http_client: httpx.Client,
) -> Classification:
    """Classify one job title via the rules -> embedding -> LLM cascade.

    Args:
        title: The job title to classify (title_for_display preferred
            — see core.classification.seniority's docstring for why).
        centroids: Precomputed category centroids (see
            core.classification.embeddings.build_centroids) — built
            once per batch, not once per title.
        qa_category_map: The classification-category -> qa_category
            mapping (see load_qa_category_map).
        adapters: Every available LLM adapter, keyed by provider — only
            touched if both the rules and embedding stages decline.
        embedding_base_url: Ollama server base URL.
        embedding_model: Ollama embedding model tag.
        http_client: The HTTP client for embedding calls.

    Returns:
        The resolved `Classification`.
    """
    seniority_band = derive_seniority_band(title)

    category = classify_by_rules(title) if title is not None else None
    if category is not None:
        confidence, method = 0.9, "rules"
    else:
        embedding_result = (
            classify_by_embedding(
                title,
                centroids,
                base_url=embedding_base_url,
                model=embedding_model,
                client=http_client,
            )
            if title is not None and centroids
            else None
        )
        if embedding_result is not None:
            category, confidence = embedding_result
            method = "embedding"
        else:
            category, confidence = classify_by_llm(
                title if title is not None else "Untitled posting",
                adapters=adapters,
            )
            method = "llm"

    return Classification(
        category=category,
        category_confidence=confidence,
        category_method=method,
        qa_category=qa_category_map.get(category),
        seniority_band=seniority_band,
    )
```

`packages/core/core/classification/write_job_category.py`:

```python
"""Batch write path for silver.job_category (PLAN.md Step 11a).

Only classifies job_group_ids not already in silver.job_category — a
routine `classify-jobs` run must not re-embed/re-call-the-LLM for
already-classified, stable jobs every time (unlike job_identity_map's
insert-only immutability guarantee, this is purely a cost control: the
table's own UPSERT semantics still allow a deliberate re-classification
run later, e.g. after tuning the seed set, by clearing specific rows
first).
"""

from __future__ import annotations

import httpx
from sqlalchemy import Engine, text

from core.classification.classify import classify_title, load_qa_category_map
from core.classification.embeddings import build_centroids, load_seed_examples
from core.llm.types import LLMAdapter
from core.settings import get_settings

_SELECT_UNCLASSIFIED = text(
    """
    SELECT dj.job_group_id, dj.title_for_display
    FROM gold.dim_job AS dj
    LEFT JOIN silver.job_category AS jc ON dj.job_group_id = jc.job_group_id
    WHERE jc.job_group_id IS NULL
    """
)

_UPSERT = text(
    """
    INSERT INTO silver.job_category (
        job_group_id, category, category_confidence, category_method,
        qa_category, seniority_band
    ) VALUES (
        :job_group_id, :category, :category_confidence, :category_method,
        :qa_category, :seniority_band
    )
    ON CONFLICT (job_group_id) DO UPDATE SET
        category = EXCLUDED.category,
        category_confidence = EXCLUDED.category_confidence,
        category_method = EXCLUDED.category_method,
        qa_category = EXCLUDED.qa_category,
        seniority_band = EXCLUDED.seniority_band,
        computed_at = now()
    """
)


def write_job_category(
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    http_client: httpx.Client,
) -> int:
    """Classify and upsert every job_group_id not yet in job_category.

    Args:
        engine: The migration/owner engine — SHARED-zone, like every
            other dedup/silver Python-written table.
        adapters: Every available LLM adapter, keyed by provider.
        http_client: The HTTP client for embedding calls.

    Returns:
        The number of job_group_id rows written (0 on a rerun once
        every gold.dim_job row already has a job_category row).
    """
    settings = get_settings()

    with engine.begin() as conn:
        rows = conn.execute(_SELECT_UNCLASSIFIED).all()
        if not rows:
            return 0

        seed_examples = load_seed_examples()
        centroids = build_centroids(
            seed_examples,
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            client=http_client,
        )
        qa_category_map = load_qa_category_map()

        written = 0
        for row in rows:
            classification = classify_title(
                row.title_for_display,
                centroids=centroids,
                qa_category_map=qa_category_map,
                adapters=adapters,
                embedding_base_url=settings.ollama_base_url,
                embedding_model=settings.embedding_model,
                http_client=http_client,
            )
            conn.execute(
                _UPSERT,
                {
                    "job_group_id": row.job_group_id,
                    "category": classification.category,
                    "category_confidence": classification.category_confidence,
                    "category_method": classification.category_method,
                    "qa_category": classification.qa_category,
                    "seniority_band": classification.seniority_band,
                },
            )
            written += 1
    return written
```

- [ ] **Step 4: Wire the `classify-jobs` CLI subcommand**

In `apps/pipeline/app/cli.py`, add the imports alongside the other
`core.dedup.write_*`/`core.classification` imports:

```python
import httpx  # already imported at module level — reuse it, don't re-add
from core.classification.write_job_category import write_job_category
```

Add the command function, next to `_cmd_compute_survivorship`:

```python
def _cmd_classify_jobs(args: argparse.Namespace) -> int:
    """Run the `classify-jobs` subcommand.

    Args:
        args: Parsed CLI arguments (none beyond the subcommand itself).

    Returns:
        0 on success.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        adapters = _build_llm_adapters(http_client)
        written = write_job_category(engine, adapters=adapters, http_client=http_client)
        print(f"classify-jobs complete: rows_written={written}")
        return 0
    finally:
        http_client.close()
```

This reuses `_build_llm_adapters`, the exact same helper `_cmd_ingest`
already calls — no new adapter-construction logic needed.

Register it in `main()`, next to the `compute-survivorship` subparser:

```python
    subparsers.add_parser(
        "classify-jobs",
        help="Classify every unclassified dim_job row (category, seniority_band)",
    )
```

And dispatch it alongside the other `args.command ==` checks:

```python
    if args.command == "classify-jobs":
        return _cmd_classify_jobs(args)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
../../venv/bin/python -m coverage run -m unittest tests.test_classification_classify tests.integration.test_write_job_category -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/classification/classify.py \
        packages/core/core/classification/write_job_category.py \
        packages/core/tests/test_classification_classify.py \
        packages/core/tests/integration/test_write_job_category.py \
        apps/pipeline/app/cli.py
git commit -m "feat(job_search): add classify-jobs pipeline subcommand"
```

---

### Task 6: `dim_job` integration

**Files:**
- Modify: `dbt/models/gold/dim_job.sql`
- Modify: `dbt/models/gold/_gold.yml`
- Create: `dbt/tests/assert_dim_job_has_a_category_for_every_row.sql`

**Interfaces:**
- Consumes: `silver.job_category` (Task 1/5, via `source()`).
- Produces: `gold.dim_job` gains `category`, `category_confidence`,
  `category_method`, `qa_category`, `seniority_band` columns.

- [ ] **Step 1: Add the `job_category` source to `_gold.yml`**

In `dbt/models/gold/_gold.yml`, add a new table entry to the existing
`silver_ingest` source block (alongside `job_identity_map` and
`job_survivorship`):

```yaml
      - name: job_category
        description: >
          Written by the classify-jobs pipeline CLI subcommand
          (core.classification.write_job_category), not dbt — the
          rules/embedding/LLM cascade is Python logic tested in
          packages/core/tests (PLAN.md Step 11a). One row per
          job_group_id: category, category_confidence, category_method,
          qa_category, seniority_band.
        columns:
          - name: job_group_id
            description: "Matches gold.dim_job.job_group_id."
```

- [ ] **Step 2: Add the five new columns to `dim_job`'s entry in `_gold.yml`**

Append to `dim_job`'s `columns:` list:

```yaml
      - name: category
        description: >
          The 7-value classification taxonomy (PLAN.md Step 11a) —
          the analytical grain for market/skill-demand marts and the
          skill gap analysis.
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values:
                - software_engineer
                - data_engineer
                - data_scientist
                - ai_ml_engineer
                - analytics_engineer
                - platform_devops
                - other
      - name: category_confidence
        description: "Confidence in `category`, 0.0-1.0, per whichever stage resolved it."
        data_type: numeric
        tests:
          - not_null
      - name: category_method
        description: "Which cascade stage resolved `category` — 'rules', 'embedding', or 'llm'."
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: ['rules', 'embedding', 'llm']
      - name: qa_category
        description: >
          The separate 4-value question-bank taxonomy (PLAN.md Step
          11a) — coarser than `category` by design. Null when
          `category = 'other'`, which has no reasonable question-bank
          equivalent. dbt's `accepted_values` test passes NULLs through
          by default, so no `not_null` test belongs here — nullability
          is intentional, not a gap.
        data_type: text
        tests:
          - accepted_values:
              values: [software_engineer, data_engineer, data_scientist, ai_engineer]
      - name: seniority_band
        description: "Derived from the job title's own wording (PLAN.md Step 11a)."
        data_type: text
        tests:
          - not_null
          - accepted_values:
              values: [junior, mid, senior, lead, principal]
```

- [ ] **Step 3: Add the five columns and the join to `dim_job.sql`**

In `dbt/models/gold/dim_job.sql`, add a new CTE alongside the existing
`survivorship`/`identity_map` CTEs:

```sql
job_category AS (

    SELECT * FROM {{ source('silver_ingest', 'job_category') }}

),
```

Then join it into the final `SELECT ... FROM survivorship AS s` block
(a `LEFT JOIN`, not `INNER` — a job_group_id that hasn't been
classified yet, e.g. between `cluster-jobs` running and `classify-jobs`
catching up, must still appear in `dim_job` with `category` fields
`NULL`, exactly the same out-of-band-staleness reasoning already
documented for `dedup__similarity_scores`/`dim_job`'s existing INNER
joins elsewhere in this file):

```sql
LEFT JOIN job_category AS jc
    ON s.job_group_id = jc.job_group_id
```

and add to the final column list:

```sql
    jc.category,
    jc.category_confidence,
    jc.category_method,
    jc.qa_category,
    jc.seniority_band,
```

Since this join is `LEFT`, `category`/`category_confidence`/
`category_method`/`seniority_band`'s `not_null` tests (Step 2 above)
will only pass once `classify-jobs` has actually run for every existing
`dim_job` row — run it as part of this task's own verification (Step 5
below) before running `dbt build`, or these tests will correctly fail
on any not-yet-classified row and tell you so.

- [ ] **Step 4: Write the coverage singular test**

`dbt/tests/assert_dim_job_has_a_category_for_every_row.sql` (mirrors
the `assert_dim_job_covers_every_job_group.sql` precedent from Step
11's final review — same class of hazard, one join further out):

```sql
-- assert_dim_job_has_a_category_for_every_row: dim_job's join to
-- job_category is LEFT (so a not-yet-classified row still appears),
-- but every row SHOULD eventually get classified — this catches a
-- classify-jobs run that silently stopped partway (e.g. hit an
-- unhandled exception on one row and never retried the rest) rather
-- than letting `category IS NULL` rows accumulate invisibly. A test
-- passes on zero rows returned.

SELECT job_group_id
FROM {{ ref('dim_job') }}
WHERE category IS NULL
```

- [ ] **Step 5: Run `classify-jobs`, then build and verify**

```bash
cd job_search
docker compose up -d postgres ollama
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
PYTHONPATH="packages/core:apps/pipeline" \
  venv/bin/python -m apps.pipeline.app.cli classify-jobs
```

Expected: `classify-jobs complete: rows_written=<N>` where N is close
to `gold.dim_job`'s total row count (2602 as of Step 11). This will
make real Ollama embedding calls and, for the residual, real Anthropic
API calls — expect this to take a while and to incur a small real API
cost; that is expected, not a bug (see this plan's own scope notes).

Then:

```bash
cd dbt
POSTGRES_USER=job_search_owner POSTGRES_PASSWORD=change-me POSTGRES_DB=job_search \
  DBT_PROFILES_DIR=. dbt build --select dim_job
```

Expected: model builds, all schema tests and the new singular test
PASS.

- [ ] **Step 6: Spot-check the classification breakdown against real data**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "
  SELECT category, category_method, COUNT(*) FROM gold.dim_job
  GROUP BY 1, 2 ORDER BY 1, 2;
"
```

Expected: a plausible spread across categories, and — per PLAN.md's own
"~70% coverage" target for the rules stage — `category_method =
'rules'` should account for a clear majority of rows. If it doesn't
(e.g. most rows fall to `'llm'`), that's a real signal the rules table
in Task 2 needs more keyword coverage, not something to silently
accept — flag it to the user rather than adjusting the threshold
unilaterally.

- [ ] **Step 7: Commit**

```bash
git add dbt/models/gold/dim_job.sql dbt/models/gold/_gold.yml \
        dbt/tests/assert_dim_job_has_a_category_for_every_row.sql
git commit -m "feat(job_search): join job_category into gold.dim_job"
```

---

### Task 7: Docs and the manual hand-check acceptance activity

**Files:**
- Modify: `dbt/README.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Extend the gold-layer run-order section**

In `dbt/README.md`'s gold-layer bullet (added in Step 11), add a
paragraph after the existing `compute-survivorship` run-order text:

```markdown
  Category and seniority classification runs after survivorship:

  ```bash
  python3.11 -m apps.pipeline.app.cli classify-jobs
  dbt build --select dim_job
  ```

  `classify-jobs` (PLAN.md Step 11a) only classifies job_group_ids not
  already in `silver.job_category` — it makes real Ollama embedding
  calls and, for titles neither the rules nor embedding stage can
  confidently place, real (billed) Anthropic API calls. A fresh
  environment needs `docker compose up -d ollama` and `docker compose
  exec ollama ollama pull nomic-embed-text` once before this will work.
```

- [ ] **Step 2: Commit**

```bash
git add dbt/README.md
git commit -m "docs(job_search): document the classify-jobs run-order step"
```

- [ ] **Step 3: Surface the manual acceptance activity to the user**

PLAN.md's "Done when" for this step is "hand-checking 100
classifications yields agreement above 90 percent" — an activity
requiring human judgment, not something this plan automates away.
After this plan is fully executed, explicitly tell the user:
1. Real classification results now exist in `gold.dim_job` — pull a
   sample (e.g. `SELECT title_for_display, category, category_method
   FROM gold.dim_job ORDER BY random() LIMIT 100`) and hand-check
   agreement; report the measured figure back, the same way Step 9's
   calibration recorded a real measured precision/recall rather than
   an assumed one.
2. If agreement comes in under 90%, the two levers to adjust are
   Task 2's rules table (add missed keyword patterns) and Task 1's
   seed-example set (add more/better examples per category) — not
   `_CONFIDENT_COSINE_THRESHOLD`, which trades off coverage against
   accuracy in the other direction and shouldn't be the first thing
   tuned.
3. `qa_category`'s mapping (`config/category_map.yml`) and the
   seniority-band regex (`core/classification/seniority.py`) are both
   plain, human-editable files if the taxonomy needs adjusting later —
   neither requires a code review of classification logic to change.
4. "Route low-confidence classifications to a review list" (backlog.yml's
   wording) is satisfied here by `category_confidence` being a real,
   queryable column — not a dedicated review UI like Step 9's. Building
   one is real, separate follow-on work if the hand-check activity
   above shows it's actually needed.
