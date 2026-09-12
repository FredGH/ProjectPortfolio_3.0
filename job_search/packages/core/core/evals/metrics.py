"""Per-component eval metrics (PLAN.md Step 12a). Exact match and
field-level F1 for structured comparisons; `llm_judge` (Task 5) for
rubric-graded generation output where exact match is the wrong
instrument.
"""

from __future__ import annotations


def exact_match(predicted: dict[str, object], expected: dict[str, object]) -> float:
    """Score 1.0 if every field in `expected` matches `predicted` exactly.

    Extra fields in `predicted` beyond what `expected` names are
    ignored — this scores whether the required fields are right, not
    whether the predictor returned exactly the same field set.

    Args:
        predicted: The component's output.
        expected: The golden case's expected output.

    Returns:
        1.0 if every key in `expected` is present in `predicted` with
        an equal value, else 0.0.
    """
    return 1.0 if all(predicted.get(k) == v for k, v in expected.items()) else 0.0


def field_f1(predicted: dict[str, object], expected: dict[str, object]) -> float:
    """Per-field F1 between two flat dicts, for structured extraction.

    Args:
        predicted: The component's output.
        expected: The golden case's expected output.

    Returns:
        The F1 score over field-level exact matches: precision is the
        fraction of `predicted`'s fields that are correct, recall is
        the fraction of `expected`'s fields that were predicted
        correctly. Returns 1.0 when both dicts are empty (trivially
        correct), 0.0 if only one is empty.
    """
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected:
        return 0.0

    correct = sum(1 for k, v in expected.items() if predicted.get(k) == v)
    precision = correct / len(predicted)
    recall = correct / len(expected)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
