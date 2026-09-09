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
                    (
                        row.job_key_a,
                        row.job_key_b,
                        float(row.blended_score),
                        row.hard_veto,
                    )
                    for row in conn.execute(_SELECT_SIMILARITY_EDGES).all()
                ),
                threshold=threshold,
            ),
            labeled_pairs=labeled_pairs,
        )

        # Build order here (exact, then fuzzy, then manual) is provably
        # irrelevant to the outcome, not just safe by convention:
        # assign_new_job_groups's best_edge_for_key and best_bridge
        # selections both compare (confidence, _MATCH_METHOD_PRIORITY)
        # tuples rather than taking the first-seen edge, so a 'manual'
        # edge always outranks an equally-confident 'exact'/'fuzzy' one
        # regardless of which list it was concatenated from.
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
    # len(assignments) is always the exact insert count, never inflated
    # by a hit on ON CONFLICT DO NOTHING: every job_key in `assignments`
    # came from `all_job_keys` after excluding whatever `assigned`
    # already covers, and `assigned` is derived from the very same
    # (source_name, source_job_id) natural key the ON CONFLICT clause
    # targets — so no row in `assignments` can already exist in
    # silver.job_identity_map, and the conflict clause can never fire.
    return len(assignments)
