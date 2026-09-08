"""Precision/recall computation for dedup match-threshold calibration
(PLAN.md Step 9).

Pure computation, no database dependency — takes whatever labeled pairs
its caller already fetched (real ones, from core.enrichment... no, from
dedup.pair_labels joined against dedup__similarity_scores) and returns a
curve. PLAN.md Step 9 is explicit that thresholds get chosen by eye from
this curve ("the curve will tell you the knee is at 0.87"), not by an
automated cutoff-selection algorithm — this module stops at producing
the curve.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabeledPair:
    """One hand-labeled candidate pair.

    Attributes:
        job_key_a: The pair's first job.
        job_key_b: The pair's second job.
        blended_score: The pair's dedup__similarity_scores.blended_score
            at the time it was labeled.
        label: "match" or "not_match".
    """

    job_key_a: str
    job_key_b: str
    blended_score: float
    label: str


@dataclass(frozen=True)
class ThresholdMetrics:
    """Precision/recall at one candidate auto-match threshold.

    Attributes:
        threshold: A candidate auto-match cutoff — pairs with
            blended_score >= this value would be auto-matched.
        precision: Of the pairs that would be auto-matched at this
            threshold, the fraction actually labeled "match". None only
            when predicted_match_count is 0 (no pairs to compute over).
        recall: Of every pair labeled "match" in the input, the fraction
            that would be auto-matched at this threshold. None when the
            input has zero "match"-labeled pairs (recall is undefined,
            not zero — there is nothing to find).
        predicted_match_count: How many pairs would be auto-matched at
            this threshold.
    """

    threshold: float
    precision: float | None
    recall: float | None
    predicted_match_count: int


def compute_precision_recall_curve(
    labeled_pairs: list[LabeledPair],
) -> list[ThresholdMetrics]:
    """Compute precision/recall at every distinct blended_score in the input.

    Args:
        labeled_pairs: Every hand-labeled pair to calibrate against.

    Returns:
        One `ThresholdMetrics` per distinct `blended_score` present in
        `labeled_pairs`, sorted by descending threshold. Empty list if
        `labeled_pairs` is empty.
    """
    total_matches = sum(1 for pair in labeled_pairs if pair.label == "match")
    thresholds = sorted({pair.blended_score for pair in labeled_pairs}, reverse=True)

    curve = []
    for threshold in thresholds:
        predicted = [p for p in labeled_pairs if p.blended_score >= threshold]
        true_positives = sum(1 for p in predicted if p.label == "match")
        precision = true_positives / len(predicted) if predicted else None
        recall = true_positives / total_matches if total_matches else None
        curve.append(
            ThresholdMetrics(
                threshold=threshold,
                precision=precision,
                recall=recall,
                predicted_match_count=len(predicted),
            )
        )
    return curve
