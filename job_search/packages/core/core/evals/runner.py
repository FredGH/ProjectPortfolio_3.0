"""The eval runner (PLAN.md Step 12a) — loads a task's golden set, runs
every case through the task's configured metric, persists one
evals.eval_runs row, and reports the delta against the immediately-
prior run for the same (task, provider).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, text

from core.evals.golden import GoldenCase, load_golden_set
from core.evals.metrics import exact_match, field_f1
from core.llm.task_config import TaskConfig, load_task_config
from core.llm.types import LLMAdapter

# Below this many cases, one case flipping swings the score too far to
# mean anything — reported as insufficient_data rather than a
# pass/fail verdict (PLAN.md Step 12a, JOB-201).
MINIMUM_GOLDEN_SET_SIZE = 20

_METRICS: dict[str, Callable[[dict, dict], float]] = {
    "exact_match": exact_match,
    "field_f1": field_f1,
}


@dataclass(frozen=True)
class EvalRunResult:
    """The outcome of one `run_eval` invocation.

    Attributes:
        task: The task that was run.
        provider: "target" or "local", as requested.
        status: "ok" (a real run happened), "insufficient_data" (fewer
            than `MINIMUM_GOLDEN_SET_SIZE` cases exist), or
            "provider_not_configured" (the task has no `local_*`
            fields set and "local" was requested).
        score: The mean per-case metric score, or `None` unless
            `status == "ok"`.
        case_count: How many golden cases were evaluated.
        previous_score: The immediately-prior `eval_runs` score for
            this `(task, provider)`, or `None` if this is the first run.
        delta: `score - previous_score`, or `None` if there's no prior
            run.
        regressed: `True` only when `delta` is negative and its
            magnitude exceeds the task's `eval_regression_threshold`.
    """

    task: str
    provider: str
    status: Literal["ok", "insufficient_data", "provider_not_configured"]
    score: float | None
    case_count: int
    previous_score: float | None = None
    delta: float | None = None
    regressed: bool = False


def _resolve_provider(
    task_config: TaskConfig, provider_label: str
) -> tuple[str, str, str] | None:
    """Resolve `provider_label` ("target" or "local") to a concrete
    (provider, model, prompt_family) triple.

    Args:
        task_config: The task's resolved `TaskConfig`.
        provider_label: "target" (the task's production config) or
            "local" (the task's `local_*` fields).

    Returns:
        `(provider, model, prompt_family)`, or `None` if `provider_label`
        is "local" and the task has no `local_*` fields configured.
    """
    if provider_label == "target":
        return task_config.provider, task_config.model, task_config.prompt_family
    if (
        task_config.local_provider is None
        or task_config.local_model is None
        or task_config.local_prompt_family is None
    ):
        return None
    return (
        task_config.local_provider,
        task_config.local_model,
        task_config.local_prompt_family,
    )


# Per-task prediction functions — each knows how to turn one
# GoldenCase's `input` into a predicted-output dict for that task's
# configured metric to compare against `expected`. Extend this
# registry as future steps (13, 15-17, 19, 20) add their own tasks.
def _predict_job_categorisation(
    case: GoldenCase,
    *,
    provider: str,
    model: str,
    prompt_family: str,
    adapters: dict[str, LLMAdapter],
) -> tuple[dict[str, object], str | None]:
    """Predict a `job_categorisation` case's category via the LLM.

    Args:
        case: The golden case to predict — `case.input["title"]` is
            the job title to classify.
        provider: The provider to force `classify_by_llm` to use,
            overriding production routing.
        model: The model to use with `provider`.
        prompt_family: The prompt variant to load for `provider`.
        adapters: Every available LLM adapter, keyed by provider name.

    Returns:
        A tuple of (`{"category": <predicted category>}`, for
        `exact_match` to compare against the case's `expected`) and the
        real `prompt_version` that `classify_by_llm` used for this
        case.
    """
    # Imported locally, not at module level: this predictor is the
    # only place in the eval runner that needs the classification
    # package, so every other task's predictor (added by future steps)
    # can run without pulling in core.classification at all.
    from core.classification.llm_classifier import classify_by_llm

    category, _confidence, prompt_version, _model_id = classify_by_llm(
        case.input["title"],
        adapters=adapters,
        provider=provider,
        model=model,
        prompt_family=prompt_family,
    )
    return {"category": category}, prompt_version


_Predictor = Callable[..., tuple[dict[str, object], str | None]]

_PREDICTORS: dict[str, _Predictor] = {
    "job_categorisation": _predict_job_categorisation,
}


def run_eval(
    task: str,
    provider: str,
    *,
    engine: Engine,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    golden_dir: Path | None = None,
    job_group_ids: list[str] | None = None,
) -> EvalRunResult:
    """Run `task`'s golden set against `provider` and persist the result.

    Args:
        task: The task name, e.g. "job_categorisation".
        provider: "target" or "local".
        engine: The owner-role engine — reads the golden set, writes
            `evals.eval_runs`.
        adapters: Every available LLM adapter, keyed by provider name.
        config_path: Path to the task-config YAML. Defaults to
            `config/llm_tasks.yml`.
        golden_dir: Directory for file-based golden sets. Defaults to
            `evals/golden/`. Unused for DB-backed tasks.
        job_group_ids: For DB-backed golden sets only — restrict to
            these job_group_ids. `None` (the default, and what
            `run-evals` always passes in production) evaluates every
            reviewed case. Exists purely so tests can scope a shared
            table's real data to just their own fixtures — see
            `core.evals.golden.load_golden_set`'s docstring.

    Returns:
        The `EvalRunResult`.

    Raises:
        KeyError: If `task`'s configured `eval_metric` isn't a key in
            `_METRICS`, or if `task` itself isn't a key in
            `_PREDICTORS` (both indicate a task registered in
            `config/llm_tasks.yml` without a matching entry wired up
            in this module).
    """
    task_config = load_task_config(task, config_path=config_path)
    resolved = _resolve_provider(task_config, provider)
    if resolved is None:
        return EvalRunResult(
            task=task,
            provider=provider,
            status="provider_not_configured",
            score=None,
            case_count=0,
        )
    resolved_provider, resolved_model, resolved_prompt_family = resolved

    cases = load_golden_set(
        task, engine=engine, golden_dir=golden_dir, job_group_ids=job_group_ids
    )
    if len(cases) < MINIMUM_GOLDEN_SET_SIZE:
        return EvalRunResult(
            task=task,
            provider=provider,
            status="insufficient_data",
            score=None,
            case_count=len(cases),
        )

    metric_fn = _METRICS[task_config.eval_metric]
    predictor = _PREDICTORS[task]
    predictions = [
        predictor(
            case,
            provider=resolved_provider,
            model=resolved_model,
            prompt_family=resolved_prompt_family,
            adapters=adapters,
        )
        for case in cases
    ]
    scores = [
        metric_fn(prediction, case.expected)
        for (prediction, _prompt_version), case in zip(predictions, cases)
    ]
    score = sum(scores) / len(scores)

    # Every case in one run shares the same resolved (provider, model,
    # prompt_family) — see `_resolve_provider` — so the predictor
    # should return the same `prompt_version` for every case. Assert
    # that rather than silently picking one: a mismatch would mean a
    # predictor is resolving its own prompt version independently of
    # the run's resolved config, which is a bug worth surfacing loudly
    # rather than persisting an arbitrary one of the observed values.
    observed_prompt_versions = {pv for _prediction, pv in predictions if pv is not None}
    if len(observed_prompt_versions) > 1:
        raise RuntimeError(
            f"Task {task!r} predictor returned multiple distinct "
            f"prompt_version values within one run: "
            f"{sorted(observed_prompt_versions)!r}. Every case in a run "
            "shares the same resolved (provider, model, prompt_family), "
            "so this indicates a predictor bug."
        )
    if observed_prompt_versions:
        prompt_version = next(iter(observed_prompt_versions))
    else:
        # Defensive fallback only — should not happen given the
        # `insufficient_data` guard above ensures `cases` (and thus
        # `predictions`) is non-empty, but a predictor could in
        # principle return `None` for every case.
        prompt_version = f"{resolved_prompt_family}.v1"

    with engine.connect() as conn:
        previous_row = conn.execute(
            text(
                "SELECT score FROM evals.eval_runs "
                "WHERE task = :task AND provider = :provider "
                "ORDER BY run_at DESC LIMIT 1"
            ),
            {"task": task, "provider": resolved_provider},
        ).one_or_none()
    previous_score = float(previous_row.score) if previous_row else None
    delta = (score - previous_score) if previous_score is not None else None
    # `or float("inf")` would silently treat a deliberately-configured
    # zero-tolerance threshold (0) the same as "unconfigured" — falsy
    # but meaningfully different from None. Check `is not None`
    # explicitly so a threshold of 0 still flags any regression.
    threshold = task_config.eval_regression_threshold
    regressed = (
        delta is not None
        and delta < 0
        and threshold is not None
        and abs(delta) > threshold
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO evals.eval_runs "
                "(task, provider, prompt_version, metric, score, case_count) "
                "VALUES (:task, :provider, :prompt_version, :metric, "
                ":score, :case_count)"
            ),
            {
                "task": task,
                "provider": resolved_provider,
                "prompt_version": prompt_version,
                "metric": task_config.eval_metric,
                "score": score,
                "case_count": len(cases),
            },
        )

    return EvalRunResult(
        task=task,
        provider=provider,
        status="ok",
        score=score,
        case_count=len(cases),
        previous_score=previous_score,
        delta=delta,
        regressed=regressed,
    )
