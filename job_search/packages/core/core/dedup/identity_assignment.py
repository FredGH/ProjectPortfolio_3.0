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


# Tie-break order for `assign_new_job_groups`'s best_edge_for_key selection
# when two edges touching the same job_key carry equal confidence: manual is
# human-verified, exact is deterministic-but-automatic, fuzzy is
# probabilistic — so a human's explicit confirmation must win a tie even
# against an equally-confident automatic signal.
_MATCH_METHOD_PRIORITY = {"manual": 2, "exact": 1, "fuzzy": 0}


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
                if current is None or (
                    e.confidence,
                    _MATCH_METHOD_PRIORITY[e.match_method],
                ) > (
                    current.confidence,
                    _MATCH_METHOD_PRIORITY[current.match_method],
                ):
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
