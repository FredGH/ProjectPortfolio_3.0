"""Emergent-role settings from `config/emergent.yml`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "emergent.yml"


@dataclass(frozen=True)
class EmergentConfig:
    """Thresholds and word lists.

    Attributes:
        min_employers: Distinct non-agency employers before a title is a trend.
        min_history_days: History below which no novelty index is shown.
        baseline_days: Titles first seen within this many days of the start of
            collection are baseline (always there), never novel.
        min_periods: Distinct weeks with new postings required for maturity.
        min_cluster_size: HDBSCAN's smallest cluster.
        min_samples: HDBSCAN's neighbourhood density.
        cluster_selection: HDBSCAN cluster selection (`leaf` or `eom`).
        ignore_categories: Categories never used as centroids (the dustbin).
        candidate_cosine_threshold: Below this to every category centroid a
            cluster is outside the taxonomy.
        hybrid_min_gain: Required cosine gain of a blend over the best single.
        hybrid_min_weight: Minimum weight of each side of a blend.
        dismiss_suppress_days: How long a dismissed cluster is not re-prompted.
        level_words: Level words stripped from titles.
        stoplist: Title-inflation words that drop a title.
    """

    min_employers: int
    min_history_days: int
    min_periods: int
    baseline_days: int
    min_cluster_size: int
    min_samples: int
    cluster_selection: str
    ignore_categories: tuple[str, ...]
    candidate_cosine_threshold: float
    hybrid_min_gain: float
    hybrid_min_weight: float
    dismiss_suppress_days: int
    level_words: tuple[str, ...]
    stoplist: tuple[str, ...]


def load_config(path: Path | None = None) -> EmergentConfig:
    """Read the config file.

    Args:
        path: Override for tests; defaults to `config/emergent.yml`.

    Returns:
        The config.

    Raises:
        ValueError: If a threshold is missing or out of range.
    """
    raw = yaml.safe_load((path or _DEFAULT_PATH).read_text()) or {}
    ints = (
        "min_employers",
        "min_history_days",
        "min_periods",
        "baseline_days",
        "min_cluster_size",
        "min_samples",
        "dismiss_suppress_days",
    )
    floats = ("candidate_cosine_threshold", "hybrid_min_gain", "hybrid_min_weight")
    values: dict[str, object] = {}
    for key in ints:
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{key} must be a positive integer, got {value!r}")
        values[key] = value
    for key in floats:
        value = raw.get(key)
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not 0 < value <= 1
        ):
            raise ValueError(f"{key} must be a number in (0, 1], got {value!r}")
        values[key] = float(value)
    selection = str(raw.get("cluster_selection", "leaf"))
    if selection not in ("leaf", "eom"):
        raise ValueError(f"cluster_selection must be leaf or eom, got {selection!r}")
    return EmergentConfig(
        **values,  # type: ignore[arg-type]
        cluster_selection=selection,
        ignore_categories=tuple(str(c) for c in raw.get("ignore_categories", [])),
        level_words=tuple(str(w).lower() for w in raw.get("level_words", [])),
        stoplist=tuple(str(w).lower() for w in raw.get("stoplist", [])),
    )
