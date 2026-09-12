"""Golden-set loading (PLAN.md Step 12a) — dispatched per task. Most
tasks load from a hand-curated `evals/golden/<task>.yml` file;
`job_categorisation` is the one exception, loading from
`classification.category_review_labels` directly (Task 11) — that
table is JOB-170's human hand-check data, and reusing it is the whole
point: it's the same "hand-checked classifications" PLAN.md's Step 12a
asks for, not a second curation effort.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import Engine

_DEFAULT_GOLDEN_DIR = Path(__file__).resolve().parents[4] / "evals" / "golden"

# Tasks whose golden set is DB-backed rather than file-based — checked
# before falling back to the file path.
_DB_BACKED_TASKS = {"job_categorisation"}


class EvalConfigError(Exception):
    """Raised when a task has no registered golden-set source at all."""


@dataclass(frozen=True)
class GoldenCase:
    """One hand-checked (input, expected output) pair.

    Attributes:
        case_id: A stable identifier for this case, for reporting which
            case failed.
        input: The component's input, e.g. `{"title": "..."}`.
        expected: The expected output, e.g. `{"category": "..."}`.
    """

    case_id: str
    input: dict[str, object]
    expected: dict[str, object]


def load_golden_set(
    task: str,
    *,
    engine: Engine | None = None,
    golden_dir: Path | None = None,
    job_group_ids: list[str] | None = None,
) -> list[GoldenCase]:
    """Load `task`'s golden set, dispatched by source.

    Args:
        task: The task name, e.g. "job_categorisation".
        engine: Required (and used) only for DB-backed tasks — see
            `load_job_categorisation_golden_set` (Task 11).
        golden_dir: Directory containing `<task>.yml` files. Defaults
            to `evals/golden/` at the repository root. Unused for
            DB-backed tasks.
        job_group_ids: For DB-backed tasks only — restrict to these
            job_group_ids instead of every reviewed row. `None` (the
            default, and what `run_eval` always passes in production)
            loads everything. Exists for the same reason
            `write_job_category`'s own `job_group_ids` parameter does
            (see that module's docstring): a test seeding its own
            fixture rows into a SHARED table like
            `classification.category_review_labels` must not also pick
            up whatever unrelated real rows already exist there.
            Ignored for file-based tasks.

    Returns:
        The task's golden set, as a list of `GoldenCase`.

    Raises:
        EvalConfigError: If `task` is not DB-backed and no
            `<golden_dir>/<task>.yml` file exists, or if `task` is
            DB-backed and no `engine` was given.
    """
    if task in _DB_BACKED_TASKS:
        if engine is None:
            raise EvalConfigError(
                f"Task {task!r} is DB-backed and requires an `engine` argument."
            )
        from core.evals.golden_db import load_job_categorisation_golden_set

        return load_job_categorisation_golden_set(engine, job_group_ids=job_group_ids)

    directory = golden_dir or _DEFAULT_GOLDEN_DIR
    path = directory / f"{task}.yml"
    if not path.exists():
        raise EvalConfigError(
            f"No golden-set source registered for task {task!r} — expected "
            f"either a DB-backed loader or {path}."
        )
    raw = yaml.safe_load(path.read_text())
    return [
        GoldenCase(case_id=c["case_id"], input=c["input"], expected=c["expected"])
        for c in raw["cases"]
    ]
