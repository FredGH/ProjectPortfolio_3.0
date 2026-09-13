"""Reclassify previously-misclassified forward-deployed postings.

Usage:
    python3.11 scripts/backfill_forward_deployed_engineer.py           # dry run
    python3.11 scripts/backfill_forward_deployed_engineer.py --apply   # reclassify

Step 11b (JOB-444) added forward_deployed_engineer to the classification
taxonomy, but `classify-jobs` (core.classification.write_job_category)
only classifies job_group_ids not already in silver.job_category — a
deliberate cost control, not a retroactive re-run. Any posting that
looks forward-deployed but was classified before this category existed
is stuck under whatever the old 7-category cascade guessed (typically
software_engineer or other) until it is explicitly cleared and
reclassified, exactly as write_job_category's own docstring anticipates
for a "deliberate re-classification run later".

Dry run (the default) only reports candidates — matched on the same
`forward[\\s-]?deployed` phrase core.classification.rules now uses, so
this script and the rules stage agree on what counts as a forward-
deployed title. --apply deletes those rows from silver.job_category
and reclassifies them (against the now-updated rules/embedding/LLM
cascade), which is a real LLM/embedding cost and a write to the shared
dev database. Run `dbt run --select dim_job` afterward — gold.dim_job
is `table`-materialized and won't reflect the change until dbt re-runs.

Run from job_search/ with Postgres up and .env's DATABASE_URL/
OLLAMA_BASE_URL pointed at a reachable host (localhost outside Docker,
same precondition as scripts/seed_target_company.py) — .env's own
values are docker-network hostnames (`postgres`, `ollama`) that only
resolve from inside the compose network. If --apply's delete commits
but reclassification then fails (e.g. because of this), the cleared
rows are not lost: they're simply "not yet classified" the same way
any newly-ingested posting is, and the next `classify-jobs` run (or a
re-run of this script) picks them back up.
"""

from __future__ import annotations

import argparse
import sys

import httpx
from sqlalchemy import Engine, text

sys.path.insert(0, "packages/core")

from core.classification.write_job_category import write_job_category  # noqa: E402
from core.db.session import build_engine  # noqa: E402
from core.llm.adapters.anthropic import AnthropicAdapter  # noqa: E402
from core.llm.adapters.ollama import OllamaAdapter  # noqa: E402
from core.llm.types import LLMAdapter  # noqa: E402
from core.settings import get_settings  # noqa: E402

_FIND_CANDIDATES = text(
    r"""
    SELECT js.job_group_id, js.apply_title_for_display AS title, jc.category
    FROM silver.job_survivorship AS js
    JOIN silver.job_category AS jc ON js.job_group_id = jc.job_group_id
    WHERE js.apply_title_for_display ~* 'forward[\s-]?deployed'
    AND jc.category != 'forward_deployed_engineer'
    """
)

_DELETE_STALE_CLASSIFICATIONS = text(
    """
    DELETE FROM silver.job_category
    WHERE job_group_id = ANY(:job_group_ids)
    """
)


def _find_candidates(engine: Engine) -> list[tuple[str, str, str]]:
    """Find postings that look forward-deployed but aren't classified as such.

    Args:
        engine: The migration/owner engine.

    Returns:
        `(job_group_id, title, current_category)` for every candidate.
    """
    with engine.connect() as conn:
        rows = conn.execute(_FIND_CANDIDATES).all()
    return [(row.job_group_id, row.title, row.category) for row in rows]


def _build_llm_adapters(http_client: httpx.Client) -> dict[str, LLMAdapter]:
    """Build the LLM adapter registry, mirroring cli.py's own helper.

    Duplicated rather than imported — apps/pipeline/app and this
    scripts/ module are separate top-level locations, and this is a
    one-off script, not a shared library entry point.

    Args:
        http_client: The shared HTTP client, reused for the Ollama adapter.

    Returns:
        A dict keyed by provider name.
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


def main() -> int:
    """Report (or, with --apply, reclassify) forward-deployed candidates.

    Returns:
        0 on success, 1 if --apply was given without ANTHROPIC_API_KEY
        configured.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually clear and reclassify the candidates (default: dry run).",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = build_engine(settings.database_url)

    candidates = _find_candidates(engine)
    if not candidates:
        print("No forward-deployed postings are misclassified. Nothing to do.")
        return 0

    print(f"Found {len(candidates)} candidate(s) currently misclassified:")
    for job_group_id, title, category in candidates:
        print(f"  {job_group_id}  {title!r}  (currently: {category})")

    if not args.apply:
        print("\nDry run only — re-run with --apply to reclassify these rows.")
        return 0

    http_client = httpx.Client(timeout=30.0)
    try:
        adapters = _build_llm_adapters(http_client)
        if "anthropic" not in adapters:
            print(
                "\n--apply requires ANTHROPIC_API_KEY to be configured — the "
                "LLM residual stage may be needed for some candidates."
            )
            return 1

        job_group_ids = [job_group_id for job_group_id, _, _ in candidates]
        with engine.begin() as conn:
            conn.execute(
                _DELETE_STALE_CLASSIFICATIONS, {"job_group_ids": job_group_ids}
            )
        written = write_job_category(
            engine,
            adapters=adapters,
            http_client=http_client,
            job_group_ids=job_group_ids,
        )
        print(f"\nReclassified {written} row(s).")
        print(
            "Run `dbt run --select dim_job` next — gold.dim_job is "
            "table-materialized and won't reflect this until dbt re-runs."
        )
        return 0
    finally:
        http_client.close()


if __name__ == "__main__":
    sys.exit(main())
