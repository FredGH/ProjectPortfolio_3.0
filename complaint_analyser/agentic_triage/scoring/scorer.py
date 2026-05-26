from __future__ import annotations

from agentic_triage.core.config import DomainConfig


def compute_priority(
    dimension_scores: dict[str, int],
    config: DomainConfig,
) -> tuple[str, float]:
    """Return (priority_label, composite_score).

    Escalation check runs first: if any single dimension exceeds the per-level
    threshold, that level's label is returned regardless of the composite score.
    Composite check then falls through the priority list highest → lowest.
    """
    total_weight = sum(d.weight for d in config.scoring_dimensions)
    composite = (
        sum(
            dimension_scores.get(d.name, 0) * d.weight
            for d in config.scoring_dimensions
        )
        / total_weight
        if total_weight > 0
        else 0.0
    )

    # Escalation override — must run before composite check
    for level in config.priority_levels:
        if level.escalate_if_any_dimension_exceeds is not None:
            if any(
                score > level.escalate_if_any_dimension_exceeds
                for score in dimension_scores.values()
            ):
                return level.label, composite

    # Composite threshold check (levels ordered highest → lowest)
    for level in config.priority_levels:
        if composite >= level.min_composite:
            return level.label, composite

    return config.priority_levels[-1].label, composite
