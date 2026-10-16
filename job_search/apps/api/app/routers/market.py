"""Read-only market-mart endpoints (PLAN.md Step 21a).

The marts are shared data (no per-user rows), built by dbt, never by the API.
Every endpoint just reads a `gold.fct_*` table; if dbt has not built them the
answer is a 503 naming the command, not a stack trace.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

router = APIRouter()

REBUILD_COMMAND = (
    "cd dbt && ../venv-dbt/bin/dbt build --profiles-dir . --select int_market_job+"
)


def _plain(value: Any) -> Any:
    """Make a database value JSON-friendly.

    Args:
        value: A value from a result row.

    Returns:
        Decimals as floats, dates and timestamps as ISO strings, else as is.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _rows(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    """Run a read-only query and return dict rows.

    Args:
        engine: The app-role engine.
        sql: The SQL (only constants and bound parameters are interpolated).
        **params: Bound parameters.

    Returns:
        The rows as dicts with JSON-friendly values.

    Raises:
        HTTPException: 503 when the marts have not been built.
    """
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
            keys = list(result.keys())
            return [
                {k: _plain(v) for k, v in zip(keys, row, strict=True)}
                for row in result.all()
            ]
    except ProgrammingError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"The market marts are not built yet. Run: {REBUILD_COMMAND}",
        ) from exc


@router.get("/market/overview")
def overview(engine: Engine = Depends(get_app_db_engine)) -> dict[str, Any]:
    """Describe the last build and what the marts cover.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The build info plus the categories, regions and the latest month.
    """
    info = _rows(engine, "SELECT * FROM gold.market_build_info")
    if not info:
        raise HTTPException(
            status_code=503,
            detail=f"The market marts are empty. Run: {REBUILD_COMMAND}",
        )
    categories = _rows(
        engine,
        "SELECT category, SUM(n_postings) AS n FROM gold.fct_market_composition "
        "GROUP BY category ORDER BY n DESC",
    )
    stamp = _rows(
        engine,
        "SELECT MAX(period_start) AS latest_month, BOOL_AND(panel_complete) AS "
        "panel_complete, BOOL_OR(trend_break) AS trend_break "
        "FROM gold.fct_market_composition",
    )
    return {
        **info[0],
        "rebuild_command": REBUILD_COMMAND,
        "categories": [c["category"] for c in categories],
        **(stamp[0] if stamp else {}),
    }


@router.get("/market/salary")
def salary(
    category: str | None = None, engine: Engine = Depends(get_app_db_engine)
) -> list[dict[str, Any]]:
    """Salary statistics with their n and disclosure rate.

    Args:
        category: Restrict to one category, or None for all.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `fct_market_salary` rows, most disclosed first.
    """
    return _rows(
        engine,
        "SELECT * FROM gold.fct_market_salary WHERE (CAST(:c AS text) IS NULL "
        "OR category = :c) ORDER BY n_disclosed DESC, n_postings DESC, category, "
        "region, contract_type",
        c=category,
    )


@router.get("/market/skills")
def skills(
    category: str,
    region: str | None = None,
    limit: int = Query(25, ge=1, le=200),
    engine: Engine = Depends(get_app_db_engine),
) -> list[dict[str, Any]]:
    """The most demanded skills of a category in the latest month.

    Args:
        category: The category.
        region: Restrict to one region, or None to sum across regions.
        limit: How many skills.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Skills ranked by frequency, with share, must-have ratio and the 90-day
        rank delta (NULL until history exists).
    """
    return _rows(
        engine,
        "SELECT skill_label, SUM(frequency) AS frequency, "
        "SUM(frequency)::numeric / NULLIF(SUM(category_postings), 0) AS share, "
        "SUM(must_have_postings)::numeric / NULLIF(SUM(must_have_postings + "
        "nice_to_have_postings), 0) AS must_have_ratio, "
        "MAX(rank_delta_90d) AS rank_delta_90d "
        "FROM gold.fct_skill_demand WHERE category = :c "
        "AND period_start = (SELECT MAX(period_start) FROM gold.fct_skill_demand) "
        "AND (CAST(:r AS text) IS NULL OR region = :r) "
        "GROUP BY skill_label ORDER BY frequency DESC, skill_label LIMIT :n",
        c=category,
        r=region,
        n=limit,
    )


@router.get("/market/premium")
def premium(
    category: str, engine: Engine = Depends(get_app_db_engine)
) -> list[dict[str, Any]]:
    """Salary premium per skill (published only with enough data).

    Args:
        category: The category.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `fct_skill_salary_premium` rows with a premium, largest first; rows
        without enough n are included last with a NULL premium.
    """
    return _rows(
        engine,
        "SELECT * FROM gold.fct_skill_salary_premium WHERE category = :c "
        "ORDER BY premium DESC NULLS LAST, n_with DESC LIMIT 100",
        c=category,
    )


@router.get("/market/cooccurrence")
def cooccurrence(
    category: str,
    limit: int = Query(25, ge=1, le=200),
    engine: Engine = Depends(get_app_db_engine),
) -> list[dict[str, Any]]:
    """Skill pairs that appear together.

    Args:
        category: The category.
        limit: How many pairs.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Pairs by support, with lift.
    """
    return _rows(
        engine,
        "SELECT skill_a_label, skill_b_label, support, lift "
        "FROM gold.fct_skill_cooccurrence WHERE category = :c "
        "ORDER BY support DESC, lift DESC LIMIT :n",
        c=category,
        n=limit,
    )


@router.get("/market/composition")
def composition(
    category: str, engine: Engine = Depends(get_app_db_engine)
) -> list[dict[str, Any]]:
    """Work-model, contract and seniority mix per month.

    Args:
        category: The category.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `fct_market_composition` rows summed across regions (weighted by
        postings), per month.
    """
    shares = (
        "share_remote",
        "share_hybrid",
        "share_onsite",
        "share_work_model_unknown",
        "share_permanent",
        "share_contract",
        "share_contract_type_unknown",
        "share_junior",
        "share_mid",
        "share_senior",
        "share_lead",
        "share_principal",
        "share_seniority_unknown",
    )
    weighted = ", ".join(
        f"SUM({s} * n_postings) / NULLIF(SUM(n_postings), 0) AS {s}" for s in shares
    )
    return _rows(
        engine,
        f"SELECT period_start, SUM(n_postings) AS n_postings, {weighted}, "
        "BOOL_AND(panel_complete) AS panel_complete, MAX(matrix_version) AS "
        "matrix_version FROM gold.fct_market_composition WHERE category = :c "
        "GROUP BY period_start ORDER BY period_start",
        c=category,
    )


@router.get("/market/volume")
def volume(
    category: str, engine: Engine = Depends(get_app_db_engine)
) -> list[dict[str, Any]]:
    """Weekly postings of a category, with change only where it is allowed.

    Args:
        category: The category.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Per week: postings, the panel stamps, and week-on-week change, which is
        NULL unless every segment's change is allowed (a segment that fell to
        zero has no row, so a total of changes can understate a drop).
    """
    return _rows(
        engine,
        "SELECT period_start, SUM(postings) AS postings, "
        "BOOL_AND(panel_complete) AS panel_complete, MAX(matrix_version) AS "
        "matrix_version, BOOL_OR(trend_break) AS trend_break, "
        "CASE WHEN COUNT(wow_change) = COUNT(*) THEN SUM(wow_change) END AS "
        "wow_change FROM gold.fct_market_volume WHERE category = :c "
        "GROUP BY period_start ORDER BY period_start",
        c=category,
    )


@router.get("/market/employers")
def employers(
    category: str,
    limit: int = Query(25, ge=1, le=200),
    engine: Engine = Depends(get_app_db_engine),
) -> list[dict[str, Any]]:
    """The most active employers of a category.

    Args:
        category: The category.
        limit: How many employers.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Employers by postings with their agency share.
    """
    return _rows(
        engine,
        "SELECT company_name, SUM(postings) AS postings, "
        "SUM(agency_postings)::numeric / NULLIF(SUM(postings), 0) AS agency_share, "
        "MIN(first_seen_month) AS first_seen_month "
        "FROM gold.fct_employer_activity WHERE category = :c "
        "GROUP BY company_name ORDER BY postings DESC, company_name LIMIT :n",
        c=category,
        n=limit,
    )


@router.get("/market/lifecycle")
def lifecycle(
    category: str | None = None, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, Any]:
    """How long postings stay live (live ones are censored).

    Args:
        category: Restrict to one category, or None for all.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Counts of live and gone jobs and the median days live of the gone ones
        (NULL when none has disappeared yet).
    """
    rows = _rows(
        engine,
        "SELECT COUNT(*) AS n_jobs, COUNT(*) FILTER (WHERE still_live) AS n_live, "
        "COUNT(*) FILTER (WHERE NOT still_live) AS n_gone, "
        "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_live) AS median_days_live "
        "FROM gold.fct_posting_lifecycle WHERE (CAST(:c AS text) IS NULL "
        "OR category = :c)",
        c=category,
    )
    return rows[0]


@router.get("/market/macro/ai-penetration")
def ai_penetration(
    category: str, engine: Engine = Depends(get_app_db_engine)
) -> list[dict[str, Any]]:
    """Share of postings mentioning each AI term, by month.

    Args:
        category: The category.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `fct_ai_penetration` rows (term `any` first), with n and the stamps.
    """
    return _rows(
        engine,
        "SELECT * FROM gold.fct_ai_penetration WHERE category = :c "
        "ORDER BY period_start, (term <> 'any'), term",
        c=category,
    )


@router.get("/market/macro/role-drift")
def role_drift(
    category: str, engine: Engine = Depends(get_app_db_engine)
) -> list[dict[str, Any]]:
    """How far the category's skill demand has moved (NULL until comparable).

    Args:
        category: The category.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `fct_role_drift` rows.
    """
    return _rows(
        engine,
        "SELECT * FROM gold.fct_role_drift WHERE category = :c "
        "ORDER BY period_start, lookback_months",
        c=category,
    )


@router.get("/market/macro/skill-migration")
def skill_migration(
    engine: Engine = Depends(get_app_db_engine),
) -> list[dict[str, Any]]:
    """Where AI-mentioning postings sit, by category and month.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        `fct_ai_skill_migration` rows.
    """
    return _rows(
        engine,
        "SELECT * FROM gold.fct_ai_skill_migration ORDER BY period_start, "
        "ai_postings DESC",
    )


@router.get("/market/macro/displacement")
def displacement(
    category: str, engine: Engine = Depends(get_app_db_engine)
) -> list[dict[str, Any]]:
    """Oversight vs build wording by month (a weak signal).

    Args:
        category: The category.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `fct_displacement_language` rows.
    """
    return _rows(
        engine,
        "SELECT * FROM gold.fct_displacement_language WHERE category = :c "
        "ORDER BY period_start",
        c=category,
    )
