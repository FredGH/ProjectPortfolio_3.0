# Step 12a — LLM Eval Harness and Prompt Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the generic eval-harness infrastructure (prompt registry, metrics, golden-set loading, per-provider run history, CLI) and wire it end-to-end for `job_categorisation` — the one LLM component that exists today — so every future LLM step (13, 15–17, 19, 20) can add its own golden set on top of working infrastructure instead of building the harness itself.

**Architecture:** A prompt registry replaces `llm_classifier.py`'s inline template. The LLM gateway gains optional provider/model overrides and fixed temperature/seed, so the same classification code path serves both production routing and eval-time provider selection. A golden-set loader dispatches per task — DB-backed (reusing JOB-170's human-reviewed `classification.category_review_labels`) for `job_categorisation`, file-based for future tasks. A new `evals.eval_runs` table persists one row per `(task, provider)` run for regression comparison, surfaced through a `run-evals` pipeline CLI subcommand.

**Tech Stack:** Python 3.11, SQLAlchemy + Alembic, FastAPI/Streamlit unaffected, `unittest` + `coverage`, real Postgres for integration tests (no DB mocking per project convention).

**Spec:** `docs/superpowers/specs/2026-09-12-step12a-eval-harness-design.md`

## Global Constraints

- Python 3.11, `black` (line length 88), `isort` (profile black), `ruff` (`select = ["E", "F", "UP"]`) — run on every touched file before each commit.
- Google-style docstrings with `Args`/`Returns`/`Raises` on every function, including private ones (`.claude/rules/python-style.md`).
- `unittest` + `coverage`, mirrored `tests/` tree, one test file per source module. **No mocking the database** — integration tests use a real Postgres connection (`postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search`), matching every existing `tests/integration/*.py` file in this repo.
- Every migration: `CREATE SCHEMA IF NOT EXISTS` where a new schema is introduced, explicit grants, and a working `downgrade()` — verify with an upgrade → downgrade → upgrade round-trip before committing.
- `docker compose up -d postgres` must be running for integration tests and for applying migrations locally (`alembic -c db/alembic.ini upgrade head` from `job_search/`, with `DATABASE_URL=postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search` exported).
- Anthropic-routed calls in tests are gated with `@unittest.skipUnless(get_settings().anthropic_api_key, ...)`, matching `test_classification_llm.py`'s existing pattern — never require a live key for a unit test.
- Never commit real secrets. `.env`'s `ANTHROPIC_API_KEY` is already present locally; nothing in this plan writes it anywhere new.
- Branch: `feat/JOB-182-eval-harness` (already created, already holds the committed design spec).

---

## Task 1: Prompt registry — loader + migrate `job_categorisation`'s inline prompt

**Files:**
- Create: `prompts/job_categorisation/claude.v1.md`
- Create: `packages/core/core/llm/prompts.py`
- Create: `packages/core/tests/test_llm_prompts.py`
- Modify: `packages/core/core/classification/llm_classifier.py`

**Interfaces:**
- Produces: `core.llm.prompts.load_prompt(task: str, model_family: str, version: int) -> str`

- [ ] **Step 1: Write the failing test**

`packages/core/tests/test_llm_prompts.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from core.llm.prompts import load_prompt


class TestLoadPrompt(unittest.TestCase):
    def test_loads_the_real_job_categorisation_claude_v1_prompt(self) -> None:
        text = load_prompt("job_categorisation", "claude", 1)
        self.assertIn("{categories}", text)
        self.assertIn("{title}", text)

    def test_raises_file_not_found_for_a_missing_prompt(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_prompt("does_not_exist", "claude", 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_prompts -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.llm.prompts'`

- [ ] **Step 3: Create the prompt registry file**

Copy `llm_classifier.py`'s current `_PROMPT_TEMPLATE` verbatim into
`prompts/job_categorisation/claude.v1.md` (repo root, i.e.
`job_search/prompts/job_categorisation/claude.v1.md`):

```
Classify this job title into exactly one of these categories: {categories}.

Job title: {title}

Respond with ONLY a JSON object, no other text: {{"category": "<one of the categories above>", "confidence": <float 0-1>}}
```

- [ ] **Step 4: Write the loader**

`packages/core/core/llm/prompts.py`:

```python
"""Prompt registry — versioned prompt files, keyed on (task, model_family).

Prompts are code: versioned files in the repo, never inline in Python
(DECISIONS.md §1). Never convert a prompt between families — write the
target-family variant when ready, keep both.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_ROOT = Path(__file__).resolve().parents[4] / "prompts"


def load_prompt(task: str, model_family: str, version: int) -> str:
    """Load one prompt registry file's raw text.

    Args:
        task: The task name, e.g. "job_categorisation".
        model_family: Which prompt variant family, e.g. "claude" or "local".
        version: The prompt version number.

    Returns:
        The prompt file's raw text, with `{placeholder}` tokens intact
        for the caller to `str.format(...)`.

    Raises:
        FileNotFoundError: If `prompts/<task>/<model_family>.v<version>.md`
            does not exist.
    """
    path = _PROMPTS_ROOT / task / f"{model_family}.v{version}.md"
    return path.read_text()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_prompts -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Update `llm_classifier.py` to load from the registry**

Replace the inline `_PROMPT_TEMPLATE` constant and `_PROMPT_VERSION` in
`packages/core/core/classification/llm_classifier.py` — remove:

```python
_PROMPT_VERSION = "job_categorisation-v1"
...
_PROMPT_TEMPLATE = (
    "Classify this job title into exactly one of these categories: "
    "{categories}.\n\n"
    "Job title: {title}\n\n"
    "Respond with ONLY a JSON object, no other text: "
    '{{"category": "<one of the categories above>", "confidence": <float 0-1>}}'
)
```

Replace with:

```python
from core.llm.prompts import load_prompt

_PROMPT_FAMILY = "claude"
_PROMPT_VERSION_NUMBER = 1
_PROMPT_VERSION = f"{_PROMPT_FAMILY}.v{_PROMPT_VERSION_NUMBER}"
```

And change `classify_by_llm`'s body from:

```python
    prompt = _PROMPT_TEMPLATE.format(categories=", ".join(_CATEGORIES), title=title)
```

to:

```python
    prompt_template = load_prompt(
        "job_categorisation", _PROMPT_FAMILY, _PROMPT_VERSION_NUMBER
    )
    prompt = prompt_template.format(categories=", ".join(_CATEGORIES), title=title)
```

Leave the rest of `classify_by_llm` (the `complete(...)` call, JSON
parsing, fallback) unchanged for this task — Task 8 extends its
signature.

- [ ] **Step 7: Run the full classification test suite to verify nothing broke**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_classification_classify tests.test_classification_rules discover -s tests -p "test_classification*" -v`
Expected: PASS, `prompt_version` still resolves to `"claude.v1"` (same
string as before, just now computed rather than hardcoded)

- [ ] **Step 8: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/llm/prompts.py packages/core/core/classification/llm_classifier.py packages/core/tests/test_llm_prompts.py && isort packages/core/core/llm/prompts.py packages/core/core/classification/llm_classifier.py packages/core/tests/test_llm_prompts.py && ruff check packages/core/core/llm/prompts.py packages/core/core/classification/llm_classifier.py packages/core/tests/test_llm_prompts.py`
Expected: clean

- [ ] **Step 9: Commit**

```bash
git add prompts/job_categorisation/claude.v1.md packages/core/core/llm/prompts.py packages/core/tests/test_llm_prompts.py packages/core/core/classification/llm_classifier.py
git commit -m "feat(job_search): add prompt registry, migrate job_categorisation's inline prompt (JOB-188, JOB-190)"
```

---

## Task 2: Metrics library — `exact_match` and `field_f1`

**Files:**
- Create: `packages/core/core/evals/__init__.py`
- Create: `packages/core/core/evals/metrics.py`
- Create: `packages/core/tests/test_evals_metrics.py`

**Interfaces:**
- Produces: `core.evals.metrics.exact_match(predicted: dict, expected: dict) -> float`
- Produces: `core.evals.metrics.field_f1(predicted: dict, expected: dict) -> float`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_evals_metrics.py`:

```python
from __future__ import annotations

import unittest

from core.evals.metrics import exact_match, field_f1


class TestExactMatch(unittest.TestCase):
    def test_identical_dicts_score_one(self) -> None:
        self.assertEqual(exact_match({"category": "data_engineer"}, {"category": "data_engineer"}), 1.0)

    def test_differing_dicts_score_zero(self) -> None:
        self.assertEqual(exact_match({"category": "data_engineer"}, {"category": "other"}), 0.0)

    def test_extra_predicted_keys_are_ignored(self) -> None:
        # Only the fields present in `expected` are compared — a
        # predictor returning extra metadata fields must not be
        # penalised for it.
        predicted = {"category": "data_engineer", "confidence": 0.9}
        expected = {"category": "data_engineer"}
        self.assertEqual(exact_match(predicted, expected), 1.0)

    def test_missing_expected_key_scores_zero(self) -> None:
        self.assertEqual(exact_match({}, {"category": "data_engineer"}), 0.0)


class TestFieldF1(unittest.TestCase):
    def test_all_fields_match_scores_one(self) -> None:
        predicted = {"skill": "python", "years": 5}
        expected = {"skill": "python", "years": 5}
        self.assertEqual(field_f1(predicted, expected), 1.0)

    def test_no_fields_match_scores_zero(self) -> None:
        predicted = {"skill": "java", "years": 2}
        expected = {"skill": "python", "years": 5}
        self.assertEqual(field_f1(predicted, expected), 0.0)

    def test_partial_match_scores_between_zero_and_one(self) -> None:
        # 1 of 2 fields correct, no extra predicted fields:
        # precision = 1/2, recall = 1/2, f1 = 0.5
        predicted = {"skill": "python", "years": 2}
        expected = {"skill": "python", "years": 5}
        self.assertAlmostEqual(field_f1(predicted, expected), 0.5)

    def test_extra_predicted_fields_reduce_precision(self) -> None:
        # 1 correct field out of 2 predicted, 1 expected field total:
        # precision = 1/2, recall = 1/1, f1 = 2*(0.5*1)/(0.5+1) = 0.667
        predicted = {"skill": "python", "spurious": "x"}
        expected = {"skill": "python"}
        self.assertAlmostEqual(field_f1(predicted, expected), 0.6666666666666666)

    def test_both_empty_scores_one(self) -> None:
        # No fields expected, none predicted — trivially correct rather
        # than a division-by-zero.
        self.assertEqual(field_f1({}, {}), 1.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_evals_metrics -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.evals'`

- [ ] **Step 3: Create the package and metrics module**

`packages/core/core/evals/__init__.py`: empty file.

`packages/core/core/evals/metrics.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_evals_metrics -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py && isort packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py && ruff check packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/evals/__init__.py packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py
git commit -m "feat(job_search): add exact_match and field_f1 eval metrics (JOB-185)"
```

---

## Task 3: `LLMAdapter` gains fixed temperature/seed, both real adapters updated, every fake adapter in the test suite updated to match

**Files:**
- Modify: `packages/core/core/llm/types.py`
- Modify: `packages/core/core/llm/adapters/anthropic.py`
- Modify: `packages/core/core/llm/adapters/ollama.py`
- Modify: `packages/core/tests/test_llm_adapters.py`
- Modify: `packages/core/tests/test_llm_gateway.py`
- Modify: `packages/core/tests/test_classification_classify.py`
- Modify: `packages/core/tests/integration/test_classification_llm.py`
- Modify: `packages/core/tests/test_api_ingest.py`
- Modify: `packages/core/tests/test_extraction.py`

**Interfaces:**
- Produces: `LLMAdapter.complete(self, *, model: str, prompt: str, temperature: float = 0.0, seed: int | None = None) -> LLMResponse`

- [ ] **Step 1: Update the Protocol**

In `packages/core/core/llm/types.py`, change:

```python
class LLMAdapter(Protocol):
    """The interface every provider adapter implements identically."""

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        ...
```

to:

```python
class LLMAdapter(Protocol):
    """The interface every provider adapter implements identically."""

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Run one completion call.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.
            temperature: Sampling temperature. Defaults to 0.0 — the eval
                harness (PLAN.md Step 12a) depends on every call being as
                deterministic as the provider allows, so 0.0 is the
                default for every caller, not an eval-only opt-in.
            seed: A fixed seed, where the provider supports one. `None`
                means "no seed requested."

        Returns:
            The normalised `LLMResponse`.
        """
        ...
```

- [ ] **Step 2: Write the failing adapter tests**

In `packages/core/tests/test_llm_adapters.py`, add two new test
methods (keep the existing two tests unchanged):

```python
class TestOllamaAdapter(unittest.TestCase):
    """Test Ollama adapter request/response parsing."""

    def test_complete_parses_ollama_response_shape(self) -> None:
        """Verify Ollama adapter parses response shape correctly."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/generate")
            return httpx.Response(
                200,
                json={
                    "response": "hello from ollama",
                    "prompt_eval_count": 12,
                    "eval_count": 7,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)

        result = adapter.complete(model="llama3.1:8b", prompt="say hello")

        self.assertEqual(result.text, "hello from ollama")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "llama3.1:8b")
        self.assertEqual(result.input_tokens, 12)
        self.assertEqual(result.output_tokens, 7)

    def test_complete_sends_temperature_and_seed_in_options(self) -> None:
        captured_json: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_json
            captured_json = json.loads(request.content)
            return httpx.Response(
                200, json={"response": "ok", "prompt_eval_count": 1, "eval_count": 1}
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)

        adapter.complete(model="llama3.1:8b", prompt="hi", temperature=0.2, seed=42)

        self.assertEqual(captured_json["options"]["temperature"], 0.2)
        self.assertEqual(captured_json["options"]["seed"], 42)

    def test_complete_without_seed_omits_it_from_options(self) -> None:
        captured_json: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_json
            captured_json = json.loads(request.content)
            return httpx.Response(
                200, json={"response": "ok", "prompt_eval_count": 1, "eval_count": 1}
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)

        adapter.complete(model="llama3.1:8b", prompt="hi")

        self.assertNotIn("seed", captured_json["options"])
        self.assertEqual(captured_json["options"]["temperature"], 0.0)
```

Add `import json` to the top of `test_llm_adapters.py`.

For `TestAnthropicAdapter`, add:

```python
    def test_complete_sends_temperature_but_not_seed(self) -> None:
        fake_message = mock.Mock()
        fake_message.content = [mock.Mock(text="hello from claude")]
        fake_message.usage = mock.Mock(input_tokens=20, output_tokens=9)

        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_message

        adapter = AnthropicAdapter(api_key="test-key", client=fake_client)
        adapter.complete(model="claude-sonnet-5", prompt="say hello", temperature=0.5, seed=42)

        fake_client.messages.create.assert_called_once_with(
            model="claude-sonnet-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": "say hello"}],
            temperature=0.5,
        )
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_adapters -v`
Expected: FAIL — `TypeError: complete() got an unexpected keyword argument 'temperature'`

- [ ] **Step 4: Update `OllamaAdapter`**

In `packages/core/core/llm/adapters/ollama.py`, change:

```python
    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call against Ollama.

        Args:
            model: The Ollama model tag, e.g. "llama3.1:8b".
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        response = self.client.post(
            f"{self.base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
```

to:

```python
    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Run one completion call against Ollama.

        Args:
            model: The Ollama model tag, e.g. "llama3.1:8b".
            prompt: The prompt text.
            temperature: Sampling temperature, sent as `options.temperature`.
            seed: A fixed seed, sent as `options.seed` when given — Ollama
                supports true seeded determinism, unlike Anthropic.

        Returns:
            The normalised `LLMResponse`.
        """
        options: dict[str, object] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        response = self.client.post(
            f"{self.base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": options,
            },
        )
```

- [ ] **Step 5: Update `AnthropicAdapter`**

In `packages/core/core/llm/adapters/anthropic.py`, change:

```python
    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call against Claude.

        Args:
            model: The Anthropic model identifier, e.g. "claude-sonnet-5".
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        message = self.client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
```

to:

```python
    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Run one completion call against Claude.

        Args:
            model: The Anthropic model identifier, e.g. "claude-sonnet-5".
            prompt: The prompt text.
            temperature: Sampling temperature, passed straight through.
            seed: Ignored — the Anthropic Messages API has no seed
                parameter, and Anthropic does not guarantee bit-for-bit
                reproducibility even at `temperature=0`. Accepted (not
                rejected) so this adapter satisfies the same `LLMAdapter`
                Protocol as `OllamaAdapter`, which does support it.

        Returns:
            The normalised `LLMResponse`.
        """
        message = self.client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
```

- [ ] **Step 6: Run adapter tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_adapters -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Update every fake/broken adapter elsewhere in the test suite**

The Protocol change means every hand-written fake adapter must accept
(and may ignore) the two new keyword arguments, or callers that already
pass them (none yet — this task only touches adapters, Task 4 threads
overrides through the gateway) will break. Update these four
`def complete(self, *, model: str, prompt: str)` definitions to
`def complete(self, *, model: str, prompt: str, temperature: float = 0.0, seed: int | None = None)`,
changing only the signature line, nothing in each function's body:

- `packages/core/tests/test_classification_classify.py` — `_FakeAdapter.complete`
- `packages/core/tests/integration/test_classification_llm.py` — the
  inline `_BrokenAdapter.complete` inside
  `test_malformed_response_falls_back_to_other_rather_than_raising`
- `packages/core/tests/test_api_ingest.py` — locate its fake adapter
  with `grep -n "def complete" packages/core/tests/test_api_ingest.py`
  and apply the same signature change
- `packages/core/tests/test_extraction.py` — same, locate with
  `grep -n "def complete" packages/core/tests/test_extraction.py`

(`test_llm_gateway.py`'s `_FakeAdapter` is updated in Task 4, since that
task's tests also need it to record the new kwargs.)

- [ ] **Step 8: Run the full test suite to verify nothing else broke**

Run: `cd job_search && source venv/bin/activate && set -a && source .env && set +a && cd packages/core && python3.11 -m unittest discover -s tests`
Expected: same baseline as before this task (2 pre-existing unrelated
errors in `test_api_ingest.py`'s `/data` read-only-filesystem issue, 14
environment-gated skips) — no new failures

- [ ] **Step 9: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/llm/types.py packages/core/core/llm/adapters/anthropic.py packages/core/core/llm/adapters/ollama.py packages/core/tests/test_llm_adapters.py packages/core/tests/test_classification_classify.py packages/core/tests/integration/test_classification_llm.py packages/core/tests/test_api_ingest.py packages/core/tests/test_extraction.py && isort <same files> && ruff check <same files>`
Expected: clean

- [ ] **Step 10: Commit**

```bash
git add packages/core/core/llm/types.py packages/core/core/llm/adapters/anthropic.py packages/core/core/llm/adapters/ollama.py packages/core/tests/test_llm_adapters.py packages/core/tests/test_classification_classify.py packages/core/tests/integration/test_classification_llm.py packages/core/tests/test_api_ingest.py packages/core/tests/test_extraction.py
git commit -m "feat(job_search): fixed temperature/seed on every LLMAdapter (JOB-199)"
```

---

## Task 4: Gateway gains provider/model override and temperature/seed passthrough

**Files:**
- Modify: `packages/core/core/llm/gateway.py`
- Modify: `packages/core/tests/test_llm_gateway.py`

**Interfaces:**
- Consumes: `LLMAdapter.complete(..., temperature: float = 0.0, seed: int | None = None)` (Task 3)
- Produces: `core.llm.gateway.complete(task, prompt, *, prompt_version, adapters, config_path=None, provider=None, model=None, temperature=0.0, seed=None) -> LLMResponse`

- [ ] **Step 1: Write the failing tests**

In `packages/core/tests/test_llm_gateway.py`, update the fake adapter
and add new test methods:

```python
class _FakeAdapter:
    """Fake adapter for testing gateway routing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int | None]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record call and return fake response."""
        self.calls.append((model, prompt, temperature, seed))
        return LLMResponse(
            text=f"echo: {prompt}",
            provider="fake",
            model=model,
            input_tokens=3,
            output_tokens=5,
        )
```

Add to `TestGatewayComplete`:

```python
    def test_defaults_to_zero_temperature_and_no_seed(self) -> None:
        complete(
            "skill_extraction",
            "prompt",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter},
            config_path=self.config_path,
        )
        self.assertEqual(self.fake_adapter.calls, [("fake-model-v1", "prompt", 0.0, None)])

    def test_passes_through_a_given_temperature_and_seed(self) -> None:
        complete(
            "skill_extraction",
            "prompt",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter},
            config_path=self.config_path,
            temperature=0.7,
            seed=42,
        )
        self.assertEqual(self.fake_adapter.calls, [("fake-model-v1", "prompt", 0.7, 42)])

    def test_provider_and_model_override_bypass_task_config_resolution(self) -> None:
        # skill_extraction's config_path entry routes to "fake" — override
        # to a different adapter/model entirely, proving the override
        # takes precedence over what the task's own config says.
        other_adapter = _FakeAdapter()
        complete(
            "skill_extraction",
            "prompt",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter, "other": other_adapter},
            config_path=self.config_path,
            provider="other",
            model="other-model-v9",
        )
        self.assertEqual(self.fake_adapter.calls, [])
        self.assertEqual(
            other_adapter.calls, [("other-model-v9", "prompt", 0.0, None)]
        )
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_gateway -v`
Expected: FAIL — `complete() got an unexpected keyword argument 'temperature'`

- [ ] **Step 3: Update `gateway.complete`**

In `packages/core/core/llm/gateway.py`, change:

```python
def complete(
    task: str,
    prompt: str,
    *,
    prompt_version: str,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
) -> LLMResponse:
    """Run a completion for `task`, routed to its configured provider.

    Args:
        task: The task name, resolved against `config/llm_tasks.yml`.
        prompt: The prompt text to send.
        prompt_version: The versioned prompt identifier that produced
            `prompt` — stamped onto the call log and, later, onto every
            generated artefact row.
        adapters: Every available adapter, keyed by provider name (e.g.
            `{"ollama": OllamaAdapter(...), "anthropic": AnthropicAdapter(...)}`).
            Callers construct and inject these explicitly rather than the
            gateway constructing them, so tests never need real credentials
            or network access.
        config_path: Path to the task-config YAML. Defaults to
            `config/llm_tasks.yml` at the repository root.

    Returns:
        The adapter's `LLMResponse`.

    Raises:
        core.llm.task_config.TaskConfigError: If `task` has no entry in the
            task config file.
        KeyError: If the resolved provider has no matching entry in
            `adapters`.
    """
    task_config = load_task_config(task, config_path=config_path)
    adapter = adapters[task_config.provider]
    response = adapter.complete(model=task_config.model, prompt=prompt)
    log_llm_call(task=task, response=response, prompt_version=prompt_version)
    return response
```

to:

```python
def complete(
    task: str,
    prompt: str,
    *,
    prompt_version: str,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    seed: int | None = None,
) -> LLMResponse:
    """Run a completion for `task`, routed to its configured provider.

    Args:
        task: The task name, resolved against `config/llm_tasks.yml`.
        prompt: The prompt text to send.
        prompt_version: The versioned prompt identifier that produced
            `prompt` — stamped onto the call log and, later, onto every
            generated artefact row.
        adapters: Every available adapter, keyed by provider name (e.g.
            `{"ollama": OllamaAdapter(...), "anthropic": AnthropicAdapter(...)}`).
            Callers construct and inject these explicitly rather than the
            gateway constructing them, so tests never need real credentials
            or network access.
        config_path: Path to the task-config YAML. Defaults to
            `config/llm_tasks.yml` at the repository root.
        provider: Overrides the task's configured provider. Used by the
            eval harness (PLAN.md Step 12a) to force a specific provider
            for an eval run, independent of what the task routes to in
            production. `None` (the default) resolves from
            `config/llm_tasks.yml` as before — every existing caller is
            unaffected.
        model: Overrides the task's configured model. Must be given
            together with `provider` — an override that supplies one
            without the other resolves the missing one from task config,
            which is almost never what an eval-time caller wants.
        temperature: Sampling temperature, passed straight through to the
            adapter. Defaults to 0.0 for maximum reproducibility.
        seed: A fixed seed, passed straight through — honoured by Ollama,
            ignored by Anthropic (see `AnthropicAdapter.complete`).

    Returns:
        The adapter's `LLMResponse`.

    Raises:
        core.llm.task_config.TaskConfigError: If `task` has no entry in the
            task config file and no full override is given.
        KeyError: If the resolved provider has no matching entry in
            `adapters`.
    """
    if provider is None or model is None:
        task_config = load_task_config(task, config_path=config_path)
        provider = provider or task_config.provider
        model = model or task_config.model
    adapter = adapters[provider]
    response = adapter.complete(
        model=model, prompt=prompt, temperature=temperature, seed=seed
    )
    log_llm_call(task=task, response=response, prompt_version=prompt_version)
    return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_gateway -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full classification + gateway test set to verify no regressions**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_gateway tests.test_llm_adapters tests.test_classification_classify -v`
Expected: PASS, all green

- [ ] **Step 6: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/llm/gateway.py packages/core/tests/test_llm_gateway.py && isort packages/core/core/llm/gateway.py packages/core/tests/test_llm_gateway.py && ruff check packages/core/core/llm/gateway.py packages/core/tests/test_llm_gateway.py`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/llm/gateway.py packages/core/tests/test_llm_gateway.py
git commit -m "feat(job_search): gateway.complete gains provider/model override + temperature/seed (JOB-191, JOB-199)"
```

---

## Task 5: `llm_judge` metric

**Files:**
- Modify: `packages/core/core/evals/metrics.py`
- Modify: `packages/core/tests/test_evals_metrics.py`
- Modify: `config/llm_tasks.yml`

**Interfaces:**
- Consumes: `core.llm.gateway.complete` (existing), `core.llm.types.LLMAdapter` (existing)
- Produces: `core.evals.metrics.JudgeResult` (dataclass: `score: float`, `rationale: str`)
- Produces: `core.evals.metrics.llm_judge(output: str, rubric: str, *, adapters: dict[str, LLMAdapter]) -> JudgeResult`

- [ ] **Step 1: Add the `eval_judge` task to `config/llm_tasks.yml`**

Append to the `tasks:` map in `config/llm_tasks.yml`:

```yaml
  eval_judge:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
```

(Routed to Anthropic, not Ollama — a judge validated on a weaker model
than what it's judging produces false confidence, the same reasoning
DECISIONS.md §1 applies to the fabrication critic.)

- [ ] **Step 2: Write the failing test**

Append to `packages/core/tests/test_evals_metrics.py`:

```python
from core.evals.metrics import JudgeResult, llm_judge
from core.llm.types import LLMResponse


class _FakeJudgeAdapter:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[str] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        self.calls.append(prompt)
        return LLMResponse(
            text=self._response_text,
            provider="anthropic",
            model=model,
            input_tokens=10,
            output_tokens=10,
        )


class TestLlmJudge(unittest.TestCase):
    def test_parses_a_well_formed_judge_response(self) -> None:
        adapter = _FakeJudgeAdapter(
            '{"score": 0.8, "rationale": "mostly accurate, minor omission"}'
        )
        result = llm_judge(
            "The candidate has 5 years of Python.",
            "Score 0-1 on factual grounding against the source CV.",
            adapters={"anthropic": adapter},
        )
        self.assertIsInstance(result, JudgeResult)
        self.assertEqual(result.score, 0.8)
        self.assertEqual(result.rationale, "mostly accurate, minor omission")
        self.assertEqual(len(adapter.calls), 1)
        self.assertIn("Score 0-1 on factual grounding", adapter.calls[0])
        self.assertIn("The candidate has 5 years of Python.", adapter.calls[0])

    def test_malformed_response_returns_zero_score_not_a_raised_exception(self) -> None:
        adapter = _FakeJudgeAdapter("not json at all")
        result = llm_judge("output", "rubric", adapters={"anthropic": adapter})
        self.assertEqual(result.score, 0.0)
        self.assertIn("could not parse", result.rationale.lower())
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_evals_metrics -v`
Expected: FAIL — `ImportError: cannot import name 'JudgeResult'`

- [ ] **Step 4: Implement `llm_judge`**

Append to `packages/core/core/evals/metrics.py` (add `import json` and
the gateway import at the top alongside the existing imports):

```python
import json
from dataclasses import dataclass

from core.llm.gateway import complete
from core.llm.types import LLMAdapter

_JUDGE_PROMPT_TEMPLATE = (
    "You are grading one piece of output against a rubric.\n\n"
    "Rubric: {rubric}\n\n"
    "Output to grade:\n{output}\n\n"
    "Respond with ONLY a JSON object, no other text: "
    '{{"score": <float 0-1>, "rationale": "<one sentence>"}}'
)


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
    """
    prompt = _JUDGE_PROMPT_TEMPLATE.format(rubric=rubric, output=output)
    response = complete(
        task="eval_judge",
        prompt=prompt,
        prompt_version="inline-v1",
        adapters=adapters,
    )
    try:
        parsed = json.loads(response.text.strip())
        return JudgeResult(score=float(parsed["score"]), rationale=parsed["rationale"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        return JudgeResult(score=0.0, rationale=f"could not parse judge response: {exc}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_evals_metrics -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py config/llm_tasks.yml 2>/dev/null; black packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py && isort packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py && ruff check packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py`
Expected: clean (black/isort/ruff don't apply to YAML — that line is a no-op for `llm_tasks.yml`, only the two `.py` files matter)

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/evals/metrics.py packages/core/tests/test_evals_metrics.py config/llm_tasks.yml
git commit -m "feat(job_search): add llm_judge eval metric + eval_judge task config (JOB-185)"
```

---

## Task 6: `TaskConfig` gains eval fields; `config/llm_tasks.yml` updated for `job_categorisation`

**Files:**
- Modify: `packages/core/core/llm/task_config.py`
- Modify: `packages/core/tests/test_task_config.py`
- Modify: `config/llm_tasks.yml`

**Interfaces:**
- Produces: `TaskConfig` gains `eval_metric: str | None`, `eval_regression_threshold: float | None`, `local_provider: str | None`, `local_model: str | None`, `local_prompt_family: str | None` (all default `None`)

- [ ] **Step 1: Write the failing tests**

In `packages/core/tests/test_task_config.py`, extend `_SAMPLE_YAML` and
add a new test method:

```python
_SAMPLE_YAML = """
tasks:
  skill_extraction:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
  fabrication_critic:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
  job_categorisation:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
    eval_metric: exact_match
    eval_regression_threshold: 0.05
"""
```

Add:

```python
    def test_resolves_eval_fields_when_present(self) -> None:
        config = load_task_config("job_categorisation", config_path=self.config_path)
        self.assertEqual(config.eval_metric, "exact_match")
        self.assertEqual(config.eval_regression_threshold, 0.05)
        self.assertIsNone(config.local_provider)
        self.assertIsNone(config.local_model)
        self.assertIsNone(config.local_prompt_family)

    def test_eval_fields_default_to_none_when_absent(self) -> None:
        config = load_task_config("skill_extraction", config_path=self.config_path)
        self.assertIsNone(config.eval_metric)
        self.assertIsNone(config.eval_regression_threshold)
```

Also update `test_resolves_a_known_task` — it constructs a `TaskConfig`
directly for equality comparison, which still works unchanged as long
as the new fields default identically on both sides:

```python
    def test_resolves_a_known_task(self) -> None:
        """Test loading a known task configuration."""
        config = load_task_config("skill_extraction", config_path=self.config_path)
        self.assertEqual(
            config,
            TaskConfig(
                task="skill_extraction",
                provider="ollama",
                model="llama3.1:8b",
                prompt_family="local",
            ),
        )
```
(No change needed to this test's body — leaving it here to confirm it
still passes once `TaskConfig`'s new fields default to `None`.)

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_task_config -v`
Expected: FAIL — `AttributeError: 'TaskConfig' object has no attribute 'eval_metric'`

- [ ] **Step 3: Update `TaskConfig` and `load_task_config`**

In `packages/core/core/llm/task_config.py`, change:

```python
@dataclass(frozen=True)
class TaskConfig:
    """Resolved provider/model configuration for one LLM task.

    Attributes:
        task: The task name, e.g. "skill_extraction".
        provider: Which adapter serves this task.
        model: The provider-specific model identifier.
        prompt_family: Which prompt variant family to load — prompts are
            versioned per (task, model_family) and never converted between
            families (DECISIONS.md §1).
    """

    task: str
    provider: Literal["ollama", "anthropic"]
    model: str
    prompt_family: str
```

to:

```python
@dataclass(frozen=True)
class TaskConfig:
    """Resolved provider/model configuration for one LLM task.

    Attributes:
        task: The task name, e.g. "skill_extraction".
        provider: Which adapter serves this task.
        model: The provider-specific model identifier.
        prompt_family: Which prompt variant family to load — prompts are
            versioned per (task, model_family) and never converted between
            families (DECISIONS.md §1).
        eval_metric: Which `core.evals.metrics` function grades this
            task's output, e.g. "exact_match". `None` if this task has
            no eval configured yet.
        eval_regression_threshold: How far a re-run's score may drop
            below the prior run before `run-evals` reports a regression.
            `None` if unconfigured.
        local_provider: The provider to use when the eval harness is
            asked to run this task's golden set against "local" instead
            of its production-configured provider. `None` if no local
            variant is configured for this task (PLAN.md Step 12a: not
            every task has a tuned local prompt).
        local_model: The model to use with `local_provider`.
        local_prompt_family: The prompt family to load with
            `local_provider`.
    """

    task: str
    provider: Literal["ollama", "anthropic"]
    model: str
    prompt_family: str
    eval_metric: str | None = None
    eval_regression_threshold: float | None = None
    local_provider: str | None = None
    local_model: str | None = None
    local_prompt_family: str | None = None
```

And change `load_task_config`'s return statement from:

```python
    entry = tasks[task]
    return TaskConfig(
        task=task,
        provider=entry["provider"],
        model=entry["model"],
        prompt_family=entry["prompt_family"],
    )
```

to:

```python
    entry = tasks[task]
    return TaskConfig(
        task=task,
        provider=entry["provider"],
        model=entry["model"],
        prompt_family=entry["prompt_family"],
        eval_metric=entry.get("eval_metric"),
        eval_regression_threshold=entry.get("eval_regression_threshold"),
        local_provider=entry.get("local_provider"),
        local_model=entry.get("local_model"),
        local_prompt_family=entry.get("local_prompt_family"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_task_config -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Update `config/llm_tasks.yml`'s real `job_categorisation` entry**

Change the existing entry:

```yaml
  job_categorisation:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
```

to:

```yaml
  job_categorisation:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
    eval_metric: exact_match
    eval_regression_threshold: 0.05
```

(No `local_*` fields — `job_categorisation` has no tuned local prompt
variant yet; `prompts/job_categorisation/local.v1.md` does not exist.
`run-evals --provider local` for this task will report
`provider_not_configured`, built in Task 9.)

- [ ] **Step 6: Run the full task_config + gateway test set**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_task_config tests.test_llm_gateway -v`
Expected: PASS, all green

- [ ] **Step 7: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/llm/task_config.py packages/core/tests/test_task_config.py && isort packages/core/core/llm/task_config.py packages/core/tests/test_task_config.py && ruff check packages/core/core/llm/task_config.py packages/core/tests/test_task_config.py`
Expected: clean

- [ ] **Step 8: Commit**

```bash
git add packages/core/core/llm/task_config.py packages/core/tests/test_task_config.py config/llm_tasks.yml
git commit -m "feat(job_search): TaskConfig gains eval_metric/eval_regression_threshold/local_* fields (JOB-194)"
```

---

## Task 7: Migration — `prompt_version`/`model_id` on `silver.job_category`

**Files:**
- Create: `db/migrations/versions/0015_add_prompt_version_to_job_category.py`

**Interfaces:**
- Produces: `silver.job_category` gains nullable `prompt_version TEXT`, `model_id TEXT` columns

- [ ] **Step 1: Write the migration**

`db/migrations/versions/0015_add_prompt_version_to_job_category.py`:

```python
"""add prompt_version/model_id to silver.job_category

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-12

Stamps every LLM-produced row in silver.job_category with the prompt
version and model that produced it (PLAN.md Step 12a, JOB-196:
"stamp prompt_version and model_id on every generated artefact row").
silver.job_category is the only table an LLM call actually produces
end-to-end today — every future artefact table (Steps 17, 19, 20) does
the same when it lands.

Both columns are nullable: a 'rules'- or 'embedding'-method row never
called an LLM, so both stay NULL for those rows by design, not by
omission.

No new grants needed — job_search_app already inherits SELECT on every
silver table via migration 0010's `ALTER DEFAULT PRIVILEGES FOR ROLE
job_search_owner IN SCHEMA silver` rule, and nothing outside the owner
role writes to job_category (see 0013's docstring).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_category",
        sa.Column("prompt_version", sa.Text(), nullable=True),
        schema="silver",
    )
    op.add_column(
        "job_category",
        sa.Column("model_id", sa.Text(), nullable=True),
        schema="silver",
    )


def downgrade() -> None:
    op.drop_column("job_category", "model_id", schema="silver")
    op.drop_column("job_category", "prompt_version", schema="silver")
```

- [ ] **Step 2: Apply and verify the round-trip**

Run (from `job_search/`, with Postgres up via `docker compose up -d postgres`):

```bash
export DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
alembic -c db/alembic.ini upgrade head
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "\d silver.job_category"
alembic -c db/alembic.ini downgrade -1
alembic -c db/alembic.ini upgrade head
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "\d silver.job_category"
```

Expected: `\d silver.job_category` shows `prompt_version` and
`model_id` both present after each `upgrade head`, both nullable, no
errors on downgrade or re-upgrade.

- [ ] **Step 3: Commit**

```bash
git add db/migrations/versions/0015_add_prompt_version_to_job_category.py
git commit -m "feat(job_search): add prompt_version/model_id to silver.job_category (JOB-196)"
```

---

## Task 8: `classify_by_llm` gains provider/model/prompt_family override + returns prompt_version/model_id; `Classification` carries them through

**Files:**
- Modify: `packages/core/core/classification/llm_classifier.py`
- Modify: `packages/core/core/classification/classify.py`
- Modify: `packages/core/tests/test_classification_llm.py` *(unit test — new file, distinct from the existing integration test of the same area)*
- Modify: `packages/core/tests/test_classification_classify.py`
- Modify: `packages/core/tests/integration/test_classification_llm.py`

**Interfaces:**
- Consumes: `core.llm.prompts.load_prompt` (Task 1), `core.llm.gateway.complete`'s `provider`/`model` override (Task 4)
- Produces: `classify_by_llm(title, *, adapters, provider=None, model=None, prompt_family=None) -> tuple[str, float, str | None, str | None]` (category, confidence, prompt_version, model_id)
- Produces: `Classification` gains `prompt_version: str | None = None`, `model_id: str | None = None`

- [ ] **Step 1: Write the failing unit test**

Create `packages/core/tests/test_llm_classifier.py` (unit-level, fake
adapter only — distinct from
`tests/integration/test_classification_llm.py`, which hits the real
Anthropic API when a key is configured):

```python
from __future__ import annotations

import unittest

from core.classification.llm_classifier import classify_by_llm
from core.llm.types import LLMResponse


class _FakeAdapter:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        self.calls.append((model, prompt))
        return LLMResponse(
            text=self._response_text,
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestClassifyByLlm(unittest.TestCase):
    def test_returns_prompt_version_and_model_id_alongside_category(self) -> None:
        adapter = _FakeAdapter('{"category": "data_engineer", "confidence": 0.7}')
        category, confidence, prompt_version, model_id = classify_by_llm(
            "Some Title", adapters={"anthropic": adapter}
        )
        self.assertEqual(category, "data_engineer")
        self.assertEqual(confidence, 0.7)
        self.assertEqual(prompt_version, "claude.v1")
        self.assertEqual(model_id, "claude-sonnet-5")

    def test_provider_and_model_override_route_to_the_overridden_adapter(self) -> None:
        overridden_adapter = _FakeAdapter(
            '{"category": "software_engineer", "confidence": 0.6}'
        )
        default_adapter = _FakeAdapter('{"category": "other", "confidence": 0.1}')
        category, _, prompt_version, model_id = classify_by_llm(
            "Some Title",
            adapters={"anthropic": default_adapter, "ollama": overridden_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="claude",
        )
        self.assertEqual(category, "software_engineer")
        self.assertEqual(model_id, "llama3.1:8b")
        self.assertEqual(prompt_version, "claude.v1")
        self.assertEqual(default_adapter.calls, [])
        self.assertEqual(len(overridden_adapter.calls), 1)

    def test_malformed_response_falls_back_to_other_with_no_prompt_version(self) -> None:
        adapter = _FakeAdapter("not json")
        category, confidence, prompt_version, model_id = classify_by_llm(
            "Some Title", adapters={"anthropic": adapter}
        )
        self.assertEqual(category, "other")
        self.assertEqual(confidence, 0.0)
        self.assertIsNone(prompt_version)
        self.assertIsNone(model_id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_classifier -v`
Expected: FAIL — `ValueError: too many values to unpack` (current
return is a 2-tuple)

- [ ] **Step 3: Update `classify_by_llm`**

In `packages/core/core/classification/llm_classifier.py`, change the
full function to:

```python
def classify_by_llm(
    title: str,
    *,
    adapters: dict[str, LLMAdapter],
    provider: str | None = None,
    model: str | None = None,
    prompt_family: str | None = None,
) -> tuple[str, float, str | None, str | None]:
    """Classify one title via the LLM gateway's job_categorisation task.

    Args:
        title: The job title to classify.
        adapters: Every available LLM adapter, keyed by provider —
            passed straight through to core.llm.gateway.complete.
        provider: Overrides the production-configured provider — used
            by the eval harness (PLAN.md Step 12a) to force a specific
            provider regardless of what `config/llm_tasks.yml` routes
            `job_categorisation` to. `None` (the default) uses
            production routing.
        model: The model to use with `provider`. Must be given together
            with `provider`.
        prompt_family: Which prompt file to load — defaults to this
            module's own `_PROMPT_FAMILY` ("claude") when `provider` is
            given without an explicit `prompt_family`, so an eval run
            forcing a different provider still needs to say which
            prompt variant that provider should use.

    Returns:
        `(category, confidence, prompt_version, model_id)`. Falls back
        to `("other", 0.0, None, None)` if the response can't be parsed
        as the expected JSON shape or names a category outside the
        taxonomy — one malformed response should not fail the whole
        classification batch, and a fallback carries no meaningful
        prompt_version/model_id since nothing was successfully
        classified.
    """
    resolved_family = prompt_family or _PROMPT_FAMILY
    prompt_template = load_prompt(
        "job_categorisation", resolved_family, _PROMPT_VERSION_NUMBER
    )
    prompt = prompt_template.format(categories=", ".join(_CATEGORIES), title=title)
    prompt_version = f"{resolved_family}.v{_PROMPT_VERSION_NUMBER}"
    response = complete(
        task="job_categorisation",
        prompt=prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        provider=provider,
        model=model,
    )
    try:
        parsed = json.loads(response.text.strip())
        category = parsed["category"]
        confidence = float(parsed["confidence"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return "other", 0.0, None, None
    if category not in _CATEGORIES:
        return "other", 0.0, None, None
    return category, confidence, prompt_version, response.model
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_classifier -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Update the existing integration test's call sites**

`packages/core/tests/integration/test_classification_llm.py` calls
`classify_by_llm(...)` expecting a 2-tuple — update both call sites to
unpack 4 values:

```python
    def test_classifies_an_unambiguous_residual_title(self) -> None:
        category, confidence, prompt_version, model_id = classify_by_llm(
            "Chief Ethics Officer", adapters=self.adapters
        )
        self.assertEqual(category, "other")
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)
        self.assertEqual(prompt_version, "claude.v1")
        self.assertIsNotNone(model_id)

    def test_malformed_response_falls_back_to_other_rather_than_raising(self) -> None:
        class _BrokenAdapter:
            def complete(
                self,
                *,
                model: str,
                prompt: str,
                temperature: float = 0.0,
                seed: int | None = None,
            ):
                from core.llm.types import LLMResponse

                return LLMResponse(
                    text="not json at all",
                    provider="anthropic",
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                )

        category, confidence, prompt_version, model_id = classify_by_llm(
            "Some Title", adapters={"anthropic": _BrokenAdapter()}
        )
        self.assertEqual(category, "other")
        self.assertEqual(confidence, 0.0)
        self.assertIsNone(prompt_version)
        self.assertIsNone(model_id)
```

- [ ] **Step 6: Update `Classification` and `classify_title`**

In `packages/core/core/classification/classify.py`, change the
`Classification` dataclass to add two fields:

```python
@dataclass(frozen=True)
class Classification:
    """One job's resolved classification.

    Attributes:
        category: One of the 7-value classification taxonomy.
        category_confidence: Confidence in `category`, 0.0-1.0.
        category_method: Which stage produced `category` — 'rules',
            'embedding', or 'llm'.
        qa_category: The mapped 4-value question-bank category, or
            `None` when `category` has no reasonable question-bank
            equivalent (currently only 'other').
        seniority_band: One of the 5-value seniority taxonomy.
        prompt_version: The prompt version that produced `category`,
            when `category_method == 'llm'`. `None` for 'rules'/
            'embedding' rows, which never called an LLM.
        model_id: The model that produced `category`, when
            `category_method == 'llm'`. `None` otherwise.
    """

    category: str
    category_confidence: float
    category_method: str
    qa_category: str | None
    seniority_band: str
    prompt_version: str | None = None
    model_id: str | None = None
```

Change the LLM-stage branch in `classify_title` from:

```python
        else:
            category, confidence = classify_by_llm(title, adapters=adapters)
            method = "llm"

    return Classification(
        category=category,
        category_confidence=confidence,
        category_method=method,
        qa_category=qa_category_map.get(category),
        seniority_band=seniority_band,
    )
```

to:

```python
        else:
            category, confidence, prompt_version, model_id = classify_by_llm(
                title, adapters=adapters
            )
            method = "llm"
            return Classification(
                category=category,
                category_confidence=confidence,
                category_method=method,
                qa_category=qa_category_map.get(category),
                seniority_band=seniority_band,
                prompt_version=prompt_version,
                model_id=model_id,
            )

    return Classification(
        category=category,
        category_confidence=confidence,
        category_method=method,
        qa_category=qa_category_map.get(category),
        seniority_band=seniority_band,
    )
```

(The `rules`/`embedding` branches fall through to the final `return`
unchanged, leaving `prompt_version`/`model_id` at their `None`
defaults. The `llm` branch returns early with both populated — this
mirrors the existing early-`return` shape the `title is None` branch
already uses earlier in the same function.)

- [ ] **Step 7: Update `test_classification_classify.py`'s `_FakeAdapter`**

Its `complete` method currently returns confidence only via
`{"category": ..., "confidence": ...}` JSON — no change needed there
since `classify_by_llm`'s parsing is unchanged, but add one new
assertion to `test_llm_stage_only_fires_when_rules_and_embedding_both_decline`:

```python
    def test_llm_stage_only_fires_when_rules_and_embedding_both_decline(
        self,
    ) -> None:
        adapter = _FakeAdapter(category="software_engineer", confidence=0.6)
        result = classify_title(
            "Chief Vibes Officer",
            centroids={},  # empty centroids -> embedding stage can never match
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertTrue(adapter.called)
        self.assertEqual(result.category, "software_engineer")
        self.assertEqual(result.category_method, "llm")
        self.assertEqual(result.category_confidence, 0.6)
        self.assertEqual(result.prompt_version, "claude.v1")
        self.assertEqual(result.model_id, "claude-sonnet-5")
```

And add one new test confirming rules-stage rows carry no
prompt_version/model_id:

```python
    def test_rules_stage_result_carries_no_prompt_version_or_model_id(self) -> None:
        adapter = _FakeAdapter()
        result = classify_title(
            "Senior Data Engineer",
            centroids={},
            qa_category_map=self._qa_map(),
            adapters={"anthropic": adapter},
            embedding_base_url="unused",
            embedding_model="unused",
            http_client=None,  # type: ignore[arg-type]
        )
        self.assertIsNone(result.prompt_version)
        self.assertIsNone(result.model_id)
```

- [ ] **Step 8: Run all classification tests**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_llm_classifier tests.test_classification_classify -v`
Expected: PASS, all green

- [ ] **Step 9: Run the integration test if an Anthropic key is configured**

Run: `cd job_search/packages/core && set -a && source ../../.env && set +a && python3.11 -m unittest tests.integration.test_classification_llm -v`
Expected: PASS (2 tests) — skipped cleanly if `ANTHROPIC_API_KEY` is unset

- [ ] **Step 10: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/classification/llm_classifier.py packages/core/core/classification/classify.py packages/core/tests/test_llm_classifier.py packages/core/tests/test_classification_classify.py packages/core/tests/integration/test_classification_llm.py && isort <same files> && ruff check <same files>`
Expected: clean

- [ ] **Step 11: Commit**

```bash
git add packages/core/core/classification/llm_classifier.py packages/core/core/classification/classify.py packages/core/tests/test_llm_classifier.py packages/core/tests/test_classification_classify.py packages/core/tests/integration/test_classification_llm.py
git commit -m "feat(job_search): thread prompt_version/model_id through classify_by_llm and Classification (JOB-196)"
```

---

## Task 9: `write_job_category` persists `prompt_version`/`model_id`

**Files:**
- Modify: `packages/core/core/classification/write_job_category.py`
- Modify: `packages/core/tests/integration/test_write_job_category.py`

**Interfaces:**
- Consumes: `Classification.prompt_version`, `Classification.model_id` (Task 8), migration 0015's new columns (Task 7)

- [ ] **Step 1: Write the failing assertion**

In `packages/core/tests/integration/test_write_job_category.py`, add a
new test method (after whatever the file's existing final test is —
locate the class body with `grep -n "class TestWriteJobCategory" -A 5` and append inside it):

```python
    def test_llm_classified_row_carries_prompt_version_and_model_id(self) -> None:
        write_job_category(
            self.engine,
            adapters={
                "anthropic": AnthropicAdapter(
                    api_key=_settings.anthropic_api_key,
                    client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
                )
            },
            http_client=self.http_client,
            job_group_ids=[self.job_group_id],
        )
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT category_method, prompt_version, model_id "
                    "FROM silver.job_category WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            ).one()
        if row.category_method == "llm":
            self.assertIsNotNone(row.prompt_version)
            self.assertIsNotNone(row.model_id)
        else:
            # Rules/embedding resolved this fixture's title before the
            # LLM stage — still a valid outcome (this fixture's title
            # isn't guaranteed to reach the LLM stage), and NULL is the
            # documented-correct value for a non-LLM row.
            self.assertIsNone(row.prompt_version)
            self.assertIsNone(row.model_id)
```

(This reuses whatever fixture title/setup the existing test class
already seeds — check the file's `setUp` for the exact
`self.job_group_id` fixture already in place, per the file excerpt
already read this session.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && set -a && source ../../.env && set +a && python3.11 -m unittest tests.integration.test_write_job_category -v`
Expected: FAIL — `UndefinedColumn: column "prompt_version" does not exist` (if migration 0015 hasn't been applied to this environment yet, apply it first per Task 7 Step 2) or the assertion itself fails because `_UPSERT` doesn't write these columns yet

- [ ] **Step 3: Update `_UPSERT` and the write loop**

In `packages/core/core/classification/write_job_category.py`, change
`_UPSERT` from:

```python
_UPSERT = text(
    """
    INSERT INTO silver.job_category (
        job_group_id, category, category_confidence, category_method,
        qa_category, seniority_band
    ) VALUES (
        :job_group_id, :category, :category_confidence, :category_method,
        :qa_category, :seniority_band
    )
    ON CONFLICT (job_group_id) DO UPDATE SET
        category = EXCLUDED.category,
        category_confidence = EXCLUDED.category_confidence,
        category_method = EXCLUDED.category_method,
        qa_category = EXCLUDED.qa_category,
        seniority_band = EXCLUDED.seniority_band,
        computed_at = now()
    """
)
```

to:

```python
_UPSERT = text(
    """
    INSERT INTO silver.job_category (
        job_group_id, category, category_confidence, category_method,
        qa_category, seniority_band, prompt_version, model_id
    ) VALUES (
        :job_group_id, :category, :category_confidence, :category_method,
        :qa_category, :seniority_band, :prompt_version, :model_id
    )
    ON CONFLICT (job_group_id) DO UPDATE SET
        category = EXCLUDED.category,
        category_confidence = EXCLUDED.category_confidence,
        category_method = EXCLUDED.category_method,
        qa_category = EXCLUDED.qa_category,
        seniority_band = EXCLUDED.seniority_band,
        prompt_version = EXCLUDED.prompt_version,
        model_id = EXCLUDED.model_id,
        computed_at = now()
    """
)
```

And add the two fields to the `conn.execute(_UPSERT, {...})` call's
parameter dict:

```python
            conn.execute(
                _UPSERT,
                {
                    "job_group_id": row.job_group_id,
                    "category": classification.category,
                    "category_confidence": classification.category_confidence,
                    "category_method": classification.category_method,
                    "qa_category": classification.qa_category,
                    "seniority_band": classification.seniority_band,
                    "prompt_version": classification.prompt_version,
                    "model_id": classification.model_id,
                },
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd job_search/packages/core && set -a && source ../../.env && set +a && python3.11 -m unittest tests.integration.test_write_job_category -v`
Expected: PASS, skipped cleanly if Ollama unreachable or no Anthropic key

- [ ] **Step 5: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/classification/write_job_category.py packages/core/tests/integration/test_write_job_category.py && isort <same> && ruff check <same>`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/classification/write_job_category.py packages/core/tests/integration/test_write_job_category.py
git commit -m "feat(job_search): write_job_category persists prompt_version/model_id (JOB-196)"
```

---

## Task 10: Golden-set loader — file-based path + `GoldenCase`/`EvalConfigError`

**Files:**
- Create: `packages/core/core/evals/golden.py`
- Create: `packages/core/tests/test_evals_golden.py`

**Interfaces:**
- Produces: `core.evals.golden.GoldenCase` (dataclass: `case_id: str`, `input: dict`, `expected: dict`)
- Produces: `core.evals.golden.EvalConfigError(Exception)`
- Produces: `core.evals.golden.load_golden_set(task: str, *, engine: Engine | None = None, golden_dir: Path | None = None, job_group_ids: list[str] | None = None) -> list[GoldenCase]`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_evals_golden.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.evals.golden import EvalConfigError, GoldenCase, load_golden_set

_SAMPLE_YAML = """
cases:
  - case_id: "case-001"
    input: {"title": "Senior Data Engineer"}
    expected: {"category": "data_engineer"}
  - case_id: "case-002"
    input: {"title": "Product Manager"}
    expected: {"category": "other"}
"""


class TestLoadGoldenSetFileBased(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        golden_dir = Path(self.tmp_dir.name)
        (golden_dir / "some_future_task.yml").write_text(_SAMPLE_YAML)
        self.golden_dir = golden_dir

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_loads_cases_from_a_yaml_file(self) -> None:
        cases = load_golden_set("some_future_task", golden_dir=self.golden_dir)
        self.assertEqual(len(cases), 2)
        self.assertEqual(
            cases[0],
            GoldenCase(
                case_id="case-001",
                input={"title": "Senior Data Engineer"},
                expected={"category": "data_engineer"},
            ),
        )

    def test_raises_when_no_source_is_registered_for_the_task(self) -> None:
        with self.assertRaises(EvalConfigError):
            load_golden_set("truly_unregistered_task", golden_dir=self.golden_dir)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_evals_golden -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.evals.golden'`

- [ ] **Step 3: Implement the file-based path**

`packages/core/core/evals/golden.py`:

```python
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
            `<golden_dir>/<task>.yml` file exists.
    """
    if task in _DB_BACKED_TASKS:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_evals_golden -v`
Expected: PASS (2 tests) — note this task deliberately imports
`core.evals.golden_db` lazily inside the DB-backed branch, which
doesn't exist until Task 11; the file-based tests never hit that
branch, so they pass now regardless

- [ ] **Step 5: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/evals/golden.py packages/core/tests/test_evals_golden.py && isort packages/core/core/evals/golden.py packages/core/tests/test_evals_golden.py && ruff check packages/core/core/evals/golden.py packages/core/tests/test_evals_golden.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add packages/core/core/evals/golden.py packages/core/tests/test_evals_golden.py
git commit -m "feat(job_search): file-based golden-set loader (JOB-183, JOB-184)"
```

---

## Task 11: DB-backed golden set for `job_categorisation`

**Files:**
- Create: `packages/core/core/evals/golden_db.py`
- Create: `packages/core/tests/integration/test_evals_golden_db.py`

**Interfaces:**
- Consumes: `core.evals.golden.GoldenCase` (Task 10), `classification.category_review_labels` (migration 0014, already merged into `main` via PR #11), `gold.dim_job`
- Produces: `core.evals.golden_db.load_job_categorisation_golden_set(engine: Engine, *, job_group_ids: list[str] | None = None) -> list[GoldenCase]`

- [ ] **Step 1: Write the failing integration test**

`packages/core/tests/integration/test_evals_golden_db.py`:

```python
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.evals.golden import GoldenCase
from core.evals.golden_db import load_job_categorisation_golden_set

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


def _insert_dim_job(conn, **overrides: object) -> None:
    values = {
        "job_group_id": None,
        "title_for_display": "Data Engineer",
        "title_raw": "Data Engineer",
        "company": "Acme Ltd",
        "location": "London",
        "description": "A test description.",
        "category": "data_engineer",
        "category_confidence": 0.9,
        "category_method": "rules",
        "qa_category": "data_engineer",
        "seniority_band": "mid",
    }
    values.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO gold.dim_job (
                job_group_id, title_for_display, title_raw, company,
                location, description, category, category_confidence,
                category_method, qa_category, seniority_band
            ) VALUES (
                :job_group_id, :title_for_display, :title_raw, :company,
                :location, :description, :category, :category_confidence,
                :category_method, :qa_category, :seniority_band
            )
            """
        ),
        values,
    )


class TestLoadJobCategorisationGoldenSet(unittest.TestCase):
    """Integration test against real Postgres — no mocking the database."""

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.job_group_id = f"test-golden-{uuid.uuid4().hex}"
        with self.engine.begin() as conn:
            _insert_dim_job(
                conn, job_group_id=self.job_group_id, title_raw="Senior Data Engineer"
            )
            conn.execute(
                text(
                    "INSERT INTO classification.category_review_labels "
                    "(job_group_id, reviewed_category, reviewed_seniority_band) "
                    "VALUES (:id, 'data_engineer', 'senior')"
                ),
                {"id": self.job_group_id},
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = :id"),
                {"id": self.job_group_id},
            )
        self.engine.dispose()

    def test_includes_the_seeded_reviewed_case(self) -> None:
        # Scoped to just this fixture's job_group_id — this dev database's
        # classification.category_review_labels is SHARED, real state
        # (other tests, and eventually a real JOB-170 hand-check, write
        # into the same table), so an unscoped load here would be
        # asserting against whatever else happens to exist too.
        cases = load_job_categorisation_golden_set(
            self.engine, job_group_ids=[self.job_group_id]
        )
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertIsInstance(case, GoldenCase)
        self.assertEqual(case.case_id, self.job_group_id)
        self.assertEqual(case.input, {"title": "Senior Data Engineer"})
        self.assertEqual(case.expected, {"category": "data_engineer"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_evals_golden_db -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.evals.golden_db'`

Note: this test requires this session's `feat/JOB-170-categorisation-review-tool`
branch's `classification.category_review_labels` table (migration
0014). If working from a fresh checkout of `main` before that PR
merges, apply migration 0014 first (already committed on that branch's
history) or cherry-pick it — `run-evals` overall depends on that
table existing, same as this task.

- [ ] **Step 3: Implement**

`packages/core/core/evals/golden_db.py`:

```python
"""DB-backed golden-set loader for job_categorisation — reads JOB-170's
hand-check table directly rather than duplicating it into a file (see
core.evals.golden's module docstring for why).
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.evals.golden import GoldenCase

_SELECT_REVIEWED_CASES = text(
    """
    SELECT r.job_group_id, d.title_raw, r.reviewed_category
    FROM classification.category_review_labels AS r
    INNER JOIN gold.dim_job AS d ON r.job_group_id = d.job_group_id
    """
)

# A `job_group_ids`-scoped variant of _SELECT_REVIEWED_CASES — same
# reasoning as write_job_category.py's own _SELECT_UNCLASSIFIED_SCOPED:
# a test seeding fixture rows into this SHARED table must not also
# pick up whatever unrelated real rows already exist there.
_SELECT_REVIEWED_CASES_SCOPED = text(
    """
    SELECT r.job_group_id, d.title_raw, r.reviewed_category
    FROM classification.category_review_labels AS r
    INNER JOIN gold.dim_job AS d ON r.job_group_id = d.job_group_id
    WHERE r.job_group_id = ANY(:job_group_ids)
    """
)


def load_job_categorisation_golden_set(
    engine: Engine, *, job_group_ids: list[str] | None = None
) -> list[GoldenCase]:
    """Load every hand-checked job_categorisation case.

    Args:
        engine: An engine with SELECT on `classification.
            category_review_labels` and `gold.dim_job`.
        job_group_ids: Restrict to these job_group_ids only, instead of
            every reviewed row. `None` (the default, and what
            production `run_eval` calls always pass) loads everything.

    Returns:
        One `GoldenCase` per reviewed job, keyed by `job_group_id`.
        Empty until JOB-170's hand-check has actually been done through
        the Categorisation Review page.
    """
    with engine.connect() as conn:
        if job_group_ids is None:
            rows = conn.execute(_SELECT_REVIEWED_CASES).all()
        else:
            rows = conn.execute(
                _SELECT_REVIEWED_CASES_SCOPED, {"job_group_ids": job_group_ids}
            ).all()
    return [
        GoldenCase(
            case_id=row.job_group_id,
            input={"title": row.title_raw},
            expected={"category": row.reviewed_category},
        )
        for row in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_evals_golden_db -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run Task 10's file-based tests again to confirm the lazy import now resolves**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_evals_golden -v`
Expected: PASS (2 tests, unchanged)

- [ ] **Step 6: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/evals/golden_db.py packages/core/tests/integration/test_evals_golden_db.py && isort <same> && ruff check <same>`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/evals/golden_db.py packages/core/tests/integration/test_evals_golden_db.py
git commit -m "feat(job_search): DB-backed golden set for job_categorisation, reusing JOB-170's hand-check table (JOB-183)"
```

---

## Task 12: Migration — `evals.eval_runs`

**Files:**
- Create: `db/migrations/versions/0016_create_evals_eval_runs.py`

**Interfaces:**
- Produces: `evals.eval_runs (id SERIAL PK, task TEXT, provider TEXT, prompt_version TEXT, metric TEXT, score NUMERIC, case_count INTEGER, run_at TIMESTAMPTZ)`

- [ ] **Step 1: Write the migration**

`db/migrations/versions/0016_create_evals_eval_runs.py`:

```python
"""create evals.eval_runs

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-12

evals.eval_runs is SHARED data (PLAN.md's two-zone rule): how well a
prompt version performs against a golden set is the same fact for
every user of this project, so no user_id, no RLS — same two-zone
reasoning as dedup.calibration_thresholds.

Append-only, same reasoning as calibration_thresholds (0010): every
`run-evals` invocation adds a row rather than updating one, so history
is free and the most recent row per (task, provider) is "current" —
exactly what the regression comparison in run-evals needs.

Written only by the pipeline CLI (owner role) — no request-serving
access needed, unlike classification.category_review_labels (0014):
nothing in apps/api reads or writes this table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS evals")
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="evals",
    )


def downgrade() -> None:
    op.drop_table("eval_runs", schema="evals")
    op.execute("DROP SCHEMA IF EXISTS evals")
```

- [ ] **Step 2: Apply and verify the round-trip**

Run (from `job_search/`, with Postgres up):

```bash
export DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
alembic -c db/alembic.ini upgrade head
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "\dt evals.*"
alembic -c db/alembic.ini downgrade -1
alembic -c db/alembic.ini upgrade head
docker compose exec -T postgres psql -U job_search_owner -d job_search -c "\dt evals.*"
```

Expected: `evals.eval_runs` present after each `upgrade head`, no
errors on downgrade or re-upgrade.

- [ ] **Step 3: Commit**

```bash
git add db/migrations/versions/0016_create_evals_eval_runs.py
git commit -m "feat(job_search): add evals.eval_runs (JOB-194)"
```

---

## Task 13: Eval runner — ties golden set, metric, and run history together

**Files:**
- Create: `packages/core/core/evals/runner.py`
- Create: `packages/core/tests/integration/test_evals_runner.py`

**Interfaces:**
- Consumes: `core.evals.golden.load_golden_set` (Tasks 10–11), `core.evals.metrics.exact_match`/`field_f1` (Task 2), `core.llm.task_config.load_task_config` (Task 6), `core.classification.llm_classifier.classify_by_llm` (Task 8)
- Produces: `core.evals.runner.MINIMUM_GOLDEN_SET_SIZE: int = 20`
- Produces: `core.evals.runner.EvalRunResult` (dataclass: `task`, `provider`, `status: Literal["ok", "insufficient_data", "provider_not_configured"]`, `score: float | None`, `case_count: int`, `previous_score: float | None`, `delta: float | None`, `regressed: bool`)
- Produces: `core.evals.runner.run_eval(task: str, provider: str, *, engine: Engine, adapters: dict[str, LLMAdapter], config_path: Path | None = None, golden_dir: Path | None = None, job_group_ids: list[str] | None = None) -> EvalRunResult`

- [ ] **Step 1: Write the failing integration tests**

`packages/core/tests/integration/test_evals_runner.py`:

```python
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.db.session import build_engine
from core.evals.runner import EvalRunResult, run_eval
from core.llm.types import LLMResponse

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"


def _insert_dim_job(conn, **overrides: object) -> None:
    values = {
        "job_group_id": None,
        "title_for_display": "Data Engineer",
        "title_raw": "Data Engineer",
        "company": "Acme Ltd",
        "location": "London",
        "description": "A test description.",
        "category": "data_engineer",
        "category_confidence": 0.9,
        "category_method": "rules",
        "qa_category": "data_engineer",
        "seniority_band": "mid",
    }
    values.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO gold.dim_job (
                job_group_id, title_for_display, title_raw, company,
                location, description, category, category_confidence,
                category_method, qa_category, seniority_band
            ) VALUES (
                :job_group_id, :title_for_display, :title_raw, :company,
                :location, :description, :category, :category_confidence,
                :category_method, :qa_category, :seniority_band
            )
            """
        ),
        values,
    )


class _FakeAdapter:
    """Always classifies correctly against whatever title it's given,
    by echoing back a category the test controls per-title."""

    def __init__(self, category_by_title: dict[str, str]) -> None:
        self._category_by_title = category_by_title

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        import json
        import re

        match = re.search(r"Job title: (.+)", prompt)
        title = match.group(1).strip() if match else ""
        category = self._category_by_title.get(title, "other")
        return LLMResponse(
            text=json.dumps({"category": category, "confidence": 0.9}),
            provider="anthropic",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestRunEvalInsufficientData(unittest.TestCase):
    """No seeded golden-set rows at all — under MINIMUM_GOLDEN_SET_SIZE."""

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_reports_insufficient_data_rather_than_a_false_pass(self) -> None:
        # Relies on this dev database's classification.category_review_labels
        # genuinely having fewer than MINIMUM_GOLDEN_SET_SIZE rows at the
        # point this test runs (true as of this branch — JOB-170's
        # hand-check hasn't been done yet). If that ever changes, this
        # test's premise no longer holds and should be revisited rather
        # than the runner's behaviour.
        with self.engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM classification.category_review_labels")
            ).scalar_one()
        if count >= 20:
            self.skipTest(
                "category_review_labels already has >= MINIMUM_GOLDEN_SET_SIZE "
                "rows in this environment"
            )
        result = run_eval(
            "job_categorisation", "target", engine=self.engine, adapters={}
        )
        self.assertEqual(result.status, "insufficient_data")
        self.assertIsNone(result.score)


class TestRunEvalWithEnoughCases(unittest.TestCase):
    """Seeds MINIMUM_GOLDEN_SET_SIZE reviewed rows directly so the run
    proceeds regardless of this dev database's real JOB-170 progress.
    """

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.job_group_ids: list[str] = []
        with self.engine.begin() as conn:
            # eval_runs has no per-test scoping column (unlike
            # category_review_labels, which is scoped via job_group_ids
            # below) — as of this plan, nothing outside these tests
            # writes job_categorisation rows into it yet, so clearing
            # them here as well as in tearDown keeps
            # test_perfect_predictions_score_one_and_persist_a_run's
            # "no previous run" assertion true regardless of leftover
            # state from an earlier failed test run.
            conn.execute(
                text("DELETE FROM evals.eval_runs WHERE task = 'job_categorisation'")
            )
            for i in range(20):
                job_group_id = f"test-runner-{uuid.uuid4().hex}"
                self.job_group_ids.append(job_group_id)
                title = f"Data Engineer {i}"
                _insert_dim_job(
                    conn, job_group_id=job_group_id, title_raw=title
                )
                conn.execute(
                    text(
                        "INSERT INTO classification.category_review_labels "
                        "(job_group_id, reviewed_category, reviewed_seniority_band) "
                        "VALUES (:id, 'data_engineer', 'mid')"
                    ),
                    {"id": job_group_id},
                )
        self.adapters = {
            "anthropic": _FakeAdapter(
                {f"Data Engineer {i}": "data_engineer" for i in range(20)}
            )
        }

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM classification.category_review_labels "
                    "WHERE job_group_id = ANY(:ids)"
                ),
                {"ids": self.job_group_ids},
            )
            conn.execute(
                text("DELETE FROM gold.dim_job WHERE job_group_id = ANY(:ids)"),
                {"ids": self.job_group_ids},
            )
            conn.execute(
                text("DELETE FROM evals.eval_runs WHERE task = 'job_categorisation'")
            )
        self.engine.dispose()

    def test_perfect_predictions_score_one_and_persist_a_run(self) -> None:
        result = run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=self.adapters,
            job_group_ids=self.job_group_ids,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.score, 1.0)
        self.assertGreaterEqual(result.case_count, 20)
        self.assertIsNone(result.previous_score)
        self.assertFalse(result.regressed)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT task, provider, metric, score, case_count "
                    "FROM evals.eval_runs WHERE task = 'job_categorisation' "
                    "ORDER BY run_at DESC LIMIT 1"
                )
            ).one()
        self.assertEqual(row.provider, "anthropic")
        self.assertEqual(row.metric, "exact_match")
        self.assertEqual(float(row.score), 1.0)

    def test_a_worse_second_run_is_flagged_as_a_regression(self) -> None:
        run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=self.adapters,
            job_group_ids=self.job_group_ids,
        )
        # Second run: adapter now gets everything wrong.
        worse_adapters = {"anthropic": _FakeAdapter({})}
        result = run_eval(
            "job_categorisation",
            "target",
            engine=self.engine,
            adapters=worse_adapters,
            job_group_ids=self.job_group_ids,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.previous_score, 1.0)
        self.assertEqual(result.delta, -1.0)
        self.assertTrue(result.regressed)

    def test_provider_not_configured_for_local(self) -> None:
        # job_categorisation has no local_provider configured (Task 6) —
        # requesting "local" must not crash, and must not persist a row.
        result = run_eval(
            "job_categorisation", "local", engine=self.engine, adapters={}
        )
        self.assertEqual(result.status, "provider_not_configured")
        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM evals.eval_runs "
                    "WHERE task = 'job_categorisation' AND provider = 'ollama'"
                )
            ).scalar_one()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_evals_runner -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.evals.runner'`

- [ ] **Step 3: Implement the runner**

`packages/core/core/evals/runner.py`:

```python
"""The eval runner (PLAN.md Step 12a) — loads a task's golden set, runs
every case through the task's configured metric, persists one
evals.eval_runs row, and reports the delta against the immediately-
prior run for the same (task, provider).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

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
    return task_config.local_provider, task_config.local_model, task_config.local_prompt_family


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
) -> dict[str, object]:
    from core.classification.llm_classifier import classify_by_llm

    category, _confidence, _prompt_version, _model_id = classify_by_llm(
        case.input["title"],
        adapters=adapters,
        provider=provider,
        model=model,
        prompt_family=prompt_family,
    )
    return {"category": category}


_PREDICTORS: dict[str, Callable] = {
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
    """
    task_config = load_task_config(task, config_path=config_path)
    resolved = _resolve_provider(task_config, provider)
    if resolved is None:
        return EvalRunResult(
            task=task, provider=provider, status="provider_not_configured",
            score=None, case_count=0,
        )
    resolved_provider, resolved_model, resolved_prompt_family = resolved

    cases = load_golden_set(
        task, engine=engine, golden_dir=golden_dir, job_group_ids=job_group_ids
    )
    if len(cases) < MINIMUM_GOLDEN_SET_SIZE:
        return EvalRunResult(
            task=task, provider=provider, status="insufficient_data",
            score=None, case_count=len(cases),
        )

    metric_fn = _METRICS[task_config.eval_metric]
    predictor = _PREDICTORS[task]
    scores = [
        metric_fn(
            predictor(
                case,
                provider=resolved_provider,
                model=resolved_model,
                prompt_family=resolved_prompt_family,
                adapters=adapters,
            ),
            case.expected,
        )
        for case in cases
    ]
    score = sum(scores) / len(scores)

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
    regressed = (
        delta is not None
        and delta < 0
        and abs(delta) > (task_config.eval_regression_threshold or float("inf"))
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO evals.eval_runs "
                "(task, provider, prompt_version, metric, score, case_count) "
                "VALUES (:task, :provider, :prompt_version, :metric, :score, :case_count)"
            ),
            {
                "task": task,
                "provider": resolved_provider,
                "prompt_version": f"{resolved_prompt_family}.v1",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.integration.test_evals_runner -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full classification + evals test set**

Run: `cd job_search/packages/core && set -a && source ../../.env && set +a && python3.11 -m unittest discover -s tests -p "*evals*" -v && python3.11 -m unittest discover -s tests -p "test_classification*" -v && python3.11 -m unittest discover -s tests -p "test_llm*" -v`
Expected: PASS, all green

- [ ] **Step 6: Lint and format**

Run: `cd job_search && source venv/bin/activate && black packages/core/core/evals/runner.py packages/core/tests/integration/test_evals_runner.py && isort packages/core/core/evals/runner.py packages/core/tests/integration/test_evals_runner.py && ruff check packages/core/core/evals/runner.py packages/core/tests/integration/test_evals_runner.py`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add packages/core/core/evals/runner.py packages/core/tests/integration/test_evals_runner.py
git commit -m "feat(job_search): add the eval runner — golden set + metric + regression comparison (JOB-191, JOB-193, JOB-197, JOB-198, JOB-201)"
```

---

## Task 14: `run-evals` pipeline CLI subcommand

**Files:**
- Modify: `apps/pipeline/app/cli.py`
- Create: `packages/core/tests/test_pipeline_cli_run_evals.py`

**Interfaces:**
- Consumes: `core.evals.runner.run_eval`, `core.evals.runner.EvalRunResult` (Task 13)

- [ ] **Step 1: Write the failing test**

`packages/core/tests/test_pipeline_cli_run_evals.py` — unit-level,
mocking only `run_eval` itself (not the database — this test never
touches Postgres, it verifies argument parsing and output formatting):

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402

from core.evals.runner import EvalRunResult  # noqa: E402


class TestRunEvalsSubcommand(unittest.TestCase):
    @mock.patch("app.cli.run_eval")
    def test_reports_ok_result_and_exits_zero(self, mock_run_eval: mock.Mock) -> None:
        mock_run_eval.return_value = EvalRunResult(
            task="job_categorisation",
            provider="target",
            status="ok",
            score=0.95,
            case_count=25,
            previous_score=0.90,
            delta=0.05,
            regressed=False,
        )
        exit_code = main(
            ["run-evals", "--task", "job_categorisation", "--provider", "target"]
        )
        self.assertEqual(exit_code, 0)
        mock_run_eval.assert_called_once()
        self.assertEqual(mock_run_eval.call_args.args, ("job_categorisation", "target"))

    @mock.patch("app.cli.run_eval")
    def test_exits_non_zero_on_a_flagged_regression(
        self, mock_run_eval: mock.Mock
    ) -> None:
        mock_run_eval.return_value = EvalRunResult(
            task="job_categorisation",
            provider="target",
            status="ok",
            score=0.5,
            case_count=25,
            previous_score=0.95,
            delta=-0.45,
            regressed=True,
        )
        exit_code = main(
            ["run-evals", "--task", "job_categorisation", "--provider", "target"]
        )
        self.assertEqual(exit_code, 1)

    @mock.patch("app.cli.run_eval")
    def test_all_flag_runs_every_configured_task(
        self, mock_run_eval: mock.Mock
    ) -> None:
        mock_run_eval.return_value = EvalRunResult(
            task="job_categorisation",
            provider="target",
            status="insufficient_data",
            score=None,
            case_count=0,
        )
        exit_code = main(["run-evals", "--all", "--provider", "target"])
        self.assertEqual(exit_code, 0)
        self.assertGreaterEqual(mock_run_eval.call_count, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_pipeline_cli_run_evals -v`
Expected: FAIL — `argparse` error, unrecognized subcommand `run-evals`

- [ ] **Step 3: Add the subcommand**

In `apps/pipeline/app/cli.py`, add to the imports at the top:

```python
from core.evals.runner import EvalRunResult, run_eval
from core.llm.task_config import load_task_config
```

Add a list of tasks `--all` iterates over, and the command function,
placed after `_cmd_classify_jobs`:

```python
# Tasks with an eval configured — extend as future steps (13, 15-17,
# 19, 20) add their own eval_metric entry to config/llm_tasks.yml.
_EVAL_TASKS = ["job_categorisation"]


def _report_eval_result(result: EvalRunResult) -> None:
    """Print one EvalRunResult in a human-readable line.

    Args:
        result: The result to report.
    """
    if result.status == "insufficient_data":
        print(
            f"{result.task} ({result.provider}): insufficient_data "
            f"({result.case_count} cases, need {20})"
        )
        return
    if result.status == "provider_not_configured":
        print(f"{result.task} ({result.provider}): provider_not_configured")
        return
    delta_str = f", delta={result.delta:+.3f}" if result.delta is not None else ""
    regressed_str = " REGRESSED" if result.regressed else ""
    print(
        f"{result.task} ({result.provider}): score={result.score:.3f} "
        f"(n={result.case_count}){delta_str}{regressed_str}"
    )


def _cmd_run_evals(args: argparse.Namespace) -> int:
    """Run the `run-evals` subcommand.

    Args:
        args: Parsed CLI arguments — `task` (or `all`) and `provider`.

    Returns:
        0 if every run reported no regression, 1 if any did.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url)
    http_client = httpx.Client(timeout=30.0)
    try:
        adapters = _build_llm_adapters(http_client)
        tasks = _EVAL_TASKS if args.all else [args.task]
        provider_labels = ["target", "local"] if args.provider == "both" else [args.provider]

        any_regressed = False
        for task in tasks:
            for provider_label in provider_labels:
                result = run_eval(
                    task, provider_label, engine=engine, adapters=adapters
                )
                _report_eval_result(result)
                any_regressed = any_regressed or result.regressed
        return 1 if any_regressed else 0
    finally:
        http_client.close()
```

Register the subparser (add alongside the other `subparsers.add_parser(...)`
calls, before the `args = parser.parse_args(argv)` line):

```python
    run_evals_parser = subparsers.add_parser(
        "run-evals",
        help="Run a task's golden set against target/local/both providers",
    )
    run_evals_group = run_evals_parser.add_mutually_exclusive_group(required=True)
    run_evals_group.add_argument("--task", choices=_EVAL_TASKS)
    run_evals_group.add_argument("--all", action="store_true")
    run_evals_parser.add_argument(
        "--provider", required=True, choices=["target", "local", "both"]
    )
```

And add the dispatch line alongside the other `if args.command == ...`
checks:

```python
    if args.command == "run-evals":
        return _cmd_run_evals(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_pipeline_cli_run_evals -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full pipeline CLI test suite**

Run: `cd job_search/packages/core && python3.11 -m unittest tests.test_pipeline_cli tests.test_pipeline_cli_run_evals -v`
Expected: PASS (existing `test_pipeline_cli.py` tests unaffected —
adding a new subparser doesn't change existing ones)

- [ ] **Step 6: Manual end-to-end smoke test against real Postgres**

Run (with `docker compose up -d postgres` already running, from
`job_search/`, matching this project's established
`docker compose run --rm pipeline <subcommand>` convention for every
other CLI subcommand):

```bash
docker compose run --rm pipeline run-evals --task job_categorisation --provider target
echo "exit code: $?"
```

Expected: prints `job_categorisation (target): insufficient_data (0
cases, need 20)` — real, honest output reflecting that JOB-170's
hand-check hasn't been done yet, exit code 0 (insufficient data is not
a regression).

- [ ] **Step 7: Lint and format**

Run: `cd job_search && source venv/bin/activate && black apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli_run_evals.py && isort apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli_run_evals.py && ruff check apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli_run_evals.py`
Expected: clean

- [ ] **Step 8: Run the full repo test suite one final time**

Run: `cd job_search/packages/core && set -a && source ../../.env && set +a && python3.11 -m unittest discover -s tests`
Expected: same baseline as Task 3 Step 8 (2 pre-existing unrelated
`test_api_ingest.py` errors, 14 environment-gated skips) plus every
new test from this plan passing — no new failures

- [ ] **Step 9: Commit**

```bash
git add apps/pipeline/app/cli.py packages/core/tests/test_pipeline_cli_run_evals.py
git commit -m "feat(job_search): add run-evals pipeline CLI subcommand (JOB-192, JOB-197)"
```

---

## Not covered by this plan (deliberately, per the spec)

- RAGAS faithfulness (JOB-186, JOB-187) — deferred until Step 17 (tailoring) exists; see spec's Context section for the dependency-tree reasoning.
- CI wiring (JOB-195) — deferred; needs a GitHub `ANTHROPIC_API_KEY` secret only the user can add. `run-evals`'s exit code (0/1) already makes this a one-file addition later.
- Golden sets for CV extraction, JD skill extraction, scoring rationale, tailoring, cover letter, Q&A (parts of JOB-183/184) — no such component exists yet; each lands with its own step.
