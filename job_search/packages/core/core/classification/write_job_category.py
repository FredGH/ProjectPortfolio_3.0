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
    SELECT js.job_group_id, js.apply_title_for_display AS title_for_display
    FROM silver.job_survivorship AS js
    LEFT JOIN silver.job_category AS jc ON js.job_group_id = jc.job_group_id
    WHERE jc.job_group_id IS NULL
    """
)
_SELECT_UNCLASSIFIED_SCOPED = text(
    """
    SELECT js.job_group_id, js.apply_title_for_display AS title_for_display
    FROM silver.job_survivorship AS js
    LEFT JOIN silver.job_category AS jc ON js.job_group_id = jc.job_group_id
    WHERE jc.job_group_id IS NULL
    AND js.job_group_id = ANY(:job_group_ids)
    """
)
"""A `job_group_ids`-scoped variant of _SELECT_UNCLASSIFIED.

Exists so a caller — specifically test_write_job_category.py — can
exercise write_job_category's real end-to-end write path against one
fixture row without also picking up every other unclassified
job_group_id already sitting in silver.job_survivorship. Without this,
running the integration test in any environment where Step 11's
compute-survivorship has already populated real data (as it has here:
2,602 real rows) would classify the entire real dataset on every test
run — a real, uncapped LLM/embedding cost and a long-held open
transaction on the shared dev database, not a test. The CLI's
classify-jobs subcommand never passes job_group_ids, so production
behaviour (classify everything unclassified) is unchanged."""
"""Reads silver.job_survivorship directly rather than gold.dim_job.

gold.dim_job's title_for_display column is literally
silver.job_survivorship.apply_title_for_display passed through
unchanged (see dbt/models/gold/dim_job.sql), so this is behaviourally
identical for the columns this query needs — but gold.dim_job is dbt-
materialised as a `table` (dbt_project.yml), meaning a job_group_id
only appears there after an intervening `dbt run`. Querying it here
would silently under-classify (or, in the integration test, find zero
rows) for any job_group_id created since the last `dbt run` —
including a fixture inserted directly into the silver tables the way
test_write_job_category.py (and the established
test_write_blocking_keys.py / test_write_job_survivorship.py pattern
for this same class of Python-written table) does. See the Task 5
report for the full write-up of this deviation from the task brief.
"""

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
    job_group_ids: list[str] | None = None,
) -> int:
    """Classify and upsert every job_group_id not yet in job_category.

    Args:
        engine: The migration/owner engine — SHARED-zone, like every
            other dedup/silver Python-written table.
        adapters: Every available LLM adapter, keyed by provider.
        http_client: The HTTP client for embedding calls.
        job_group_ids: Restrict classification to these job_group_ids
            only, instead of every unclassified row in
            silver.job_survivorship. `None` (the default, and what the
            `classify-jobs` CLI always passes) classifies everything —
            this parameter exists for callers, such as this module's
            own integration test, that need to exercise the write path
            against a small fixture without also reclassifying every
            other unclassified row already in the database.

    Returns:
        The number of job_group_id rows written (0 on a rerun once
        every silver.job_survivorship row already has a job_category
        row).
    """
    settings = get_settings()

    with engine.begin() as conn:
        if job_group_ids is None:
            rows = conn.execute(_SELECT_UNCLASSIFIED).all()
        else:
            rows = conn.execute(
                _SELECT_UNCLASSIFIED_SCOPED, {"job_group_ids": job_group_ids}
            ).all()
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
