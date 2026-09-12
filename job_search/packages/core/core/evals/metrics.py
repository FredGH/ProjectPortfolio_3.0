"""Per-component eval metrics (PLAN.md Step 12a). Exact match and
field-level F1 for structured comparisons; `llm_judge` (Task 5) for
rubric-graded generation output where exact match is the wrong
instrument.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.llm.gateway import complete
from core.llm.prompts import load_prompt
from core.llm.types import LLMAdapter


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


@dataclass(frozen=True)
class JudgeResult:
    """One LLM-as-judge grading result.

    Attributes:
        score: The judge's score, 0.0-1.0.
        rationale: The judge's one-sentence explanation. Also carries a
            parse-failure message when the judge's response couldn't be
            read as the expected JSON shape.
    """

    score: float
    rationale: str


def llm_judge(
    output: str, rubric: str, *, adapters: dict[str, LLMAdapter]
) -> JudgeResult:
    """Grade `output` against `rubric` via the `eval_judge` LLM task.

    Args:
        output: The generated text to grade.
        rubric: The grading rubric, in plain language.
        adapters: Every available LLM adapter, keyed by provider —
            passed through to `core.llm.gateway.complete`.

    Returns:
        The `JudgeResult`. Falls back to `JudgeResult(0.0, "could not
        parse judge response: ...")` if the judge's response can't be
        parsed as the expected JSON shape — one malformed judge
        response should not crash a whole eval run.

    Raises:
        KeyError: If the resolved judge provider is not in `adapters`.
        FileNotFoundError: If the `eval_judge` prompt registry file
            (`prompts/eval_judge/claude.v1.md`) is missing.
    """
    prompt_template = load_prompt("eval_judge", "claude", 1)
    prompt = prompt_template.format(rubric=rubric, output=output)
    response = complete(
        task="eval_judge",
        prompt=prompt,
        prompt_version="claude.v1",
        adapters=adapters,
    )
    try:
        parsed = json.loads(response.text.strip())
        return JudgeResult(score=float(parsed["score"]), rationale=parsed["rationale"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        return JudgeResult(
            score=0.0, rationale=f"could not parse judge response: {exc}"
        )
