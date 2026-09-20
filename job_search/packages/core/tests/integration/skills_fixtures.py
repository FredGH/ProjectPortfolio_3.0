"""Shared helpers for the Step 14 integration tests.

The dev Postgres is shared with the running stack, so every test uses
fixture-prefixed IDs and strings and removes them with `purge_fixtures`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from sqlalchemy import Engine, text

from core.db.session import build_engine
from core.settings import get_settings

FIXTURE_ESCO_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "esco"


def live_owner_engine() -> Engine:
    """Build the owner-role engine, skipping the test if Postgres is down.

    Returns:
        An `Engine` on `Settings.database_url`.

    Raises:
        unittest.SkipTest: If Postgres is not reachable.
    """
    engine = build_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any connection failure means skip
        raise unittest.SkipTest(f"Postgres not reachable ({exc})") from None
    return engine


def live_app_engine() -> Engine:
    """Build the RLS-subject app-role engine.

    Returns:
        An `Engine` on `Settings.app_database_url`.
    """
    return build_engine(get_settings().app_database_url)


def sparse_vector(components: dict[int, float], dimension: int = 768) -> list[float]:
    """Build a vector that is zero except at the given indices.

    Args:
        components: Map of index to value.
        dimension: Total length of the vector.

    Returns:
        The vector. Real embeddings are dense, so a sparse vector on a
        high axis has near-zero cosine similarity with them — fixtures
        therefore win or lose nearest-neighbour searches predictably
        even when real ESCO rows are loaded.
    """
    vector = [0.0] * dimension
    for index, value in components.items():
        vector[index] = value
    return vector


def axis_vector(index: int) -> list[float]:
    """Build a unit vector along one axis.

    Args:
        index: The axis.

    Returns:
        A 768-dim unit vector.
    """
    return sparse_vector({index: 1.0})


def purge_fixtures(engine: Engine) -> None:
    """Delete every fixture row this suite may have created.

    Args:
        engine: The owner-role engine.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM silver.job_skill_extraction "
                "WHERE job_group_id LIKE 'fixture-job-%'"
            )
        )
        conn.execute(
            text(
                "DELETE FROM silver.job_survivorship "
                "WHERE job_group_id LIKE 'fixture-job-%'"
            )
        )
        conn.execute(
            text("DELETE FROM silver.skill_mapping WHERE raw_norm LIKE 'zzfixture%'")
        )
        conn.execute(
            text("DELETE FROM silver.skill_alias WHERE alias_norm LIKE 'zzfixture%'")
        )
        conn.execute(
            text(
                "DELETE FROM silver.custom_skill "
                "WHERE skill_id LIKE 'custom:fixture-%' "
                "OR skill_id LIKE 'custom:zzfixture-%'"
            )
        )
        conn.execute(
            text("DELETE FROM esco.occupation WHERE occupation_id LIKE 'fixture-%'")
        )
        conn.execute(text("DELETE FROM esco.skill WHERE skill_id LIKE 'fixture-%'"))


def insert_mapping(
    conn,
    raw_norm: str,
    *,
    skill_id: str | None = None,
    method: str = "none",
    score: float | None = None,
    review_status: str | None = "open",
    candidate_skill_id: str | None = None,
    candidate_score: float | None = None,
    seen_in_cv: bool = False,
) -> None:
    """Insert one `silver.skill_mapping` fixture row.

    Args:
        conn: An open connection inside the caller's transaction.
        raw_norm: The normalised string (also used as `raw_example`).
        skill_id: The mapped skill, or None if unmapped.
        method: alias | label | embedding | none.
        score: Cosine score for an embedding match.
        review_status: NULL, open, rejected, resolved or dismissed.
        candidate_skill_id: Best below-threshold neighbour, if any.
        candidate_score: Its cosine score.
        seen_in_cv: Whether a CV contained this string.
    """
    conn.execute(
        text(
            "INSERT INTO silver.skill_mapping (raw_norm, raw_example, skill_id, "
            "method, score, candidate_skill_id, candidate_score, review_status, "
            "seen_in_cv) VALUES (:raw_norm, :raw_norm, :skill_id, :method, :score, "
            ":candidate_skill_id, :candidate_score, :review_status, :seen_in_cv)"
        ),
        {
            "raw_norm": raw_norm,
            "skill_id": skill_id,
            "method": method,
            "score": score,
            "candidate_skill_id": candidate_skill_id,
            "candidate_score": candidate_score,
            "review_status": review_status,
            "seen_in_cv": seen_in_cv,
        },
    )


def insert_job_skills(
    conn,
    job_group_id: str,
    prompt_version: str,
    skills: list[tuple[str, str, str]],
) -> None:
    """Insert a `job_skill_extraction` row and its `job_skill_raw` rows.

    Args:
        conn: An open connection inside the caller's transaction.
        job_group_id: The job (use a `fixture-job-` prefix).
        prompt_version: The extraction's prompt version.
        skills: `(raw_skill, raw_norm, requirement_level)` tuples.
    """
    conn.execute(
        text(
            "INSERT INTO silver.job_skill_extraction "
            "(job_group_id, prompt_version, model) "
            "VALUES (:job, :version, 'fixture-model')"
        ),
        {"job": job_group_id, "version": prompt_version},
    )
    for raw_skill, raw_norm, level in skills:
        conn.execute(
            text(
                "INSERT INTO silver.job_skill_raw (job_group_id, prompt_version, "
                "raw_skill, raw_norm, requirement_level) "
                "VALUES (:job, :version, :raw, :norm, :level)"
            ),
            {
                "job": job_group_id,
                "version": prompt_version,
                "raw": raw_skill,
                "norm": raw_norm,
                "level": level,
            },
        )
