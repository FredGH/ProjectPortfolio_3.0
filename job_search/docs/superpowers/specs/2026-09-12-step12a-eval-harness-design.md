# Step 12a — LLM eval harness and prompt versioning (JOB-182)

**Status:** Approved for planning
**Date:** 2026-09-12

## Context

PLAN.md's Step 12a and `backlog.yml`'s JOB-182 describe an eval harness
covering seven LLM components: CV extraction, JD skill extraction,
categorisation, scoring rationale, tailoring, cover letters, Q&A. Of
those, only **categorisation** (Step 11a) exists in the codebase today —
CV extraction (Step 13), scoring (Step 15/16), tailoring (Step 17),
cover letters (Step 19) and Q&A (Step 20) are all future steps. A golden
set needs a real input/output contract to hand-curate cases against,
so this spec scopes Step 12a to what can actually be built and proven
today: **generic, reusable harness infrastructure, plus a real
end-to-end golden set for categorisation** — the one component that
exists. Each future step (13, 15–17, 19, 20) is expected to add its own
golden set when it lands, using this same infrastructure.

Two things are deliberately deferred, both confirmed with the user:

- **CI wiring** (the weekly target-provider run PLAN.md describes) — no
  CI exists for this project yet, and it needs an `ANTHROPIC_API_KEY`
  GitHub secret only the user can add. The CLI is written so adding a
  workflow later is a config-only change, not a runner change.
- **RAGAS faithfulness** — `pip install ragas` pulls in the full
  LangChain + LangGraph + OpenAI-SDK stack (~40 packages) as transitive
  dependencies. DECISIONS.md §3 names RAGAS as an allowed exception to
  "no LLM orchestration framework," but in practice its dependency
  footprint reintroduces exactly that framework surface. Its only
  caller (Step 17's tailoring critic) doesn't exist yet. Deferred until
  Step 17 lands and the trade-off can be weighed against a real caller,
  not speculatively.

## Scope

**In scope:**

1. Prompt registry (`prompts/<task>/<family>.v<N>.md`) + loader
2. Migrate `job_categorisation`'s existing inline prompt into the registry
3. Metrics library: `exact_match`, `field_f1`, `llm_judge`
4. Golden-set loading, dispatched per task (DB-backed for
   `job_categorisation`; file-based for future tasks)
5. Minimum-golden-set-size enforcement (documented and code-enforced)
6. `evals.eval_runs` table — persists every run, per provider, for
   regression comparison
7. `run-evals` pipeline CLI subcommand
8. Fixed temperature/seed support in both LLM adapters, for reproducible
   eval runs
9. `prompt_version`/`model_id` stamped on `silver.job_category`'s
   LLM-produced rows — the one existing "generated artefact" row-type
10. Tests for all of the above

**Out of scope (this pass):** RAGAS/faithfulness, CI workflow, any
golden set for a component that doesn't exist yet.

## Architecture

### 1. Prompt registry

```
prompts/
  job_categorisation/
    claude.v1.md
```

Plain text files — the prompt template body, with `{placeholder}`
tokens for `str.format`-style interpolation, matching the shape
`llm_classifier.py`'s current inline `_PROMPT_TEMPLATE` already uses.

```python
# core/llm/prompts.py
def load_prompt(task: str, model_family: str, version: int) -> str:
    """Load prompts/<task>/<model_family>.v<version>.md's raw text.

    Raises:
        FileNotFoundError: if no such file exists.
    """
```

`llm_classifier.py` changes to load its template via
`load_prompt("job_categorisation", "claude", 1)` instead of the inline
constant, and derives `prompt_version = "claude.v1"` from the same
`(family, version)` pair — replacing today's ad-hoc
`"job_categorisation-v1"` string with the `<family>.v<N>` shape
DECISIONS.md §1 specifies. `job_categorisation`'s `prompt_family` value
in `config/llm_tasks.yml` (already `claude`) is the source of truth for
which family a task loads.

Note found in passing, not fixed here (flagged, not silently changed):
DECISIONS.md §1's task-split table lists categorisation as "local —
never migrates," but `config/llm_tasks.yml` currently routes
`job_categorisation` to `anthropic`. Out of scope for this step —
belongs to whoever owns Step 11a's provider choice.

### 2. Golden sets

```python
# core/evals/golden.py
@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    input: dict[str, object]
    expected: dict[str, object]

def load_golden_set(task: str, *, engine: Engine | None = None) -> list[GoldenCase]:
    """Dispatch per task: job_categorisation reads
    classification.category_review_labels joined to gold.dim_job
    (engine required); every other task reads
    evals/golden/<task>.yml (engine unused, may be None).

    Raises:
        EvalConfigError: if `task` has no registered golden-set source.
    """
```

`job_categorisation`'s cases: `input = {"title": dim_job.title_raw}`,
`expected = {"category": reviewed_category}` — one row per
`classification.category_review_labels` entry, joined to `dim_job` for
the title. This is JOB-170's hand-check data, reused directly — not a
separate curation effort.

File-based format for future tasks (`evals/golden/<task>.yml`):

```yaml
cases:
  - case_id: "case-001"
    input: {...}
    expected: {...}
```

### 3. Minimum golden-set size

```python
# core/evals/runner.py
MINIMUM_GOLDEN_SET_SIZE = 20  # below this, one case flips the score too far to mean anything
```

Documented here and in `core/evals/runner.py`'s module docstring. A
task with fewer cases than this reports `status="insufficient_data"`,
never a pass/fail verdict.

### 4. Metrics library

```python
# core/evals/metrics.py
def exact_match(predicted: dict, expected: dict) -> float:  # 1.0 or 0.0
def field_f1(predicted: dict, expected: dict) -> float:      # 0.0-1.0, per-field
def llm_judge(
    output: str, rubric: str, *, adapters: dict[str, LLMAdapter]
) -> JudgeResult:  # JudgeResult(score: float, rationale: str)
```

`llm_judge` calls `core.llm.gateway.complete` with a dedicated
`eval_judge` task entry in `config/llm_tasks.yml` (routed to
`anthropic` — a judge validated on a weaker model than what it's
judging produces false confidence, same reasoning DECISIONS.md §1
applies to the fabrication critic). Tested with the same fake-adapter
pattern `test_llm_gateway.py` already uses — no real network calls in
unit tests.

`job_categorisation`'s eval uses `exact_match` (single-label
classification; F1/judge add nothing here). `eval_metric` is
recorded per task in `config/llm_tasks.yml`.

### 5. `evals.eval_runs` — run history and regression comparison

New migration (`0015_create_evals_eval_runs.py`), SHARED data (same
two-zone reasoning as `dedup.calibration_thresholds`):

```sql
evals.eval_runs (
    id SERIAL PRIMARY KEY,
    task TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    metric TEXT NOT NULL,
    score NUMERIC NOT NULL,
    case_count INTEGER NOT NULL,
    run_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

Written only by the pipeline CLI (owner role) — no request-serving
access needed, unlike `dedup.pair_labels`/`category_review_labels`.
Append-only, same reasoning as `calibration_thresholds`: every run adds
a row, "current" is the latest per `(task, provider)`, and history is
free for later trend-watching.

### 6. `run-evals` CLI subcommand

```
docker compose run --rm pipeline run-evals --task job_categorisation --provider target
docker compose run --rm pipeline run-evals --all --provider both
```

For each `(task, provider)` pair: load the golden set, skip
(`insufficient_data`) if under the minimum, else run every case through
the task's configured metric and average the per-case scores into one
`score` (e.g. for `exact_match`, this is plain accuracy over the golden
set). Persist one `eval_runs` row, then compare `score` to the
immediately-prior `eval_runs` row for the same `(task, provider)` —
most recent by `run_at`, regardless of `prompt_version`, since the
whole point is catching a regression introduced by a prompt-version
bump — and report the delta. `--provider both` runs local and target
in the same invocation and prints them side by side (PLAN.md: "so the
quality gap is visible, not assumed"). Exit non-zero only when the
score **decreases** by more than the task's configured threshold
(`eval_regression_threshold` in `config/llm_tasks.yml`, default e.g.
0.05) — an improvement, however large, never fails the run. This is
what lets CI (when wired later) fail a PR on regression.

### 7. Reproducibility — fixed temperature/seed where supported

Neither adapter currently accepts a temperature or seed parameter at
all (`LLMAdapter.complete(self, *, model: str, prompt: str)`) — an eval
regression could be pure sampling noise rather than a real prompt/model
change, which would make the whole regression-detection premise
unreliable. Extend the Protocol and both adapters:

```python
def complete(
    self, *, model: str, prompt: str, temperature: float = 0.0, seed: int | None = None
) -> LLMResponse: ...
```

- **Ollama**: passes both through as `options.temperature` /
  `options.seed` — Ollama supports true seeded determinism.
- **Anthropic**: passes `temperature` through; **ignores `seed`** — the
  Messages API has no seed parameter, and Anthropic does not guarantee
  bit-for-bit reproducibility even at `temperature=0`. Documented in
  the adapter's docstring so this isn't mistaken for an oversight.

Backward-compatible (new params default to today's implicit behaviour
for every existing caller — `llm_classifier.py` needs no changes), and
the eval runner relies on the defaults rather than passing its own
values, so "deterministic by default" holds for every task, not just
eval runs.

### 8. Stamping `prompt_version`/`model_id` on generated rows

`silver.job_category` is the only table in the codebase today produced
by an LLM call end-to-end (the `'llm'`-method rows from the
classification cascade), so it's the concrete target for "stamp
prompt_version and model_id on every generated artefact row" right
now — every future artefact table (Steps 17, 19, 20) does the same
when it lands, using the same fields.

- `Classification` (`core/classification/classify.py`) gains
  `prompt_version: str | None` and `model_id: str | None`, both `None`
  for `'rules'`/`'embedding'` rows.
- `classify_by_llm` (`llm_classifier.py`) returns the `LLMResponse`'s
  `model` and the loaded prompt's version alongside `(category,
  confidence)`, instead of discarding them as it does today.
- Migration `0016_add_prompt_version_to_job_category.py` adds
  `prompt_version TEXT NULL` and `model_id TEXT NULL` to
  `silver.job_category`.
- `write_job_category.py`'s `_UPSERT` and its call site pass these
  through.

## Config additions

`config/llm_tasks.yml` gains, per task:

```yaml
job_categorisation:
  provider: anthropic
  model: claude-sonnet-5
  prompt_family: claude
  eval_metric: exact_match
  eval_regression_threshold: 0.05
eval_judge:
  provider: anthropic
  model: claude-sonnet-5
  prompt_family: claude
```

## Testing

- `core/evals/metrics.py` — unit tests, no I/O (`exact_match`,
  `field_f1` fully synthetic; `llm_judge` via the fake-adapter pattern)
- `core/evals/golden.py` — unit test for file-based loading (synthetic
  YAML fixture); integration test for the DB-backed `job_categorisation`
  path (real Postgres, seeded `category_review_labels` + `dim_job` rows,
  following this session's `test_classification_router.py` pattern)
- `core/llm/prompts.py` — unit test (`load_prompt` against a real
  registry file; `FileNotFoundError` for a missing one)
- `core/evals/runner.py` — integration test: seed a golden set below the
  minimum → `insufficient_data`; seed enough cases → real score persisted
  to `eval_runs`, second run detects a regression correctly
- Migrations 0015, 0016 — upgrade/downgrade/upgrade round-trip, same
  style as 0014
- `AnthropicAdapter`/`OllamaAdapter` — unit tests asserting `temperature`
  is sent on every call, `seed` is sent for Ollama and silently dropped
  for Anthropic
- `write_job_category.py` — integration test asserting an `'llm'`-method
  row gets a non-null `prompt_version`/`model_id`, and a `'rules'`- or
  `'embedding'`-method row gets both `NULL`

## Done when

- `run-evals --task job_categorisation --provider target` runs the
  real golden set (once JOB-170 populates it) and reports pass/fail
  against the configured metric
- Every `eval_runs` row records `task`, `provider`, `prompt_version`,
  and `score` — never blended across providers
- `job_categorisation`'s prompt lives in `prompts/job_categorisation/claude.v1.md`,
  loaded at runtime — `_PROMPT_TEMPLATE` no longer exists as an inline
  constant
- Every LLM call sends an explicit `temperature` (default `0.0`); Ollama
  calls also send `seed` when one is given
- Every `'llm'`-method row in `silver.job_category` carries the
  `prompt_version`/`model_id` that produced it
- A golden set under the minimum size reports `insufficient_data`, not
  a false pass
