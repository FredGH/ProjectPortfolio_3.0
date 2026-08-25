# Notes

## How the Workers Are Started

The same Docker image (built from [Dockerfile](Dockerfile)) is used for three services. The difference is the `command` each container runs, as defined in [docker-compose.yml](docker-compose.yml):

| Service | Command |
|---|---|
| `api` | `python scripts/run_api.py` (Dockerfile default) |
| `worker-fast` | `python -m arq agentic_triage.workers.tasks.FastWorkerSettings` |
| `worker-assess` | `python -m arq agentic_triage.workers.tasks.AssessWorkerSettings` |

`python -m arq <path.to.SettingsClass>` is arq's standard CLI entry point. It reads the settings class ([agentic_triage/workers/tasks.py](agentic_triage/workers/tasks.py) `:: FastWorkerSettings` / `AssessWorkerSettings`) to know:

- which Redis to connect to
- which functions to register as job handlers (`functions = [fast_preprocess_task]`)
- how many concurrent jobs to allow (`max_jobs`)
- which queue name to listen on (`queue_name = "fast"` / `"assess"`)

It then calls `on_startup = startup` once per worker process — that is where all config loading, SymSpell/keyword processors, LangGraph compilation, and DB/Qdrant connections happen (see Step 1 below).

**Startup order** is enforced via `depends_on`: both workers wait for `api` to be healthy, and `api` waits for postgres, redis, ollama, and qdrant to all be healthy first.

The [scripts/entrypoint.sh](scripts/entrypoint.sh) runs before any command — it reads the Postgres password from a Docker secret file (`/run/secrets/postgres_password`) and injects it as `DATABASE_URL`. In local dev the file is absent and `DATABASE_URL` is taken from the environment directly.

---

## System Flow — Step by Step

The system is built around three layers: an **HTTP API** (FastAPI), two **async worker queues** (arq + Redis), and a **LangGraph agent**. A complaint travels from API → fast worker → (optionally) assess worker → Postgres + Qdrant.

---

### Step 1 — Startup ([agentic_triage/workers/tasks.py](agentic_triage/workers/tasks.py) `:: startup`)

Once per worker process, before any job is accepted:

1. Connect to Qdrant, spaCy, and Postgres.
2. For every [`domains/*/config.yaml`](domains/banking_complaints/config.yaml), parse it into a `DomainConfig` dataclass ([agentic_triage/core/config.py](agentic_triage/core/config.py) `:: DomainConfig.from_dict`).
3. From each config, build two preprocessing tools per domain:
   - `SymSpell` normalizer (spell correction)
   - `KeywordProcessor` (FlashText exact-match, optional)
4. Pre-wire node factories (`make_sanitize_node`, `make_preprocess_node`, etc.) into a `fast_nodes` dict, keyed by domain.
5. Compile a `build_assess_graph` LangGraph for each domain (the LLM subgraph).

All of this lives in `ctx` and is reused for every job — nothing is rebuilt per request.

---

### Step 2 — API receives the batch ([agentic_triage/api/router.py](agentic_triage/api/router.py) `:: batch_submit`)

`POST /batch/submit/{domain}` with a list of `{ input_id, text }` items.

1. Generate a `batch_id` (UUID), insert a row into `triage_batches` (Postgres).
2. For each item, check if `input_id` already exists in `triage_results` — skip if so.
3. Enqueue `fast_preprocess_task` on the `"fast"` Redis queue.

The API returns immediately with `{ batch_id, enqueued, already_done }`.

---

### Step 3 — Fast worker: sanitize + cache check ([agentic_triage/workers/tasks.py](agentic_triage/workers/tasks.py) `:: fast_preprocess_task`)

The **fast worker** is cheap — it never calls the LLM.

1. **Init state** — create a blank `TriageState` TypedDict ([agentic_triage/core/state.py](agentic_triage/core/state.py)) with all fields zeroed.
2. **Sanitize node** ([agentic_triage/preprocessing/sanitizer.py](agentic_triage/preprocessing/sanitizer.py)) — strip HTML, PII hints, control characters from `raw_text` → `sanitized_text`.
3. **Semantic cache lookup** ([agentic_triage/retrieval/cache.py](agentic_triage/retrieval/cache.py)) — embed `sanitized_text` via Ollama, query Qdrant's cache collection. On a hit: write the cached result straight to Postgres and return. The LLM is never called.

---

### Step 4 — Fast worker: preprocess + retrieve + pre-filter

If the cache missed:

4. **Preprocess node** ([agentic_triage/agent/graph.py](agentic_triage/agent/graph.py) `:: make_preprocess_node`):
   - `normalize(text, sym_spell)` ([agentic_triage/preprocessing/normalizer.py](agentic_triage/preprocessing/normalizer.py)) — fix misspellings using the SymSpell model built from `keywords.txt`.
   - `extract_entities(cleaned, ner_labels, nlp)` ([agentic_triage/preprocessing/ner.py](agentic_triage/preprocessing/ner.py)) — spaCy NER; extracts MONEY, DATE, ORG, etc. into `entities`.
   - `extract_keywords(cleaned, kp)` ([agentic_triage/preprocessing/keyword.py](agentic_triage/preprocessing/keyword.py)) — FlashText scan; any match from `keywords.txt` goes into `triggered_keywords`.

5. **Retrieve node** ([agentic_triage/agent/graph.py](agentic_triage/agent/graph.py) `:: make_retrieve_node`):
   - For each collection in config (`complaints_history`, `regulatory_rules`, `risk_taxonomy`), call `retriever.search(collection, query, top_k, search_mode)` ([agentic_triage/retrieval/qdrant.py](agentic_triage/retrieval/qdrant.py)) against Qdrant.
   - Deduplicate hits by point ID (keep highest score).
   - Rerank merged chunks with the primary query ([agentic_triage/retrieval/reranker.py](agentic_triage/retrieval/reranker.py)).
   - Also average `dimension_scores` from `complaints_history` hits → `precedent_scores` (used later for confidence).
   - Returns: `retrieved_context` (role → chunks), `retrieval_scores` (collection → best score), `precedent_scores`.

6. **Pre-filter node** ([agentic_triage/agent/pre_filter.py](agentic_triage/agent/pre_filter.py) `:: is_auto_p4`):
   All three conditions must hold to skip the LLM:
   - No `triggered_keywords`
   - No high-value entities (MONEY, ORG, DATE, PERSON)
   - Best `retrieval_score` < 0.45

   If auto-P4: call `apply_auto_p4` → fills state with `priority_levels[-1]` (P4), zero scores, 0.95 confidence, skips LLM.
   If not auto-P4: set `is_auto_p4 = False` and continue.

7. **If auto-P4**: finalize, write to Postgres, write to semantic cache, done.
   **If not auto-P4**: enqueue `assess_task` on the `"assess"` Redis queue, passing the full state + embedding.

---

### Step 5 — Assess worker: LLM scoring loop ([agentic_triage/workers/tasks.py](agentic_triage/workers/tasks.py) `:: assess_task`)

The **assess worker** runs the compiled LangGraph assess subgraph (`build_assess_graph`). Entry point is the `assess` node — the fast nodes (sanitize, preprocess, pre-filter) are not repeated.

**Assess node** ([agentic_triage/agent/graph.py](agentic_triage/agent/graph.py) `:: make_assess_node`):

1. Build the prompt from `_ASSESS_SYSTEM` template, filling in:
   - `complaint` = `sanitized_text`
   - `context` = formatted `retrieved_context` (role-labelled chunks, truncated at 400 chars each)
   - `dimensions` = formatted `scoring_dimensions` (name, range, description, examples)
2. Call Ollama (`/api/generate`, `format: json`, `temperature: 0.0`).
3. Parse the JSON response with `_parse_dimension_scores` — clamps each score to `[d.min_score, d.max_score]`.
4. Call `compute_priority(dimension_scores, config)` ([agentic_triage/scoring/scorer.py](agentic_triage/scoring/scorer.py)):
   - Compute weighted `composite` score.
   - Check escalation override first (any single dimension > 4.0 → P1).
   - Walk priority levels highest→lowest: first where `composite >= min_composite` wins.
5. Call `compute_confidence(state, config)` ([agentic_triage/agent/confidence.py](agentic_triage/agent/confidence.py)):
   - Penalty −0.5 if any retrieval score < 0.6.
   - Penalty −0.5 if any dimension diverges > 1.5 from `precedent_scores` average.
   - `confidence = 1.0 − penalties` (floor 0.0).
6. Look up `recommended_action` from the matched priority level.

---

### Step 6 — Re-retrieval loop (conditional)

After assess, the graph router `_should_reretrieve` ([agentic_triage/agent/graph.py](agentic_triage/agent/graph.py)) checks:

```
confidence < confidence_threshold  AND  loop_count < max_reretrieval_loops
```

If yes → **Query rewrite node** ([agentic_triage/agent/query_rewriter.py](agentic_triage/agent/query_rewriter.py)):

1. Identify collections with retrieval score < 0.6 (the weak ones).
2. Ask Ollama to rewrite the complaint as a concise 1–2 sentence search query targeting those collections.
3. Update `cleaned_text` with the rewritten query, increment `loop_count`.
4. Return to **retrieve** → **assess** with the new query.

If no (confidence OK or loop cap hit) → go to **finalize**.

---

### Step 7 — Finalize node ([agentic_triage/agent/graph.py](agentic_triage/agent/graph.py) `:: make_finalize_node`)

Assembles `retrieved_references`: a dict of `role → [point_id, ...]` from `retrieved_context`. This is the audit trail of which KB entries influenced the decision.

---

### Step 8 — Persist results

After the graph completes:

- `write_triage_result(db, state, domain)` ([agentic_triage/retrieval/feedback.py](agentic_triage/retrieval/feedback.py)) — inserts a row into `triage_results` (Postgres) with priority, scores, confidence, reasoning, recommended_action.
- `write_cache(qdrant, embedding, result, domain)` ([agentic_triage/retrieval/cache.py](agentic_triage/retrieval/cache.py)) — stores the result in the semantic cache so identical (or near-identical) future complaints skip the LLM entirely.
- `_increment_batch_counter` ([agentic_triage/workers/tasks.py](agentic_triage/workers/tasks.py)) — updates `done` or `failed` counter on `triage_batches`.

---

### Step 9 — Feedback loop (optional, Workflow 2)

`POST /feedback/{domain}` ([agentic_triage/api/router.py](agentic_triage/api/router.py) `:: feedback`) — analyst submits a corrected priority after reviewing the result.

1. Write `analyst_override` to `triage_results` in Postgres.
2. Re-fetch the original triage result, rebuild a `TriageResult` object ([agentic_triage/core/schema.py](agentic_triage/core/schema.py)).
3. Call `ingest_confirmed_result(result, cleaned_text, "complaints_history", qdrant)` ([agentic_triage/retrieval/feedback.py](agentic_triage/retrieval/feedback.py)) — upserts the corrected result as a new vector point in the `complaints_history` Qdrant collection.

This closes the learning loop: future similar complaints will retrieve this corrected case as a precedent, pulling `precedent_scores` closer to the analyst-verified values.

---

### Step 10 — Reporting

`POST /report/{domain}` ([agentic_triage/api/router.py](agentic_triage/api/router.py) `:: report`) — fetches all `triage_results` for a `batch_id` from Postgres and passes them to `generate_summary` ([agentic_triage/reporting/reporter.py](agentic_triage/reporting/reporter.py)), which calls Ollama to produce a narrative summary of the batch.

---

### Data flow summary

```
POST /batch/submit
        │
        └─► fast queue (Redis)
                │
                ├─ sanitize
                ├─ [cache hit?] ──► write DB → done
                ├─ preprocess (normalize, NER, keywords)
                ├─ retrieve (Qdrant: 3 collections, rerank)
                ├─ pre_filter
                │       │
                │       ├─ [auto-P4?] ──► finalize ──► write DB + cache → done
                │       │
                │       └─ enqueue assess queue
                │
                └─► assess queue (Redis)
                        │
                        └─ LangGraph assess subgraph
                                │
                                ├─ assess (Ollama LLM → scores → priority → confidence)
                                │       │
                                │       ├─ [low confidence & loops left?]
                                │       │       └─ query_rewrite → retrieve → assess (loop)
                                │       │
                                │       └─ [done] → finalize
                                │
                                └─► write DB + cache → done
```

---

## `config.yaml` — How Key Fields Work

Config file location: [`domains/banking_complaints/config.yaml`](domains/banking_complaints/config.yaml)

---

### 1. `keyword_library_path`

Points to [`domains/banking_complaints/keywords.txt`](domains/banking_complaints/keywords.txt) (relative to `config.yaml`), which contains domain-specific terms like `GDPR`, `FCA`, `unauthorised transaction`, etc.

**How it's consumed:**

At startup ([agentic_triage/workers/tasks.py](agentic_triage/workers/tasks.py)), the path is passed into two builders:

- `build_symspell(path)` ([agentic_triage/preprocessing/normalizer.py](agentic_triage/preprocessing/normalizer.py)) — creates a spell-correction normalizer so misspellings like "unautorised" still match
- `build_keyword_processor(path)` ([agentic_triage/preprocessing/keyword.py](agentic_triage/preprocessing/keyword.py)) — creates a FlashText processor for fast exact-string matching

In the graph ([agentic_triage/agent/graph.py](agentic_triage/agent/graph.py)), the pre-process node uses these to normalize incoming complaint text before it's scored. The path is optional — if `None`, the keyword processor is skipped.

---

### 2. `scoring_dimensions`

Each entry defines a named dimension with a `weight`, `description`, and `high_score_examples`.

**How it's consumed in three places:**

**a) LLM prompt** ([agentic_triage/agent/graph.py:63](agentic_triage/agent/graph.py#L63)) — dimensions are formatted into the system prompt so the LLM knows what to score and what a high score looks like:
```
- reputational_risk (0–5): Regulatory breaches, GDPR violations, fee errors
  High score: GDPR breach with third-party data exposure; ...
```

**b) Score parsing** ([agentic_triage/agent/graph.py:398](agentic_triage/agent/graph.py#L398)) — the LLM's raw score output is clamped to each dimension's `[min_score, max_score]` bounds.

**c) Composite score calculation** ([agentic_triage/scoring/scorer.py](agentic_triage/scoring/scorer.py)) — a weighted average:
```
composite = Σ(score_i × weight_i) / Σ(weight_i)
```
With `weight: 1.0` on both dimensions, they contribute equally.

---

### 3. `priority_levels`

An ordered list (P1 highest → P4 lowest), each with a `min_composite` threshold, optional `escalate_if_any_dimension_exceeds`, and metadata (`response_sla`, `recommended_action`).

**How priority is assigned** ([agentic_triage/scoring/scorer.py:28](agentic_triage/scoring/scorer.py#L28)):

1. **Escalation override (runs first):** If any single dimension score exceeds `escalate_if_any_dimension_exceeds` (4.0 for P1), it immediately assigns P1 — regardless of composite. This catches cases where one dimension is catastrophic.
2. **Composite threshold walk:** Iterates levels highest→lowest, assigns the first level where `composite >= min_composite`.
3. **Fallback:** If nothing matches, assigns the last level (P4).

**Pre-filter shortcut** ([agentic_triage/agent/pre_filter.py:42](agentic_triage/agent/pre_filter.py#L42)): complaints with no matching keywords skip the LLM entirely and are auto-assigned `priority_levels[-1]` (P4) with all dimension scores set to 0.

**Finalize step** ([agentic_triage/agent/graph.py:220](agentic_triage/agent/graph.py#L220)): once the priority label is resolved, the matching level's `recommended_action` is looked up and written to the output state.
