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
                "DELETE FROM silver.custom_skill WHERE skill_id LIKE 'custom:fixture-%'"
            )
        )
        conn.execute(
            text("DELETE FROM esco.occupation WHERE occupation_id LIKE 'fixture-%'")
        )
        conn.execute(text("DELETE FROM esco.skill WHERE skill_id LIKE 'fixture-%'"))
